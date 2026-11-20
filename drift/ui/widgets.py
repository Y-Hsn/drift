"""Small hand-drawn controls: icon buttons, the morphing play button, the Shoreline (the seek bar drawn from the song's
own loudness), sliders, chips, mix tiles, and the Dock (now playing, floating at the bottom of the room)."""
from PyQt6.QtCore import QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .covers import shared as covers
from .lists import mmss


class IconButton(QPushButton):
    """A round icon that lights up on hover; `checked` icons take the accent."""

    def __init__(self, name, size=20, box=36, tip='', checkable=False, parent=None):
        super().__init__(parent)
        self.name, self.isize = name, size
        self.setFixedSize(box, box)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.hover = False
        self.dot = False
        self.dark_ink = False      # over a light room (a canvas sketch's Now page): dark icons
        theme.accent.changed.connect(lambda _c: self.update())

    def set_icon(self, name):
        self.name = name
        self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hover and self.isEnabled():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 22) if self.dark_ink else QColor(255, 255, 255, 18))
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        on = self.isChecked()
        text, muted = (QColor(theme.INK), QColor(theme.INK_MUTED)) if self.dark_ink else \
            (QColor(theme.TEXT), QColor(theme.MUTED))
        color = theme.accent.color if on else (text if self.hover else muted)
        if not self.isEnabled():
            color = QColor(theme.DIM)
        px = theme.pixmap(self.name, color, self.isize)
        p.drawPixmap((self.width() - self.isize) // 2, (self.height() - self.isize) // 2, px)
        if on and self.dot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.accent.color)
            p.drawEllipse(QPointF(self.width() / 2, self.height() - 4), 2, 2)


class PlayButton(QPushButton):
    """The round play button. Play and pause morph into each other (140 ms) instead of swapping pictures."""

    # the triangle split in two halves <-> the two pause bars (on a 24 grid)
    PLAY = (((8, 5), (14, 8.6), (14, 15.4), (8, 19)), ((14, 8.6), (19.6, 12), (19.6, 12), (14, 15.4)))
    PAUSE = (((6.5, 5), (10.5, 5), (10.5, 19), (6.5, 19)), ((13.5, 5), (17.5, 5), (17.5, 19), (13.5, 19)))

    def __init__(self, box=42, parent=None):
        super().__init__(parent)
        self.setFixedSize(box, box)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.playing = False
        self.busy = False
        self.angle = 0
        self.t = 0.0        # 0 = the play shape, 1 = the pause shape
        self.spin = QTimer(self, interval=30, timeout=self._spin)
        self.morph = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self.morph.valueChanged.connect(self._morph)

    def set_playing(self, on):
        if on == self.playing:
            return
        self.playing = on
        self.morph.stop()
        self.morph.setStartValue(self.t)
        self.morph.setEndValue(1.0 if on else 0.0)
        self.morph.start()

    def _morph(self, v):
        self.t = float(v)
        self.update()

    def set_busy(self, on):
        self.busy = on
        self.spin.start() if on else self.spin.stop()
        self.update()

    def _spin(self):
        self.angle = (self.angle + 9) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#ffffff') if self.underMouse() else QColor(theme.TEXT))
        p.drawEllipse(r)
        ink = QColor('#0b0b0d')
        if self.busy:
            pen = QPen(ink, 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(r.adjusted(13, 13, -13, -13), -self.angle * 16, 100 * 16)
            return
        k = self.width() * 0.46 / 24
        ox = (self.width() - 24 * k) / 2 + (1 - self.t) * 0.8 * k
        oy = (self.height() - 24 * k) / 2
        p.setBrush(ink)
        for a, b in zip(self.PLAY, self.PAUSE):
            path = QPainterPath()
            for i, ((x0, y0), (x1, y1)) in enumerate(zip(a, b)):
                pt = QPointF(ox + (x0 + (x1 - x0) * self.t) * k, oy + (y0 + (y1 - y0) * self.t) * k)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            path.closeSubpath()
            p.drawPath(path)


class Shoreline(QWidget):
    """The seek bar is the song itself: its loudness, bar by bar, as the core measured it (a quiet intro sits low, the
    chorus stands up). The played part takes the accent; under the mouse the bars brighten up to where a click lands
    (`hovered(fraction)`, -1 when the mouse leaves). Until the shape is known it is a quiet dotted line; when it
    arrives the bars rise (420 ms). A small dot above the bars marks the best part: the loudest stretch of the song
    (usually the chorus or the drop); click the dot (or press J) to jump there."""
    moved = pyqtSignal(float)
    released = pyqtSignal(float)
    hovered = pyqtSignal(float)
    peak_clicked = pyqtSignal(float)

    STEP, BAR, TOP = 4.0, 2.2, 7

    def __init__(self, height=36, parent=None, style='shore'):
        super().__init__(parent)
        self.style = style        # 'shore' (the song's hills) or 'groove' (a record's groove, unrolled)
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.value = 0.0
        self.wave = []
        self.best = None
        self.rise = 1.0
        self.hover_x = -1.0
        self.dragging = False
        self.anim = QVariantAnimation(self, duration=420, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self.anim.valueChanged.connect(self._rise)
        theme.accent.changed.connect(lambda _c: self.update())

    def _rise(self, v):
        self.rise = float(v)
        self.update()

    def set_wave(self, wave):
        wave = list(wave or [])
        if wave == self.wave:
            return
        self.wave = wave
        self.best = best_part(wave)
        if wave:
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()
        self.update()

    def set_value(self, v):
        if self.dragging:
            return
        v = max(0.0, min(1.0, v))
        if abs(v - self.value) * self.width() >= 0.5 or v == 0:   # repaint only when a pixel changes
            self.value = v
            self.update()

    def _at(self, x):
        return max(0.0, min(1.0, x / max(1, self.width())))

    def _on_peak(self, pos):
        if self.best is None:
            return False
        x = self.best * self.width()
        return abs(pos.x() - x) <= 7 and pos.y() <= self.TOP + 4

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._on_peak(e.position()):
            self.peak_clicked.emit(self.best)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.value = self._at(e.position().x())
            self.moved.emit(self.value)
            self.update()

    def mouseMoveEvent(self, e):
        self.setToolTip('The best part: click to jump there (J)' if self._on_peak(e.position()) else '')
        self.hover_x = e.position().x()
        self.hovered.emit(self._at(self.hover_x))
        if self.dragging:
            self.value = self._at(self.hover_x)
            self.moved.emit(self.value)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.dragging:
            self.dragging = False
            self.released.emit(self.value)
            self.update()

    def leaveEvent(self, e):
        self.hover_x = -1.0
        self.hovered.emit(-1.0)
        self.update()

    def bars(self, n):
        """The shape resampled to `n` bars (0..1)."""
        w = self.wave
        if not w:
            return [0.0] * n
        out = []
        for i in range(n):
            a = int(i * len(w) / n)
            b = max(a + 1, int((i + 1) * len(w) / n))
            out.append(max(w[a:b]) / 255.0)
        return out

    def paintEvent(self, e):
        """The same shore as on Now, far away: the song's hills as one silhouette on a thin waterline; the played part
        lit in the accent with a bright rim, a lantern where the song is."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.style == 'groove':
            return self._groove(p)
        w, h = self.width(), self.height()
        base = h - 3.0
        reach = base - self.TOP - 1
        n = max(16, min(160, int(w / 6)))
        vals = self.bars(n) if self.wave else [0.0] * n
        if self.wave:
            lo, hi = min(vals), max(vals)
            vals = [0.08 + 0.92 * (v - lo) / ((hi - lo) or 1) for v in vals]
        pts = [QPointF(i * w / (n - 1), base - (0.04 + v * self.rise) * reach) for i, v in enumerate(vals)]
        edge = QPainterPath(pts[0])
        for i in range(n - 1):
            p0 = pts[i - 1] if i else pts[i]
            p1, p2 = pts[i], pts[i + 1]
            p3 = pts[i + 2] if i + 2 < n else p2
            edge.cubicTo(QPointF(p1.x() + (p2.x() - p0.x()) / 6, p1.y() + (p2.y() - p0.y()) / 6),
                         QPointF(p2.x() - (p3.x() - p1.x()) / 6, p2.y() - (p3.y() - p1.y()) / 6), p2)
        fill = QPainterPath(edge)
        fill.lineTo(QPointF(w, base))
        fill.lineTo(QPointF(0, base))
        fill.closeSubpath()
        played = self.value * w
        acc = theme.accent.color
        hover = self.hover_x
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawPath(fill)
        if 0 <= hover:
            p.save()
            p.setClipRect(QRectF(0, 0, hover, h))
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawPath(fill)
            p.restore()
        if played > 0:
            p.save()
            p.setClipRect(QRectF(0, 0, played, h))
            lit = QColor(acc)
            lit.setAlpha(70)
            p.setBrush(lit)
            p.drawPath(fill)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(acc, 1.5))
            p.drawPath(edge)
            p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRect(QRectF(0, base, w, 1))                      # the waterline
        if played > 0:
            i = min(n - 1, int(played / w * (n - 1)))
            y = pts[i].y()
            p.setBrush(QColor('#ffffff'))
            p.drawEllipse(QPointF(played, y), 2.4, 2.4)
        if self.best is not None and self.rise > 0.5:
            c = QColor(acc if self.value >= self.best else theme.TEXT)
            c.setAlphaF(min(1.0, (self.rise - 0.5) * 2))
            p.setBrush(c)
            p.drawEllipse(QPointF(self.best * w, 3), 2.4, 2.4)


    def _groove(self, p):
        """The record's groove unrolled: fine cuts as deep as the song is loud, the played part warm, the needle
        standing where the song is, a gold mark over the best part."""
        w, h = self.width(), self.height()
        base = h - 8.0
        reach = base - self.TOP
        n = max(24, min(260, int(w / 3)))
        vals = self.bars(n) if self.wave else [0.0] * n
        if self.wave:
            lo, hi = min(vals), max(vals)
            vals = [(v - lo) / ((hi - lo) or 1) for v in vals]
        played = self.value * w
        hover = self.hover_x
        acc = theme.accent.color
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(vals):
            x = (i + 0.5) * w / n
            hh = 1.5 + v * self.rise * reach
            if x <= played:
                c = QColor(acc)
                c.setAlpha(210)
            else:
                c = QColor(255, 255, 255, 70 if 0 <= hover and x <= hover else 38)
            p.setBrush(c)
            p.drawRect(QRectF(x - 0.6, base - hh, 1.2, hh))
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawRect(QRectF(0, base + 3, w, 1))
        if played > 0:
            p.setBrush(acc)
            p.drawRect(QRectF(0, base + 3, played, 1))
            p.setBrush(QColor('#f4efe6'))
            p.drawRect(QRectF(played - 0.75, self.TOP - 4, 1.5, base - self.TOP + 8))
            p.drawEllipse(QPointF(played, self.TOP - 4), 2.6, 2.6)
        if self.best is not None and self.rise > 0.5:
            c = QColor('#ffd98a' if self.value < self.best else acc)
            c.setAlphaF(min(1.0, (self.rise - 0.5) * 2))
            p.setBrush(c)
            x = self.best * w
            path = QPainterPath(QPointF(x, 0.5))
            for dx, dy in ((3, 3.5), (0, 7), (-3, 3.5)):
                path.lineTo(QPointF(x + dx, 0.5 + dy))
            path.closeSubpath()
            p.drawPath(path)


def best_part(wave, share=0.1):
    """Where the loudest stretch of a song starts (0..1), or None for short or flat songs. One tenth of the song is
    weighed at a time; the start is moved back to where that stretch rises, so a jump lands on its first beat."""
    n = len(wave)
    k = max(3, int(n * share))
    if n < 40 or max(wave) - min(wave) < 40:
        return None
    sums = [sum(wave[i:i + k]) for i in range(n - k + 1)]
    i = max(range(len(sums)), key=sums.__getitem__)
    if sums[i] / k < (sum(wave) / n) * 1.08:
        return None          # no part stands out
    floor = sum(wave[i:i + k]) / k * 0.8
    while i > 0 and wave[i - 1] >= floor:
        i -= 1
    return i / n


class Slider(QWidget):
    """A thin line that thickens under the mouse; drag or click anywhere. moved(value) while dragging, released(value)."""
    moved = pyqtSignal(float)
    released = pyqtSignal(float)

    def __init__(self, height=18, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.value = 0.0          # 0..1
        self.dragging = False
        self.hover = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.accent.changed.connect(lambda _c: self.update())

    def set_value(self, v):
        if not self.dragging:
            self.value = max(0.0, min(1.0, v))
            self.update()

    def _at(self, x):
        return max(0.0, min(1.0, (x - 6) / max(1, self.width() - 12)))

    def mousePressEvent(self, e):
        self.dragging = True
        self.value = self._at(e.position().x())
        self.moved.emit(self.value)
        self.update()

    def mouseMoveEvent(self, e):
        if self.dragging:
            self.value = self._at(e.position().x())
            self.moved.emit(self.value)
            self.update()

    def mouseReleaseEvent(self, e):
        if self.dragging:
            self.dragging = False
            self.released.emit(self.value)
            self.update()

    def wheelEvent(self, e):
        step = 0.04 if e.angleDelta().y() > 0 else -0.04
        self.value = max(0.0, min(1.0, self.value + step))
        self.moved.emit(self.value)
        self.released.emit(self.value)
        self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.hover or self.dragging
        h = 5 if active else 3
        y = self.height() / 2
        track = QRectF(6, y - h / 2, self.width() - 12, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRoundedRect(track, h / 2, h / 2)
        fill = QRectF(track.left(), track.top(), track.width() * self.value, h)
        p.setBrush(theme.accent.color if active else QColor(theme.TEXT))
        p.drawRoundedRect(fill, h / 2, h / 2)
        if active:
            p.setBrush(QColor('#ffffff'))
            p.drawEllipse(QPointF(fill.right(), y), 6, 6)


class Chip(QPushButton):
    def __init__(self, text, checkable=False, parent=None):
        super().__init__(text, parent)
        self.setObjectName('Chip')
        self.setFixedHeight(32)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


def pill(text, ghost=False):
    b = QPushButton(text)
    b.setObjectName('Ghost' if ghost else 'Pill')
    b.setFixedHeight(36)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def label(text='', size=10, weight=400, color=None, display=False):
    l = QLabel(text)
    color = color or theme.TEXT
    l.setFont(theme.font(size, weight, display))
    l.setStyleSheet(f'color: {color}; background: transparent;')
    return l


def eyebrow(text=''):
    """Spaced capitals in the song's accent, above a title (the same line Now sets over the song's name)."""
    l = QLabel(text.upper())
    l.setObjectName('Eyebrow')
    l.setFont(theme.caps(8.6, 700, 2.4))
    return l


def heading(text, size=14):
    """A section title in the serif."""
    return label(text, size, 700, display=True)


def search_box(hint, height=42, glass=18):
    """A rounded field with the search glass inside it."""
    from PyQt6.QtWidgets import QLineEdit
    box = QLineEdit()
    box.setPlaceholderText(hint)
    box.setClearButtonEnabled(True)
    box.setFixedHeight(height)
    icon = QLabel(box)
    icon.setPixmap(theme.pixmap('search', theme.MUTED, glass))
    icon.move(14, (height - glass) // 2)
    icon.setStyleSheet('background: transparent;')
    return box


class Toggle(QPushButton):
    """An on/off switch: a small sun that walks along its track and takes the accent when on (140 ms)."""

    def __init__(self, on=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.t = 1.0 if on else 0.0
        self.anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self.anim.valueChanged.connect(lambda v: (setattr(self, 't', float(v)), self.update()))
        self.toggled.connect(self._slide)
        theme.accent.changed.connect(lambda _c: self.update())

    def _slide(self, on):
        self.anim.stop()
        self.anim.setStartValue(self.t)
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        acc = theme.accent.color
        p.setPen(QPen(theme.mix(QColor(255, 255, 255, 40), acc, self.t), 1))
        p.setBrush(theme.mix(QColor(255, 255, 255, 14), QColor(acc.red(), acc.green(), acc.blue(), 70), self.t))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - 6 - d) * self.t
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.mix(QColor(theme.MUTED), QColor('#ffffff'), self.t))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


def draw_quiet(p, rect, title, note=''):
    """An empty page is never just words: a small horizon in the song's colours (far hills, a still sun just over
    them, its light on the water), a line in the serif and a quiet note under it."""
    import math
    pal = theme.scene.pal
    w = min(280.0, rect.width())
    x0, y0 = rect.center().x() - w / 2, rect.top()
    hz = y0 + 64
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    glow = QColor(pal.glow)
    for i, a in enumerate((26, 16, 9)):
        glow.setAlpha(a)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        r = 17 + i * 11
        p.drawEllipse(QPointF(x0 + w * 0.62, hz - 14), r, r)
    p.setBrush(pal.star)
    p.drawEllipse(QPointF(x0 + w * 0.62, hz - 14), 10, 10)
    for k, (amp, col, alpha) in enumerate(((20, theme.mix(pal.far, pal.glow, 0.35), 235), (12, pal.mid, 255))):
        path = QPainterPath(QPointF(x0, hz))
        for i in range(41):
            x = x0 + w * i / 40
            edge = math.sin(math.pi * i / 40) ** 1.6
            y = hz - edge * amp * (0.55 + 0.45 * math.sin(i * 0.55 + k * 2.1) * math.cos(i * 0.21 + k))
            path.lineTo(x, y)
        path.lineTo(x0 + w, hz)
        path.closeSubpath()
        c = QColor(col)
        c.setAlpha(alpha)
        p.setBrush(c)
        p.drawPath(path)
    p.setPen(QPen(QColor(pal.lit), 1))
    p.drawLine(QPointF(x0, hz + 0.5), QPointF(x0 + w, hz + 0.5))
    lit = QColor(pal.star)
    for i, (dx, a) in enumerate(((10, 110), (7, 70), (4, 40))):
        lit.setAlpha(a)
        p.setPen(QPen(lit, 1.2))
        y = hz + 6 + i * 5
        p.drawLine(QPointF(x0 + w * 0.62 - dx, y), QPointF(x0 + w * 0.62 + dx, y))
    p.setPen(QColor(theme.TEXT))
    p.setFont(theme.serif(17, 500, italic=True))
    p.drawText(QRectF(rect.left(), hz + 26, rect.width(), 30), Qt.AlignmentFlag.AlignHCenter, title)
    if note:
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.font(10, 400))
        nw = min(460.0, rect.width())
        p.drawText(QRectF(rect.center().x() - nw / 2, hz + 62, nw, rect.bottom() - hz - 62),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, note)
    p.restore()


class Quiet(QWidget):
    """The look of an empty page (draw_quiet), as a widget."""

    def __init__(self, title='', note='', parent=None):
        super().__init__(parent)
        self.title, self.note = title, note
        self.setMinimumHeight(190)
        theme.scene.changed.connect(self.update)

    def say(self, title, note=''):
        self.title, self.note = title, note
        self.update()

    def paintEvent(self, e):
        draw_quiet(QPainter(self), QRectF(self.rect()).adjusted(0, 36, 0, 0), self.title, self.note)


class KeyCap(QWidget):
    """A key, drawn as a small keycap, and what it does: one line of the keys sheet in Settings."""

    def __init__(self, keys, meaning):
        super().__init__()
        self.keys, self.meaning = keys, meaning
        self.cap = theme.caps(8.4, 700, 1.2)
        self.f = theme.font(9.5, 400)
        fm, mf = QFontMetrics(self.cap), QFontMetrics(self.f)
        self.widths = [fm.horizontalAdvance(k.upper()) + 16 for k in keys]
        self.setFixedSize(sum(self.widths) + 6 * (len(keys) - 1) + 10 + mf.horizontalAdvance(meaning) + 4, 26)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = 0.0
        for k, w in zip(self.keys, self.widths):
            r = QRectF(x + 0.5, 1.5, w - 1, 21)
            p.setPen(QPen(QColor(255, 255, 255, 34), 1))
            p.setBrush(QColor(255, 255, 255, 12))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QPen(QColor(0, 0, 0, 90), 1))
            p.drawLine(QPointF(r.left() + 4, r.bottom() + 1), QPointF(r.right() - 4, r.bottom() + 1))
            p.setPen(QColor(theme.TEXT))
            p.setFont(self.cap)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, k.upper())
            x += w + 6
        p.setPen(QColor(theme.MUTED))
        p.setFont(self.f)
        p.drawText(QRectF(x + 4, 0, self.width() - x - 4, 24), Qt.AlignmentFlag.AlignVCenter, self.meaning)


class MixTile(QWidget):
    """A square card for a mix: big soft colour from the artist, their name set large, a play button on hover."""
    clicked = pyqtSignal()

    def __init__(self, title, subtitle, seed, track=None, size=168, parent=None):
        super().__init__(parent)
        self.title, self.subtitle, self.seed, self.track, self.size_ = title, subtitle, seed, track, size
        self.setFixedSize(size, size + 52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hover = False
        if track is not None:
            covers().ready.connect(self._cover_ready)   # a bound method: let go when this tile is deleted

    def _cover_ready(self, uid):
        if self.track is not None and uid == self.track.uid:
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

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.size_
        rect = QRectF(0, 0, s, s)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        p.setClipPath(path)
        base = theme.tint(self.seed)
        img = covers().image(self.track) if self.track is not None else None
        if img is not None:
            px = theme.rounded(img, s, 14)
            p.drawPixmap(0, 0, px)
            p.fillRect(rect, QColor(0, 0, 0, 100))
        else:
            p.fillRect(rect, base.darker(230))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(base.darker(140))
            p.drawEllipse(QRectF(s * 0.3, s * 0.32, s * 1.0, s * 1.0))
            p.setBrush(base)
            p.drawEllipse(QRectF(s * 0.62, s * 0.62, s * 0.55, s * 0.55))
        p.setPen(QColor('#ffffff'))
        p.setFont(theme.font(15, 800, True))
        p.drawText(QRectF(14, 12, s - 28, s - 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                   | Qt.TextFlag.TextWordWrap, self.title)
        if self.hover:
            p.setBrush(QColor(theme.TEXT))
            p.setPen(Qt.PenStyle.NoPen)
            c = QRectF(s - 52, s - 52, 40, 40)
            p.drawEllipse(c)
            p.drawPixmap(int(c.left() + 11), int(c.top() + 10), theme.pixmap('play', '#0b0b0d', 19))
        p.setClipping(False)
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.font(10, 600))
        fm = QFontMetrics(p.font())
        p.drawText(QRect(2, s + 8, s - 4, 20), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.subtitle, Qt.TextElideMode.ElideRight, s - 4))


class Dock(QWidget):
    """The player. Two looks: 'strip' along the bottom of every page (the song's shore far away as the seek bar, the
    cover as a small sun, names, the controls) and 'lake' on Now, where it floats in the water under the title with
    only the controls (the landscape itself is the seek bar there)."""

    def __init__(self, engine, parent=None, look=None):
        super().__init__(parent)
        self.engine = engine
        self.look = look
        self.mode = 'strip'
        col = QVBoxLayout(self)
        col.setContentsMargins(22, 14, 18, 10)
        col.setSpacing(2)
        self.col = col
        self.seek = Shoreline(30, style=look.seek_style if look else 'shore')
        col.addWidget(self.seek)
        row = QHBoxLayout()
        row.setSpacing(12)
        col.addLayout(row)
        # left: what plays
        self.cover = QLabel()
        self.cover.setFixedSize(46, 46)
        self.cover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cover.setToolTip('Lyrics')
        self.title = label('Nothing playing', 10.5, 600)
        self.artist = QLabel('')
        self.artist.setFont(theme.caps(8.6, 400, 1.8))
        self.artist.setStyleSheet(f'color: {theme.MUTED}; background: transparent;')
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addStretch()
        names.addWidget(self.title)
        names.addWidget(self.artist)
        names.addStretch()
        self.heart = IconButton('heart', 18, 32, 'Like (Ctrl+L)', True)
        left = QHBoxLayout()
        left.setSpacing(12)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.cover)
        left.addLayout(names)
        left.addWidget(self.heart)
        self.keep = IconButton('download', 18, 32, 'Save to this PC', True)
        self.keep.hide()
        left.addWidget(self.keep)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMinimumWidth(170)
        # centre: transport
        self.shuffle = IconButton('shuffle', 17, 34, 'Shuffle (Ctrl+S)', True)
        self.shuffle.dot = True
        self.prev = IconButton('prev', 18, 36, 'Previous (Ctrl+Left)')
        self.play = PlayButton(44)
        self.play.setToolTip('Play / pause (Space)')
        self.next = IconButton('next', 18, 36, 'Next (Ctrl+Right)')
        self.repeat = IconButton('repeat', 17, 34, 'Repeat (Ctrl+R)', True)
        self.repeat.dot = True
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.setContentsMargins(0, 0, 0, 0)
        for b in (self.shuffle, self.prev, self.play, self.next, self.repeat):
            buttons.addWidget(b)
        cw = QWidget()
        cw.setLayout(buttons)
        # right: time and the rest
        self.time = QLabel('0:00 / 0:00')
        self.time.setFont(theme.caps(9.4, 400, 1.2))
        self.time.setMinimumWidth(92)
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.drift = IconButton('radio', 19, 34, 'Drift: keep playing songs you will like', True)
        self.drift.dot = True
        self.lyrics = IconButton('lyrics', 18, 34, 'Lyrics (Ctrl+Y)', True)
        self.queue = IconButton('queue', 19, 34, 'Queue (Ctrl+Q)', True)
        self.mute = IconButton('volume', 18, 32, 'Mute (M)')
        self.volume = Slider(16)
        self.volume.setFixedWidth(88)
        right = QHBoxLayout()
        right.setSpacing(2)
        right.setContentsMargins(0, 0, 0, 0)
        right.addStretch()
        right.addWidget(self.time)
        right.addSpacing(8)
        for w in (self.drift, self.lyrics, self.queue, self.mute, self.volume):
            right.addWidget(w)
        rw = QWidget()
        rw.setLayout(right)
        rw.setMinimumWidth(150)
        self.sides = (left, buttons, right)
        self.row, self.lw = row, lw
        row.addWidget(lw, 3)
        row.addWidget(cw, 0)
        row.addWidget(rw, 3)
        self.side = (lw, rw)
        self.hover_at = -1.0
        self._wire()
        self.show_track(None)

    def _wire(self):
        e = self.engine
        self.play.clicked.connect(e.toggle)
        self.next.clicked.connect(lambda: e.next())
        self.prev.clicked.connect(e.previous)
        self.shuffle.toggled.connect(e.set_shuffle)
        self.repeat.clicked.connect(self._repeat)
        self.drift.setChecked(e.drift)
        self.drift.toggled.connect(e.set_drift)
        self.heart.clicked.connect(lambda: e.current and e.like(e.current))
        self.seek.moved.connect(lambda v: self._time(v * (e.duration or 0), True))
        self.seek.released.connect(lambda v: e.seek(v * (e.duration or 0)))
        self.seek.hovered.connect(self._hover)
        self.seek.peak_clicked.connect(lambda v: e.seek(v * (e.duration or 0)))
        self.volume.set_value(e.volume)
        self.volume.moved.connect(e.set_volume)
        self.volume.moved.connect(self._volume_icon)
        self.mute.clicked.connect(self._mute)
        e.track_changed.connect(self.show_track)
        e.track_updated.connect(lambda t: e.current and t.uid == e.current.uid and self._marks(t))
        e.downloads_changed.connect(lambda *_: e.current and self._marks(e.current))
        e.playing_changed.connect(self.play.set_playing)
        e.loading.connect(self.play.set_busy)
        e.position_changed.connect(self._position)
        e.wave_ready.connect(self._wave)
        covers().ready.connect(lambda uid: e.current and uid == e.current.uid and self._cover(e.current))
        self._volume_icon(e.volume)

    def jump_best(self):
        b = self.seek.best
        if b is not None and self.engine.duration:
            self.engine.seek(b * self.engine.duration)
            return True
        return False

    def _wave(self, uid, wave):
        cur = self.engine.current
        if cur and uid == cur.uid:
            self.seek.set_wave(wave)

    def _hover(self, v):
        self.hover_at = v
        if v >= 0:
            self._time(v * (self.engine.duration or 0), True)
        else:
            self._time(self.engine.position)

    def _time(self, ms, aim=False):
        text = f'{mmss(ms)} / {mmss(self.engine.duration)}'
        if text != self.time.text():
            self.time.setText(text)
        style = f'color: {theme.accent.color.name() if aim else theme.MUTED}; background: transparent;'
        if style != self.time.styleSheet():     # re-styling repaints: only when it changes
            self.time.setStyleSheet(style)

    def _repeat(self):
        mode = self.engine.cycle_repeat()
        self.repeat.setChecked(mode != 'off')
        self.repeat.set_icon('repeat1' if mode == 'one' else 'repeat')

    def _mute(self):
        self.engine.toggle_mute()
        self._volume_icon(self.engine.volume)

    def _volume_icon(self, v):
        self.mute.set_icon('mute' if self.engine.muted or v == 0 else 'volume')

    def _position(self, ms, dur):
        dur = dur or (self.engine.current.duration if self.engine.current else 0)
        if not self.seek.dragging:
            self.seek.set_value(ms / dur if dur else 0)
            if self.hover_at < 0:
                self._time(ms)

    def show_track(self, t):
        if t is None:
            self.title.setText('Nothing playing')
            self.artist.setText('Press play: Drift picks for you')
            self.cover.setPixmap(self._cover_px(None))
            self.seek.set_wave([])
            return
        fm = QFontMetrics(self.title.font())
        w = max(80, min(320, self.side[0].width() - 110))
        self.title.setText(fm.elidedText(t.title, Qt.TextElideMode.ElideRight, w))
        self.title.setToolTip(t.title)
        self.artist.setText(QFontMetrics(self.artist.font()).elidedText(t.subtitle.upper(), Qt.TextElideMode.ElideRight, w))
        self._marks(t)
        uid, wave = self.engine.wave
        self.seek.set_wave(wave if uid == t.uid else [])
        self._cover(t)

    def _marks(self, t):
        """The heart (liked) and, beside it, saved on this PC: an arrow to save it, a tick once it is here."""
        self.heart.setChecked(bool(t.liked))
        saved = t.local
        self.keep.setVisible(bool(t.page) or saved)
        self.keep.set_icon('saved' if saved else 'download')
        self.keep.setChecked(saved)
        self.keep.setToolTip('On this PC (plays offline). Click to remove it' if saved else
                             'Saving to this PC…' if self.engine.saving(t) else 'Save to this PC (plays offline)')

    def _cover_px(self, t):
        if self.look is not None:
            return self.look.dock_cover(t, 46)
        return covers().get(t, 46, 23) if t else theme.placeholder('drift', 46, 23)

    def _cover(self, t):
        self.cover.setPixmap(self._cover_px(t))
        img = covers().image(t)
        if img is None and t.uid not in covers().images:
            return                      # still loading: the colours change once, when it arrives
        from .covers import keep_for_card
        keep_for_card(t.uid, img)
        pal = theme.scene.of(t.uid, img, t.artist or t.title)
        theme.accent.set(pal.acc)
        theme.scene.follow(pal)

    def set_mode(self, mode):
        """'strip' (every page) or 'lake' (Now: only the controls, floating in the water)."""
        if mode == self.mode:
            return
        self.mode = mode
        lake = mode == 'lake'
        self.seek.setVisible(not lake)
        for w in (self.cover, self.title, self.artist):
            w.setVisible(not lake)
        self.col.setContentsMargins(12, 0, 12, 0) if lake else self.col.setContentsMargins(22, 14, 18, 10)
        self._fit()
        self.update()

    def paintEvent(self, e):
        if self.mode != 'strip' or self.look is None:
            return
        p = QPainter(self)      # the page fades into the sketch's player strip
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.look.paint_dock(p, self)

    def _fit(self):
        w = self.width()
        if self.mode == 'strip':
            self._centred(True)
            self.volume.setVisible(w > 900)
            self.lyrics.setVisible(w > 640)
            self.time.setVisible(w > 720)
            self.shuffle.setVisible(w > 560)
            self.repeat.setVisible(w > 560)
            return
        # 'lake' is only the controls: centred if they all fit, else packed after the heart, and only then dropping the
        # least needed first
        drop = ((self.volume,), (self.time,), (self.lyrics,), (self.shuffle, self.repeat))
        for group in drop:
            for b in group:
                b.setVisible(True)
        left, mid, right = self.sides
        m = self.col.contentsMargins()

        def need(centred):
            for lay in self.sides:
                lay.invalidate()
            lw, rw = left.minimumSize().width(), right.minimumSize().width()
            sides = 2 * max(170, lw, rw) if centred else lw + rw
            return sides + mid.minimumSize().width() + 2 * 12 + m.left() + m.right()
        self._centred(True)
        if need(True) <= w:
            return
        self._centred(False)
        for group in drop:
            if need(False) <= w:
                break
            for b in group:
                b.setVisible(False)

    def _centred(self, on):
        self.lw.setMinimumWidth(170 if on else 0)
        self.row.setStretch(0, 3 if on else 0)
        self.row.setStretch(2, 3 if on else 1)

    def resizeEvent(self, e):
        self._fit()
        if self.engine.current and self.mode == 'strip':
            self.show_track(self.engine.current)
        super().resizeEvent(e)

    def sizeHint(self):
        return QSize(900, 60 if self.mode == 'lake' else 108)


Bar = Dock
