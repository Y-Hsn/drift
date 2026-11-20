"""Lyrics, fetched once per song (and kept on disk, so they show offline), shared by the Lyrics page and the whisper
line on Now."""
from PyQt6.QtCore import QObject, pyqtSignal

from .. import online, tasks

KEEP = 60


class LyricsFeed(QObject):
    ready = pyqtSignal(str)     # uid: its lines are known now (maybe none)

    def __init__(self):
        super().__init__()
        self.cache = {}          # uid -> [(ms | None, line)]
        self.waiting = set()

    def known(self, track):
        """True when the song's lyrics are at hand without the internet (in memory, or kept on disk)."""
        if track.uid not in self.cache:
            kept = online.lyrics_kept(track)
            if kept is None:
                return False
            self._got(track.uid, kept, quiet=True)
        return True

    def lines(self, track):
        """The song's lines when known; None while unknown (a fetch starts; `ready` follows)."""
        if track is None:
            return []
        if track.uid in self.cache:
            return self.cache[track.uid]
        self.fetch(track)
        return None

    def fetch(self, track):
        uid = track.uid
        if uid in self.waiting or uid in self.cache:
            return
        self.waiting.add(uid)
        tasks.run(online.lyrics, track, done=lambda lines: self._got(uid, lines), failed=lambda e: self._got(uid, []))

    def _got(self, uid, lines, quiet=False):
        self.waiting.discard(uid)
        self.cache[uid] = lines or []
        while len(self.cache) > KEEP:
            self.cache.pop(next(iter(self.cache)))
        if not quiet:
            self.ready.emit(uid)


def line_at(lines, ms):
    """Index of the synced line being sung at `ms` (-1 before the first)."""
    i = -1
    for k, (t, _text) in enumerate(lines):
        if t is None:
            return -1
        if t <= ms + 250:
            i = k
        else:
            break
    return i


feed = None


def shared():
    global feed
    if feed is None:
        feed = LyricsFeed()
    return feed
