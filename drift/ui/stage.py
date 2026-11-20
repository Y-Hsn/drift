"""Two small pieces shared by Now and Lyrics: a cover that dissolves into the next one, and the Whisper (the line being
sung, the next one faint under it)."""
from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from . import theme
from .covers import shared as covers
from .lyricsfeed import line_at


class CoverView(QWidget):
    """A square cover; a new one dissolves over the old (260 ms)."""

    def __init__(self, size=280, radius=18, parent=None):
        super().__init__(parent)
        self.side, self.radius = size, radius
        self.setFixedSize(size, size)
        self.now = self.before = None
        self.t = 1.0
        self.track = None
        self.anim = QVariantAnimation(self, duration=260, easingCurve=QEasingCurve(QEasingCurve.Type.InOutSine))
        self.anim.valueChanged.connect(self._step)
        covers().ready.connect(self._cover_ready)

    def _cover_ready(self, uid):
        if self.track is not None and uid == self.track.uid:
            self._refresh()

    def _step(self, v):
        self.t = float(v)
        self.update()

    def show_track(self, t):
        same = self.track is not None and t is not None and t.uid == self.track.uid
        self.track = t
        if same:
            return self._refresh()
        self.before, self.now = self.now, (covers().get(t, self.side, self.radius) if t else None)
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def _refresh(self):
        if self.track is not None:
            self.now = covers().get(self.track, self.side, self.radius)
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.before is not None and self.t < 1:
            p.setOpacity(1 - self.t)
            p.drawPixmap(0, 0, self.before)
        if self.now is not None:
            p.setOpacity(self.t if self.before is not None else 1.0)
            p.drawPixmap(0, 0, self.now)


class Whisper(QWidget):
    """The line being sung, and the next one faint below it. A new line rises in (240 ms). Silent between songs."""
    seek = pyqtSignal(int)

    def __init__(self, parent=None, centred=False, scale=1.0, serif=False):
        super().__init__(parent)
        self.align = Qt.AlignmentFlag.AlignHCenter if centred else Qt.AlignmentFlag.AlignLeft
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.lines = []
        self.at = -1
        self.t = 1.0
        self.serif = serif
        self.scale = 0
        self.scale_to(scale)
        self.anim = QVariantAnimation(self, duration=240, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self.anim.valueChanged.connect(self._step)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Click: jump to this line')

    def scale_to(self, scale):
        if scale == self.scale:
            return
        self.scale = scale
        self.setFixedHeight(int(66 * scale))
        if self.serif:      # written in the sky: the serif's italic
            self.f_now = theme.serif(14.5 * scale, 500, italic=True)
            self.f_next = theme.serif(11.5 * scale, 400, italic=True)
        else:
            self.f_now = theme.font(14.5 * scale, 600, True)
            self.f_next = theme.font(11.5 * scale, 500, True)
        self.update()

    def _step(self, v):
        self.t = float(v)
        self.update()

    def set_lines(self, lines):
        synced = bool(lines) and lines[0][0] is not None
        self.lines = lines if synced else []
        self.at = -1
        self.update()

    def position(self, ms):
        if not self.lines:
            return
        i = line_at(self.lines, ms)
        if i != self.at:
            self.at = i
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()

    def mouseReleaseEvent(self, e):
        if self.lines and 0 <= self.at < len(self.lines):
            self.seek.emit(self.lines[self.at][0])

    def paintEvent(self, e):
        if not self.lines:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        k = self.scale
        rise = (1 - self.t) * 10 * k
        flags = self.align | Qt.AlignmentFlag.AlignVCenter
        now = self.lines[self.at][1] if self.at >= 0 else ''
        nxt = self.lines[self.at + 1][1] if self.at + 1 < len(self.lines) else ''
        if self.at < 0:
            now, nxt = '', self.lines[0][1]
        p.setFont(self.f_now)
        c = QColor(theme.TEXT)
        c.setAlphaF(0.95 * self.t)
        p.setPen(c)
        p.drawText(QRectF(0, rise, w, 30 * k), flags,
                   QFontMetrics(self.f_now).elidedText(now or '', Qt.TextElideMode.ElideRight, w))
        p.setFont(self.f_next)
        c = QColor(theme.MUTED)
        c.setAlphaF(0.55 * self.t)
        p.setPen(c)
        p.drawText(QRectF(0, 32 * k + rise, w, 24 * k), flags,
                   QFontMetrics(self.f_next).elidedText(nxt or '', Qt.TextElideMode.ElideRight, w))


