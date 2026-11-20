"""Everything that touches the internet: search (YouTube Music, YouTube, SoundCloud, any pasted link), 'songs like this'
(YouTube's own mix of a song), stream links, saving a song for offline, covers and synced lyrics (lrclib.net).
Every call blocks: run them off the window's thread (tasks.run)."""
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .store import Track, split_artist

UA = 'Drift/1.0 (music player)'
SOURCES = (('ytmusic', 'YouTube Music'), ('youtube', 'YouTube'), ('soundcloud', 'SoundCloud'))
_streams = {}                       # uid -> (url, expires)
_lock = threading.Lock()
_yt_blocked_until = 0.0             # YouTube said "confirm you're not a bot" / 429: leave it alone for a while
BLOCK_PAUSE = 30 * 60


def blocked(track=None):
    """True while YouTube is refusing this connection (for `track`: only if it streams from YouTube)."""
    if track is not None and 'youtu' not in (track.page or ''):
        return False
    return time.time() < _yt_blocked_until


def _note_block(err):
    global _yt_blocked_until
    text = str(err).lower()
    if 'not a bot' in text or '429' in text or 'too many requests' in text:
        _yt_blocked_until = time.time() + BLOCK_PAUSE


def _ydl(**options):
    import yt_dlp
    base = {'quiet': True, 'no_warnings': True, 'noprogress': True, 'socket_timeout': 15, 'retries': 2,
            'extractor_retries': 1, 'skip_download': True, 'noplaylist': True, 'logger': _Quiet()}
    base.update(options)
    return yt_dlp.YoutubeDL(base)


class _Quiet:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def is_online(timeout=1.5):
    for host in (('1.1.1.1', 53), ('8.8.8.8', 53)):
        try:
            socket.create_connection(host, timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def is_url(text):
    return bool(re.match(r'^(https?://|www\.)\S+$', (text or '').strip()))


def _thumb(e, vid=None):
    if vid:
        return f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'
    thumbs = e.get('thumbnails') or []
    if thumbs:
        best = max(thumbs, key=lambda t: (t.get('width') or 0) * (t.get('height') or 0) or t.get('preference') or 0)
        return best.get('url')
    return e.get('thumbnail')


def entry_track(e, source=None):
    """A yt-dlp entry (flat or full) -> Track, or None when it is not a playable song (channel, playlist...)."""
    if not e or e.get('_type') == 'playlist':
        return None
    key = (e.get('ie_key') or e.get('extractor_key') or e.get('extractor') or '').lower()
    url = e.get('webpage_url') or e.get('url') or ''
    vid = e.get('id') if ('youtube' in key or 'youtube.com' in url or 'youtu.be' in url) else None
    if vid and not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid or ''):
        return None
    if vid:
        uid, src, page = f'yt:{vid}', 'youtube', f'https://www.youtube.com/watch?v={vid}'
    elif 'soundcloud' in key or 'soundcloud.com' in url:
        uid, src, page = f'sc:{e.get("id")}', 'soundcloud', url
    else:
        if not url:
            return None
        uid, src, page = f'web:{e.get("id") or url}', 'web', url
    uploader = e.get('artist') or e.get('creator') or e.get('channel') or e.get('uploader') or ''
    if isinstance(e.get('artists'), list) and e['artists']:
        uploader = ', '.join(e['artists'][:2])
    title, artist = split_artist(e.get('track') or e.get('title') or '', uploader)
    if e.get('track') and (e.get('artist') or e.get('artists')):
        title, artist = e['track'], uploader
    if not title:
        return None
    return Track(uid, title, artist, e.get('album') or '', int((e.get('duration') or 0) * 1000), page=page,
                 thumb=_thumb(e, vid), source=source or src)


_local = threading.local()


def ytm():
    """YouTube Music's own catalogue (songs with artist, album and length; radio; moods). One client per thread."""
    if getattr(_local, 'ytm', None) is None:
        from ytmusicapi import YTMusic
        _local.ytm = YTMusic()
    return _local.ytm


def ytm_track(x):
    """A YouTube Music song dict -> Track (None for videos without an id or unavailable songs)."""
    vid = x.get('videoId')
    if not vid or x.get('isAvailable') is False:
        return None
    artists = [a.get('name') for a in (x.get('artists') or []) if a.get('name')]
    secs = x.get('duration_seconds')
    if not secs and (x.get('length') or x.get('duration')):
        try:
            parts = [int(p) for p in str(x.get('length') or x.get('duration')).split(':')]
            secs = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        except ValueError:
            secs = 0
    thumbs = x.get('thumbnails') or x.get('thumbnail') or []
    thumb = max(thumbs, key=lambda t: t.get('width') or 0).get('url') if thumbs else None
    if thumb and 'lh3.googleusercontent' in thumb:
        thumb = re.sub(r'=w\d+-h\d+', '=w320-h320', thumb)
    title, artist = x.get('title') or '', ', '.join(artists[:2])
    if x.get('videoType') not in (None, 'MUSIC_VIDEO_TYPE_ATV'):
        title, guessed = split_artist(title, artist)
        artist = artist or guessed
    return Track(f'yt:{vid}', title, artist, (x.get('album') or {}).get('name') or '', int((secs or 0) * 1000),
                 page=f'https://www.youtube.com/watch?v={vid}', thumb=thumb, source='youtube')


def _ytm_list(items):
    out, seen = [], set()
    for x in items or []:
        t = ytm_track(x)
        if t and t.uid not in seen:
            seen.add(t.uid)
            out.append(t)
    return out


def moods():
    """[(section, [(title, params)])]: YouTube Music's moods and genres."""
    return [(k, [(c['title'], c['params']) for c in v]) for k, v in ytm().get_mood_categories().items()]


def _playlist_ids(node, out):
    """Every playlist link on a raw YouTube Music page, in page order (items without a link are skipped)."""
    if isinstance(node, dict):
        bid = (node.get('browseEndpoint') or {}).get('browseId') or ''
        if bid.startswith('VL') and bid[2:] not in out:
            out.append(bid[2:])
        for v in node.values():
            _playlist_ids(v, out)
    elif isinstance(node, list):
        for v in node:
            _playlist_ids(v, out)
    return out


def mood_playlists(params, n=3):
    """First playlist ids of a mood or genre. Read from the raw page: ytmusicapi's parser drops the whole page
    when one item has no link (YouTube Music's genre pages since Nov 2026)."""
    try:
        return [pl['playlistId'] for pl in ytm().get_mood_playlists(params)][:n]
    except (KeyError, TypeError, IndexError):
        page = ytm()._send_request('browse', {'browseId': 'FEmusic_moods_and_genres_category', 'params': params})
        return _playlist_ids(page, [])[:n]


def mood_tracks(params, n=60):
    """Songs from the first playlists of a mood or genre."""
    picked = []
    for pid in mood_playlists(params):
        try:
            picked.append(_ytm_list(ytm().get_playlist(pid, limit=40).get('tracks')))
        except Exception:
            continue
    from .taste import interleave
    return interleave(picked, n)


def _sized(url, px=320):
    """A Google image link at `px` square (they end in '=w120-h120-...')."""
    return re.sub(r'=w\d+-h\d+', f'=w{px}-h{px}', url or '') or None


def _artist_id(name):
    hits = ytm().search(name, filter='artists', limit=1)
    hit = next((h for h in hits if h.get('browseId')), None)
    return hit['browseId'] if hit else None


def artist(name=None, browse_id=None):
    """An artist on YouTube Music: {'name', 'id', 'thumb', 'radio', 'songs': [Track], 'related': [artist stubs]}.
    A stub is {'name', 'id', 'thumb'}."""
    browse_id = browse_id or _artist_id(name)
    if not browse_id:
        return None
    a = ytm().get_artist(browse_id)
    thumbs = a.get('thumbnails') or [{}]
    related = []
    for r in (a.get('related') or {}).get('results') or []:
        if r.get('browseId') and r.get('title'):
            related.append({'name': r['title'], 'id': r['browseId'],
                            'thumb': _sized(((r.get('thumbnails') or [{}])[-1]).get('url'))})
    return {'name': a.get('name') or name, 'id': browse_id, 'thumb': _sized(thumbs[0].get('url')),
            'radio': a.get('radioId'), 'songs': _ytm_list((a.get('songs') or {}).get('results')), 'related': related}


def discover(names, known=(), n=14):
    """New artists for you: the artists YouTube Music relates to the ones you like (`names`, best first), taken in
    turn so each favourite gets a say; artists you already have (`known`, normalised names) are left out.
    Each stub gains 'because' = the favourite it came from."""
    from .store import norm
    have = set(known) | {norm(x) for x in names}
    lists = []
    for name in names[:4]:
        try:
            a = artist(name)
        except Exception:
            continue
        if a:
            lists.append([dict(r, because=a['name']) for r in a['related'] if norm(r['name']) not in have])
    out, seen = [], set()
    for row in zip(*[x + [None] * (n - len(x)) for x in lists]) if lists else ():
        for r in row:
            if r and r['id'] not in seen and len(out) < n:
                seen.add(r['id'])
                out.append(r)
    return out


def sounds_like(browse_id, n=24):
    """Songs you would like if you like this artist: their YouTube Music radio (them plus similar artists), with
    a few of their own best songs first when the radio is short. Falls back to the radio of their top song."""
    a = artist(browse_id=browse_id)
    if not a:
        return []
    tracks = []
    if a.get('radio'):
        try:
            tracks = _ytm_list(ytm().get_watch_playlist(playlistId=a['radio'], limit=n + 6).get('tracks'))
        except Exception:
            tracks = []
    if len(tracks) < n // 2 and a['songs']:
        tracks = a['songs'][:3] + related(a['songs'][0], n)
    out, seen = [], set()
    for t in tracks:
        if t.uid not in seen:
            seen.add(t.uid)
            out.append(t)
    return out[:n]


def search(query, source='ytmusic', n=24):
    query = (query or '').strip()
    if not query:
        return []
    if is_url(query):
        target = query if query.startswith('http') else 'https://' + query
        with _ydl(extract_flat='in_playlist', playlistend=200, noplaylist=False) as y:
            info = y.extract_info(target, download=False)
        entries = info.get('entries') if info.get('_type') == 'playlist' else [info]
    else:
        if source == 'soundcloud':
            target = f'scsearch{n}:{query}'
        elif source == 'youtube':
            target = f'ytsearch{n}:{query}'
        else:
            try:
                return _ytm_list(ytm().search(query, filter='songs', limit=n))[:n]
            except Exception:
                target = 'https://music.youtube.com/search?q=' + urllib.parse.quote(query) + '#songs'
        with _ydl(extract_flat='in_playlist', playlistend=n, noplaylist=False) as y:
            info = y.extract_info(target, download=False)
        entries = info.get('entries') or []
    out, seen = [], set()
    for e in entries:
        t = entry_track(e)
        if t and t.uid not in seen:
            seen.add(t.uid)
            out.append(t)
    return out


def find_id(track, match=None):
    """A YouTube id for a song that has none (a local file): the best YouTube Music match for artist + title."""
    if track.video_id:
        return track.video_id
    try:
        hits = search(f'{track.artist} {track.title}'.strip(), 'ytmusic', 5 if match else 3)
    except Exception:
        return None
    return next((h.video_id for h in hits if h.video_id and (match is None or match(h) == match(track))), None)


def related(track, n=25, match=None):
    """Songs like `track`: YouTube's own mix for it (a radio of similar songs), first song (itself) dropped."""
    vid = find_id(track, match)
    if not vid:
        return []
    try:
        tracks = _ytm_list(ytm().get_watch_playlist(videoId=vid, radio=True, limit=n + 1).get('tracks'))
        tracks = [t for t in tracks if t.video_id != vid]
        if tracks:
            return tracks[:n]
    except Exception:
        pass
    with _ydl(extract_flat='in_playlist', playlistend=n + 1, noplaylist=False) as y:
        info = y.extract_info(f'https://www.youtube.com/watch?v={vid}&list=RD{vid}', download=False)
    out = []
    for e in info.get('entries') or []:
        t = entry_track(e)
        if t and t.video_id != vid:
            out.append(t)
    return out


def stream(track):
    """(audio URL, http headers) to play `track` without saving it; cached ~1 h (links expire after ~6 h)."""
    with _lock:
        hit = _streams.get(track.uid)
        if hit and hit[2] > time.time():
            return hit[0], hit[1]
    # plain https files only: the core fetches them whole (no HLS playlists)
    if blocked(track):
        raise RuntimeError('youtube is blocking streams for now')
    try:
        with _ydl(format='bestaudio[ext=m4a][protocol^=http]/bestaudio[protocol^=http][acodec!=none]/best[protocol^=http]') as y:
            info = y.extract_info(track.page, download=False)
    except Exception as e:
        _note_block(e)
        raise
    url = info.get('url')
    if not url and info.get('requested_formats'):
        url = info['requested_formats'][0].get('url')
    if not url:
        raise RuntimeError('no playable audio')
    if not track.duration and info.get('duration'):
        track.duration = int(info['duration'] * 1000)
    headers = info.get('http_headers') or {}
    with _lock:
        _streams[track.uid] = (url, headers, time.time() + 3600)
        if len(_streams) > 200:
            for k in sorted(_streams, key=lambda k: _streams[k][2])[:100]:
                _streams.pop(k, None)
    return url, headers


def cached_link(uid):
    """A fresh stream link already looked up for `uid`: (url, headers), else (None, {})."""
    with _lock:
        hit = _streams.get(uid)
    return (hit[0], hit[1]) if hit and hit[2] > time.time() else (None, {})


def core_cache(uid):
    """Where drift-core keeps a song it streamed (FNV-1a of the id, like core/src/audio.rs `cached`)."""
    from .store import DATA
    h = 0xcbf29ce484222325
    for b in uid.encode():
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return DATA / 'stream' / f'{h:016x}.m4a'


def forget_stream(uid):
    with _lock:
        _streams.pop(uid, None)


def safe_name(text, limit=120):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', ' ', text or '').strip().rstrip('.')
    return re.sub(r'\s{2,}', ' ', text)[:limit] or 'song'


def download(track, folder, progress=lambda done, total: None):
    """Save `track` into `folder` as '<artist> - <title> [id].<ext>' with title, artist and cover; returns the path."""
    from .library import write_tags
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tag = f' [{track.video_id}]' if track.video_id else ''
    stem = safe_name(f'{track.artist} - {track.title}' if track.artist else track.title) + tag

    played = core_cache(track.uid)
    if played.is_file() and track.video_id:   # just streamed it: the core already has the whole song
        import shutil
        path = folder / (stem + '.m4a')
        shutil.copyfile(played, path)
        cover = fetch(track.thumb, 3_000_000) if track.thumb else None
        write_tags(str(path), track, cover)
        _keep_words(track)
        return str(path)

    def hook(d):
        if d.get('status') == 'downloading':
            progress(d.get('downloaded_bytes') or 0, d.get('total_bytes') or d.get('total_bytes_estimate') or 0)

    opts = {'format': 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio', 'outtmpl': str(folder / (stem + '.%(ext)s')),
            'skip_download': False, 'progress_hooks': [hook], 'overwrites': False, 'continuedl': True}
    with _ydl(**opts) as y:
        info = y.extract_info(track.page, download=True)
        path = Path(y.prepare_filename(info))
    if not path.is_file():
        found = sorted(folder.glob(_glob_escape(stem) + '.*'), key=lambda p: p.stat().st_mtime, reverse=True)
        found = [p for p in found if not p.name.endswith('.part')]
        if not found:
            raise RuntimeError('the download left no file')
        path = found[0]
    cover = fetch(track.thumb, 3_000_000) if track.thumb else None
    write_tags(str(path), track, cover)
    _keep_words(track)
    return str(path)


def _keep_words(track):
    """A saved song keeps its lyrics too, so they show offline."""
    try:
        lyrics(track)
    except Exception:
        pass


def _glob_escape(text):
    return re.sub(r'([\[\]*?])', r'[\1]', text)


def fetch(url, limit=2_000_000, timeout=10):
    """Bytes of a small file (a cover); None on any failure."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit)
    except Exception:
        return None


def _kept(kind, key):
    """Where a small answer about `key` is kept on disk (DATA/<kind>/<hash>)."""
    import hashlib
    from .store import DATA
    d = DATA / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / hashlib.sha1(key.encode()).hexdigest()[:20]


def lyrics_kept(track):
    """The song's lyrics when they were found before (kept on disk, so they show offline); None when unknown."""
    try:
        return [tuple(x) for x in json.loads(_kept('lyrics', track.uid).with_suffix('.json').read_text('utf-8'))]
    except (OSError, ValueError, TypeError):
        return None


def lyrics(track):
    """Synced lyrics [(ms, line)] or plain [(None, line)] from lrclib.net (kept on disk once found); [] when none are
    known."""
    kept = lyrics_kept(track)
    if kept is not None:
        return kept
    out = _lrclib(track)
    if out:
        try:
            _kept('lyrics', track.uid).with_suffix('.json').write_text(json.dumps(out, ensure_ascii=False), 'utf-8')
        except OSError:
            pass
    return out


def _lrclib(track):
    q = {'track_name': track.title, 'artist_name': track.artist}
    if track.duration:
        q['duration'] = round(track.duration / 1000)
    data = None
    for url in ('https://lrclib.net/api/get?' + urllib.parse.urlencode(q),
                'https://lrclib.net/api/search?' + urllib.parse.urlencode({'q': f'{track.artist} {track.title}'})):
        raw = fetch(url, 1_000_000)
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, list):
            value = next((v for v in value if v.get('syncedLyrics')), value[0] if value else None)
        if value and (value.get('syncedLyrics') or value.get('plainLyrics')):
            data = value
            break
    if not data:
        return []
    if data.get('syncedLyrics'):
        out = []
        for line in data['syncedLyrics'].splitlines():
            for m in re.finditer(r'\[(\d+):(\d+(?:\.\d+)?)\]', line):
                text = re.sub(r'\[\d+:\d+(?:\.\d+)?\]', '', line).strip()
                out.append((int((int(m.group(1)) * 60 + float(m.group(2))) * 1000), text))
        return sorted(out)
    return [(None, line) for line in data['plainLyrics'].splitlines()]


def cover(url):
    """Cover image bytes, kept on disk so the same cover is fetched once."""
    name = _kept('covers', url).with_suffix('.img')
    if name.is_file():
        try:
            return name.read_bytes()
        except OSError:
            pass
    data = fetch(url)
    if data:
        try:
            name.write_bytes(data)
        except OSError:
            pass
    return data


def folder_size(folder):
    total = 0
    for root, _d, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
