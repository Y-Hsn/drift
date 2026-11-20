"""Drift's memory: one small SQLite file with every song it knows (local or online), what you liked, played and
skipped, your playlists and a few settings. Thread-safe: one connection behind a lock (writes are tiny)."""
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

DATA = Path(os.environ.get('DRIFT_DATA') or Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Drift')

SCHEMA = """
create table if not exists tracks(
  uid text primary key, title text not null, artist text default '', album text default '',
  duration integer default 0, path text, page text, thumb text, source text default 'file',
  liked integer default 0, plays integer default 0, skips integer default 0,
  last_played real default 0, added real default 0, mtime real default 0);
create index if not exists tracks_path on tracks(path);
create table if not exists playlists(id integer primary key, name text unique not null, created real);
create table if not exists playlist_items(playlist integer, pos integer, uid text, primary key(playlist, pos));
create table if not exists kv(k text primary key, v text);
"""

DEFAULTS = {
    'music_folder': str(Path.home() / 'Music' / 'Drift'),
    'save_played': False,      # on: songs streamed past 60% are saved for offline too (else only the save button)
    'drift': True,             # when the queue runs out, keep playing songs that fit your taste
    'close_to_tray': True,     # closing the window keeps the music going (tray icon)
    'volume': 0.6,
    'source': 'ytmusic',
    'sound_level': True,       # every song at the same loudness (measured by the core)
    'sound_tone': True,        # gently open the upper stereo image
    'whisper': True,           # the line being sung, under the title on Now
}


@dataclass
class Track:
    uid: str
    title: str
    artist: str = ''
    album: str = ''
    duration: int = 0          # ms
    path: str | None = None    # local file, when saved
    page: str | None = None    # web page it streams from
    thumb: str | None = None   # cover image URL
    source: str = 'file'       # file | youtube | soundcloud | web
    liked: int = 0
    plays: int = 0
    skips: int = 0
    last_played: float = 0.0
    added: float = 0.0
    mtime: float = 0.0
    extra: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def local(self):
        return bool(self.path) and os.path.isfile(self.path)

    @property
    def video_id(self):
        return self.uid[3:] if self.uid.startswith('yt:') else None

    @property
    def subtitle(self):
        return self.artist or 'Unknown artist'


COLUMNS = [f.name for f in fields(Track) if f.name != 'extra']


def row_track(row):
    return Track(**{k: row[k] for k in COLUMNS})


# --- names ----------------------------------------------------------------------------------------------------
_JUNK = re.compile(r'\s*[\(\[\{][^)\]\}]*\b(official|lyrics?|lyric video|audio|visuali[sz]er|clip|paroles|video|hd|4k|'
                   r'hq|mv|m/v|explicit|remaster(ed)?|color coded|traduction|مترجمة|كلمات)\b[^)\]\}]*[\)\]\}]', re.I)
_ID_TAG = re.compile(r'\s*\[[A-Za-z0-9_-]{11}\]\s*$')
_HASH = re.compile(r'\s+#\S+')
_TAIL = re.compile(r'\s*(?:[-–|]+\s*|\s)(official\s+)?(music\s+)?(official\s+music\s+video|music\s+video|lyrics?\s+video|lyrics?|video\s*clip|visuali[sz]er|official\s+video|official\s+audio|clip\s+officiel)\s*$', re.I)
_LEAD_NO = re.compile(r'^\d{1,2}\s*[-.]\s+')


def clean_title(title):
    """'07 - MAGHRIBI (lyric video) #27album [abc123def45]' -> 'MAGHRIBI'."""
    t = _ID_TAG.sub('', title or '')
    t = _JUNK.sub('', t)
    t = _HASH.sub('', t)
    t = _TAIL.sub('', t)
    t = _LEAD_NO.sub('', t)
    t = re.sub(r'\s{2,}', ' ', t).strip(' -–|·')
    return t or (title or '').strip()


def split_artist(title, artist=''):
    """Most uploads are 'Artist - Song': use that when the uploader is not the artist."""
    t = clean_title(title)
    a = re.sub(r'\s*-\s*Topic$|VEVO$', '', artist or '').strip()
    for sep in (' - ', ' – ', ' — ', ' | '):
        if sep in t:
            left, right = t.split(sep, 1)
            if left and right and len(left) < 60:
                return clean_title(right), left.strip()
    return t, a


def norm(text):
    """Comparison key: case, accents of spacing, 'feat.' tails and punctuation do not matter."""
    t = (text or '').lower()
    t = re.split(r'\s+(feat\.?|ft\.?|featuring|prod\.?|x)\s+', t)[0]
    return re.sub(r'[^\w]+', '', t)


def fold(text):
    """Lower case without accents: 'Télépopmusik' is found by 'telepop'."""
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD', (text or '').lower()) if not unicodedata.combining(c))


def matching(tracks, query):
    """The songs whose title, artist or album hold every word of `query` (a filter, or search while offline)."""
    words = fold(query).split()
    return [t for t in tracks if all(w in fold(f'{t.title} {t.artist} {t.album}') for w in words)]


# --- the store ------------------------------------------------------------------------------------------------
class Store:
    def __init__(self, path=None):
        path = Path(path) if path else DATA / 'drift.db'
        self.folder = path.parent
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = self._open(path)
        if not self._healthy():
            self.db = self._rebuild(path)
        with self.lock:
            self.db.executescript(SCHEMA)

    @staticmethod
    def _open(path):
        db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('pragma journal_mode=wal')
        db.execute('pragma synchronous=normal')
        return db

    def _healthy(self):
        """A damaged file ("database disk image is malformed") hid every song and artist: check it on every start.
        Most damage is only in an index, which reindex rebuilds from the rows."""
        try:
            if self.db.execute('pragma integrity_check(1)').fetchone()[0] == 'ok':
                return True
            self.db.execute('reindex')
            return self.db.execute('pragma integrity_check(1)').fetchone()[0] == 'ok'
        except sqlite3.DatabaseError:
            return False

    def _rebuild(self, path):
        """Copy every row that can still be read into a fresh file; keep the damaged one next to it."""
        fresh = path.with_name(path.name + '.new')
        for p in (fresh, Path(str(fresh) + '-wal'), Path(str(fresh) + '-shm')):
            p.unlink(missing_ok=True)
        new = sqlite3.connect(str(fresh), isolation_level=None)
        new.executescript(SCHEMA)
        for table in ('tracks', 'playlists', 'playlist_items', 'kv'):
            try:
                rows = self.db.execute(f'select * from {table}').fetchall()
            except sqlite3.DatabaseError:
                continue
            for row in rows:
                try:
                    new.execute(f'insert or ignore into {table} values({",".join("?" * len(row))})', tuple(row))
                except sqlite3.DatabaseError:
                    pass
        new.close()
        self.db.close()
        stamp = time.strftime('%Y%m%d-%H%M%S')
        for ext in ('', '-wal', '-shm'):
            old = Path(str(path) + ext)
            if old.exists():
                old.replace(path.with_name(f'{path.name}.damaged-{stamp}{ext}'))
        fresh.replace(path)
        return self._open(path)

    # settings
    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute('select v from kv where k=?', (key,)).fetchone()
        if row is None:
            return DEFAULTS.get(key, default)
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def set(self, key, value):
        with self.lock:
            self.db.execute('insert or replace into kv values(?, ?)', (key, json.dumps(value)))

    # tracks
    def upsert(self, track, keep_stats=True):
        """Insert or refresh a song; likes/plays/skips already known are kept, a known local file is never lost."""
        with self.lock:
            old = self.db.execute('select * from tracks where uid=?', (track.uid,)).fetchone()
            if old is not None and keep_stats:
                for k in ('liked', 'plays', 'skips', 'last_played', 'added'):
                    setattr(track, k, old[k])
                if not track.path and old['path']:
                    track.path = old['path']
                for k in ('thumb', 'page', 'album'):
                    if not getattr(track, k) and old[k]:
                        setattr(track, k, old[k])
            if not track.added:
                track.added = time.time()
            self.db.execute(f'insert or replace into tracks({",".join(COLUMNS)}) values({",".join("?" * len(COLUMNS))})',
                            [getattr(track, k) for k in COLUMNS])
        return track

    def track(self, uid):
        with self.lock:
            row = self.db.execute('select * from tracks where uid=?', (uid,)).fetchone()
        return row_track(row) if row else None

    def refresh(self, track):
        """Fill a track's stored stats (liked, path...) in place; returns it."""
        known = self.track(track.uid)
        if known:
            for k in ('liked', 'plays', 'skips', 'last_played', 'path'):
                setattr(track, k, getattr(known, k))
        return track

    def query(self, where='1', args=(), order='added desc', limit=None):
        sql = f'select * from tracks where {where} order by {order}' + (f' limit {int(limit)}' if limit else '')
        with self.lock:
            return [row_track(r) for r in self.db.execute(sql, args)]

    def local_tracks(self):
        return [t for t in self.query("path is not null and path != ''", order='artist collate nocase, title collate nocase')
                if t.local]

    def liked(self):
        return self.query('liked=1', order='last_played desc, added desc')

    def recent(self, n=20):
        return self.query('last_played>0', order='last_played desc', limit=n)

    def set_field(self, uid, **values):
        with self.lock:
            sets = ', '.join(f'{k}=?' for k in values)
            self.db.execute(f'update tracks set {sets} where uid=?', (*values.values(), uid))

    def played(self, uid):
        with self.lock:
            self.db.execute('update tracks set plays=plays+1, last_played=? where uid=?', (time.time(), uid))

    def skipped(self, uid):
        with self.lock:
            self.db.execute('update tracks set skips=skips+1, last_played=? where uid=?', (time.time(), uid))

    def forget_missing(self, existing_paths):
        """Files that are gone: online songs keep their row (still streamable), plain local files are dropped."""
        with self.lock:
            rows = self.db.execute("select uid, path, source from tracks where path is not null and path != ''").fetchall()
            for r in rows:
                if r['path'] not in existing_paths:
                    if r['source'] == 'file':
                        self.db.execute('delete from tracks where uid=?', (r['uid'],))
                    else:
                        self.db.execute('update tracks set path=null where uid=?', (r['uid'],))

    def drop_track(self, uid):
        """Forget a song entirely (its row and its places in playlists)."""
        with self.lock:
            self.db.execute('delete from playlist_items where uid=?', (uid,))
            self.db.execute('delete from tracks where uid=?', (uid,))

    # playlists
    def playlist_sizes(self):
        """{playlist id: number of songs}."""
        with self.lock:
            return {r[0]: r[1] for r in self.db.execute('select playlist, count(*) from playlist_items group by playlist')}

    def playlists(self):
        with self.lock:
            return [(r['id'], r['name']) for r in self.db.execute('select * from playlists order by created')]

    def new_playlist(self, name):
        with self.lock:
            cur = self.db.execute('insert or ignore into playlists(name, created) values(?, ?)', (name.strip(), time.time()))
            if cur.lastrowid:
                return cur.lastrowid
            return self.db.execute('select id from playlists where name=?', (name.strip(),)).fetchone()[0]

    def rename_playlist(self, pid, name):
        with self.lock:
            self.db.execute('update playlists set name=? where id=?', (name.strip(), pid))

    def drop_playlist(self, pid):
        with self.lock:
            self.db.execute('delete from playlist_items where playlist=?', (pid,))
            self.db.execute('delete from playlists where id=?', (pid,))

    def playlist_tracks(self, pid):
        with self.lock:
            rows = self.db.execute('select t.* from playlist_items i join tracks t on t.uid=i.uid where i.playlist=? '
                                   'order by i.pos', (pid,)).fetchall()
        return [row_track(r) for r in rows]

    def add_to_playlist(self, pid, tracks):
        with self.lock:
            pos = self.db.execute('select coalesce(max(pos), -1) from playlist_items where playlist=?', (pid,)).fetchone()[0]
            for t in tracks:
                self.upsert(t)
                pos += 1
                self.db.execute('insert into playlist_items values(?, ?, ?)', (pid, pos, t.uid))

    def remove_from_playlist(self, pid, uid):
        with self.lock:
            self.db.execute('delete from playlist_items where playlist=? and uid=?', (pid, uid))
