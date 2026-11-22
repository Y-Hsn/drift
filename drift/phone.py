"""The PC side of Drift on the phone (docs/phone-sync.md): what goes on each phone's shelf, getting radio songs and
wishes ready for it, and taking in what happened on the phone. The core's door serves the files and checks every
request; it runs this as `Drift.exe --net phone-shelf|phone-fill|phone-events` (helper.py) so Python only wakes
for a sync.

DATA/phone/
  devices.json            paired phones (written by the core's door)
  files/                  songs fetched for phones (radio picks, wishes): not part of your library
  <device>/shelf.json     the manifest the door answers with; files{} maps song ids to paths for the door
  <device>/crate.json     radio picks and wishes waiting on that phone, newest picks last
  <device>/inbox/*.json   event batches from the phone, applied once (seq per device)"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import online, taste
from .store import DATA, Store, Track

ROOT = DATA / 'phone'
FILES = ROOT / 'files'
KINDS = {'.m4a': 'audio/mp4', '.mp4': 'audio/mp4', '.aac': 'audio/aac', '.mp3': 'audio/mpeg', '.flac': 'audio/flac',
         '.wav': 'audio/wav', '.ogg': 'audio/ogg'}
PHONE_PLAYS = set(KINDS) - {'.ogg'}           # what iPhone Safari plays (no Ogg/Vorbis)
FILL_AT_ONCE = 12                             # radio songs fetched per sync, so a sync never takes long
GB = 1024 ** 3


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def device_dir(device):
    if not device or not all(c.isalnum() or c in '-_' for c in device):
        raise ValueError('bad device id')
    return ROOT / device


# ---- file details, kept so a sync never re-reads every song ----

_hashes = None


def _details(path):
    """(bytes, sha256) of a song file, from a cache keyed by path + size + mtime."""
    global _hashes
    if _hashes is None:
        _hashes = _read(ROOT / 'hashes.json', {})
    st = os.stat(path)
    key = f'{path}|{st.st_size}|{int(st.st_mtime)}'
    if key not in _hashes:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        _hashes[key] = h.hexdigest()
    return st.st_size, _hashes[key]


def _save_hashes():
    if _hashes is not None:
        live = {k: v for k, v in _hashes.items()
                if os.path.isfile(k.split('|')[1] if k.startswith(('cover|', 'look|')) else k.split('|')[0])}
        _write(ROOT / 'hashes.json', live)


def _cover(t, path, fetch=False):
    """A cover file for the phone: the one Drift already keeps for the song's picture link, else the picture inside
    the file (extracted once), else (fetch=True, in the background fill) downloaded. None when there is none."""
    global _hashes
    kept = online._kept('covers', t.thumb).with_suffix('.img') if t.thumb else None
    if kept and kept.is_file():
        return str(kept)
    out = ROOT / 'covers' / (hashlib.sha1(t.uid.encode()).hexdigest()[:20] + '.jpg')
    if out.is_file():
        return str(out)
    if _hashes is None:
        _hashes = _read(ROOT / 'hashes.json', {})
    tried = f'cover|{path}|{int(os.path.getmtime(path))}'
    data = None
    if tried not in _hashes:
        _hashes[tried] = ''
        try:
            from .library import cover_bytes
            data = cover_bytes(path)
        except Exception:
            data = None
    if not data and fetch and kept:
        try:
            data = online.cover(t.thumb)
        except Exception:
            data = None
        if data and kept.is_file():
            return str(kept)
    look = f'look|{path}|{t.uid}'
    if not data and fetch and look not in _hashes:      # no picture anywhere: the same song on YouTube Music has one
        _hashes[look] = ''
        data = _look_up_cover(t)
    if data:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return str(out)
    return None


def _look_up_cover(t):
    """Cover bytes of the same song on YouTube Music (same title; same first artist when the song has one), or None."""
    from .store import clean_title, norm
    title = norm(clean_title(t.title))
    if not title:
        return None
    artist = norm((t.artist or '').split(',')[0])
    try:
        hits = online.search(f'{t.artist or ""} {clean_title(t.title)}'.strip(), 'ytmusic', 5)
    except Exception:
        return None
    for h in hits:
        same = norm(clean_title(h.title))
        same_title = same == title or same.startswith(title) or title.startswith(same)
        same_artist = not artist or artist in norm(h.artist) or norm((h.artist or '').split(',')[0]) in artist
        if h.thumb and same and same_title and same_artist:
            try:
                return online.cover(online._sized(h.thumb, 544) if 'googleusercontent.com' in h.thumb else h.thumb)
            except Exception:
                return None
    return None


def _song(t, path, why, score):
    size, sha = _details(path)
    return {'id': t.uid, 'title': t.title, 'artist': t.artist, 'album': t.album, 'secs': round((t.duration or 0) / 1000),
            'bytes': size, 'sha256': sha, 'type': KINDS.get(Path(path).suffix.lower(), 'audio/mp4'),
            'cover': bool(t.thumb), 'liked': bool(t.liked), 'why': why, 'score': round(score, 3)}


# ---- the shelf ----

def shelf(store, device, size=2 * GB, fetch_covers=False):
    """Pick what goes on this phone, most wanted first, up to `size` bytes: wishes, liked songs, songs in playlists,
    then the radio crate. Writes <device>/shelf.json (with the file paths for the door) and returns it."""
    size = max(int(size or 0), 256 * 1024 ** 2)
    crate = _read(device_dir(device) / 'crate.json', [])
    now = time.time()
    picked, files, covers, used = [], {}, {}, 0

    def add(t, path, why, score):
        nonlocal used
        if t.uid in files or not path or Path(path).suffix.lower() not in PHONE_PLAYS or not os.path.isfile(path):
            return
        if used + os.path.getsize(path) > size:       # check room first: only songs that go are read (sha256)
            return
        s = _song(t, path, why, score)
        used += s['bytes']
        c = _cover(t, path, fetch_covers)
        s['cover'] = bool(c)
        if c:
            covers[t.uid] = c
        picked.append(s)
        files[t.uid] = str(path)

    known = {t.uid: t for t in store.query('1', order='added desc')}
    for c in crate:                                     # wishes first: the phone asked for these
        if c.get('wish') and c['uid'] in known:
            add(known[c['uid']], c['path'], 'You asked for it', 2.0)
    for t in store.liked():
        if t.local:
            add(t, t.path, 'Liked', 1.0 + taste.song_score(t, now) / 10)
    playlists = []
    for pid, name in store.playlists():
        items = []
        for i, t in enumerate(store.playlist_tracks(pid)):
            path = t.path if t.local else next((c['path'] for c in crate if c['uid'] == t.uid), None)
            add(t, path, name, 0.9)
            if t.uid in files:
                items.append({'item': f'{pid}:{i}', 'song': t.uid})
        if items:
            playlists.append({'id': str(pid), 'name': name, 'items': items})
    for c in reversed(crate):                           # newest radio picks first
        if not c.get('wish') and c['uid'] in known:
            add(known[c['uid']], c['path'], 'Drift radio', 0.5 + c.get('score', 0) / 10)
    for t in store.local_tracks():                      # room left: the rest of your saved songs
        add(t, t.path, 'Saved', 0.4 + taste.song_score(t, now) / 20)

    weights = taste.artist_weights(list(known.values()))
    top = max(weights.values(), default=1) or 1
    taste_snap = {'artists': {a: round(w / top, 3) for a, w in sorted(weights.items(), key=lambda x: -x[1])[:300]},
                  'songs': {s['id']: s['score'] for s in picked}}
    rev = hashlib.sha256(json.dumps([[s['id'], s['sha256'], s['cover'], s['liked']] for s in picked] + playlists,
                                    sort_keys=True).encode()).hexdigest()[:16]
    out = {'rev': rev, 'songs': picked, 'playlists': playlists, 'taste': taste_snap, 'bytes': used, 'files': files,
           'covers': covers, 'made': now}
    _write(device_dir(device) / 'shelf.json', out)
    _save_hashes()
    return out


def fill(store, device, size=2 * GB, at_once=FILL_AT_ONCE):
    """Get radio songs (and wishes) ready for this phone while there is room: Drift radio's picks for this phone,
    fetched into DATA/phone/files. A few per sync; the phone collects them at its next sync."""
    if online.blocked() or not online.is_online():
        return 0
    d = device_dir(device)
    crate = _read(d / 'crate.json', [])
    got = 0
    for w in _read(d / 'wishes.json', []):             # wishes first
        if got >= at_once or w.get('done'):
            continue
        known = store.track(w['uid']) if w.get('uid') else None
        hits = [known] if known else online.search(w['query'], 'ytmusic', 1)
        if hits:
            t = store.upsert(hits[0])
            path = t.path if t.local else _fetch(t)
            if path:
                crate.append({'uid': t.uid, 'path': path, 'wish': True, 'at': time.time()})
                got += 1
        w['done'] = True
    _write(d / 'wishes.json', [w for w in _read(d / 'wishes.json', []) if not w.get('done')])
    crate_bytes = sum(os.path.getsize(c['path']) for c in crate if os.path.isfile(c['path']))
    room = size * 0.6 - crate_bytes                    # radio may take up to 60 % of the shelf
    if got < at_once and room > 0:
        have = {c['uid'] for c in crate}
        saved = store.local_tracks()
        radio = taste.Radio.from_store(store)
        catalog = taste.RadioCatalog(lambda t, n: online.related(t, n, match=taste.recording))
        for _round in range(4):
            picks = [t for t in radio.plan(catalog, saved, n=6) if t.uid not in have]
            if not picks:
                break
            for t in picks:
                if got >= at_once or room <= 0:
                    break
                t = store.upsert(t)
                path = t.path if t.local else _fetch(t)
                if not path:
                    continue
                crate.append({'uid': t.uid, 'path': path, 'score': taste.song_score(t), 'at': time.time()})
                have.add(t.uid)
                room -= os.path.getsize(path)
                got += 1
                radio.queue.append(t)                   # the next round drifts on from here, never repeats
                radio.excluded_ids.add(t.uid)
                radio.excluded.add(taste.recording(t))
            if got >= at_once or room <= 0:
                break
    _write(d / 'crate.json', crate[-600:])
    return got


def _fetch(t):
    try:
        return online.download(t, FILES)
    except Exception as e:
        print('phone: could not fetch', t.uid, e)
        return None


def tidy():
    """Remove fetched songs no phone's crate keeps any more."""
    keep = set()
    for crate in ROOT.glob('*/crate.json'):
        keep |= {os.path.normcase(c['path']) for c in _read(crate, [])}
    for f in FILES.glob('*') if FILES.is_dir() else ():
        if os.path.normcase(str(f)) not in keep and time.time() - f.stat().st_mtime > 3600:
            try:
                f.unlink()
            except OSError:
                pass


# ---- what happened on the phone ----

def apply_events(store, device, events):
    """Plays, skips, likes and wishes from the phone, each applied once (per-device seq). Returns the highest
    seq now stored."""
    d = device_dir(device)
    seen = _read(d / 'seen.json', {'seq': 0})
    last = seen['seq']
    wishes = _read(d / 'wishes.json', [])
    crate = _read(d / 'crate.json', [])
    for ev in sorted(events, key=lambda e: e.get('seq', 0)):
        seq = int(ev.get('seq') or 0)
        if seq <= last:
            continue                                    # already counted
        if seq != last + 1:
            break                                       # a gap: wait until the phone sends the missing ones
        kind, song = ev.get('kind'), ev.get('song') or ''
        if song and ev.get('meta'):
            _know(store, song, ev['meta'])
        if kind == 'play' and song:
            store.played(song)
        elif kind == 'skip' and song:
            store.skipped(song)
            crate = [c for c in crate if c['uid'] != song or c.get('wish')]
        elif kind == 'like' and song:
            store.set_field(song, liked=1 if ev.get('on') else 0)
            if ev.get('on') and ev.get('meta'):            # liked while streaming: fetch it for the shelf
                wishes.append({'query': f"{ev['meta'].get('artist', '')} {ev['meta'].get('title', '')}".strip(),
                               'uid': song, 'at': ev.get('at')})
        elif kind == 'wish' and (ev.get('query') or '').strip():
            wishes.append({'query': ev['query'].strip()[:200], 'at': ev.get('at')})
        last = seq
    _write(d / 'wishes.json', wishes)
    _write(d / 'crate.json', crate)
    _write(d / 'seen.json', {'seq': last})
    return last


def _know(store, uid, meta):
    """A song the phone streamed from YouTube (the Android app, id "yt:<video id>"): the PC keeps it like any
    YouTube song it found itself (it plays and saves from its page). The page is rebuilt from the id, never taken
    from the phone."""
    vid = uid[3:] if uid.startswith('yt:') else ''
    if store.track(uid) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
        return
    try:
        secs = max(0, int(float(meta.get('secs') or 0)))
    except (TypeError, ValueError):
        secs = 0
    art = str(meta.get('art') or '')
    store.upsert(Track(uid, str(meta.get('title') or '')[:300], str(meta.get('artist') or '')[:200],
                       duration=secs * 1000, page=f'https://www.youtube.com/watch?v={vid}',
                       thumb=art[:500] if art.startswith('https://') else None,
                       source='youtube', extra={'from': 'phone'}))


def inbox(store, device):
    """Apply every event batch the door left for this phone, oldest first."""
    folder = device_dir(device) / 'inbox'
    last = None
    for f in sorted(folder.glob('*.json')) if folder.is_dir() else ():
        last = apply_events(store, device, _read(f, {}).get('events', []))
        f.unlink(missing_ok=True)
    return last


def run(op, device, size=None):
    """Entry for the helper (`Drift.exe --net phone-<op> --device <id> --size <bytes>`)."""
    store = Store()
    if op == 'events':
        return {'ack': inbox(store, device)}
    if op == 'shelf':
        inbox(store, device)
        return {'rev': shelf(store, device, size or 2 * GB)['rev']}
    if op == 'fill':
        inbox(store, device)
        got = fill(store, device, size or 2 * GB)
        rev = shelf(store, device, size or 2 * GB, fetch_covers=True)['rev']
        tidy()
        return {'got': got, 'rev': rev}
    raise ValueError(op)
