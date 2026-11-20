"""Paper Theatre. A song is a performance in a hand-cut tabletop theatre."""
from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QHBoxLayout, QSlider, QStyle, QWidget

from .. import theme
from ..covers import shared as covers
from ..lists import TrackDelegate, TrackList, TrackRole, mmss
from ..lyricsfeed import line_at, shared as lyrics
from ..widgets import pill
from . import Stage
from .theatre_set import BRASS, CUE_NAMES, HEIGHT, INK, IVORY, WIDTH, CueSheet, TheatreSet, alpha, fibre, gradient, screw

HOUSE = '#181118'
CREAM = '#f7e6ce'
CENTRE = Qt.AlignmentFlag.AlignCenter
LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def text(p, rect, value, font, colour, align=LEFT):
    p.setFont(font)
    p.setPen(QColor(colour))
    p.drawText(QRectF(rect), align,
               QFontMetrics(font).elidedText(value, Qt.TextElideMode.ElideRight, max(0, int(rect.width()))))


def house(p, widget, rect):
    p.fillRect(rect, QColor(HOUSE))
    glow = QRadialGradient(QPointF(widget.width() * .5, widget.height() * .3), max(widget.width() * .65, 1))
    glow.setColorAt(0, QColor('#3e2630'))
    glow.setColorAt(.62, QColor('#261922'))
    glow.setColorAt(1, QColor(HOUSE))
    p.fillRect(rect, glow)
    fibre(p, QRectF(rect), .7)


class CueRail(QSlider):
    """A brass fader with playable cue marks. Dragging previews the set until release."""

    seek = pyqtSignal(int)

    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.sheet = CueSheet()
        self.hover = None
        self.setTracking(False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setAccessibleName('Theatre cue rail')
        self.setAccessibleDescription('Seek through the song and move the scenery. Use the arrow keys or drag.')
        self.setRange(0, 0)
        self.setSingleStep(5000)
        self.setPageStep(15000)
        self.valueChanged.connect(self.seek.emit)

    def clock(self, ms, duration):
        if self.isSliderDown():
            return
        with QSignalBlocker(self):
            self.setRange(0, max(0, duration))
            self.setValue(ms)

    def cancel(self, duration):
        with QSignalBlocker(self):
            self.setSliderDown(False)
            self.setRange(0, max(0, duration))
            self.setValue(0)
        self.hover = None

    def span(self):
        return 29., max(1., self.width() - 58.)

    def _at(self, pos):
        start, span = self.span()
        return min(self.maximum(), max(0, round((pos.x() - start) / span * self.maximum())))

    def event(self, event):
        if (event.type() == QEvent.Type.ShortcutOverride and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)):
            event.accept()
            return True
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.maximum():
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setSliderDown(True)
        self.hover = None
        self.setSliderPosition(self._at(event.position()))
        self.update()

    def mouseMoveEvent(self, event):
        at = self._at(event.position())
        if self.isSliderDown():
            self.setSliderPosition(at)
        else:
            self.hover = at
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            position = self._at(event.position())
            with QSignalBlocker(self):
                self.setSliderPosition(position)
                self.setSliderDown(False)
                self.setValue(position)
            self.seek.emit(position)
            self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        start, span = self.span()
        duration = self.maximum()
        progress = self.sliderPosition() / max(1, duration)
        x, y = start + progress * span, 27.
        housing = QRectF(1, 1, w - 2, h - 3)
        p.setPen(QPen(QColor('#e5b884' if self.hasFocus() else '#8c5747'), 1))
        p.setBrush(gradient(0, h, (0, '#63413d'), (.08, '#432a31'), (.55, '#2e1d26'), (1, '#351f26')))
        p.drawRoundedRect(housing, 8, 8)
        p.fillRect(QRectF(start - 8, y - 4, span + 16, 8), QColor('#161019'))
        p.fillRect(QRectF(start, y, span, 1.25), QColor('#87684c'))
        p.fillRect(QRectF(start, y, x - start, 1.25), QColor(BRASS))
        for side in (11, w - 11):
            screw(p, side, y, 2.5)

        if duration:
            cue_font = theme.caps(8.2, 600, .95)
            for act, f in enumerate(self.sheet.cues):
                cx = start + span * f
                c = IVORY if act == self.sheet.act(progress) else '#c29775'
                text(p, QRectF(cx - 16, 3, 32, 15), CUE_NAMES[act], cue_font, c, CENTRE)
                p.fillRect(QRectF(cx - .5, 19, 1, 4), QColor(c))
            if self.hover is not None and not self.isSliderDown():
                hx = start + self.hover / duration * span
                p.fillRect(QRectF(hx - .5, y - 4, 1, 8), alpha(IVORY, .75))
            p.setPen(QPen(QColor('#9c613a'), 1))
            p.setBrush(gradient(y - 7, y + 8, (0, '#ffe3a6'), (.36, '#d7a36a'), (.55, '#f1cb8c'), (1, '#935634')))
            p.drawRoundedRect(QRectF(x - 7, y - 6, 14, 13), 3, 3)
            p.fillRect(QRectF(x - .6, y - 3, 1.2, 7), QColor('#7d4b34'))

        time_font = theme.caps(8.3, 400, .5)
        left = mmss(self.hover if self.hover is not None and duration else self.sliderPosition())
        text(p, QRectF(start, 37, span / 2, 14), left if duration else '—', time_font, CREAM)
        text(p, QRectF(start + span / 2, 37, span / 2, 14), mmss(duration) if duration else '—',
             time_font, '#d0ad8e', RIGHT)


class Theatre(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win, self.engine = win, win.engine
        self.track = None
        self.calm = False
        self.ms = 0
        self.duration = 0
        self.sheet = CueSheet()
        self.lines = []
        self.frame = None
        self.set_box, self.heading_box = QRect(), QRect()
        self.rail = CueRail(self)
        self.rail.setFixedHeight(56)
        self.rail.sliderMoved.connect(self._preview)
        self.rail.seek.connect(self.engine.seek)
        self.greet = QWidget(self)
        row = QHBoxLayout(self.greet)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        play = pill('Start Drift')
        play.clicked.connect(lambda: self.engine.start_radio())
        row.addWidget(play)
        self.shuffle = pill('Shuffle saved songs', ghost=True)
        self.shuffle.clicked.connect(lambda: self.engine.play_list(self.win.store.local_tracks(), True))
        row.addWidget(self.shuffle)
        row.addStretch()
        self.engine.track_changed.connect(self._track)
        self.engine.position_changed.connect(self._position)
        self.engine.wave_ready.connect(self._wave)
        self.engine.online_changed.connect(self._online)
        self.engine.library_changed.connect(self._greeting)
        covers().ready.connect(self._cover_ready)
        lyrics().ready.connect(self._lyric_ready)
        self._layout()

    def dock_rect(self):
        w = min(self.width(), 1216)
        return QRect((self.width() - w) // 2, self.height() - 66, w, 58)

    def resizeEvent(self, event):
        self._layout()
        super().resizeEvent(event)

    def _layout(self):
        top = 28 if self.calm else 58
        bottom = self.height() - (28 if self.calm else 70)
        title = 68 if self.width() >= 680 else 58
        rail = 0 if self.calm else 60
        room_h = max(65, bottom - top - title - rail)
        w = max(1, min(self.width() - 30, 1150, round(room_h * 2.39)))
        h = min(room_h, round(w * HEIGHT / WIDTH))
        x = (self.width() - w) // 2
        y = top + title + max(0, (room_h - h) // 2)
        self.set_box = QRect(x, y, w, h)
        self.heading_box = QRect(x + round(w * .027), y - title, max(1, w - round(w * .054)), title - 5)
        self.rail.setGeometry(x + round(w * .081), y + h + 1, max(1, round(w * .838)), 56)
        self.greet.setGeometry(x, y + h + 9, w, 46)
        self._greeting()
        self.update()

    def _greeting(self):
        self.rail.setVisible(self.track is not None and not self.calm)
        self.greet.setVisible(self.track is None and not self.calm)
        if self.greet.isVisibleTo(self):
            self.shuffle.setVisible(bool(self.win.store.local_tracks()))

    def set_calm(self, on):
        if on != self.calm:
            self.calm = on
            self._layout()

    def let_go(self):
        self.frame = None

    def hideEvent(self, event):
        self.let_go()
        super().hideEvent(event)

    def refresh(self):
        self._track(self.engine.current)

    def _track(self, track):
        old = self.track.uid if self.track else None
        self.track = track
        uid, wave = self.engine.wave
        if old != (track.uid if track else None):
            self.rail.cancel((self.engine.duration or track.duration or 0) if track else 0)
            self.lines = []
            self.let_go()
            self._greeting()
        self._waves(wave if track and track.uid == uid else ())
        duration = (self.engine.duration or track.duration or 0) if track else 0
        self._position(self.engine.position if track else 0, duration)
        self._read_lyrics()
        self.update()

    def _wave(self, uid, wave):
        if self.track and self.track.uid == uid:
            self._waves(wave)

    def _waves(self, wave):
        self.sheet = CueSheet(wave)
        self.rail.sheet = self.sheet
        self.rail.update()
        self.update(self.set_box)

    def _position(self, ms, duration):
        if self.track is None:
            return
        self.ms, self.duration = ms, duration or self.track.duration or 0
        if not self.rail.isSliderDown():
            self.rail.clock(ms, self.duration)
            if self.isVisible():
                self.update(self.set_box)

    def _preview(self, _ms):
        self.update(self.set_box)

    def _cover_ready(self, uid):
        if self.track and self.track.uid == uid:
            self.let_go()
            self.update(self.set_box)

    def _lyric_ready(self, uid):
        if self.track and self.track.uid == uid:
            self._read_lyrics()
            self.update(self.set_box)

    def _online(self, online):
        if online:
            self._read_lyrics()

    def _read_lyrics(self):
        self.lines = []
        if self.track and self.win.store.get('whisper', True):
            feed = lyrics()
            if self.engine.online or feed.known(self.track):
                self.lines = feed.lines(self.track) or []

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.set_box.contains(event.position().toPoint()):
            self.win.go('home' if self.calm else 'calm')

    def _set(self):
        # Bound the physical cache on large and high-DPI displays. The frame can be redrawn after trimming.
        ratio = max(.65, min(1.65, self.devicePixelRatioF() * self.set_box.width() / WIDTH))
        if not self.frame or abs(self.frame[0] - ratio) > .12:
            image = covers().image(self.track) if self.track else None
            colour = theme.accent_from(image) if image is not None else QColor('#b77791')
            self.frame = (ratio, TheatreSet(colour, image, closed=self.track is None, ratio=ratio))
        return self.frame[1]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.win.look.paint_room(p, self, event.rect())
        if event.rect().intersects(self.heading_box):
            self._heading(p)
        if event.rect().intersects(self.set_box):
            ms = self.rail.sliderPosition() if self.rail.isSliderDown() else self.ms
            progress = min(1., max(0., ms / max(1, self.duration))) if self.track else 0.
            self._set().draw(p, QRectF(self.set_box), self.sheet, progress)
            self._surtitles(p, ms)

    def _heading(self, p):
        r = QRectF(self.heading_box)
        t = self.track
        title = t.title if t else 'Paper Theatre'
        subtitle = t.subtitle.upper() if t else 'THE STAGE IS READY'
        big = theme.serif(max(23, min(36, r.width() / 25)), 600)
        text(p, r.adjusted(0, 0, 0, -17), title, big, CREAM)
        text(p, QRectF(r.x() + 1, r.bottom() - 17, r.width() - 2, 17), subtitle, theme.caps(8.8, 500, 1.8), BRASS)

    def _surtitles(self, p, ms):
        at = line_at(self.lines, ms)
        if at < 0 or not self.lines[at][1].strip():
            return
        r = QRectF(self.set_box)
        font = theme.serif(max(11, min(16, r.width() / 63)), 500, italic=True)
        w = min(r.width() * .75, QFontMetrics(font).horizontalAdvance(self.lines[at][1]) + 28)
        label = QRectF(r.center().x() - w / 2, r.y() + r.height() * .126, w, 26)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(alpha('#231821', .9))
        p.drawRoundedRect(label, 4, 4)
        text(p, label.adjusted(12, 0, -12, 0), self.lines[at][1], font, CREAM, CENTRE)


class PlaybillDelegate(TrackDelegate):
    """Paper programmes with a perforated play stub. Keep the shared list's row and button geometry."""

    def __init__(self, view, numbered):
        super().__init__(view, numbered)
        self.f_title = theme.serif(14, 650)
        self.f_sub = theme.font(8.5, 500)
        self.f_num = theme.caps(8.8, 600, .4)

    def paint(self, p, option, index):
        track = index.data(TrackRole)
        current = index.model().current_uid == track.uid
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        r = QRectF(option.rect.adjusted(4, 3, -10, -4))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        stub = 48 if self.numbered else 11
        stock = QPainterPath()
        stock.addRoundedRect(r, 4, 4)
        for y in (r.top() - .6, r.bottom() + .6):
            hole = QPainterPath()
            hole.addEllipse(QPointF(r.left() + stub, y), 4.5, 4.5)
            stock = stock.subtracted(hole)
        p.fillPath(stock.translated(2.5, 2.5), QColor('#110c12'))
        p.fillPath(stock, gradient(r.top(), r.bottom(), (0, '#fff0d3' if hover or selected else '#f0d7ae'),
                                   (1, '#edd0a4' if current or hover else '#d7b88f')))
        if selected or current:
            p.strokePath(stock, QPen(QColor('#a25247' if current else '#83503e'), 1.6))
        p.save()
        p.setClipPath(stock)
        fibre(p, r, .8)
        p.restore()

        if self.numbered:
            x = r.x() + stub
            p.setPen(QPen(QColor('#92735c'), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x, r.y() + 6), QPointF(x, r.bottom() - 6))
            if hover or current:
                icon = theme.pixmap('play' if hover else 'volume', '#743f3a', 16)
                p.drawPixmap(int(r.x() + 15), int(r.center().y() - 8), icon)
            else:
                text(p, QRectF(r.x() + 4, r.y(), stub - 8, r.height()), str(index.row() + 1),
                     self.f_num, '#674839', CENTRE)

        cover_x, cover_y = int(r.x() + stub + 10), int(r.y() + (r.height() - 34) / 2)
        cover = covers().get(track, 34, 0)
        p.fillRect(QRectF(cover_x - 2, cover_y - 2, 38, 38), QColor('#fee6c1'))
        p.drawPixmap(cover_x, cover_y, cover)
        start, end = cover_x + 46, r.right() - 137
        text(p, QRectF(start, r.y() + 4, max(1, end - start), 25), track.title, self.f_title, INK)
        text(p, QRectF(start, r.y() + 29, max(1, end - start), 17), track.subtitle, self.f_sub, '#664b43')
        if track.duration:
            text(p, QRectF(r.right() - 61, r.y(), 46, r.height()), mmss(track.duration), self.f_num, '#674839', RIGHT)
        keep = self.keep_rect(option.rect).center()
        heart = self.heart_rect(option.rect).center()
        if track.local or (hover and track.page):
            p.drawPixmap(keep.x() - 8, keep.y() - 8,
                         theme.pixmap('saved' if track.local else 'download', '#6b4940', 16))
        if track.liked or hover:
            p.drawPixmap(heart.x() - 8, heart.y() - 8,
                         theme.pixmap('heartfill' if track.liked else 'heart', '#853f42', 16))
        p.restore()


class PlaybillList(TrackList):
    def __init__(self, numbered=True, grow=True):
        super().__init__(numbered=numbered, grow=grow)
        self.delegate.deleteLater()
        self.delegate = PlaybillDelegate(self, numbered)
        self.setItemDelegate(self.delegate)


class PaperTheatreStage(Stage):
    id = 'paper-theatre'
    title = 'Paper Theatre'
    blurb = 'Every song is a performance in a hand-cut theatre.'
    tabs = {**Stage.tabs, 'home': 'Stage', 'queue': 'Playbill'}
    words = {'queue': 'The Playbill'}
    seek_style = 'groove'

    def now_page(self, win):
        return Theatre(win)

    def track_list(self, numbered=True, grow=True):
        return PlaybillList(numbered, grow)

    def tune(self, pal):
        tuned = super().tune(pal)
        if self.k.get('accent') or self.k.get('mood') == 'mono':
            return tuned
        colors = {name: getattr(tuned, name) for name in theme.Palette.NAMES}
        colors['acc'] = QColor(BRASS) if pal is theme.DEFAULT_PALETTE else theme.mix(tuned.acc, BRASS, .68)
        return theme.Palette(night=tuned.night, **colors)

    def paint_room(self, p, widget, rect):
        house(p, widget, rect)
        self.paint_texture(p, rect)

    def paint_top(self, p, widget, solid):
        if solid:
            p.fillRect(widget.rect(), gradient(0, widget.height(), (0, '#211620'), (.8, '#251720'), (1, '#372430')))
            p.fillRect(QRectF(0, widget.height() - 1, widget.width(), 1), alpha(BRASS, .26))

    def paint_dock(self, p, dock):
        h = dock.height()
        p.fillRect(dock.rect(), gradient(0, h, (0, '#2e1d26'), (.36, '#372530'), (1, '#21151d')))
        p.fillRect(QRectF(0, 0, dock.width(), 1), alpha(BRASS, .45))

    def paint_mark(self, p, rect, color):
        p.fillRect(QRectF(rect.center().x() - 3, rect.center().y() - 1, 6, 2), QColor(BRASS))

    @classmethod
    def thumbnail(cls, knobs, w, h, folder=None):
        px = QPixmap(w, h)
        px.fill(QColor(HOUSE))
        p = QPainter(px)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        set_h = max(1, min(h - 6, (w - 6) * HEIGHT / WIDTH))
        set_w = set_h * WIDTH / HEIGHT
        colour = QColor(knobs.get('accent') or '#bd7184')
        scenery = TheatreSet(colour, ratio=.60)
        scenery.draw(p, QRectF((w - set_w) / 2, (h - set_h) / 2, set_w, set_h), CueSheet(), .64)
        p.end()
        return px
