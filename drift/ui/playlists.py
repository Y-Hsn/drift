"""Playlists: a page of playlist cards (plus a New playlist card), and Drift's own small name box for creating and
renaming them. A playlist lives inside Drift: a name and an ordered list of songs. Nothing is copied or moved."""
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from . import theme
from .pages import Page
from .widgets import MixTile, label, pill

TILE = 168
GAP = 16


def ask_name(parent, title, text='', action='Create'):
    """A small Drift-styled box asking for a name; the name, or None when cancelled."""
    d = QDialog(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    d.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    d.setModal(True)
    outer = QVBoxLayout(d)
    outer.setContentsMargins(0, 0, 0, 0)
    card = QFrame()
    card.setObjectName('NameBox')
    card.setStyleSheet(f'QFrame#NameBox {{ background: {theme.RAISED}; border: 1px solid {theme.EDGE}; '
                       'border-radius: 16px; }')
    outer.addWidget(card)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(14)
    lay.addWidget(label(title, 14, 700, display=True))
    field = QLineEdit(text)
    field.setPlaceholderText('Name')
    field.setFixedHeight(40)
    field.setStyleSheet('padding: 8px 16px;')     # no search glass here
    field.selectAll()
    lay.addWidget(field)
    row = QHBoxLayout()
    row.addStretch()
    cancel = pill('Cancel', ghost=True)
    ok = pill(action)
    cancel.clicked.connect(d.reject)
    ok.clicked.connect(lambda: field.text().strip() and d.accept())
    field.returnPressed.connect(ok.click)
    ok.setEnabled(bool(text.strip()))
    field.textChanged.connect(lambda s: ok.setEnabled(bool(s.strip())))
    row.addWidget(cancel)
    row.addWidget(ok)
    lay.addLayout(row)
    d.setFixedWidth(380)
    d.adjustSize()
    if parent is not None:
        top = parent.window()
        c = top.mapToGlobal(top.rect().center())
        d.move(c.x() - d.width() // 2, c.y() - d.height() // 2 - 60)
    field.setFocus()
    return field.text().strip() if d.exec() == QDialog.DialogCode.Accepted and field.text().strip() else None


class NewTile(QWidget):
    """The card that makes a new playlist: a quiet outline with a plus."""
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(TILE, TILE + 52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Make a new playlist')
        self.hover = False

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, TILE - 2, TILE - 2)
        p.setBrush(QColor(255, 255, 255, 16 if self.hover else 8))
        pen = QPen(QColor(theme.accent.color if self.hover else theme.EDGE), 1.4, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(r, 14, 14)
        color = theme.accent.color if self.hover else QColor(theme.MUTED)
        p.drawPixmap(TILE // 2 - 16, TILE // 2 - 16, theme.pixmap('plus', color, 32, 1.6))
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(10, 600))
        p.drawText(QRectF(2, TILE + 8, TILE - 4, 20), Qt.AlignmentFlag.AlignVCenter, 'New playlist')


class PlaylistsPage(Page):
    """All playlists as cards. open(pid) asks the window to show one; new() asks it to make one."""
    open = pyqtSignal(int)
    new = pyqtSignal()

    def __init__(self, store, title='Playlists'):
        super().__init__(title)
        self.store = store
        self.tiles = []
        lay = self.body
        note = label('A playlist is a list kept inside Drift: songs are not copied or moved, and a song can be in '
                     'many playlists. Add songs with right-click › Add to playlist. Songs you save to this PC are '
                     'in your music folder (Settings).', 10, 400, theme.MUTED)
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addSpacing(8)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(GAP)
        self.grid.setVerticalSpacing(18)
        gw = QWidget()
        gw.setLayout(self.grid)
        self.gw = gw
        lay.addWidget(gw)
        lay.addStretch()
        self._cols = 0

    def refresh(self):
        for t in self.tiles:
            t.deleteLater()
        self.tiles = []
        new = NewTile()
        new.clicked.connect(self.new.emit)
        self.tiles.append(new)
        sizes = self.store.playlist_sizes()
        for pid, name in self.store.playlists():
            songs = self.store.playlist_tracks(pid)
            n = sizes.get(pid, 0)
            tile = MixTile(name, f'{n} song{"s" if n != 1 else ""}', name, songs[0] if songs else None, TILE)
            tile.clicked.connect(lambda p=pid: self.open.emit(p))
            self.tiles.append(tile)
        for t in self.tiles:
            t.setParent(self.gw)
        self._cols = 0
        self._fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        cols = max(2, (self.viewport().width() - 80 + GAP) // (TILE + GAP))
        if cols == self._cols:
            return
        self._cols = cols
        while self.grid.count():
            self.grid.takeAt(0)
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for i, t in enumerate(self.tiles):
            self.grid.addWidget(t, i // cols, i % cols, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            t.show()
        self.grid.setColumnStretch(cols, 1)
