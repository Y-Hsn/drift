"""Turn shared music links into tracks the Drift core can play."""
import base64
import json
import re
from urllib.parse import parse_qs, urlsplit

from . import online
from .store import fold, norm

_SPOTIFY_PATH = re.compile(r'/(?:intl-[\w-]+/)?(track|album|playlist)/([A-Za-z0-9]{22})/?')
_STATE = re.compile(rb'<script\b[^>]*\bid="initialState"[^>]*>([^<]+)</script>')
_VERSION = re.compile(r'\b(live|acoustic|remix|demo|instrumental|karaoke|sped up|slowed|remaster\w*)\b')


def spotify_parts(url):
    parsed = urlsplit(url)
    if parsed.hostname in ('open.spotify.com', 'www.open.spotify.com'):
        match = _SPOTIFY_PATH.fullmatch(parsed.path)
        if match:
            return match.groups()
    return None


def _spotify_catalog(kind, identifier):
    # Public share-page metadata only. No Spotify client session, credentials or audio.
    page = online.fetch(f'https://open.spotify.com/{kind}/{identifier}', limit=5_000_000)
    state = _STATE.search(page or b'')
    if not state:
        raise ValueError('Spotify could not show the details for this link.')
    try:
        entities = json.loads(base64.b64decode(state[1]))['entities']['items']
        item = entities[f'spotify:{kind}:{identifier}']
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('Spotify could not show the details for this link.') from exc
    if kind == 'track':
        return [item], 1
    listing = item.get('tracksV2' if kind == 'album' else 'content') or {}
    rows = listing.get('items') or []
    tracks = [row.get('track') if kind == 'album' else (row.get('itemV2') or {}).get('data') for row in rows]
    return tracks, max(len(tracks), listing.get('totalCount') or 0)


def _key(name):
    name = re.split(r'\s*[\[(]\s*(?:feat\.?|ft\.?)\s+', fold(name), maxsplit=1)[0]
    return norm(name)


def _matches(track, title, artists, duration):
    if _key(track.title) != _key(title) or not track.artist:
        return False
    if set(_VERSION.findall(fold(title))) != set(_VERSION.findall(fold(track.title))):
        return False
    credited = {_key(name) for name in track.artist.split(', ')} | {_key(track.artist)}
    return bool(credited.intersection(map(_key, artists))) and (
        not duration or not track.duration or abs(track.duration - duration) <= 5000)


def _match(item, prepare):
    if not item or item.get('__typename') != 'Track' or not item.get('name'):
        return None
    title = item['name']
    credits = (item.get('artists') or {}).get('items') or [
        a for key in ('firstArtist', 'otherArtists') for a in (item.get(key) or {}).get('items', [])]
    artists = [a['profile']['name'] for a in credits
               if (a.get('profile') or {}).get('name')]
    if not artists:
        return None
    duration = (item.get('duration') or {}).get('totalMilliseconds') or 0
    query = f'{artists[0]} {title}'
    for source in ('ytmusic', 'youtube', 'soundcloud'):
        if source != 'soundcloud' and online.blocked():
            continue
        try:
            candidates = online.search(query, source, 6)
        except Exception:
            continue
        matches = [t for t in candidates if _matches(t, title, artists, duration)]
        matches.sort(key=lambda t: abs(t.duration - duration) if t.duration and duration else float('inf'))
        for track in matches[:2]:
            if prepare:
                try:
                    online.stream(track)
                except Exception:
                    continue
            return track
    return None


def tracks_from_link(url, cancelled, emit):
    """Report playable matches in order, then return the number of missing entries."""
    url = url if '://' in url else 'https://' + url
    spotify = spotify_parts(url)
    if spotify:
        tracks, missing = _spotify_catalog(*spotify)
        first = True
        for item in tracks:
            if cancelled.is_set():
                break
            track = _match(item, prepare=first)
            if track:
                emit(track)
                first = False
                missing -= 1
        return missing

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    youtube = parsed.hostname in ('youtu.be', 'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com')
    is_video = youtube and (parsed.hostname == 'youtu.be' or 'v' in query or parsed.path.startswith(('/shorts/', '/live/')))
    with online._ydl(extract_flat='in_playlist', noplaylist=is_video, ignoreerrors='only_download') as extractor:
        info = extractor.extract_info(url, download=False) or {}
        entries = info.get('entries') if 'entries' in info else [info]
        missing = 0
        first = True
        for entry in entries or ():
            if cancelled.is_set():
                break
            track = online.entry_track(entry)
            if track:
                if first:
                    try:
                        online.stream(track)
                    except Exception:
                        if is_video or online.blocked(track) or 'entries' not in info:
                            raise
                        missing += 1
                        continue
                emit(track)
                first = False
            else:
                missing += 1
    return missing
