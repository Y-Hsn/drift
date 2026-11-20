"""Covers, loaded once off the window's thread and kept small: a bounded cache of 320 px images plus per-size pixmaps."""
from collections import OrderedDict

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from .. import library, online, tasks
from . import theme

MAX_IMAGES = 160


def _load(track):
    data = None
    if track.local:
        data = library.cover_bytes(track.path)
    if not data and track.thumb:
        data = online.cover(track.thumb)
    img = theme.image_from(data)
    if img is not None and max(img.width(), img.height()) > 320:
        img = img.scaled(320, 320, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    return img


class Covers(QObject):
    ready = pyqtSignal(str)     # uid

    def __init__(self):
        super().__init__()
        self.images = OrderedDict()   # uid -> QImage | None (None = has no cover)
        self.pix = OrderedDict()      # (uid, size, radius) -> QPixmap
        self.waiting = set()

    def image(self, track):
        if track.uid in self.images:
            self.images.move_to_end(track.uid)
            return self.images[track.uid]
        self._request(track)
        return None

    def get(self, track, size, radius=6):
        """A rounded cover at `size`; a placeholder until the real one arrives (ready(uid) fires then)."""
        key = (track.uid, size, radius)
        if key in self.pix:
            self.pix.move_to_end(key)
            return self.pix[key]
        img = self.image(track)
        if track.uid in self.images:
            px = theme.rounded(img, size, radius) if img is not None else None
            px = px or theme.placeholder(track.artist or track.title, size, radius)
            self.pix[key] = px
            while len(self.pix) > 400:
                self.pix.popitem(last=False)
            return px
        return theme.placeholder(track.artist or track.title, size, radius)

    def _request(self, track):
        if track.uid in self.waiting:
            return
        self.waiting.add(track.uid)
        tasks.run(_load, track, done=lambda img: self._done(track.uid, img), failed=lambda e: self._done(track.uid, None))

    def _done(self, uid, img):
        self.waiting.discard(uid)
        self.images[uid] = img
        while len(self.images) > MAX_IMAGES:
            old, _ = self.images.popitem(last=False)
            for k in [k for k in self.pix if k[0] == old]:
                self.pix.pop(k, None)
        for k in [k for k in self.pix if k[0] == uid]:
            self.pix.pop(k, None)
        self.ready.emit(uid)

    def forget(self, uid):
        self.images.pop(uid, None)
        for k in [k for k in self.pix if k[0] == uid]:
            self.pix.pop(k, None)


def keep_for_card(uid, img):
    """The music core's player card (Pause key) shows the cover too, even with this window closed: it reads it from
    DATA/art/<fnv32 of the song id>.png. Written once per song, small (160 px); the folder is kept to ~400 covers."""
    if img is None:
        return
    from ..store import DATA
    h = 0x811c9dc5
    for b in uid.encode():
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    folder = DATA / 'art'
    path = folder / f'{h:08x}.png'
    if path.is_file():
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        small = img.scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        small.save(str(path), 'PNG')
        files = sorted(folder.glob('*.*'), key=lambda f: f.stat().st_mtime)
        for old in files[:-400]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


covers = None


def shared():
    global covers
    if covers is None:
        covers = Covers()
    return covers
