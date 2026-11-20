"""Now: every song is a landscape.

The song's own loudness, bar by bar as the core measured it, is the near range of hills at the shore: a quiet intro
lies low, the chorus stands up as the summit. Behind it two hazier ranges are slower memories of the same shape. The
cover is the sun: it rises on the left when the song starts, crosses the sky as it plays and sets on the right, glowing
brighter where the song is loud. Everything sits over a still lake that mirrors it. The played part of the shore is
lit by the sun; a small lantern walks along the ridge where the song is now. Click or drag the shore to go anywhere,
the star above the summit jumps to the best part. The title is set in the water, the line being sung is written in
the sky, and the next songs wait along the bottom.

Light on the machine: nothing moves on a timer. The picture changes only when the song moves on (four times a second)
and then only the small parts that changed are painted again: the sun's patch and a thin strip of the shore."""
import math
import random
import time

from PyQt6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, \
    QRegion, QTransform
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from . import theme
from .covers import shared as covers
from .lists import mmss
from .lyricsfeed import shared as lyrics
from .stage import Whisper
from .widgets import best_part, pill

N = 160          # points along each range (the core's shape has 160 bars)


def smooth(v, k):
    """Moving average over k points (edges use what they have)."""
    if k <= 1 or not v:
        return list(v)
    ps = [0.0]
    for x in v:
        ps.append(ps[-1] + x)
    n, h = len(v), k // 2
    return [(ps[min(n, i + h + 1)] - ps[max(0, i - h)]) / (min(n, i + h + 1) - max(0, i - h)) for i in range(n)]


def noise(seed, n=N, octaves=((1.3, 1.0), (2.9, 0.5), (6.1, 0.22))):
    """A calm, repeatable rolling line (0..1) for one seed: a few slow waves added up."""
    r = random.Random(seed)
    waves = [(f * r.uniform(0.8, 1.25), a, r.uniform(0, 6.283)) for f, a in octaves]
    vals = [sum(a * math.sin(6.283 * f * i / n + ph) for f, a, ph in waves) for i in range(n)]
    lo, hi = min(vals), max(vals)
    return [(x - lo) / ((hi - lo) or 1) for x in vals]


def ranges(seed, wave):
    """(far, mid, near) heights 0..1. Near is the song itself (stretched to its own quiet..loud), the others are
    slower echoes of it mixed with the seed's own hills, so even a flat song gets a horizon."""
    if wave:
        lo, hi = min(wave), max(wave)
        shape = [(x - lo) / ((hi - lo) or 1) for x in wave]
        if len(shape) != N:
            shape = [shape[min(len(shape) - 1, int(i * len(shape) / N))] for i in range(N)]
        near = [0.07 + 0.93 * v for v in smooth(shape, 3)]
    else:
        shape = noise(seed + 'w')
        near = [0.08 + 0.22 * v for v in shape]
    mid = [0.5 * a + 0.5 * b for a, b in zip(smooth(shape, 13), noise(seed + 'm', N, ((1.7, 1), (3.9, .45), (8.3, .2))))]
    far = [0.35 * a + 0.65 * b for a, b in zip(smooth(shape, 35), noise(seed + 'f', N, ((0.9, 1), (2.3, .5), (5.1, .25))))]
    return far, mid, near


def ridge(vals, w, base, amp, bottom):
    """(filled silhouette, its top edge) through vals across width w, as smooth curves (Catmull-Rom)."""
    n = len(vals)
    pts = [(i * w / (n - 1), base - v * amp) for i, v in enumerate(vals)]
    edge = QPainterPath(QPointF(*pts[0]))
    for i in range(n - 1):
        x0, y0 = pts[i - 1] if i else pts[i]
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        x3, y3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        edge.cubicTo(QPointF(x1 + (x2 - x0) / 6, y1 + (y2 - y0) / 6), QPointF(x2 - (x3 - x1) / 6, y2 - (y3 - y1) / 6),
                     QPointF(x2, y2))
    fill = QPainterPath(edge)
    fill.lineTo(QPointF(w + 2, pts[-1][1]))
    fill.lineTo(QPointF(w + 2, bottom))
    fill.lineTo(QPointF(-2, bottom))
    fill.lineTo(QPointF(-2, pts[0][1]))
    fill.closeSubpath()
    return fill, edge


def lerp(a, b, t):
    return [x + (y - x) * t for x, y in zip(a, b)]


class Scene(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win, self.engine = win, win.engine
        self.look = win.look
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)    # it paints every pixel itself
        self.track = None
        self.calm = False
        self.frac = 0.0
        self.loud = 0.0
        self.hover_x = -1.0
        self.drag = None
        self.wave = []
        self.best = None
        self.heights = ranges('drift', [])
        self.heights_from = self.heights
        self.rise = 1.0
        self.paths = self.layers = None
        self.then = []          # (rect, track)
        self.fade = None        # the last picture, dissolving over a new song's landscape
        self.fade_t = 1.0
        self.g = {}
        self.whisper = Whisper(self, centred=True, scale=1.18, serif=True)
        self.whisper.seek.connect(self.engine.seek)
        self.uid = None
        self.greet = QWidget(self)
        row = QHBoxLayout(self.greet)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        go = pill('  Start Drift')
        go.setIcon(theme.icon('radio', theme.ink_on(theme.accent.color), 18))
        go.setToolTip('Endless music picked from what you like')
        go.clicked.connect(lambda: self.engine.start_radio())
        self.shuffle_saved = pill('Shuffle saved songs', ghost=True)
        self.shuffle_saved.clicked.connect(lambda: self.engine.play_list(self.win.store.local_tracks(), shuffle=True))
        row.addWidget(go)
        row.addWidget(self.shuffle_saved)
        row.addStretch()
        self.grow = QVariantAnimation(self, duration=900, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self.grow.setStartValue(0.0)
        self.grow.setEndValue(1.0)
        self.grow.valueChanged.connect(self._grow)
        self.dissolve = QVariantAnimation(self, duration=650, easingCurve=QEasingCurve(QEasingCurve.Type.InOutSine))
        self.dissolve.setStartValue(0.0)
        self.dissolve.setEndValue(1.0)
        self.dissolve.valueChanged.connect(self._dissolve)
        self.dissolve.finished.connect(self._dissolved)
        e = self.engine
        e.track_changed.connect(self.show_track)
        e.queue_changed.connect(self._queue)
        e.position_changed.connect(self._position)
        e.wave_ready.connect(self._wave)
        theme.scene.changed.connect(self._palette)
        covers().ready.connect(self._cover_ready)
        lyrics().ready.connect(self._lyrics)

    # ---- geometry -------------------------------------------------------------------------------------------------
    def resizeEvent(self, e):
        self._layout()
        super().resizeEvent(e)

    def _layout(self):
        W, H = max(1, self.width()), max(1, self.height())
        g = self.g
        g['W'], g['H'] = W, H
        g['pad'] = pad = max(28, int(W * 0.055))
        g['hy'] = hy = int(H * (0.6 if self.calm else 0.52 if H >= 760 else 0.49))
        g['k'] = 0.92 if self.calm else self.look.reflect()        # calm: the lake mirrors everything
        g['far'], g['mid'], g['near'] = hy * 0.34, hy * 0.24, hy * 0.15
        g['r'] = r = int(max(34, min(W * 0.10, H * 0.082, 100)))
        g['then'] = then = 44 if H >= 700 and not self.calm else 0
        wy = int(hy * 0.30) if self.calm else 64
        self.whisper.scale_to(1.55 if self.calm else 1.18)
        self.whisper.setGeometry(pad, wy, W - 2 * pad, self.whisper.height())
        g['apex'] = wy + self.whisper.height() + r + 10
        g['low'] = hy - r * 0.1
        g['dock_y'] = H - 60 - then - 14
        g['text_y'] = hy + max(26, int(g['far'] * self.g['k'] * 0.55))     # moved down to sit on the controls (_text)
        self.paths = self.layers = None
        self._text()
        self._place_greet()
        self.update()

    def dock_rect(self):
        g = self.g
        if not g:
            return QRect(0, 0, self.width(), 60)
        return QRect(g['pad'] - 12, g['dock_y'], g['W'] - 2 * g['pad'] + 24, 60)

    def sun(self, frac=None):
        g = self.g
        f = self.frac if frac is None else frac
        f = max(0.0, min(1.0, f))
        x = g['W'] * 0.12 + g['W'] * 0.76 * f
        lift = math.sin(math.pi * f) ** 0.7
        y = g['low'] - (g['low'] - g['apex']) * lift
        return QPointF(x, y)

    def sun_rect(self, frac=None):
        c, r = self.sun(frac), self.g['r']
        k = r * 2.0
        top = QRectF(c.x() - k, c.y() - k, 2 * k, 2 * k)
        hy = self.g['hy']
        streak = QRectF(c.x() - r * 1.3, hy, r * 2.6, self.g['far'] * self.g['k'] + 12)   # its path on the water
        return top.united(streak).toAlignedRect().adjusted(-2, -2, 2, 2)

    def shore_rect(self, x):
        g = self.g
        top = g['hy'] - g['near'] - 30
        return QRect(int(x) - 14, int(top), 28, int(g['hy'] + g['near'] * self.g['k'] + 8 - top))

    def ridge_y(self, x):
        """Height of the near range (the song) at x."""
        self._paths()
        near = self._near_now
        f = max(0.0, min(1.0, x / max(1, self.g['W']))) * (N - 1)
        i = int(f)
        v = near[i] if i >= N - 1 else near[i] + (near[i + 1] - near[i]) * (f - i)
        return self.g['hy'] + 1 - v * self.g['near']

    def _paths(self):
        if self.paths is None:
            g = self.g
            W, hy = g['W'], g['hy']
            far, mid, near = (lerp(a, b, self.rise) for a, b in zip(self.heights_from, self.heights))
            self.paths = {
                'far': ridge(far, W, hy + 1, g['far'], hy + 2),
                'mid': ridge(mid, W, hy + 1, g['mid'], hy + 2),
                'near': ridge(near, W, hy + 1, g['near'], hy + 2),
            }
            self._near_now = near
        return self.paths

    # ---- text -------------------------------------------------------------------------------------------------
    def _text(self):
        """Fit the title (the serif, as large as two lines allow) between the water's edge and the controls."""
        g = self.g
        if not g:
            return
        W = g['W']
        width = W - 2 * g['pad']
        room = g['dock_y'] - g['text_y'] - 64
        t = self.track
        title = t.title if t else self.greeting()
        size = 56
        while size > 18:
            f = theme.serif(size, 600)
            fm = QFontMetrics(f)
            br = fm.boundingRect(QRect(0, 0, width, 10000), Qt.TextFlag.TextWordWrap, title)
            if br.height() <= fm.lineSpacing() * 2 + 2 and br.height() <= room:
                break
            size -= 2
        f = theme.serif(size, 600)
        fm = QFontMetrics(f)
        br = fm.boundingRect(QRect(0, 0, width, 10000), Qt.TextFlag.TextWordWrap, title)
        if br.height() > fm.lineSpacing() * 2 + 2:
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, width * 2 - 40)
            br = fm.boundingRect(QRect(0, 0, width, 10000), Qt.TextFlag.TextWordWrap, title)
        g['title'] = title
        g['title_font'] = f
        block = 24 + br.height() + 4 + 40           # eyebrow, title, artist
        low = g['dock_y'] - 22 - block                # the words rest just above the controls...
        g['text_y'] = max(g['text_y'], low)
        g['title_rect'] = QRect(g['pad'], g['text_y'] + 24, width, br.height() + 4)
        g['text_rect'] = QRect(0, g['text_y'] - 4, W, br.height() + 24 + 4 + 44)

    def greeting(self):
        h = time.localtime().tm_hour
        return 'Good morning' if 5 <= h < 12 else 'Good afternoon' if 12 <= h < 18 else 'Good evening'

    def eyebrow(self):
        t = self.track
        if t is None:
            return time.strftime('%A · %d %B').upper()
        bits = ['Drifting · picked for you' if self.engine.drift and not t.local else 'Now playing']
        if t.local:
            bits.append('on this PC')
        if t.duration:
            bits.append(mmss(t.duration))
        return ' · '.join(bits).upper()

    def _place_greet(self):
        g = self.g
        if not g:
            return
        self.greet.setVisible(self.track is None and not self.calm)
        self.shuffle_saved.setVisible(bool(self.win.store.local_tracks()))
        r = g['title_rect']
        self.greet.setGeometry(g['pad'], r.bottom() + 54, g['W'] - 2 * g['pad'], 40)

    # ---- the song ---------------------------------------------------------------------------------------------
    def refresh(self):
        self.show_track(self.engine.current)

    def set_calm(self, on):
        if on != self.calm:
            self.calm = on
            self._layout()

    def show_track(self, t):
        new = (t.uid if t else None) != self.uid
        if new and self.isVisible() and self.width() > 0 and self.fade is None and self.uid is not None:
            self.fade = self.grab()
            self.fade_t = 0.0
            self.dissolve.stop()
            self.dissolve.start()
        self.track = t
        if new:
            self.uid = t.uid if t else None
            uid, wave = self.engine.wave
            self._set_wave(wave if t and uid == t.uid else [], animate=False)
            self.frac = (self.engine.position / self.engine.duration) if t and self.engine.duration else 0.0
            self.whisper.set_lines([])
            if t is not None and self.win.store.get('whisper', True):
                feed = lyrics()
                lines = feed.lines(t) if self.engine.online or feed.known(t) else None
                self.whisper.set_lines(lines or [])
                self.whisper.position(self.engine.position)
        self._text()
        self._queue()
        self._place_greet()
        self.update()

    def _palette(self):
        self.layers = None
        if self.isVisible():
            self.update()

    def let_go(self):
        self.layers = None

    def hideEvent(self, e):
        self.layers = None          # out of sight: let the pictures go
        super().hideEvent(e)

    def _cover_ready(self, uid):
        if self.track is not None and uid == self.track.uid and self.isVisible() and self.g:
            self.update(self.sun_rect())

    def _lyrics(self, uid):
        if uid == self.uid and self.win.store.get('whisper', True):
            self.whisper.set_lines(lyrics().cache.get(uid) or [])
            self.whisper.position(self.engine.position)

    def _wave(self, uid, wave):
        if self.track is not None and uid == self.track.uid:
            self._set_wave(wave, animate=self.isVisible())

    def _set_wave(self, wave, animate):
        wave = list(wave or [])
        self.wave = wave
        self.best = best_part(wave) if wave else None
        target = ranges(self.uid or 'drift', wave)
        if animate:
            self.heights_from = [lerp(a, b, self.rise) for a, b in zip(self.heights_from, self.heights)]
            self.heights = target
            self.rise = 0.0
            self.grow.stop()
            self.grow.start()
        else:
            self.heights_from = self.heights = target
            self.rise = 1.0
        self.paths = self.layers = None

    def _grow(self, v):
        self.rise = float(v)
        self.paths = self.layers = None
        if self.g:
            g = self.g
            top = int(g['hy'] - g['far'] - 40)
            self.update(QRect(0, top, g['W'], int(g['hy'] + g['far'] * self.g['k'] + 20 - top)))

    def _dissolve(self, v):
        self.fade_t = float(v)
        self.update()

    def _dissolved(self):
        self.fade = None
        self.update()

    def _queue(self):
        if self.isVisible():
            self._lay_then()
            if self.g and self.g['then']:
                self.update(QRect(0, self.g['H'] - self.g['then'] - 12, self.g['W'], self.g['then'] + 12))

    def _position(self, ms, dur):
        if not self.isVisible() or self.track is None or not self.g:
            return
        if self.drag is None:
            frac = ms / dur if dur else 0.0
        else:
            frac = self.frac
        self.whisper.position(ms)
        loud = 0.0
        if self.wave:
            i = min(len(self.wave) - 1, int(frac * len(self.wave)))
            loud = self.wave[i] / 255.0
        W = self.g['W']
        if abs(frac - self.frac) * W < 1.8 and abs(loud - self.loud) < 0.06:     # nothing a pixel would show
            return
        old_sun, old_x = self.sun_rect(), self.frac * W
        self.frac, self.loud = frac, loud
        region = QRegion(old_sun).united(QRegion(self.sun_rect()))
        region = region.united(QRegion(self.shore_rect(old_x))).united(QRegion(self.shore_rect(frac * W)))
        if abs(frac * W - old_x) > 30:       # a jump: the whole shore changes
            g = self.g
            top = int(g['hy'] - g['near'] - 30)
            region = region.united(QRegion(QRect(0, top, W, int(g['hy'] + g['near'] * self.g['k'] + 10 - top))))
        self.update(region)

    # ---- mouse ------------------------------------------------------------------------------------------------
    def _on_shore(self, pos):
        g = self.g
        return g and g['hy'] - g['near'] - 18 <= pos.y() <= g['hy'] + 22 and self.track is not None

    def _on_sun(self, pos):
        if self.track is None or not self.g:
            return False
        c = self.sun()
        return math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= self.g['r']

    def _on_best(self, pos):
        if self.best is None or not self.g:
            return False
        bx = self.best * self.g['W']
        by = self.ridge_y(bx) - 16
        return abs(pos.x() - bx) <= 10 and abs(pos.y() - by) <= 10

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.drag is not None:
            self._drag_to(pos.x())
            return
        shore = self._on_shore(pos)
        hand = shore or self._on_sun(pos) or self._on_best(pos) or any(r.contains(pos.toPoint()) for r, _ in self.then)
        self.setCursor(Qt.CursorShape.PointingHandCursor if hand else Qt.CursorShape.ArrowCursor)
        self.setToolTip('The best part: click to jump there (J)' if self._on_best(pos) else
                        'Click: lyrics · double click: calm view (F)' if self._on_sun(pos) else '')
        new = pos.x() if shore else -1.0
        if new != self.hover_x:
            old = self.hover_x
            self.hover_x = new
            r = QRegion()
            for x in (old, new):
                if x >= 0:
                    r = r.united(QRegion(self.shore_rect(x).adjusted(-30, -20, 30, 0)))
            self.update(r)

    def leaveEvent(self, e):
        if self.hover_x >= 0:
            old, self.hover_x = self.hover_x, -1.0
            self.update(self.shore_rect(old).adjusted(-30, -20, 30, 0))

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            if e.button() == Qt.MouseButton.RightButton:
                for r, t in self.then:
                    if r.contains(e.position().toPoint()):
                        self.win.track_menu(t, self.engine.queue, e.globalPosition().toPoint())
                        return
            return
        pos = e.position()
        if self._on_best(pos):
            self.engine.seek(self.best * (self.engine.duration or 0))
            return
        if self._on_shore(pos):
            self.drag = pos.x()
            self._drag_to(pos.x())
            return
        for r, t in self.then:
            if r.contains(pos.toPoint()):
                self.win.jump_to(t)
                return

    def _drag_to(self, x):
        g = self.g
        self.drag = x
        frac = max(0.0, min(1.0, x / max(1, g['W'])))
        old_sun = self.sun_rect()
        self.frac = frac
        self.hover_x = x
        top = int(g['hy'] - g['near'] - 30)
        r = QRegion(old_sun).united(QRegion(self.sun_rect()))
        r = r.united(QRegion(QRect(0, top, g['W'], int(g['hy'] + g['near'] * self.g['k'] + 10 - top))))
        self.update(r)

    def mouseReleaseEvent(self, e):
        if self.drag is not None:
            self.drag = None
            self.engine.seek(self.frac * (self.engine.duration or 0))
            return
        if e.button() == Qt.MouseButton.LeftButton and self._on_sun(e.position()):
            self.win.go('lyrics')

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        if self._on_sun(pos) or pos.y() < self.g.get('hy', 0) - self.g.get('far', 0):
            self.win.go('home' if self.calm else 'calm')

    # ---- the next songs, along the bottom ---------------------------------------------------------------------
    def _lay_then(self):
        self.then = []
        g = self.g
        if not g or not g['then'] or self.track is None:
            return
        x = g['pad'] + QFontMetrics(theme.caps(8.5, 700, 2.4)).horizontalAdvance('THEN') + 22
        y = g['H'] - g['then'] - 6
        f1, f2 = theme.font(9.6, 600), theme.font(9.2)
        for t in self.engine.queue[:6]:
            w = 30 + min(200, QFontMetrics(f1).horizontalAdvance(t.title)) + 6 + \
                min(130, QFontMetrics(f2).horizontalAdvance(t.subtitle))
            if x + w > g['W'] - g['pad']:
                break
            self.then.append((QRect(int(x), int(y), int(w), 30), t))
            x += w + 26

    # ---- paint --------------------------------------------------------------------------------------------------
    # The hills and the water change only with a new song, a new size or new colours, so they are drawn once into two
    # thin pictures of the shore band ('land': every range and its mirror; 'lit': the near range as the sun lights
    # it). A tick then costs: a patch of sky, the sun, two picture copies, the lantern.
    def band(self):
        g = self.g
        top = int(g['hy'] - g['far'] * 1.02 - 14)
        return top, int(g['hy'] + g['far'] * g['k'] + 18)

    def _layers(self):
        busy = self.grow.state() == QVariantAnimation.State.Running or \
            theme.scene.anim.state() == QVariantAnimation.State.Running
        if self.layers is None and not busy:
            top, bottom = self.band()
            ratio = self.devicePixelRatioF()
            out = []
            for draw in (self._land, self._lit):
                px = QPixmap(int(self.g['W'] * ratio), int((bottom - top) * ratio))
                px.setDevicePixelRatio(ratio)
                px.fill(Qt.GlobalColor.transparent)
                q = QPainter(px)
                q.setRenderHint(QPainter.RenderHint.Antialiasing)
                q.translate(0, -top)
                draw(q, theme.scene.pal, self._paths())
                q.end()
                out.append(px)
            self.layers = (top, out[0], out[1])
        return self.layers

    def paintEvent(self, e):
        if not self.g:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QRectF(e.rect())
        pal = theme.scene.pal
        g = self.g
        W, H, hy = g['W'], g['H'], g['hy']
        px = self.frac * W if self.track is not None else -1
        top, bottom = self.band()
        if clip.top() < hy:                                   # sky, stars, the sun
            sky = QLinearGradient(0, 0, 0, hy)
            sky.setColorAt(0.0, pal.sky_top)
            sky.setColorAt(0.62, pal.sky_mid)
            sky.setColorAt(1.0, pal.glow)
            p.fillRect(QRectF(0, 0, W, hy + 1).intersected(clip), sky)
            back = self.look.backdrop(W, hy + 1, self.devicePixelRatioF()) if hasattr(self.look, 'backdrop') else None
            if back is not None:                              # the sketch's own sky picture
                from .sketches.landscape import SKY_PICTURE
                r = QRectF(0, 0, W, hy + 1).intersected(clip)
                ratio = back.devicePixelRatio()
                p.setOpacity(SKY_PICTURE)
                p.drawPixmap(r, back, QRectF(r.x() * ratio, r.y() * ratio, r.width() * ratio, r.height() * ratio))
                p.setOpacity(1.0)
            self._stars(p, pal, clip)
            self._sun(p, pal)
        if clip.bottom() > bottom:                            # deep water
            p.fillRect(QRectF(0, bottom, W, H - bottom).intersected(clip), pal.lake_bottom)
        if clip.intersects(QRectF(0, top, W, bottom - top)):  # the shore band
            layers = self._layers()
            if layers is None:                                # colours or hills are moving: draw them live
                self._land(p, pal, self._paths())
                if px > 0:
                    p.save()
                    p.setClipRect(QRectF(0, top, px, bottom - top), Qt.ClipOperation.IntersectClip)
                    self._lit(p, pal, self._paths())
                    p.restore()
            else:
                _t, land, lit = layers
                p.drawPixmap(QPointF(0, top), land)
                if px > 0 and self.track is not None:
                    p.save()
                    p.setClipRect(QRectF(0, top, px, bottom - top), Qt.ClipOperation.IntersectClip)
                    p.drawPixmap(QPointF(0, top), lit)
                    p.restore()
            self._streak(p, pal)
            self._marks(p, pal, px)
        self.look.grain(p, clip)
        if not self.calm and g['text_rect'].intersects(e.rect()):
            self._words(p, pal)
        if self.then and e.rect().bottom() >= g['H'] - g['then'] - 10:
            self._then(p, pal)
        if self.fade is not None:
            p.setOpacity(1.0 - self.fade_t)
            p.drawPixmap(0, 0, self.fade)
            p.setOpacity(1.0)

    def _stars(self, p, pal, clip):
        g = self.g
        if pal.night < 0.4 or not self.look.k.get('stars', True):
            return
        r = random.Random(self.uid or 'drift')
        top = g['hy'] - g['far'] * 0.55
        star = QColor(pal.star)
        p.setPen(Qt.PenStyle.NoPen)
        for _ in range(110):
            x, y = r.random() * g['W'], (r.random() ** 1.6) * top
            s, a = 0.45 + r.random() * 0.9, r.random()
            if not clip.contains(x, y):
                continue
            star.setAlphaF(min(1.0, (0.12 + 0.55 * a) * pal.night * (1 - y / top) ** 0.5))
            p.setBrush(star)
            p.drawEllipse(QPointF(x, y), s, s)

    def _sun(self, p, pal):
        g = self.g
        c, r = self.sun(0.36 if self.track is None else None), g['r']
        halo = QRadialGradient(c, r * 2.0)
        glow = QColor(pal.acc)
        glow.setAlphaF(0.30 + 0.30 * self.loud)
        halo.setColorAt(0.42, glow)
        glow2 = QColor(pal.acc)
        glow2.setAlphaF(0.07 + 0.08 * self.loud)
        halo.setColorAt(0.62, glow2)
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, r * 2.0, r * 2.0)
        if self.track is None:
            p.setBrush(theme.mix(pal.star, pal.glow, 0.25))
            p.drawEllipse(c, r * 0.72, r * 0.72)
            return
        px = covers().get(self.track, 2 * r, r)
        p.drawPixmap(QPointF(c.x() - r, c.y() - r), px)
        ring = QColor(pal.star)
        ring.setAlphaF(0.30)
        p.setPen(QPen(ring, 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, r + 0.5, r + 0.5)

    def _range(self, p, path, top, bottom, hy, shadow):
        fill, _edge = path
        box = fill.boundingRect()
        if shadow:      # paper cut: each range casts a soft shadow onto the one behind it
            p.setPen(Qt.PenStyle.NoPen)
            for dy, a in ((-5, shadow * 0.45), (-2.5, shadow)):
                p.setBrush(QColor(0, 0, 0, int(a)))
                p.drawPath(fill.translated(0, dy))
        grad = QLinearGradient(0, box.top(), 0, hy)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(fill)

    def _land(self, p, pal, paths):
        """Every range, the water under them and their mirror in it (the still part of the shore band)."""
        g = self.g
        W, hy, k = g['W'], g['hy'], g['k']
        depth = g['far'] * k
        _top, bottom = self.band()
        self._range(p, paths['far'], pal.far, theme.mix(pal.far, pal.glow, 0.35), hy, shadow=18)
        self._range(p, paths['mid'], pal.mid, theme.mix(pal.mid, pal.glow, 0.14), hy, shadow=38)
        lake = QLinearGradient(0, hy, 0, hy + depth + 6)
        lake.setColorAt(0.0, pal.lake_top)
        lake.setColorAt(1.0, pal.lake_bottom)
        p.fillRect(QRectF(0, hy, W, bottom - hy), lake)
        p.save()
        p.setClipRect(QRectF(0, hy, W, bottom - hy), Qt.ClipOperation.IntersectClip)
        p.setTransform(QTransform(1, 0, 0, -k, 0, (1 + k) * hy), True)
        p.setPen(Qt.PenStyle.NoPen)
        for key, color, a in (('far', theme.mix(pal.far, pal.lake_top, 0.45), 0.55),
                              ('mid', theme.mix(pal.mid, pal.lake_bottom, 0.3), 0.75),
                              ('near', pal.near, 0.9)):
            c = QColor(color)
            c.setAlphaF(a)
            p.setBrush(c)
            p.drawPath(paths[key][0])
        p.restore()
        p.fillRect(QRectF(0, hy, W, bottom - hy), self._deepen(pal, depth))
        rnd = random.Random((self.uid or 'drift') + 'water')        # ripples
        ripple = QColor(255, 255, 255)
        p.setPen(Qt.PenStyle.NoPen)
        for _ in range(26):
            y = hy + 4 + rnd.random() ** 1.3 * depth
            x, w = rnd.random() * W, 18 + rnd.random() * 110
            ripple.setAlphaF(0.025 + rnd.random() * 0.05)
            p.setBrush(ripple)
            p.drawRect(QRectF(x, y, w, 1))
        fill, edge = paths['near']                                   # the near range, not yet lit
        for dy, a in ((-6, 26), (-3, 52)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawPath(fill.translated(0, dy))
        p.setBrush(pal.near)
        p.drawPath(fill)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        p.drawPath(edge)

    def _deepen(self, pal, depth):
        """The water deepens: reflections fade away from the shore."""
        hy = self.g['hy']
        fade = QLinearGradient(0, hy, 0, hy + depth + 6)
        c0, c1 = QColor(pal.lake_bottom), QColor(pal.lake_bottom)
        c0.setAlphaF(0.0)
        fade.setColorAt(0.0, c0)
        fade.setColorAt(0.25, c0)
        fade.setColorAt(1.0, c1)
        return fade

    def _lit(self, p, pal, paths):
        """The near range as the sun lights it (shown up to where the song is), with its mirror."""
        g = self.g
        W, hy, k = g['W'], g['hy'], g['k']
        depth = g['far'] * k
        fill, edge = paths['near']
        p.save()
        p.setClipRect(QRectF(0, hy, W, depth + 16), Qt.ClipOperation.IntersectClip)
        p.setTransform(QTransform(1, 0, 0, -k, 0, (1 + k) * hy), True)
        c = theme.mix(pal.lit, pal.lake_top, 0.25)
        c.setAlphaF(0.7)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPath(fill)
        p.restore()
        p.fillRect(QRectF(0, hy, W, depth + 16), self._deepen(pal, depth))
        lit = QLinearGradient(0, hy - g['near'], 0, hy)
        lit.setColorAt(0.0, theme.mix(pal.lit, pal.acc, 0.22))
        lit.setColorAt(1.0, theme.mix(pal.lit, pal.near, 0.6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(lit)
        p.drawPath(fill)
        soft = QColor(pal.acc)
        soft.setAlphaF(0.28)
        p.setPen(QPen(soft, 5.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(edge)
        pen = QPen(theme.mix(pal.acc, QColor('#ffffff'), 0.25), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(edge)

    def _streak(self, p, pal):
        """The sun's path on the water, brighter when the song is loud."""
        g = self.g
        hy, r = g['hy'], g['r']
        depth = g['far'] * g['k']
        c = self.sun(0.36 if self.track is None else None)
        rnd = random.Random(7)
        light = QColor(pal.acc if self.track is not None else pal.star)
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(9):
            y = hy + 5 + k * (depth / 9)
            w = r * (1.25 - k * 0.11) * (0.75 + 0.5 * rnd.random())
            light.setAlphaF(max(0.0, (0.30 + 0.22 * self.loud) * (1 - k / 9)))
            p.setBrush(light)
            p.drawRoundedRect(QRectF(c.x() - w / 2 + rnd.uniform(-4, 4), y, w, 1.6), 0.8, 0.8)

    def _marks(self, p, pal, px):
        """What moves on the shore: the time under the mouse, the star over the best part, the lantern."""
        if self.track is None:
            return
        g = self.g
        hy = g['hy']
        hover = self.hover_x
        if 0 <= hover:
            y = self.ridge_y(hover)
            line = QColor(pal.star)
            line.setAlphaF(0.55)
            p.setPen(QPen(line, 1.0, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(hover, y - 6), QPointF(hover, hy + 10))
            dur = self.engine.duration or self.track.duration
            p.setFont(theme.caps(8.5, 600, 1.4))
            p.setPen(pal.star)
            p.drawText(QRectF(hover - 40, y - 28, 80, 16), Qt.AlignmentFlag.AlignCenter,
                       mmss(hover / max(1, g['W']) * dur))
        if self.best is not None:
            bx = self.best * g['W']
            by = self.ridge_y(bx) - 16
            star = QPainterPath()
            for k in range(8):
                ang = k * math.pi / 4
                rr = 5.2 if k % 2 == 0 else 1.6
                pt = QPointF(bx + rr * math.sin(ang), by - rr * math.cos(ang))
                star.moveTo(pt) if k == 0 else star.lineTo(pt)
            star.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(pal.star if self.frac < self.best else pal.acc)
            p.drawPath(star)
        if px > 0:
            y = self.ridge_y(px)
            halo = QRadialGradient(QPointF(px, y), 12)
            c = QColor(pal.acc)
            c.setAlphaF(0.55)
            halo.setColorAt(0.0, c)
            halo.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QPointF(px, y), 12, 12)
            p.setBrush(QColor('#ffffff'))
            p.drawEllipse(QPointF(px, y), 2.6, 2.6)
            c.setAlphaF(0.35)                    # and its glint on the water
            p.setBrush(c)
            p.drawEllipse(QPointF(px, hy + (hy - y) * g['k']), 1.8, 1.8)

    def _words(self, p, pal):
        g = self.g
        pad = g['pad']
        p.setPen(pal.acc)
        p.setFont(theme.caps(8.6, 700, 2.6))
        p.drawText(QRectF(pad, g['text_y'], g['W'] - 2 * pad, 18), Qt.AlignmentFlag.AlignVCenter, self.eyebrow())
        p.setFont(g['title_font'])
        p.setPen(QColor(theme.TEXT))
        r = g['title_rect']
        p.drawText(QRectF(r), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   g['title'])
        sub = self.track.subtitle if self.track else 'Press play: Drift picks songs for you, then keeps them coming.'
        if self.track:
            p.setFont(theme.caps(11, 400, 3.0))
            p.setPen(theme.mix(theme.TEXT, pal.acc, 0.25))
            text = QFontMetrics(p.font()).elidedText(sub.upper(), Qt.TextElideMode.ElideRight, r.width())
        else:
            p.setFont(theme.serif(13, 400, italic=True))
            p.setPen(QColor(theme.MUTED))
            text = sub
        p.drawText(QRectF(pad, r.bottom() + 8, r.width(), 26), Qt.AlignmentFlag.AlignVCenter, text)

    def _then(self, p, pal):
        g = self.g
        y = g['H'] - g['then'] - 6
        p.setFont(theme.caps(8.5, 700, 2.4))
        dim = QColor(pal.star)
        dim.setAlphaF(0.5)
        p.setPen(dim)
        p.drawText(QRectF(g['pad'], y, 80, 30), Qt.AlignmentFlag.AlignVCenter, 'THEN')
        f1, f2 = theme.font(9.6, 600), theme.font(9.2)
        for r, t in self.then:
            p.drawPixmap(r.left(), r.top() + 4, covers().get(t, 22, 11))
            x = r.left() + 30
            p.setFont(f1)
            p.setPen(QColor(theme.TEXT))
            title = QFontMetrics(f1).elidedText(t.title, Qt.TextElideMode.ElideRight, 200)
            p.drawText(QRectF(x, r.top(), 210, 30), Qt.AlignmentFlag.AlignVCenter, title)
            x += QFontMetrics(f1).horizontalAdvance(title) + 6
            p.setFont(f2)
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(x, r.top(), r.right() - x + 2, 30), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(f2).elidedText(t.subtitle, Qt.TextElideMode.ElideRight, int(r.right() - x + 2)))

    def showEvent(self, e):
        self.refresh()
        self._lay_then()
        super().showEvent(e)
