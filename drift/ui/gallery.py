"""The sketch gallery (on the Settings page).

Every sketch as a framed picture of itself; click one to walk into that room (the window changes around the music,
which never stops). Under it: Import (a .driftsketch someone made), Export (the one in use), New sketch (a blank one
on a layout: its folder opens, Drift redraws while you edit it) and the artist guide. Each card's menu: use, export,
open its folder, put in the Recycle Bin (your own)."""
import json

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QMenu, QVBoxLayout, QWidget

from . import sketches, theme
from .widgets import pill

CARD_W, CARD_H = 196, 122
_thumbs = {}


def thumb(sketch, w=CARD_W, h=CARD_H):
    key = (sketch.id, sketch.stage, json.dumps(sketch.full(), sort_keys=True), str(sketch.folder), w, h)
    if key not in _thumbs:
        if len(_thumbs) > 40:
            _thumbs.clear()
        _thumbs[key] = rounded(sketch.thumbnail(w, h), w, h)
    return _thumbs[key]


def rounded(px, w, h, radius=10):
    from PyQt6.QtGui import QPainterPath, QPixmap
    ratio = px.devicePixelRatio()
    out = QPixmap(px.size())
    out.setDevicePixelRatio(ratio)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, px)
    p.end()
    return out


class SketchCard(QWidget):
    """A sketch, framed: its picture, its name, who made it. clicked(); menu(QPoint global)."""
    clicked = pyqtSignal()
    menu = pyqtSignal(QPoint)

    def __init__(self, sketch, active):
        super().__init__()
        self.sketch, self.active = sketch, active
        self.hover = False
        self.setFixedSize(CARD_W, CARD_H + 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(sketch.about or sketch.name)
        self.px = thumb(sketch)

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            if QRectF(CARD_W - 34, CARD_H + 12, 30, 26).contains(e.position()):
                self.menu.emit(e.globalPosition().toPoint())
            else:
                self.clicked.emit()
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu.emit(e.globalPosition().toPoint())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawRoundedRect(QRectF(2, 4, CARD_W - 4, CARD_H), 10, 10)
        p.drawPixmap(0, 0, self.px)
        frame = QRectF(1, 1, CARD_W - 2, CARD_H - 2)
        if self.active:
            p.setPen(QPen(theme.accent.color, 2.2))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 70 if self.hover else 22), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(frame, 10, 10)
        if self.active:
            tag = QRectF(10, 10, 0, 20)
            p.setFont(theme.caps(7.4, 700, 1.5))
            tag.setWidth(QFontMetrics(p.font()).horizontalAdvance('IN USE') + 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.accent.color)
            p.drawRoundedRect(tag, 10, 10)
            p.setPen(theme.ink_on(theme.accent.color))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, 'IN USE')
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.serif(12, 600))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(2, CARD_H + 6, CARD_W - 40, 22), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.sketch.name, Qt.TextElideMode.ElideRight, CARD_W - 40))
        p.setFont(theme.caps(7.8, 500, 1.4))
        p.setPen(QColor(theme.MUTED))
        stage = sketches.STAGES.get(self.sketch.stage)
        sub = f'{(stage.title if stage else self.sketch.stage).upper()}  ·  BY {(self.sketch.by or "YOU").upper()}'
        p.drawText(QRectF(2, CARD_H + 26, CARD_W - 40, 16), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(sub, Qt.TextElideMode.ElideRight, CARD_W - 40))
        p.drawPixmap(CARD_W - 28, CARD_H + 15, theme.pixmap('more', QColor(theme.TEXT if self.hover else theme.MUTED), 20))


def gallery(win):
    """The themes as framed cards, then import, export, a new blank sketch and the artist guide."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)
    from .pages import flow
    current = win.sketch.id
    cards = []
    for s in sketches.available():
        card = SketchCard(s, s.id == current)
        card.clicked.connect(lambda s=s: _use(win, s))
        card.menu.connect(lambda pos, s=s: _card_menu(win, s, pos))
        cards.append(card)
    lay.addWidget(flow(cards, 14))          # the gallery wall: as many in a row as fit
    buttons = QHBoxLayout()
    buttons.setSpacing(10)
    imp = pill('Import…')
    imp.setToolTip('Add a .driftsketch someone made')
    imp.clicked.connect(lambda: _import(win))
    exp = pill('Export…', ghost=True)
    exp.setToolTip(f'Save {win.sketch.name} as a .driftsketch to share')
    exp.clicked.connect(lambda: _export(win, win.sketch))
    make = pill('New sketch…', ghost=True)
    make.setToolTip('Draw your own room, from a blank canvas')
    make.clicked.connect(lambda: _new(win))
    guide = pill('How to make one', ghost=True)
    guide.setToolTip('The artist guide: how to design a sketch that fits the music')
    guide.clicked.connect(sketches.open_guide)
    for b in (imp, exp, make, guide):
        buttons.addWidget(b)
    buttons.addStretch()
    lay.addLayout(buttons)
    return box


def _use(win, sketch):
    if sketch.id == win.sketch.id:
        return
    QTimer.singleShot(0, lambda: win.swap(sketch.id))


def _card_menu(win, sketch, pos):
    m = QMenu(win)
    if sketch.id != win.sketch.id:
        m.addAction('Use this sketch', lambda: _use(win, sketch))
    m.addAction('Export to share…', lambda: _export(win, sketch))
    if not sketch.builtin:
        m.addAction('Open its folder', lambda: sketches.open_folder(sketch))
        m.addSeparator()
        m.addAction('Put in the Recycle Bin', lambda: _remove(win, sketch))
    m.exec(pos)


def _export(win, sketch):
    path, _ = QFileDialog.getSaveFileName(win, 'Export sketch', sketch.id + sketches.EXT,
                                          f'Drift sketch (*{sketches.EXT})')
    if path:
        try:
            out = sketches.export(sketch, path)
            win.say(f'Saved {out.name}: anyone with Drift can import it.')
        except OSError as e:
            win.say(f'Could not export: {e}')


def _import(win):
    path, _ = QFileDialog.getOpenFileName(win, 'Import a sketch', '',
                                          f'Drift sketch (*{sketches.EXT} sketch.json);;All files (*)')
    if not path:
        return
    try:
        s = sketches.import_file(path)
    except (ValueError, OSError) as e:
        win.say(str(e))
        return
    win.say(f'Imported {s.name}.')
    QTimer.singleShot(0, lambda: win.swap(s.id))


def _remove(win, sketch):
    try:
        sketches.remove(sketch)
    except OSError as e:
        win.say(f'Could not remove it: {e}')
        return
    win.say(f'{sketch.name} is in the Recycle Bin.')
    if sketch.id == win.sketch.id:
        QTimer.singleShot(0, lambda: win.swap('sunset'))
    else:
        QTimer.singleShot(0, win.settings_page.refresh)


def _new(win):
    """A new sketch is a blank canvas: its folder opens with sketch.js, the guide opens beside it."""
    from .playlists import ask_name
    name = ask_name(win, 'Name your sketch', '', 'Start')
    if not name:
        return
    try:
        s = sketches.new(name)
    except OSError as e:
        win.say(f'Could not make it: {e}')
        return
    sketches.open_folder(s)
    sketches.open_guide()
    QTimer.singleShot(0, lambda: win.swap(s.id))
    QTimer.singleShot(400, lambda: windows_say(f'{s.name}: draw your room in sketch.js; '
                                               'Drift redraws every time you save.'))


def windows_say(text):
    from . import window
    w = window.live()
    if w is not None:
        w.say(text)
