"""Turn shared music links into tracks the Drift core can play."""
import base64
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

from . import online
from .store import fold, norm

_SPOTIFY_PATH = re.compile(r'/(?:intl-[\w-]+/)?(track|album|playlist)/([A-Za-z0-9]{22})/?')
_STATE = re.compile(rb'<script\b[^>]*\bid="initialState"[^>]*>([^<]+)</script>')
_EMBED = re.compile(rb'<script\b[^>]*\bid="__NEXT_DATA__"[^>]*>(.+?)</script>', re.S)
_TAGS = re.compile(r'<[^>]+>')
MATCHERS = 4        # songs of a playlist looked up at the same time
_VERSION = re.compile(r'\b(live|acoustic|remix|demo|instrumental|karaoke|sped up|slowed|remaster\w*)\b')


def spotify_parts(url):
    parsed = urlsplit(url)
    if parsed.hostname in ('open.spotify.com', 'www.open.spotify.com'):
        match = _SPOTIFY_PATH.fullmatch(parsed.path)
        if match:
            return match.groups()
    return None


def _text(value):
    return ' '.join(html.unescape(_TAGS.sub('', value or '')).split())


def _spotify_share(kind, identifier):
    """The share page: name, description, the first 30 songs of a playlist (all of an album), the full count."""
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
        return item, [item], 1
    listing = item.get('tracksV2' if kind == 'album' else 'content') or {}
    rows = listing.get('items') or []
    tracks = [row.get('track') if kind == 'album' else (row.get('itemV2') or {}).get('data') for row in rows]
    return item, tracks, max(len(tracks), listing.get('totalCount') or 0)


def _spotify_embed(kind, identifier):
    """The embed page lists up to 100 songs (title, artists, length), in the share page's song shape."""
    page = online.fetch(f'https://open.spotify.com/embed/{kind}/{identifier}', limit=5_000_000)
    found = _EMBED.search(page or b'')
    if not found:
        return None, []
    try:
        entity = json.loads(found[1])['props']['pageProps']['state']['data']['entity']
    except (ValueError, KeyError, TypeError):
        return None, []
    tracks = [{'__typename': 'Track', 'name': row.get('title'),
               'artists': {'items': [{'profile': {'name': name.strip()}}
                                     for name in (row.get('subtitle') or '').split(',\xa0') if name.strip()]},
               'duration': {'totalMilliseconds': row.get('duration') or 0}}
              for row in entity.get('trackList') or () if row.get('isPlayable', True) and row.get('title')]
    return entity, tracks


def _spotify_catalog(kind, identifier):
    """(about, songs, how many the list has). Public pages only: no Spotify session, credentials or audio."""
    try:
        item, tracks, total = _spotify_share(kind, identifier)
    except ValueError:
        item, tracks, total = {}, [], 0
    if kind != 'track' and (not tracks or len(tracks) < total):
        try:
            entity, more = _spotify_embed(kind, identifier)
        except Exception:
            entity, more = None, []
        if len(more) > len(tracks):
            tracks, total = more, max(total, len(more))
            item = item or {'name': (entity or {}).get('name'), 'description': (entity or {}).get('subtitle')}
    if not tracks:
        raise ValueError('Spotify could not show the details for this link.')
    note = _text(item.get('description'))
    if kind == 'album' and not note:
        artists = [a['profile']['name'] for a in (item.get('artists') or {}).get('items') or ()
                   if (a.get('profile') or {}).get('name')]
        year = (item.get('date') or {}).get('year')
        note = ' · '.join(filter(None, [', '.join(artists), str(year or '')]))
    about = {'kind': kind, 'name': _text(item.get('name')) or f'Spotify {kind}', 'note': note, 'source': 'Spotify'}
    return about, tracks, max(len(tracks), total)


def _key(name):
    name = re.split(r'\s*[\[(]\s*(?:feat\.?|ft\.?|with)\s+', fold(name), maxsplit=1)[0]
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


def _matched_in_order(items, cancelled):
    """Yield each item's match (or None) in list order, looking up MATCHERS songs at a time. The first one is also
    checked to play, so a list starts on a song that works."""
    pool = ThreadPoolExecutor(MATCHERS)
    try:
        jobs = [pool.submit(_match, item, index == 0) for index, item in enumerate(items)]
        for job in jobs:
            if cancelled.is_set():
                break
            try:
                yield job.result()
            except Exception:
                yield None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def tracks_from_link(url, cancelled, emit):
    """Report what a link holds: first, for a list (playlist, album), {'about': {kind, name, note, source,
    total}}; then the playable matches in order. Returns the number of entries with no match."""
    url = url if '://' in url else 'https://' + url
    spotify = spotify_parts(url)
    if spotify:
        about, tracks, missing = _spotify_catalog(*spotify)
        if spotify[0] != 'track':
            emit({'about': {**about, 'total': missing}})
        for track in _matched_in_order(tracks, cancelled):
            if track:
                emit(track)
                missing -= 1
        return missing

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    youtube = parsed.hostname in ('youtu.be', 'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com')
    is_video = youtube and (parsed.hostname == 'youtu.be' or 'v' in query or parsed.path.startswith(('/shorts/', '/live/')))
    with online._ydl(extract_flat='in_playlist', noplaylist=is_video, ignoreerrors='only_download') as extractor:
        info = extractor.extract_info(url, download=False) or {}
        entries = info.get('entries') if 'entries' in info else [info]
        if 'entries' in info and not is_video:
            entries = list(entries or ())
            emit({'about': {'kind': 'playlist', 'name': _text(info.get('title')) or 'Playlist',
                            'note': _text(info.get('description'))[:300], 'source': info.get('extractor_key') or '',
                            'total': len(entries)}})
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
