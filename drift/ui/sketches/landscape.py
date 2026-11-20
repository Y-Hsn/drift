"""Stage 1, Sunset (Drift's first room): every song is a landscape. The Now page is scape.Scene; this file holds
the rest of the room: the ground of every page (the song's sky after dusk), the top strip, the player strip, and the
sketch's own colours (sky, horizon, hills, lake) and backdrop picture."""
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient

from .. import theme
from . import Knob, Stage


def grain(p, rect, amount):
    """Film grain over a rect: 'off', 'fine' or 'heavy' (twice, offset)."""
    if amount == 'off':
        return
    r = QRectF(rect)
    p.drawTiledPixmap(r, theme.grain(), QPointF(r.x() % 128, r.y() % 128))
    if amount == 'heavy':
        p.drawTiledPixmap(r, theme.grain(), QPointF((r.x() + 61) % 128, (r.y() + 37) % 128))


class SunsetStage(Stage):
    id = 'sunset'
    title = 'Sunset'
    blurb = 'Every song is a landscape.'
    KNOBS = (
        Knob('stars', 'Stars at night', 'toggle', True),
        Knob('water', 'Lake', 'choice', 'still', (('still', 'Still'), ('deep', 'Deep mirror'))),
        Knob('grain', 'Film grain', 'choice', 'fine', (('off', 'None'), ('fine', 'Fine'), ('heavy', 'Heavy'))),
        Knob('sky', 'Sky colour', 'color', '', hint='Empty: each cover colours the sky'),
        Knob('horizon', 'Horizon colour', 'color', '', hint='The glow where the sky meets the hills'),
        Knob('hills', 'Hills colour', 'color', '', hint='The near hills; the far ones fade into the sky'),
        Knob('lake', 'Lake colour', 'color', '', hint='The deep water'),
        Knob('backdrop', 'Sky picture', 'image', '', hint='Your art in the sky, behind the sun and the hills'),
    )

    def tune(self, pal):
        """The cover's palette, then the sketch's own colours over it (each one is optional)."""
        pal = super().tune(pal)
        sky, hor, hills, lake = (self.k.get(k) or '' for k in ('sky', 'horizon', 'hills', 'lake'))
        if not (sky or hor or hills or lake):
            return pal
        c = {n: QColor(getattr(pal, n)) for n in theme.Palette.NAMES}
        if sky:
            c['sky_top'] = QColor(sky)
            c['sky_mid'] = theme.mix(QColor(sky), c['glow'] if not hor else QColor(hor), 0.35)
        if hor:
            c['glow'] = QColor(hor)
            c['sky_mid'] = theme.mix(c['sky_top'], QColor(hor), 0.4)
        if hills:
            h = QColor(hills)
            c['near'] = h
            c['mid'] = theme.mix(h, c['sky_mid'], 0.3)
            c['far'] = theme.mix(h, c['glow'], 0.55)
            c['lit'] = theme.mix(h, c['acc'], 0.55)
        if lake:
            c['lake_bottom'] = QColor(lake)
            c['lake_top'] = theme.mix(QColor(lake), c['glow'], 0.3)
        return theme.Palette(night=pal.night, **c)

    def backdrop(self, w, h, ratio):
        """The sketch's sky picture filling w x h (cropped, never stretched), or None. Cached per size."""
        path = self.asset('backdrop')
        if path is None:
            return None
        key = (str(path), int(w), int(h), ratio)
        if getattr(self, '_back', (None,))[0] != key:
            self._back = (key, cover_fill(path, w, h, ratio))
        return self._back[1]

    def now_page(self, win):
        from ..scape import Scene
        return Scene(win)

    def reflect(self):
        return 0.85 if self.k.get('water') == 'deep' else 0.58

    def grain(self, p, rect):
        grain(p, rect, self.k.get('grain', 'fine'))
        self.paint_texture(p, rect)

    def paint_room(self, p, widget, rect):
        pal = theme.scene.pal
        h = widget.height()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, pal.sky_top)
        g.setColorAt(0.55, theme.mix(pal.sky_top, pal.lake_bottom, 0.5))
        g.setColorAt(1.0, pal.lake_bottom)
        p.fillRect(rect, g)
        glow = QRadialGradient(widget.width() * 0.86, h * 0.02, max(320.0, widget.width() * 0.55))
        c = QColor(pal.glow)
        c.setAlphaF(0.22)
        glow.setColorAt(0.0, c)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(rect, glow)
        back = self.backdrop(widget.width(), h, widget.devicePixelRatioF())
        if back is not None:                       # the picture, quiet, behind every page
            p.setOpacity(0.22)
            r = QRectF(rect)
            ratio = back.devicePixelRatio()
            p.drawPixmap(r, back, QRectF(r.x() * ratio, r.y() * ratio, r.width() * ratio, r.height() * ratio))
            p.setOpacity(1.0)
        self.grain(p, rect)

    def paint_top(self, p, widget, solid):
        if not solid:
            return
        pal = theme.scene.pal
        g = QLinearGradient(0, 0, 0, widget.height())
        c0, c1 = QColor(pal.sky_top), QColor(pal.sky_top)
        c1.setAlphaF(0.0)
        g.setColorAt(0.0, c0)
        g.setColorAt(0.7, c0)
        g.setColorAt(1.0, c1)
        p.fillRect(widget.rect(), g)

    def paint_dock(self, p, dock):
        pal = theme.scene.pal
        g = QLinearGradient(0, 0, 0, dock.height())
        c0, c1 = QColor(pal.lake_bottom), QColor(pal.lake_bottom)
        c0.setAlphaF(0.0)
        g.setColorAt(0.0, c0)
        g.setColorAt(0.42, c1)
        g.setColorAt(1.0, c1)
        p.fillRect(dock.rect(), g)

    @classmethod
    def thumbnail(cls, knobs, w, h, folder=None):
        """Sky, three ranges of hills, a sun, a lake: the room in miniature."""
        from ..scape import ranges, ridge
        from PyQt6.QtGui import QTransform
        ratio = 2.0
        px = QPixmap(int(w * ratio), int(h * ratio))
        px.setDevicePixelRatio(ratio)
        stage = cls(_Fake(knobs, folder))
        pal = stage.tune(_demo_palette())
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hy = h * 0.58
        sky = QLinearGradient(0, 0, 0, hy)
        sky.setColorAt(0, pal.sky_top)
        sky.setColorAt(0.62, pal.sky_mid)
        sky.setColorAt(1, pal.glow)
        p.fillRect(QRectF(0, 0, w, hy + 1), sky)
        back = stage.backdrop(w, hy + 1, ratio)
        if back is not None:
            p.setOpacity(SKY_PICTURE)
            p.drawPixmap(QRectF(0, 0, w, hy + 1), back, QRectF(0, 0, back.width(), back.height()))
            p.setOpacity(1.0)
        p.setPen(Qt.PenStyle.NoPen)
        if knobs.get('stars', True):
            r = random.Random(4)
            p.setPen(Qt.PenStyle.NoPen)
            for _ in range(26):
                c = QColor(pal.star)
                c.setAlphaF(0.2 + 0.5 * r.random())
                p.setBrush(c)
                p.drawEllipse(QPointF(r.random() * w, r.random() ** 1.6 * hy * 0.6), 0.6, 0.6)
        sun = QPointF(w * 0.62, hy * 0.42)
        halo = QRadialGradient(sun, h * 0.2)
        c = QColor(pal.acc)
        c.setAlphaF(0.45)
        halo.setColorAt(0.3, c)
        halo.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(halo)
        p.drawEllipse(sun, h * 0.2, h * 0.2)
        p.setBrush(theme.mix(pal.acc, QColor('#ffffff'), 0.35))
        p.drawEllipse(sun, h * 0.075, h * 0.075)
        wave = [int(255 * v) for v in ([0.3] * 30 + [0.8] * 40 + [0.5] * 30 + [1.0] * 40 + [0.3] * 20)]
        far, mid, near = ranges('thumb', wave)
        k = 0.85 if knobs.get('water') == 'deep' else 0.58
        lake = QLinearGradient(0, hy, 0, h)
        lake.setColorAt(0, pal.lake_top)
        lake.setColorAt(1, pal.lake_bottom)
        p.fillRect(QRectF(0, hy, w, h - hy), lake)
        for vals, amp, col in ((far, h * 0.2, pal.far), (mid, h * 0.14, pal.mid), (near, h * 0.09, pal.near)):
            fill, _e = ridge(vals, w, hy + 1, amp, hy + 2)
            p.setBrush(col)
            p.drawPath(fill)
            p.save()
            p.setClipRect(QRectF(0, hy, w, h - hy))
            p.setTransform(QTransform(1, 0, 0, -k, 0, (1 + k) * hy), True)
            c = QColor(col)
            c.setAlphaF(0.55)
            p.setBrush(c)
            p.drawPath(fill)
            p.restore()
        stage.grain(p, QRectF(0, 0, w, h))
        p.end()
        return px


SKY_PICTURE = 0.88       # how strongly the sketch's sky picture shows over the song's sky


def cover_fill(path, w, h, ratio):
    """A picture scaled to cover w x h and cropped to it (centred), at the screen's pixel ratio; None if unreadable."""
    from PyQt6.QtGui import QImage
    img = QImage(str(path))
    if img.isNull() or w < 1 or h < 1:
        return None
    W, H = max(1, int(w * ratio)), max(1, int(h * ratio))
    img = img.scaled(W, H, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    img = img.copy((img.width() - W) // 2, (img.height() - H) // 2, W, H)
    px = QPixmap.fromImage(img)
    px.setDevicePixelRatio(ratio)
    return px


class _Fake:
    """Just enough of a Sketch for a thumbnail."""

    def __init__(self, knobs, folder=None):
        self.knobs, self.folder = dict(knobs), folder

    def full(self):
        return dict(self.knobs)


def _demo_palette():
    """The made-up song every thumbnail shows (a warm cover), so sketches are compared alike."""
    from PyQt6.QtGui import QImage
    img = QImage(32, 32, QImage.Format.Format_RGB32)
    q = QPainter(img)
    g = QLinearGradient(0, 0, 32, 32)
    g.setColorAt(0, QColor('#ff7a3d'))
    g.setColorAt(1, QColor('#5a1e6e'))
    q.fillRect(img.rect(), g)
    q.end()
    return theme.palette_of(img)
