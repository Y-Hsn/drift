"""Discover (top of Explore): a row of round artist portraits Drift thinks you would like (related to the artists you
play), a thin line, then a grid of songs that sound like the chosen artist. Click a portrait to change the grid, a
tile to play it with the rest after it."""
import time

from PyQt6.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from .. import online, tasks
from ..store import Track
from . import theme
from .covers import shared as covers
from .widgets import heading, label, pill

FRESH = 1800            # seconds a fetched answer stays good
_artists = {}           # seed key -> (time, [artist stubs])
_songs = {}             # artist id -> (time, [Track])
_chosen = {}            # seed key -> chosen artist id (kept while the window lives)


def _fresh(cache, key):
    hit = cache.get(key)
    return hit[1] if hit and time.time() - hit[0] < FRESH else None


def portrait(stub):
    """An artist as a Track-shaped key for the shared cover cache (round portraits load like covers)."""
    return Track('ar:' + stub['id'], stub['name'], stub['name'], thumb=stub.get('thumb'), source='youtube')


class _Hoverable(QWidget):
    clicked = pyqtSignal()

    def __init__(self, track):
        super().__init__()
        self.track = track
        self.hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        covers().ready.connect(self._cover_ready)   # a bound method: let go when this widget is deleted

    def _cover_ready(self, uid):
        if uid == self.track.uid:
            self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class ArtistBubble(_Hoverable):
    """A round portrait, the name under it and, small, who it comes from. The chosen one wears an accent ring."""
    SIZE = 104

    def __init__(self, stub):
        super().__init__(portrait(stub))
        self.stub = stub
        self.chosen = False
        self.setFixedSize(self.SIZE + 20, self.SIZE + 52)
        self.setToolTip(f"Like {stub['because']}" if stub.get('because') else stub['name'])

    def set_chosen(self, on):
        self.chosen = on
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.SIZE
        x = (self.width() - s) / 2
        circle = QRectF(x, 4, s, s)
        img = covers().image(self.track)
        px = theme.rounded(img, s, s / 2) if img is not None else None
        p.drawPixmap(int(x), 4, px or theme.placeholder(self.stub['name'], s, s / 2))
        if self.chosen or self.hover:
            ring = QColor(theme.accent.color if self.chosen else theme.MUTED)
            p.setPen(QPen(ring, 2.4 if self.chosen else 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle.adjusted(-3.5, -3.5, 3.5, 3.5))
        w = self.width() - 4
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(9.6, 600))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(2, s + 12, w, 18), Qt.AlignmentFlag.AlignHCenter,
                   fm.elidedText(self.stub['name'], Qt.TextElideMode.ElideRight, w))
        if self.stub.get('because'):
            p.setPen(QColor(theme.MUTED))
            p.setFont(theme.font(8.4, 400))
            fm = QFontMetrics(p.font())
            p.drawText(QRectF(2, s + 30, w, 16), Qt.AlignmentFlag.AlignHCenter,
                       fm.elidedText('Like ' + self.stub['because'], Qt.TextElideMode.ElideRight, w))


class SongTile(_Hoverable):
    """A song as a cover card: title and artist under it, a play button on hover; right click for the song menu."""
    SIZE = 150
    menu = pyqtSignal(QPoint)

    def __init__(self, track):
        super().__init__(track)
        self.setFixedSize(self.SIZE, self.SIZE + 48)
        self.setToolTip(f'{track.title} · {track.subtitle}')

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.menu.emit(e.globalPosition().toPoint())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.SIZE
        p.drawPixmap(0, 0, covers().get(self.track, s, 12))
        if self.hover:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, s, s), 12, 12)
            p.fillPath(path, QColor(0, 0, 0, 90))
            p.setBrush(QColor(theme.TEXT))
            p.setPen(Qt.PenStyle.NoPen)
            c = QRectF(s - 50, s - 50, 38, 38)
            p.drawEllipse(c)
            p.drawPixmap(int(c.left() + 10), int(c.top() + 10), theme.pixmap('play', '#0b0b0d', 18))
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(9.6, 600))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(2, s + 8, s - 4, 18), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self.track.title, Qt.TextElideMode.ElideRight, s - 4))
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.font(8.8, 400))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(2, s + 26, s - 4, 16), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self.track.subtitle, Qt.TextElideMode.ElideRight, s - 4))


class _Line(QWidget):
    """The thin accent line between the artists and the songs."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(2)
        theme.accent.changed.connect(self._recolour)

    def _recolour(self, _c):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        c = QColor(theme.accent.color)
        c.setAlpha(150)
        p.fillRect(self.rect(), c)


class DiscoverView(QWidget):
    """Artists for you, then songs like the chosen one. `seeds` = your favourite artists, best first; `known` =
    normalised names of artists you already have (left out of the row)."""
    play = pyqtSignal(object, object)          # track, tracks
    menu = pyqtSignal(object, object, QPoint)  # track, tracks, global position
    GAP = 14
    ROWS = 3

    def __init__(self, seeds, known=()):
        super().__init__()
        self.seeds, self.known = list(seeds), set(known)
        self.key = '|'.join(self.seeds[:4])
        self.stubs, self.bubbles, self.tracks, self.tiles = [], [], [], []
        self._stamp = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(10)
        lay.addWidget(heading('Artists for you'))
        self.note = label('Finding artists like the ones you play…', 10, 400, theme.MUTED)
        lay.addWidget(self.note)
        self.row = QHBoxLayout()
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(self.GAP)
        holder = QWidget()
        holder.setLayout(self.row)
        lay.addWidget(holder)
        self.line = _Line()
        self.line.hide()
        lay.addSpacing(4)
        lay.addWidget(self.line)
        head = QHBoxLayout()
        head.setContentsMargins(0, 8, 0, 0)
        self.title = heading('')
        head.addWidget(self.title)
        head.addStretch()
        self.play_all = pill('Play all', ghost=True)
        self.play_all.clicked.connect(lambda: self.tracks and self.play.emit(self.tracks[0], self.tracks))
        self.play_all.hide()
        head.addWidget(self.play_all)
        hw = QWidget()
        hw.setLayout(head)
        lay.addWidget(hw)
        self.songs_note = label('', 10, 400, theme.MUTED)
        self.songs_note.hide()
        lay.addWidget(self.songs_note)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(self.GAP)
        self.grid.setVerticalSpacing(18)
        self.gw = QWidget()
        self.gw.setLayout(self.grid)
        lay.addWidget(self.gw)
        self._cols = 0
        cached = _fresh(_artists, self.key)
        if cached is not None:
            self._got_artists(cached)
        else:
            tasks.run(online.discover, self.seeds, tuple(self.known), done=self._fetched, failed=self._failed)

    # ---- artists ----
    def _fetched(self, stubs):
        _artists[self.key] = (time.time(), stubs)
        try:
            self._got_artists(stubs)
        except RuntimeError:
            pass   # Explore was rebuilt meanwhile

    def _failed(self, _e):
        try:
            self.note.setText('Could not reach YouTube Music for new artists.')
        except RuntimeError:
            pass

    def _got_artists(self, stubs):
        self.stubs = stubs
        if not stubs:
            self.note.setText('Play a few songs: Drift finds new artists from what you like.')
            return
        self.note.hide()
        for st in stubs:
            b = ArtistBubble(st)
            b.clicked.connect(lambda st=st: self.choose(st['id']))
            self.row.addWidget(b)
            self.bubbles.append(b)
        self.row.addStretch()
        self._fit_row()
        self.line.show()
        ids = [st['id'] for st in stubs]
        self.choose(_chosen.get(self.key) if _chosen.get(self.key) in ids else ids[0])

    def choose(self, artist_id):
        _chosen[self.key] = artist_id
        stub = next(st for st in self.stubs if st['id'] == artist_id)
        for b in self.bubbles:
            b.set_chosen(b.stub['id'] == artist_id)
        self.title.setText(f"Sounds like {stub['name']}")
        stamp = self._stamp = time.time()
        cached = _fresh(_songs, artist_id)
        if cached is not None:
            self._show_songs(cached)
            return
        self.songs_note.setText('Loading…')
        self.songs_note.show()

        def got(tracks):
            _songs[artist_id] = (time.time(), tracks)
            if self._stamp == stamp:
                self._show_songs(tracks)

        def bad(_e):
            if self._stamp == stamp:
                self.songs_note.setText('Could not load songs for this artist. Pick another one.')

        tasks.run(online.sounds_like, artist_id, 24,
                  done=lambda v: self._alive(got, v), failed=lambda e: self._alive(bad, e))

    def _alive(self, fn, value):
        try:
            fn(value)
        except RuntimeError:
            pass   # Explore was rebuilt meanwhile

    # ---- songs ----
    def _show_songs(self, tracks):
        self.tracks = list(tracks)
        self.songs_note.setVisible(not tracks)
        if not tracks:
            self.songs_note.setText('Nothing found for this artist. Pick another one.')
        self.play_all.setVisible(bool(tracks))
        for t in self.tiles:
            t.deleteLater()
        self.tiles = []
        for tr in self.tracks:
            tile = SongTile(tr)
            tile.setParent(self.gw)
            tile.hide()
            tile.clicked.connect(lambda tr=tr: self.play.emit(tr, self._from(tr)))
            tile.menu.connect(lambda pos, tr=tr: self.menu.emit(tr, self.tracks, pos))
            self.tiles.append(tile)
        self._cols = 0
        self._fit_grid()

    def _from(self, track):
        i = next((k for k, t in enumerate(self.tracks) if t.uid == track.uid), 0)
        return self.tracks[i:] + self.tracks[:i]

    # ---- layout ----
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_row()
        self._fit_grid()

    def _fit_row(self):
        fit = max(1, (self.width() + self.GAP) // (ArtistBubble.SIZE + 20 + self.GAP))
        for i, b in enumerate(self.bubbles):
            b.setVisible(i < fit)

    def _fit_grid(self):
        cols = max(2, (self.width() + self.GAP) // (SongTile.SIZE + self.GAP))
        if cols == self._cols:
            return
        self._cols = cols
        while self.grid.count():
            self.grid.takeAt(0)
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for i, tile in enumerate(self.tiles):
            if i < cols * self.ROWS:
                self.grid.addWidget(tile, i // cols, i % cols, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                tile.show()
            else:
                tile.hide()
        self.grid.setColumnStretch(cols, 1)
