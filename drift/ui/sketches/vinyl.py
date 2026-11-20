"""Stage 2, Vinyl: a listening room in the museum. Every song is a record.

The Now page is a turntable seen from above, standing on its plinth under a gallery spotlight. The record's grooves
are the song itself: the core's 160 loudness bars are pressed as rings from the rim (the start) to the run-out (the
end), loud passages catch the light, quiet ones lie dark like the gaps between tracks. The cover is the label and
turns at 33 1/3 rpm. The tonearm is the playhead: it walks inward as the song plays, lifts when you pause (the
platter slows to a stop), and goes back to its rest when nothing plays. Touch the grooves to put the needle down
anywhere; the gold ring is the best part. Beside the deck hangs the song's wall label, written the way a museum
labels a work: artist, title, medium, duration, credit line, accession number.

Light on the machine: the room, the deck and the record (grooves and sheen: the light does not turn with the record,
so its reflection stands still, as on a real one) are drawn once into pictures. While a record turns, only the label
is drawn again, 30 times a second, and only while the window is visible; the arm is drawn again when it has moved."""
import ctypes
import hashlib
import math
import random
import sys
import time

from PyQt6.QtCore import QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import (QColor, QConicalGradient, QFont, QFontMetrics, QImage, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QRadialGradient, QRegion)
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from .. import theme
from ..covers import shared as covers
from ..lists import mmss
from ..lyricsfeed import shared as lyrics
from ..stage import Whisper
from ..widgets import best_part, pill
from . import Knob, Stage

TOP = 48
RATIO = 1.32            # the plinth: width / depth
DEG_PER_S = 200.0       # 33 1/3 rpm
LIGHT_DEG = 128.0       # the desk lamp stands up and to the left: every glint, shine and shadow agrees with it
SLEEVE_OUT = (0.2, 0.13)  # how far the sleeve sticks out from under the deck (left, top), in deck heights
SLEEVE_DEG = -7.0
FPS_MS = 40              # 25 pictures a second for the label (it turns slowly: smooth enough, half the work of 50)


# ---- small helpers ------------------------------------------------------------------------------------------------
def alpha(c, a):
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def px_font(f, px):
    f = QFont(f)
    f.setPixelSize(max(6, int(px)))
    return f


def ring(c, r_out, r_in):
    path = QPainterPath()
    path.addEllipse(c, r_out, r_out)
    if r_in > 0:
        inner = QPainterPath()
        inner.addEllipse(c, r_in, r_in)
        path = path.subtracted(inner)
    return path


def shape_of(wave, n=160):
    """The song's loudness as n values 0..1 stretched to its own quiet..loud (or a calm made-up one)."""
    if not wave:
        r = random.Random(9)
        return [0.25 + 0.15 * r.random() for _ in range(n)]
    lo, hi = min(wave), max(wave)
    vals = [(x - lo) / ((hi - lo) or 1) for x in wave]
    return [vals[min(len(vals) - 1, int(i * len(vals) / n))] for i in range(n)]


def screen_taken():
    """A full-screen game, film or presentation is on: nobody sees the record turn (the core asks Windows the same)."""
    if sys.platform != 'win32':
        return False
    try:
        state = ctypes.c_int(0)
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        return state.value in (2, 3, 4)       # busy (full screen), Direct3D full screen, presentation
    except (OSError, AttributeError):
        return False


def accession(uid):
    """A museum accession number for a song, stable forever: DR.<year it was first seen>.<4 digits>."""
    h = int(hashlib.md5((uid or 'drift').encode()).hexdigest()[:8], 16)
    return f'DR.{time.localtime().tm_year}.{h % 10000:04d}'


# ---- the materials ------------------------------------------------------------------------------------------------
class Wax:
    """A record's material: its base (a colour, or for 'sunburst' a radial pour), how much the grooves catch the
    light (lo..hi alpha of white over the base) and how strong the sheen is."""

    def __init__(self, kind, pal):
        self.kind = kind
        self.pour = None
        acc = QColor(pal.acc)
        h = acc.hslHueF() if acc.hslHueF() >= 0 else 0.08
        if kind == 'smoke':
            self.base, self.lo, self.hi, self.sheen = QColor(30, 30, 36, 200), 0.03, 0.17, 0.22
        elif kind == 'clear':
            self.base, self.lo, self.hi, self.sheen = QColor(228, 232, 240, 34), 0.02, 0.15, 0.34
        elif kind == 'oxblood':
            self.base, self.lo, self.hi, self.sheen = QColor(72, 9, 20, 236), 0.02, 0.16, 0.2
        elif kind == 'cover':
            self.base, self.lo, self.hi, self.sheen = QColor.fromHslF(h, 0.62, 0.27, 0.9), 0.02, 0.17, 0.22
        elif kind == 'sunburst':
            self.base, self.lo, self.hi, self.sheen = QColor(12, 10, 10, 245), 0.02, 0.14, 0.2
            self.pour = (QColor.fromHslF(h, 0.75, 0.55, 0.95), QColor.fromHslF((h + 0.04) % 1, 0.7, 0.3, 0.95),
                         QColor(14, 11, 12, 245))
        else:
            self.base, self.lo, self.hi, self.sheen = QColor(11, 11, 13), 0.015, 0.13, 0.17
        self.clear = kind in ('clear', 'smoke')


def wedges(c, light_deg, spread, falloff=2.4):
    """Two opposite wedges of light (a conical gradient, alpha 1 at the core): where a lamp glints on a round thing."""
    g = QConicalGradient(c, light_deg)
    stops = [(0.0, 1.0), (spread * 0.35, 0.55), (spread, 0.14), (spread * falloff, 0.0)]
    pts = {}
    for base in (0.0, 0.5, 1.0):
        for d, a in stops:
            for s in (-1, 1):
                pos = base + s * d
                if 0.0 <= pos <= 1.0:
                    pts[round(pos, 5)] = max(a, pts.get(round(pos, 5), 0.0))
    for pos in sorted(pts):
        g.setColorAt(pos, QColor(255, 255, 255, int(255 * pts[pos])))
    return g


def draw_record(p, c, R, wave, wax, light_deg=None):
    """The record (everything but its label). Seen from any angle it is nearly black: the song's passages show only as
    a faint change in the cut. Where the lamp glints, the light breaks into the grooves: loud passages are cut wide
    and scatter it into a fine bright texture, the quiet gaps between them are smooth and shine as clean lines, the
    way a real record looks under a lamp."""
    light_deg = LIGHT_DEG if light_deg is None else light_deg
    r_out, r_in, rl = R * 0.955, R * 0.42, R * 0.335
    disc = ring(c, R, 0)
    p.save()
    p.setClipPath(disc)
    p.setPen(Qt.PenStyle.NoPen)
    if wax.pour:
        g = QRadialGradient(c, R)
        a, b, d = wax.pour
        g.setColorAt(0.0, a)
        g.setColorAt(0.42, a)
        g.setColorAt(0.62, b)
        g.setColorAt(1.0, d)
        p.setBrush(g)
    else:
        p.setBrush(wax.base)
    p.drawEllipse(c, R, R)
    vals = shape_of(wave)
    n = len(vals)
    rnd = random.Random(sum(wave[:64]) if wave else 7)

    def v_at(r):
        return vals[max(0, min(n - 1, int((r_out - r) / (r_out - r_in) * n)))]

    # diffuse: the cut barely shows, a whisper of the song's passages
    step = (r_out - r_in) / n
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i, v in enumerate(vals):
        r = r_out - (i + 0.5) * step
        if v > 0.08:
            a = wax.lo * 0.7 + (wax.hi - wax.lo) * 0.2 * (v ** 1.3)
            p.setPen(QPen(QColor(255, 255, 255, int(255 * a)), step + 0.6))
        else:                                                        # a gap: smooth, deeper black
            p.setPen(QPen(QColor(0, 0, 0, 40), step + 0.6))
        p.drawEllipse(c, r, r)
    k, r = 0, r_out
    while r > r_in:
        p.setPen(QPen(QColor(0, 0, 0, 40) if k % 2 else QColor(255, 255, 255, 7), 0.55))
        p.drawEllipse(c, r, r)
        r -= 1.1
        k += 1
    # the glint: a layer of light in the grooves, kept only where the lamp's wedges fall
    dev = p.device()
    ratio = dev.devicePixelRatioF() if dev is not None else 1.0
    side = 2 * R + 2
    layer = QImage(int(side * ratio) + 1, int(side * ratio) + 1, QImage.Format.Format_ARGB32_Premultiplied)
    layer.setDevicePixelRatio(ratio)
    layer.fill(Qt.GlobalColor.transparent)
    q = QPainter(layer)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    c2 = QPointF(side / 2, side / 2)
    warm = QColor(255, 247, 236)
    q.setBrush(Qt.BrushStyle.NoBrush)
    q.setPen(QPen(alpha(warm, 0.5), max(1.0, R - r_out - 1)))        # the smooth lead-in rim
    q.drawEllipse(c2, (R + r_out) / 2, (R + r_out) / 2)
    r, k = r_out, 0
    while r > r_in:
        v = v_at(r)
        if v <= 0.08:
            a, w = 1.0, 1.6                                            # a clean, mirror-bright gap
        else:
            a = (0.34 + 0.6 * v ** 0.9) * rnd.uniform(0.6, 1.0) * (0.6 if k % 2 else 1.0)
            w = 0.6
        q.setPen(QPen(alpha(warm, a), w))
        q.drawEllipse(c2, r, r)
        r -= 1.05
        k += 1
    q.setPen(QPen(alpha(warm, 0.6), max(1.0, r_in - rl * 1.06)))     # the run-out: smooth, one broad shine
    q.drawEllipse(c2, (r_in + rl * 1.06) / 2, (r_in + rl * 1.06) / 2)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    q.fillRect(QRectF(0, 0, side, side), wedges(c2, light_deg, 0.075))
    q.end()
    p.setOpacity(min(1.0, wax.sheen * 5.5))
    p.drawImage(QRectF(c.x() - side / 2, c.y() - side / 2, side, side), layer)
    p.setOpacity(1.0)
    vinyl = ring(c, R, rl)                                             # a broad soft bloom round the glint
    bloom = wedges(c, light_deg, 0.16, 1.8)
    p.setOpacity(min(1.0, wax.sheen * 0.6))
    p.fillPath(vinyl, bloom)
    p.setOpacity(1.0)
    p.setPen(QPen(QColor(0, 0, 0, 90), 1.0))                           # the lock groove
    p.drawEllipse(c, rl * 1.12, rl * 1.12)
    p.restore()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 40 if wax.clear else 26), 1.0))
    p.drawEllipse(c, R - 0.6, R - 0.6)
    p.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
    p.drawEllipse(c, R + 0.4, R + 0.4)


def arc_text(p, text, c, radius, font, color, bottom=False):
    """Text along a circle, centred on its top (or, upright, along its bottom)."""
    fm = QFontMetrics(font)
    advs = [fm.horizontalAdvance(ch) for ch in text]
    total = sum(advs) / radius
    if total > math.pi * 1.7:
        return
    p.setFont(font)
    p.setPen(color)
    ang = (-math.pi / 2 - total / 2) if not bottom else (math.pi / 2 + total / 2)
    for ch, adv in zip(text, advs):
        half = adv / 2 / radius
        a = ang + (half if not bottom else -half)
        p.save()
        p.translate(c.x() + radius * math.cos(a), c.y() + radius * math.sin(a))
        p.rotate(math.degrees(a) + (90 if not bottom else -90))
        p.drawText(QPointF(-adv / 2, fm.ascent() * 0.35 if bottom else fm.ascent() * 0.35), ch)
        p.restore()
        ang += (2 * half) if not bottom else -(2 * half)


def label_pixmap(track, rl, style, pal, img, ratio):
    """A record's label, square (2rl px), ready to be turned. 'cover': the cover art with the title printed round its
    edge; 'printed': a typeset label in the song's colour, like a pressing plant would print it."""
    side = int(2 * rl * ratio) + 2
    px = QPixmap(side, side)
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    c = QPointF(rl, rl)
    clip = QPainterPath()
    clip.addEllipse(c, rl, rl)
    p.setClipPath(clip)
    title = (track.title if track else 'Drift') or ''
    artist = (track.subtitle if track else 'Side A') or ''
    cat = 'DR-' + accession(track.uid if track else '')[-4:]
    caps = theme.caps(9, 600, 1.2, condensed=True)
    if style == 'printed':
        acc = QColor(pal.acc)
        h = acc.hslHueF() if acc.hslHueF() >= 0 else 0.08
        base = QColor.fromHslF(h, 0.5, 0.52)
        ink = theme.ink_on(base)
        p.fillRect(QRectF(0, 0, 2 * rl, 2 * rl), base)
        p.setPen(QPen(alpha(ink, 0.55), max(0.8, rl * 0.012)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, rl * 0.93, rl * 0.93)
        arc_text(p, 'DRIFT  RECORDS', c, rl * 0.78, px_font(caps, rl * 0.095), alpha(ink, 0.85))
        arc_text(p, f'STEREO  ·  {cat}  ·  ℗ DRIFT', c, rl * 0.8, px_font(caps, rl * 0.07), alpha(ink, 0.7), bottom=True)
        p.setFont(px_font(theme.serif(10, 500, italic=True), rl * 0.15))
        p.setPen(alpha(ink, 0.9))
        p.drawText(QRectF(0, rl * 0.36, 2 * rl, rl * 0.3), Qt.AlignmentFlag.AlignCenter, 'drift')
        f = px_font(caps, rl * 0.075)
        p.setFont(f)
        p.setPen(alpha(ink, 0.75))
        p.drawText(QRectF(rl * 0.2, rl * 0.9, rl * 0.5, rl * 0.2), Qt.AlignmentFlag.AlignCenter, 'SIDE A')
        p.drawText(QRectF(rl * 1.3, rl * 0.9, rl * 0.5, rl * 0.2), Qt.AlignmentFlag.AlignCenter, '33⅓')
        size = rl * 0.15
        tf = px_font(theme.serif(10, 600), size)
        while size > rl * 0.08 and QFontMetrics(tf).boundingRect(QRect(0, 0, int(rl * 1.3), 1000),
                                                                 Qt.TextFlag.TextWordWrap, title).height() > rl * 0.42:
            size -= 1
            tf = px_font(theme.serif(10, 600), size)
        p.setFont(tf)
        p.setPen(ink)
        p.drawText(QRectF(rl * 0.35, rl * 1.14, rl * 1.3, rl * 0.42),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, title)
        p.setFont(px_font(caps, rl * 0.07))
        p.setPen(alpha(ink, 0.75))
        p.drawText(QRectF(rl * 0.3, rl * 1.56, rl * 1.4, rl * 0.14), Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(p.font()).elidedText(artist.upper(), Qt.TextElideMode.ElideRight, int(rl * 1.4)))
    else:
        if img is not None and not img.isNull():
            s = min(img.width(), img.height())
            sq = img.copy((img.width() - s) // 2, (img.height() - s) // 2, s, s)
            p.drawImage(QRectF(0, 0, 2 * rl, 2 * rl), sq)
        else:
            base = theme.tint(track.artist or track.title if track else 'drift')
            p.fillRect(QRectF(0, 0, 2 * rl, 2 * rl), base.darker(220))
            p.setBrush(base.darker(130))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(rl * 1.3, rl * 0.7), rl * 0.8, rl * 0.8)
        band = ring(c, rl, rl * 0.79)
        p.fillPath(band, QColor(8, 8, 10, 150))
        ink = QColor(244, 238, 228, 225)
        text = f'{title.upper()}   ✦   {artist.upper()}'
        f = px_font(caps, rl * 0.085)
        while QFontMetrics(f).horizontalAdvance(text) / (rl * 0.85) > math.pi * 1.25 and len(text) > 8:
            text = text[:-2]
        arc_text(p, text.strip(), c, rl * 0.86, f, ink)
        arc_text(p, f'SIDE A  ·  33⅓ RPM  ·  {cat}', c, rl * 0.87, px_font(caps, rl * 0.068), alpha(ink, 0.7),
                 bottom=True)
    shade = QRadialGradient(c, rl)
    shade.setColorAt(0.75, QColor(0, 0, 0, 0))
    shade.setColorAt(1.0, QColor(0, 0, 0, 70))
    p.fillRect(QRectF(0, 0, 2 * rl, 2 * rl), shade)
    p.drawTiledPixmap(QRectF(0, 0, 2 * rl, 2 * rl), theme.grain())
    p.end()
    return px


# ---- the deck -----------------------------------------------------------------------------------------------------
FINISH = {'walnut': ('#3d2618', '#5a3a24', True), 'ash': ('#17171a', '#222226', True),
          'lacquer': ('#e7e3db', '#f4f1ea', False)}


def engraved(finish):
    return QColor(20, 18, 16, 150) if finish == 'lacquer' else QColor(255, 244, 228, 120)


class Deck:
    """Where every part of the turntable sits, for a plinth rect."""

    def __init__(self, rect, out=None):
        self.rect = r = QRectF(rect)
        out = out or SLEEVE_OUT
        h = self.h = r.height()
        self.c = QPointF(r.left() + h * 0.5, r.center().y())
        self.rp = h * 0.43                 # platter
        self.R = self.rp * 0.965           # record
        self.rl = self.R * 0.335           # label
        self.r_out, self.r_in = self.R * 0.955, self.R * 0.42
        self.pivot = QPointF(r.right() - h * 0.17, r.top() + h * 0.2)
        d = math.hypot(self.pivot.x() - self.c.x(), self.pivot.y() - self.c.y())
        self.d = d
        self.L = d * 0.975
        self.rest = 95.0
        self.button = QRectF(r.left() + h * 0.035, r.bottom() - h * 0.105, h * 0.15, h * 0.066)
        self.led = QPointF(r.left() + h * 0.06, r.bottom() - h * 0.135)
        self.slot = QRectF(r.right() - h * 0.085, r.top() + h * 0.5, h * 0.02, h * 0.36)
        self.plate = QRectF(self.pivot.x() - h * 0.11, self.pivot.y() - h * 0.12, 0, h * 0.27)
        self.plate.setRight(r.right() - h * 0.03)
        # the record's sleeve lies on the desk, half under the deck: it sticks out up and to the left
        self.S = h * 0.86
        self.sleeve = QPointF(r.left() + self.S * 0.5 - h * out[0], r.top() + self.S * 0.5 - h * out[1])

    def radius_at(self, frac):
        return self.r_out - (self.r_out - self.r_in) * max(0.0, min(1.0, frac))

    def frac_at(self, r):
        return (self.r_out - r) / (self.r_out - self.r_in)

    def phi(self, r):
        """The arm's angle (degrees, screen) that puts the needle on the groove at radius r."""
        L, d = self.L, self.d
        cos_t = max(-1.0, min(1.0, (L * L + d * d - r * r) / (2 * L * d)))
        to_c = math.degrees(math.atan2(self.c.y() - self.pivot.y(), self.c.x() - self.pivot.x()))
        return to_c - math.degrees(math.acos(cos_t))

    def needle(self, phi):
        a = math.radians(phi)
        return QPointF(self.pivot.x() + self.L * math.cos(a), self.pivot.y() + self.L * math.sin(a))

    def volume_y(self, v):
        s = self.slot
        return s.bottom() - s.height() * max(0.0, min(1.0, v))

    def knob(self, v):
        h = self.h
        return QRectF(self.slot.center().x() - h * 0.03, self.volume_y(v) - h * 0.017, h * 0.06, h * 0.034)

    def arm_rect(self, phi, lift=1.0):
        s, piv = self.needle(phi), self.pivot
        h = self.h
        xs = [s.x(), piv.x()]
        ys = [s.y(), piv.y()]
        box = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        return box.adjusted(-h * 0.13, -h * 0.13, h * 0.13 + h * 0.05, h * 0.13 + h * 0.07).toAlignedRect()


def draw_plinth(p, deck, finish):
    """The deck: a slab of wood with a soft bevel, lit by the lamp from the upper left, its long shadow falling the
    other way. A dark anodised plate carries the arm; the controls are machined metal, the lettering engraved."""
    r, h = deck.rect, deck.h
    base, light, grained = FINISH.get(finish, FINISH['walnut'])
    lac = finish == 'lacquer'
    rad = h * 0.035
    body = QPainterPath()
    body.addRoundedRect(r, rad, rad)
    p.setPen(Qt.PenStyle.NoPen)
    for k, a in ((h * 0.16, 10), (h * 0.09, 18), (h * 0.045, 30), (h * 0.016, 60)):    # its shadow on the desk
        p.setBrush(QColor(0, 0, 0, a))
        p.drawRoundedRect(r.translated(k * 0.45, k * 0.7).adjusted(-k * 0.35, -k * 0.35, k * 0.35, k * 0.35),
                          rad + k * 0.6, rad + k * 0.6)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0.0, QColor(light))
    g.setColorAt(1.0, QColor(base))
    p.fillPath(body, g)
    p.save()
    p.setClipPath(body)
    if grained:
        rnd = random.Random(finish)
        y = r.top()
        while y < r.bottom():
            path = QPainterPath(QPointF(r.left() - 4, y))
            amp, f, ph = rnd.uniform(0.4, 2.2), rnd.uniform(0.003, 0.01), rnd.uniform(0, 6.28)
            x = 0.0
            while x <= r.width() + 24:
                path.lineTo(r.left() + x, y + amp * math.sin(f * x + ph) + 1.4 * math.sin(0.0021 * x + y * 0.05))
                x += 14
            dark = rnd.random() < 0.72
            c = QColor(0, 0, 0, rnd.randint(12, 40)) if dark else QColor(255, 232, 200, rnd.randint(5, 13))
            if finish == 'ash':
                c.setAlpha(int(c.alpha() * 0.55))
            p.setPen(QPen(c, rnd.uniform(0.4, 1.3)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            y += rnd.uniform(1.2, 3.4)
    pool = QRadialGradient(QPointF(r.left() + h * 0.15, r.top() - h * 0.25), h * 1.6)     # the lamp on the top
    pool.setColorAt(0.0, QColor(255, 236, 208, 54 if not lac else 30))
    pool.setColorAt(0.5, QColor(255, 236, 208, 10))
    pool.setColorAt(1.0, QColor(0, 0, 0, 80 if not lac else 40))
    p.fillRect(r, pool)
    p.drawTiledPixmap(r, theme.grain())
    p.restore()
    # the bevel: the edge facing the lamp catches it, the far edge falls into shade
    p.setBrush(Qt.BrushStyle.NoBrush)
    edge = QLinearGradient(r.topLeft(), r.bottomRight())
    edge.setColorAt(0.0, QColor(255, 246, 230, 150 if not lac else 255))
    edge.setColorAt(0.45, QColor(255, 246, 230, 22))
    edge.setColorAt(1.0, QColor(0, 0, 0, 150))
    p.setPen(QPen(edge, max(1.0, h * 0.004)))
    p.drawRoundedRect(r.adjusted(0.7, 0.7, -0.7, -0.7), rad, rad)
    inset = h * 0.012
    p.setPen(QPen(QColor(0, 0, 0, 46), 1.0))
    p.drawRoundedRect(r.adjusted(inset, inset, -inset, -inset), rad * 0.7, rad * 0.7)
    # the arm's plate: anodised, finely brushed
    pl = deck.plate
    pr = h * 0.022
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 70))
    p.drawRoundedRect(pl.translated(h * 0.004, h * 0.006), pr, pr)
    pg = QLinearGradient(pl.topLeft(), pl.bottomRight())
    for pos, col in ((0.0, '#3b3b41' if not lac else '#d9dadf'), (0.5, '#2a2a2f' if not lac else '#c3c5cb'),
                     (1.0, '#1c1c20' if not lac else '#aeb0b7')):
        pg.setColorAt(pos, QColor(col))
    plate = QPainterPath()
    plate.addRoundedRect(pl, pr, pr)
    p.fillPath(plate, pg)
    p.save()
    p.setClipPath(plate)
    rnd = random.Random(3)
    y = pl.top()
    while y < pl.bottom():
        p.setPen(QPen(QColor(255, 255, 255, rnd.randint(3, 13)), 0.5))
        p.drawLine(QPointF(pl.left(), y), QPointF(pl.right(), y))
        y += rnd.uniform(0.7, 1.8)
    p.restore()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 46), 0.8))
    p.drawRoundedRect(pl.adjusted(0.5, 0.5, -0.5, -0.5), pr, pr)
    ink = engraved(finish)
    # the wordmark, engraved
    p.setPen(ink)
    p.setFont(px_font(theme.serif(10, 500, italic=True), h * 0.05))
    p.drawText(QRectF(r.left() + h * 0.035, r.top() + h * 0.02, h * 0.3, h * 0.07), Qt.AlignmentFlag.AlignVCenter, 'drift')
    p.setPen(alpha(ink, 0.6))
    p.setFont(px_font(theme.caps(8, 600, 1.6, condensed=True), h * 0.016))
    p.drawText(QRectF(r.left() + h * 0.037, r.top() + h * 0.083, h * 0.4, h * 0.025), Qt.AlignmentFlag.AlignVCenter,
               'DIRECT DRIVE · STEREO')
    caps = theme.caps(8, 600, 0.8, condensed=True)
    b = deck.button                                                            # START · STOP: a machined key
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawRoundedRect(b.translated(h * 0.003, h * 0.005), h * 0.012, h * 0.012)
    kg = QLinearGradient(b.topLeft(), b.bottomLeft())
    kg.setColorAt(0.0, QColor('#d9dbe0'))
    kg.setColorAt(0.5, QColor('#a9abb2'))
    kg.setColorAt(1.0, QColor('#7d7f86'))
    p.setBrush(kg)
    p.setPen(QPen(QColor(0, 0, 0, 120), 0.8))
    p.drawRoundedRect(b, h * 0.012, h * 0.012)
    p.setPen(QPen(QColor(255, 255, 255, 150), 0.8))
    p.drawLine(QPointF(b.left() + h * 0.012, b.top() + 1), QPointF(b.right() - h * 0.012, b.top() + 1))
    p.setFont(px_font(caps, h * 0.021))
    p.setPen(QColor(30, 30, 34, 210))
    p.drawText(b, Qt.AlignmentFlag.AlignCenter, 'START · STOP')
    p.setPen(ink)
    p.drawText(QRectF(deck.led.x() + h * 0.018, deck.led.y() - h * 0.02, h * 0.12, h * 0.04),
               Qt.AlignmentFlag.AlignVCenter, '33⅓')
    s = deck.slot                                                              # the volume fader's slot
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 28 if not lac else 120))
    p.drawRoundedRect(s.translated(0.6, 1.0), s.width() / 2, s.width() / 2)
    p.setBrush(QColor(0, 0, 0, 170))
    p.drawRoundedRect(s, s.width() / 2, s.width() / 2)
    p.setPen(ink)
    p.setFont(px_font(caps, h * 0.019))
    p.drawText(QRectF(s.center().x() - h * 0.06, s.top() - h * 0.05, h * 0.12, h * 0.035), Qt.AlignmentFlag.AlignCenter,
               'VOLUME')
    for k in range(6):
        y = s.top() + s.height() * k / 5
        p.setPen(QPen(alpha(ink, 0.5), 1.0))
        p.drawLine(QPointF(s.right() + h * 0.008, y), QPointF(s.right() + h * (0.02 if k % 5 else 0.026), y))


def draw_platter(p, deck, empty_logo=True):
    c, rp, h = deck.c, deck.rp, deck.h
    p.setPen(Qt.PenStyle.NoPen)
    for k, a in ((h * 0.03, 40), (h * 0.012, 70)):
        p.setBrush(QColor(0, 0, 0, a))
        p.drawEllipse(QPointF(c.x() + k * 0.3, c.y() + k * 0.8), rp + k * 0.5, rp + k * 0.5)
    metal = QConicalGradient(c, LIGHT_DEG)
    for pos, col in ((0.0, '#e9ebf0'), (0.12, '#8a8c93'), (0.25, '#c9ccd2'), (0.38, '#6f7178'), (0.5, '#e4e6eb'),
                     (0.62, '#85878e'), (0.75, '#c6c9cf'), (0.88, '#6d6f76'), (1.0, '#e9ebf0')):
        metal.setColorAt(pos, QColor(col))
    p.setBrush(metal)
    p.drawEllipse(c, rp, rp)
    p.setBrush(QColor(255, 255, 255, 120))                    # strobe dots round the rim
    n = 144
    for k in range(n):
        a = 2 * math.pi * k / n
        p.drawEllipse(QPointF(c.x() + rp * 0.982 * math.cos(a), c.y() + rp * 0.982 * math.sin(a)),
                      rp * 0.006, rp * 0.006)
    p.setBrush(QColor('#141416'))                             # the felt mat
    p.drawEllipse(c, rp * 0.955, rp * 0.955)
    rnd = random.Random(5)
    for _ in range(900):
        a, rr = rnd.random() * 6.283, rp * 0.95 * math.sqrt(rnd.random())
        p.setBrush(QColor(255, 255, 255, rnd.randint(3, 10)) if rnd.random() < 0.5 else QColor(0, 0, 0, 30))
        p.drawEllipse(QPointF(c.x() + rr * math.cos(a), c.y() + rr * math.sin(a)), 0.8, 0.8)
    if empty_logo:
        p.setPen(QColor(255, 255, 255, 26))
        p.setFont(px_font(theme.serif(10, 500, italic=True), rp * 0.2))
        p.drawText(QRectF(c.x() - rp, c.y() - rp * 0.62, 2 * rp, rp * 0.3), Qt.AlignmentFlag.AlignCenter, 'drift')
        p.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rr in (rp * 0.72, rp * 0.74):
            p.drawEllipse(c, rr, rr)
    # the arm's rest post and cue lever (they never move)
    rest = deck.needle(deck.rest)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawEllipse(QPointF(rest.x() + h * 0.008, rest.y() + h * 0.01), h * 0.02, h * 0.02)
    p.setBrush(QColor('#2a2a2e'))
    p.drawEllipse(rest, h * 0.018, h * 0.018)
    piv = deck.pivot
    lever = QRectF(piv.x() + h * 0.075, piv.y() + h * 0.05, h * 0.022, h * 0.075)
    p.setBrush(QColor('#26262a'))
    p.drawRoundedRect(lever, h * 0.006, h * 0.006)
    p.setBrush(QColor('#bfc1c7'))
    p.drawRoundedRect(QRectF(lever.left() - h * 0.012, lever.top(), h * 0.046, h * 0.014), h * 0.004, h * 0.004)


_spindles = {}


def draw_spindle(p, deck):
    """The spindle (a small cached picture: it is drawn on every turn of the label)."""
    c, R = deck.c, deck.R
    r = R * 0.02
    key = (round(r, 1), p.device().devicePixelRatioF() if p.device() else 1.0)
    px = _spindles.get(key)
    if px is None:
        ratio = key[1]
        side = 2 * r + 4
        px = QPixmap(int(side * ratio) + 1, int(side * ratio) + 1)
        px.setDevicePixelRatio(ratio)
        px.fill(Qt.GlobalColor.transparent)
        q = QPainter(px)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        _spindle(q, QPointF(side / 2, side / 2), R)
        q.end()
        _spindles.clear() if len(_spindles) > 8 else None
        _spindles[key] = px
    side = px.width() / px.devicePixelRatio()
    p.drawPixmap(QPointF(c.x() - side / 2, c.y() - side / 2), px)


def _spindle(p, c, R):
    g = QRadialGradient(QPointF(c.x() - R * 0.008, c.y() - R * 0.008), R * 0.03)
    g.setColorAt(0.0, QColor('#ffffff'))
    g.setColorAt(1.0, QColor('#7d7f86'))
    p.setPen(QPen(QColor(0, 0, 0, 120), 0.8))
    p.setBrush(g)
    p.drawEllipse(c, R * 0.02, R * 0.02)


def draw_arm(p, deck, phi, lift, accent):
    """The tonearm: a gimbal on its plate, a ribbed counterweight, an S-shaped polished tube, a headshell with a finger
    lift and the cartridge in the song's colour. Its shadow falls away from the lamp and drifts further off as it
    lifts."""
    h, piv = deck.h, deck.pivot
    s = deck.needle(phi)
    ux, uy = (s.x() - piv.x()) / deck.L, (s.y() - piv.y()) / deck.L
    bend = math.radians(-14)
    kx = piv.x() + deck.L * 0.86 * (ux * math.cos(bend) - uy * math.sin(bend))
    ky = piv.y() + deck.L * 0.86 * (ux * math.sin(bend) + uy * math.cos(bend))
    k = QPointF(kx, ky)
    tx, ty = (kx - piv.x()), (ky - piv.y())
    tl = math.hypot(tx, ty) or 1
    tx, ty = tx / tl, ty / tl
    nx0, ny0 = -ty, tx
    sw = h * 0.034                                               # the S: out one way near the pivot, back near the head
    tube = QPainterPath(piv)
    tube.cubicTo(QPointF(piv.x() + tx * tl * 0.38 + nx0 * sw, piv.y() + ty * tl * 0.38 + ny0 * sw),
                 QPointF(kx - tx * tl * 0.36 - nx0 * sw, ky - ty * tl * 0.36 - ny0 * sw), k)
    cw = QPointF(piv.x() - tx * h * 0.1, piv.y() - ty * h * 0.1)
    tube_w = h * 0.015
    hx, hy = s.x() - kx, s.y() - ky
    hl = math.hypot(hx, hy) or 1
    hx, hy = hx / hl, hy / hl
    nx, ny = -hy, hx
    shell_w = h * 0.04
    tip = QPointF(s.x() + hx * h * 0.014, s.y() + hy * h * 0.014)
    shell = QPainterPath(QPointF(kx + nx * shell_w * 0.3, ky + ny * shell_w * 0.3))
    shell.lineTo(QPointF(tip.x() + nx * shell_w * 0.5, tip.y() + ny * shell_w * 0.5))
    shell.quadTo(QPointF(tip.x() + hx * h * 0.006, tip.y() + hy * h * 0.006),
                 QPointF(tip.x() - nx * shell_w * 0.5, tip.y() - ny * shell_w * 0.5))
    shell.lineTo(QPointF(kx - nx * shell_w * 0.3, ky - ny * shell_w * 0.3))
    shell.closeSubpath()
    cl = h * 0.045
    cart = QPainterPath()
    for i, (a, b) in enumerate(((0.36, 0.0), (0.36, cl), (-0.36, cl), (-0.36, 0.0))):
        pt = QPointF(s.x() + nx * shell_w * a + hx * (h * 0.01 - cl + b), s.y() + ny * shell_w * a + hy * (h * 0.01 - cl + b))
        cart.lineTo(pt) if i else cart.moveTo(pt)
    cart.closeSubpath()
    flat, rcap = Qt.PenCapStyle.FlatCap, Qt.PenCapStyle.RoundCap
    along = QPointF(tx, ty)
    # shadow
    off = QPointF(h * (0.014 + 0.03 * lift), h * (0.022 + 0.045 * lift))
    for spread, a in ((2.4, 0.1), (1.5, 0.18), (1.0, 0.3)):
        a *= 1.0 - 0.4 * lift
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, int(255 * a)), tube_w * spread, Qt.PenStyle.SolidLine, rcap))
        p.drawPath(tube.translated(off))
        p.setPen(QPen(QColor(0, 0, 0, int(255 * a)), h * 0.05 * min(1.6, spread * 0.8), Qt.PenStyle.SolidLine, flat))
        p.drawLine(cw - along * h * 0.035 + off * 0.6, cw + along * h * 0.035 + off * 0.6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, int(255 * a)))
        p.drawPath(shell.translated(off))
    # the gimbal: machined rings on the plate
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawEllipse(QPointF(piv.x() + h * 0.008, piv.y() + h * 0.012), h * 0.066, h * 0.066)
    ring_g = QConicalGradient(piv, LIGHT_DEG)
    for pos, col in ((0.0, '#c9cbd1'), (0.25, '#55575d'), (0.5, '#b7b9bf'), (0.75, '#45474c'), (1.0, '#c9cbd1')):
        ring_g.setColorAt(pos, QColor(col))
    p.setBrush(ring_g)
    p.drawEllipse(piv, h * 0.062, h * 0.062)
    base = QRadialGradient(QPointF(piv.x() - h * 0.02, piv.y() - h * 0.02), h * 0.07)
    base.setColorAt(0.0, QColor('#3a3a40'))
    base.setColorAt(1.0, QColor('#121214'))
    p.setBrush(base)
    p.drawEllipse(piv, h * 0.052, h * 0.052)
    p.setPen(QPen(QColor(255, 255, 255, 36), 0.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for rr in (0.04, 0.03):
        p.drawEllipse(piv, h * rr, h * rr)
    # the counterweight: a ribbed cylinder on the stub behind the pivot
    p.setPen(QPen(QColor('#7b7d84'), tube_w * 0.8, Qt.PenStyle.SolidLine, flat))
    p.drawLine(piv, cw - along * h * 0.04)
    p.save()
    p.translate(cw)
    p.rotate(math.degrees(math.atan2(ty, tx)))
    body = QRectF(-h * 0.034, -h * 0.031, h * 0.068, h * 0.062)
    cg = QLinearGradient(0, body.top(), 0, body.bottom())
    for pos, col in ((0.0, '#5b5d63'), (0.22, '#e4e6eb'), (0.45, '#9c9ea5'), (0.8, '#2a2b2f'), (1.0, '#45474c')):
        cg.setColorAt(pos, QColor(col))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(cg)
    p.drawRoundedRect(body, h * 0.006, h * 0.006)
    p.setPen(QPen(QColor(0, 0, 0, 70), 0.7))
    x = body.left() + h * 0.008
    while x < body.right() - h * 0.016:
        p.drawLine(QPointF(x, body.top() + 1), QPointF(x, body.bottom() - 1))
        x += h * 0.0075
    p.setPen(QPen(alpha(accent, 0.9), h * 0.004))                # a thin ring in the song's colour
    p.drawLine(QPointF(body.right() - h * 0.01, body.top() + 1), QPointF(body.right() - h * 0.01, body.bottom() - 1))
    p.restore()
    # the tube: a polished cylinder; the lamp's line runs along its upper-left side
    lift_px = QPointF(-h * 0.004 * lift, -h * 0.006 * lift)
    t = tube.translated(lift_px)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for col, w, dx in (('#4a4c52', 1.0, 0.0), ('#a3a5ac', 0.72, -0.08), ('#dfe1e6', 0.38, -0.17)):
        p.setPen(QPen(QColor(col), tube_w * w, Qt.PenStyle.SolidLine, rcap))
        p.drawPath(t.translated(dx * tube_w, dx * tube_w))
    p.setPen(QPen(QColor(255, 255, 255, 220), tube_w * 0.12, Qt.PenStyle.SolidLine, rcap))
    p.drawPath(t.translated(-0.24 * tube_w, -0.24 * tube_w))
    collar = piv + along * h * 0.07 + lift_px
    p.setPen(QPen(QColor('#2a2b30'), tube_w * 1.5, Qt.PenStyle.SolidLine, flat))
    p.drawLine(collar - along * h * 0.008, collar + along * h * 0.008)
    # headshell, cartridge, stylus, finger lift
    n = QPointF(nx, ny)
    shg = QLinearGradient(k + n * shell_w * 0.5, k - n * shell_w * 0.5)
    shg.setColorAt(0.0, QColor('#3a3b41'))
    shg.setColorAt(1.0, QColor('#141417'))
    p.setPen(QPen(QColor(0, 0, 0, 160), 0.8))
    p.setBrush(shg)
    p.drawPath(shell.translated(lift_px))
    cg2 = QLinearGradient(s + n * shell_w * 0.4, s - n * shell_w * 0.4)
    cg2.setColorAt(0.0, QColor(accent).lighter(135))
    cg2.setColorAt(1.0, QColor(accent).darker(150))
    p.setBrush(cg2)
    p.setPen(QPen(QColor(0, 0, 0, 120), 0.6))
    p.drawPath(cart.translated(lift_px))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 200))
    p.drawEllipse(s + lift_px, h * 0.0035, h * 0.0035)
    fl = QPointF(kx + hx * hl * 0.35 + nx * shell_w * 0.3, ky + hy * hl * 0.35 + ny * shell_w * 0.3) + lift_px
    p.setPen(QPen(QColor('#c4c6cc'), h * 0.006, Qt.PenStyle.SolidLine, rcap))
    p.drawLine(fl, fl + QPointF(nx * h * 0.042 + hx * h * 0.008, ny * h * 0.042 + hy * h * 0.008))
    cap = QRadialGradient(QPointF(piv.x() - h * 0.008, piv.y() - h * 0.008), h * 0.03)
    cap.setColorAt(0.0, QColor('#f4f5f8'))
    cap.setColorAt(1.0, QColor('#6c6e75'))
    p.setPen(QPen(QColor(0, 0, 0, 110), 0.8))
    p.setBrush(cap)
    p.drawEllipse(piv + lift_px, h * 0.024, h * 0.024)


# ---- the sleeve ---------------------------------------------------------------------------------------------------
def draw_sleeve(p, c, S, deg, track, img, finish='walnut'):
    """The record's jacket lying on the desk, half under the deck: the cover art printed on card, a little worn (the
    ring the record pressed into it, soft corners), the lamp's light across it, the paper inner sleeve slipping out."""
    p.save()
    p.translate(c)
    p.rotate(deg)
    sq = QRectF(-S / 2, -S / 2, S, S)
    rad = S * 0.008
    p.setPen(Qt.PenStyle.NoPen)
    for k, a in ((S * 0.06, 12), (S * 0.028, 24), (S * 0.01, 46)):           # its shadow, away from the lamp
        p.setBrush(QColor(0, 0, 0, a))
        p.drawRoundedRect(sq.translated(k * 0.45, k * 0.7).adjusted(-k * 0.3, -k * 0.3, k * 0.3, k * 0.3), rad + k, rad + k)
    # the paper inner sleeve slips out of the top, its hole showing the record's dark edge
    p.save()
    p.translate(S * 0.03, -S * 0.07)
    p.rotate(4.0)
    inner = sq.adjusted(S * 0.03, 0, -S * 0.03, -S * 0.1)
    ig = QLinearGradient(inner.topLeft(), inner.bottomRight())
    ig.setColorAt(0.0, QColor('#efe8da'))
    ig.setColorAt(1.0, QColor('#cfc6b4'))
    p.setBrush(QColor(0, 0, 0, 40))
    p.drawRect(inner.translated(S * 0.004, S * 0.006))
    p.setBrush(ig)
    p.drawRect(inner)
    p.setBrush(QColor(20, 19, 18, 235))
    p.drawEllipse(QPointF(inner.center().x(), inner.top() + S * 0.12), S * 0.09, S * 0.09)
    p.restore()
    # the jacket
    jacket = QPainterPath()
    jacket.addRoundedRect(sq, rad, rad)
    p.save()
    p.setClipPath(jacket)
    if img is not None and not img.isNull():
        s = min(img.width(), img.height())
        p.drawImage(sq, img.copy((img.width() - s) // 2, (img.height() - s) // 2, s, s))
    else:
        base = theme.tint((track.artist or track.title) if track else 'drift')
        g = QLinearGradient(sq.topLeft(), sq.bottomRight())
        g.setColorAt(0.0, base.darker(140))
        g.setColorAt(1.0, base.darker(320))
        p.fillRect(sq, g)
        p.setPen(alpha(theme.ink_on(base.darker(200)), 0.85))
        p.setFont(px_font(theme.serif(10, 500, italic=True), S * 0.09))
        p.drawText(sq.adjusted(S * 0.08, S * 0.08, -S * 0.08, -S * 0.08),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap,
                   (track.title if track else '') or '')
        p.setPen(Qt.PenStyle.NoPen)
    # ring wear: the record inside has pressed its circle into the card over the years
    p.setBrush(Qt.BrushStyle.NoBrush)
    rnd = random.Random(track.uid if track else 'drift')
    for k in range(5):
        start = rnd.uniform(0, 360)
        p.setPen(QPen(QColor(255, 255, 255, rnd.randint(14, 30)), S * rnd.uniform(0.004, 0.009)))
        rr = S * (0.455 + rnd.uniform(-0.004, 0.004))
        p.drawArc(QRectF(-rr, -rr, 2 * rr, 2 * rr), int(start * 16), int(rnd.uniform(40, 110) * 16))
    # the lamp across the card, the far corner in shade, paper grain
    lg = QLinearGradient(sq.topLeft(), sq.bottomRight())
    lg.setColorAt(0.0, QColor(255, 240, 215, 46))
    lg.setColorAt(0.45, QColor(255, 240, 215, 0))
    lg.setColorAt(1.0, QColor(0, 0, 0, 110))
    p.fillRect(sq, lg)
    p.drawTiledPixmap(sq, theme.grain())
    p.drawTiledPixmap(sq, theme.grain(), QPointF(37, 61))
    p.restore()
    # worn edges: the card's corners and rims rubbed lighter
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 248, 236, 58), max(1.0, S * 0.004)))
    p.drawRoundedRect(sq.adjusted(0.6, 0.6, -0.6, -0.6), rad, rad)
    for corner in (sq.topLeft(), sq.topRight(), sq.bottomLeft()):
        cg = QRadialGradient(corner, S * 0.05)
        cg.setColorAt(0.0, QColor(255, 248, 236, 60))
        cg.setColorAt(1.0, QColor(255, 248, 236, 0))
        p.save()
        p.setClipPath(jacket)
        p.fillRect(QRectF(corner.x() - S * 0.05, corner.y() - S * 0.05, S * 0.1, S * 0.1), cg)
        p.restore()
    p.restore()


# ---- the room -----------------------------------------------------------------------------------------------------
LIGHTS = {'gallery': (QColor(255, 214, 160), 0.2, '#0d0a08', 0.62),
          'daylight': (QColor(230, 238, 248), 0.16, '#27282b', 0.55),
          'night': (QColor(255, 220, 180), 0.12, '#050506', 0.85)}


def wall_color(pal, light):
    _c, _a, base, k = LIGHTS.get(light, LIGHTS['gallery'])
    if light == 'daylight':
        return theme.mix(pal.sky_mid, QColor(base), k)
    return theme.mix(pal.sky_top, QColor(base), k)


def draw_room(p, rect, size, pal, light, spot, radius, wall_px=None, look=None):
    """The wall: its colour (the song's, deep and quiet), your picture on it if the sketch hangs one, the spotlight's
    pool of light, the corners falling into dark, grain."""
    w, h = size.width(), size.height()
    p.fillRect(rect, wall_color(pal, light))
    if wall_px is not None:
        p.setOpacity(0.32 if light != 'daylight' else 0.45)
        p.drawPixmap(rect, wall_px, QRectF(rect.x() * wall_px.devicePixelRatio(), rect.y() * wall_px.devicePixelRatio(),
                                           rect.width() * wall_px.devicePixelRatio(),
                                           rect.height() * wall_px.devicePixelRatio()))
        p.setOpacity(1.0)
    col, a, _b, _k = LIGHTS.get(light, LIGHTS['gallery'])
    g = QRadialGradient(spot, radius)
    g.setColorAt(0.0, alpha(theme.mix(col, pal.acc, 0.18), a))
    g.setColorAt(0.45, alpha(theme.mix(col, pal.acc, 0.25), a * 0.42))
    g.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.fillRect(rect, g)
    v = QRadialGradient(QPointF(w / 2, h * 0.42), max(w, h) * 0.85)
    v.setColorAt(0.55, QColor(0, 0, 0, 0))
    v.setColorAt(1.0, QColor(0, 0, 0, 150 if light != 'daylight' else 90))
    p.fillRect(rect, v)
    r = QRectF(rect)
    p.drawTiledPixmap(r, theme.grain(), QPointF(r.x() % 128, r.y() % 128))
    if look is not None:
        look.paint_texture(p, r)


def scaled_wall(path, size, ratio):
    from PyQt6.QtGui import QImage
    img = QImage(str(path))
    if img.isNull():
        return None
    w, h = int(size.width() * ratio), int(size.height() * ratio)
    img = img.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    img = img.copy((img.width() - w) // 2, (img.height() - h) // 2, w, h)
    px = QPixmap.fromImage(img)
    px.setDevicePixelRatio(ratio)
    return px


# ---- a small record (player strip, crate, the flight) -------------------------------------------------------------
_minis = {}


def mini_record(track, size, kind='black', pal=None, ratio=2.0, label=True):
    """A record seen from above at `size`, its label the song's cover."""
    pal = pal or theme.scene.pal
    key = (track.uid if track else None, size, kind, label, QColor(pal.acc).name() if kind in ('cover', 'sunburst') else '')
    if key in _minis:
        return _minis[key]
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c, R = QPointF(size / 2, size / 2), size / 2 - 0.5
    wax = Wax(kind, pal)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(wax.base if not wax.pour else wax.pour[1])
    p.drawEllipse(c, R, R)
    p.setBrush(Qt.BrushStyle.NoBrush)
    r = R * 0.95
    k = 0
    while r > R * 0.4:
        p.setPen(QPen(QColor(255, 255, 255, 16 if k % 3 else 34), 0.6))
        p.drawEllipse(c, r, r)
        r -= max(1.2, R * 0.05)
        k += 1
    g = QConicalGradient(c, 112)
    for pos, a in ((0, 0.22), (0.06, 0), (0.44, 0), (0.5, 0.22), (0.56, 0), (0.94, 0), (1, 0.22)):
        g.setColorAt(pos, QColor(255, 255, 255, int(255 * a)))
    p.fillPath(ring(c, R, R * 0.36), g)
    if label:
        rl = R * 0.36
        if track is not None:
            lab = covers().get(track, max(4, int(2 * rl)), max(2, int(rl)))
            p.drawPixmap(QRectF(c.x() - rl, c.y() - rl, 2 * rl, 2 * rl), lab, QRectF(lab.rect()))
        else:
            p.setBrush(pal.acc)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(c, rl, rl)
    p.setBrush(QColor('#cfd1d6'))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(c, max(1.0, R * 0.03), max(1.0, R * 0.03))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 70), 1.0))          # the rim catches the light
    p.drawArc(QRectF(c.x() - R + 0.5, c.y() - R + 0.5, 2 * R - 1, 2 * R - 1), 20 * 16, 140 * 16)
    p.end()
    if track is not None and track.uid in covers().images:      # keep only finished pictures
        if len(_minis) > 300:
            _minis.clear()
        _minis[key] = px
    return px


# ---- the Now page -------------------------------------------------------------------------------------------------
class Turntable(QWidget):
    EMPTY = 'The deck is empty. Put a record on: Drift picks one you will like, then keeps them coming.'

    def __init__(self, win, stage):
        super().__init__(None)
        self.win, self.engine, self.stage = win, win.engine, stage
        self.k = stage.k
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.track = None
        self.uid = None
        self.calm = False
        self.frac = 0.0
        self.wave = []
        self.best = None
        self.deck = None
        self.g = {}
        self.angle = 0.0
        self.speed = 0.0
        self.target_speed = 0.0
        self.phi = 95.0
        self.lift = 1.0
        self.hover_r = -1.0
        self.hover_pos = QPointF()
        self.drag = False
        self.fader = False
        self.button_hover = False
        self.next_items = []
        self.stage_px = self.disc_px = self.label_px = None
        self.back = None             # what lies under the label (see _make_back)
        self.ring_r = -1.0           # where the needle's ring was last drawn
        self.old = None              # (disc, label, angle) of the record being taken off
        self.swap_t = 1.0
        self.last = time.monotonic()
        self.whisper = Whisper(self, centred=False, scale=1.0, serif=True)
        self.whisper.seek.connect(self.engine.seek)
        self.greet = QWidget(self)
        row = QHBoxLayout(self.greet)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.greet_row = row
        go = pill('  Put a record on')
        go.setIcon(theme.icon('radio', theme.ink_on(theme.accent.color), 18))
        go.setToolTip('Drift picks songs you will like and keeps them coming')
        go.clicked.connect(lambda: self.engine.start_radio())
        self.shuffle_saved = pill('Shuffle the crate', ghost=True)
        self.shuffle_saved.setToolTip('Every song saved on this PC, shuffled')
        self.shuffle_saved.clicked.connect(lambda: self.engine.play_list(self.win.store.local_tracks(), shuffle=True))
        row.addWidget(go)
        row.addWidget(self.shuffle_saved)
        row.addStretch()
        self.spin = QTimer(self, interval=FPS_MS, timeout=self._tick)
        self.spin.setTimerType(Qt.TimerType.PreciseTimer)
        self.arm_anim = QVariantAnimation(self, duration=420, easingCurve=QEasingCurve(QEasingCurve.Type.InOutCubic))
        self.arm_anim.valueChanged.connect(self._arm_step)
        self.lift_anim = QVariantAnimation(self, duration=260, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self.lift_anim.valueChanged.connect(self._lift_step)
        self.swap = QVariantAnimation(self, duration=1250, easingCurve=QEasingCurve(QEasingCurve.Type.Linear))
        self.swap.setStartValue(0.0)
        self.swap.setEndValue(1.0)
        self.swap.valueChanged.connect(self._swap_step)
        self.swap.finished.connect(self._swapped)
        e = self.engine
        e.track_changed.connect(self.show_track)
        e.queue_changed.connect(self._queue)
        e.position_changed.connect(self._position)
        e.wave_ready.connect(self._wave)
        e.playing_changed.connect(self._playing)
        theme.scene.changed.connect(self._palette)
        covers().ready.connect(self._cover_ready)
        lyrics().ready.connect(self._lyrics)

    # ---- geometry -------------------------------------------------------------------------------------------------
    def resizeEvent(self, e):
        self._layout()
        super().resizeEvent(e)

    def _layout(self):
        W, H = max(1, self.width()), max(1, self.height())
        pad = max(24, int(W * 0.05))
        self.g = g = {'W': W, 'H': H, 'pad': pad}
        dw = min((H - TOP - 64) * RATIO, W * 0.6)
        wide = W >= H * 1.05 and W - dw - pad * 2.1 >= 400      # the wall label needs room beside the deck
        g['wide'] = wide
        if self.calm:
            dh = min(H - TOP - 150, (W - 2 * pad) / RATIO)
            rect = QRectF((W - dh * RATIO) / 2, TOP + max(10, (H - TOP - 120 - dh) / 2), dh * RATIO, dh)
            info = QRectF(pad, rect.bottom() + 30, W - 2 * pad, H - rect.bottom() - 40)
        elif wide:                                   # room up and to the left for the sleeve on the desk
            dw = min((H - TOP - 40) / 1.25 * RATIO, W * 0.5)
            dh = dw / RATIO
            rect = QRectF(pad + dh * 0.2, TOP + (H - TOP - dh) / 2 + dh * 0.11, dw, dh)
            out = SLEEVE_OUT
            left = rect.right() + pad * 1.1
            info = QRectF(left, TOP + 24, W - left - pad, H - TOP - 40)
        else:
            room = W - 2 * pad
            dw = min(room / (1 + 0.24 / RATIO), H * 0.42 * RATIO)
            dh = dw / RATIO
            side = dh * 0.24
            rect = QRectF(pad + side * 0.6 + (room - dw - side * 0.6) / 2, TOP + dh * 0.12, dw, dh)
            out = (0.2, 0.02)       # in a narrow window the sleeve sticks out to the left, not into the tabs
            info = QRectF(pad, rect.bottom() + 30, W - 2 * pad, H - rect.bottom() - 40)
        self.deck = Deck(rect, None if self.calm else out)
        g['info'] = info
        self.stage_px = self.disc_px = self.label_px = self.back = None
        self.phi = self._phi_target()
        self._text()
        self.update()

    def dock_rect(self):
        g = self.g
        if not g or 'dock' not in g:
            return QRect(0, max(0, self.height() - 60), self.width(), 60)
        return g['dock'].toAlignedRect()

    def _blocks(self):
        """The wall label, block by block: (key, height, keep-priority)."""
        g = self.g
        t = self.track
        info = g['info']
        width = int(info.width())
        out = [('eyebrow', 26, 5)]
        if t is not None:
            out.append(('artist', 26, 9))
        title = t.title if t else self.greeting()
        big = 46 if g['wide'] else 38
        size = big
        while size > 18:
            f = theme.serif(size, 500, italic=True)
            fm = QFontMetrics(f)
            br = fm.boundingRect(QRect(0, 0, width, 10000), Qt.TextFlag.TextWordWrap, title)
            if br.height() <= fm.lineSpacing() * 2 + 2:
                break
            size -= 2
        f = theme.serif(size, 500, italic=True)
        fm = QFontMetrics(f)
        if fm.boundingRect(QRect(0, 0, width, 10000), Qt.TextFlag.TextWordWrap, title).height() > fm.lineSpacing() * 2 + 2:
            title = fm.elidedText(title, Qt.TextElideMode.ElideRight, width * 2 - 40)
        br = fm.boundingRect(QRect(0, 0, width, 10000), Qt.TextFlag.TextWordWrap, title)
        g['title'], g['title_font'] = title, f
        out.append(('title', br.height() + 8, 10))
        if t is None:
            sub = QFontMetrics(theme.serif(12.5, 400, italic=True)).boundingRect(
                QRect(0, 0, width, 1000), Qt.TextFlag.TextWordWrap, self.EMPTY).height()
            out.append(('sub', sub + 10, 9))
            stack = width < 400
            self.greet_row.setDirection(QHBoxLayout.Direction.TopToBottom if stack else QHBoxLayout.Direction.LeftToRight)
            out.append(('greet', 104 if stack else 58, 10))
            return out
        if t.album and t.album != t.title:
            out.append(('album', 24, 3))
        out += [('medium', 21, 4), ('dims', 21, 4), ('credit', 21, 2), ('acc', 26, 1)]
        if self.win.store.get('whisper', True):
            out.append(('whisper', self.whisper.height() + 10, 6))
        out.append(('dock', 76, 10))
        if self.engine.queue:
            out.append(('next', 92, 2))
        return out

    def _text(self):
        g = self.g
        if not g or self.deck is None:
            return
        info = g['info']
        blocks = self._blocks()
        if self.calm:
            blocks = [b for b in blocks if b[0] == 'whisper']
        avail = info.height()
        while sum(b[1] for b in blocks) > avail and len(blocks) > 1:
            low = min(blocks, key=lambda b: b[2])
            if low[2] >= 10:
                break
            blocks.remove(low)
        total = sum(b[1] for b in blocks)
        y = info.top() + (max(0.0, (avail - total) / 2) if g['wide'] and not self.calm else 0)
        g['blocks'] = {}
        for key, hgt, _p in blocks:
            g['blocks'][key] = QRectF(info.left(), y, info.width(), hgt)
            y += hgt
        b = g['blocks']
        wr = b.get('whisper')
        self.whisper.setVisible(wr is not None and self.track is not None)
        if wr is not None:
            if self.calm:
                self.whisper.setGeometry(int(info.left()), int(info.top()), int(info.width()), self.whisper.height())
                self.whisper.align = Qt.AlignmentFlag.AlignHCenter
            else:
                self.whisper.align = Qt.AlignmentFlag.AlignLeft
                self.whisper.setGeometry(int(wr.left()), int(wr.top() + 4), int(wr.width()), self.whisper.height())
        if 'dock' in b:
            d = b['dock']
            g['dock'] = QRectF(d.left() - 12, d.top() + 6, min(d.width() + 24, 760), 60)
        else:
            g.pop('dock', None)
        gr = b.get('greet')
        self.greet.setVisible(gr is not None and self.track is None and not self.calm)
        if gr is not None:
            self.greet.setGeometry(int(gr.left()), int(gr.top() + 10), int(min(gr.width(), 420)), int(gr.height() - 14))
        self.shuffle_saved.setVisible(bool(self.win.store.local_tracks()))
        self._lay_next()
        g['text_rect'] = QRectF(info).adjusted(-4, -4, 4, 4).toAlignedRect()

    def _lay_next(self):
        self.next_items = []
        r = self.g.get('blocks', {}).get('next')
        if r is None or self.track is None:
            return
        x, y = r.left(), r.top() + 30
        f1, f2 = theme.font(9.6, 600), theme.font(9)
        for t in self.engine.queue[:8]:
            tw = min(150, max(QFontMetrics(f1).horizontalAdvance(t.title), QFontMetrics(f2).horizontalAdvance(t.subtitle)))
            w = 52 + 10 + tw
            if x + w > r.right():
                break
            self.next_items.append((QRectF(x, y, w, 52), t))
            x += w + 22

    def greeting(self):
        h = time.localtime().tm_hour
        return 'Good morning' if 5 <= h < 12 else 'Good afternoon' if 12 <= h < 18 else 'Good evening'

    # ---- the song -------------------------------------------------------------------------------------------------
    def refresh(self):
        self.show_track(self.engine.current)

    def set_calm(self, on):
        if on != self.calm:
            self.calm = on
            self._layout()

    def let_go(self):
        self.stage_px = self.disc_px = self.label_px = self.back = None
        self.old = None

    def show_track(self, t):
        new = (t.uid if t else None) != self.uid
        if new and self.isVisible() and self.deck is not None and self.uid is not None and t is not None:
            self.old = (self._disc(), self._label(), self.angle)
            self.swap.stop()
            self.swap_t = 0.0
            self.swap.start()
        self.track = t
        if new:
            self.uid = t.uid if t else None
            self.stage_px = None                      # the new record's sleeve lies on the desk
            uid, wave = self.engine.wave
            self._set_wave(wave if t and uid == t.uid else [])
            self.frac = (self.engine.position / self.engine.duration) if t and self.engine.duration else 0.0
            self.label_px = None
            self.whisper.set_lines([])
            if t is not None and self.win.store.get('whisper', True):
                feed = lyrics()
                lines = feed.lines(t) if self.engine.online or feed.known(t) else None
                self.whisper.set_lines(lines or [])
                self.whisper.position(self.engine.position)
            if self.swap.state() != QVariantAnimation.State.Running:
                self._aim_arm(animate=self.isVisible())
        self._playing(self.engine.playing, quiet=True)
        self._text()
        self.update()

    def _set_wave(self, wave):
        self.wave = list(wave or [])
        self.best = best_part(self.wave) if self.wave else None
        self.disc_px = None

    def _wave(self, uid, wave):
        if self.track is not None and uid == self.track.uid:
            self._set_wave(wave)
            self.update(self._disc_rect())

    def _palette(self):
        if theme.scene.anim.state() != QVariantAnimation.State.Running:
            self.stage_px = None
            if self.k.get('record') in ('cover', 'sunburst'):
                self.disc_px = None
            if self.k.get('label') == 'printed':
                self.label_px = None
        if self.isVisible():
            self.update()

    def _cover_ready(self, uid):
        if self.track is not None and uid == self.track.uid:
            self.label_px = None
            self.stage_px = None                      # the sleeve too
            if self.isVisible() and self.deck:
                self.update()

    def _lyrics(self, uid):
        if uid == self.uid and self.win.store.get('whisper', True):
            self.whisper.set_lines(lyrics().cache.get(uid) or [])
            self.whisper.position(self.engine.position)

    def _queue(self):
        if self.isVisible() and self.g:
            self._text()
            self.win._place() if self.win.page == 'home' else None
            self.update(self.g['text_rect'])

    def _position(self, ms, dur):
        if self.track is None or self.drag:
            return
        self.whisper.position(ms)
        self.frac = ms / dur if dur else 0.0
        if self.swap.state() == QVariantAnimation.State.Running or not self.isVisible():
            return
        self._aim_arm(animate=True)
        if self.deck is not None:
            r = self.deck.radius_at(self.frac)
            if abs(r - self.ring_r) >= 0.6:            # the needle's ring and the played grooves moved a pixel
                self.ring_r = r
                self.update(self._disc_rect())

    def _playing(self, on, quiet=False):
        self.target_speed = 1.0 if on and self.track is not None and self.k.get('spin', True) else 0.0
        want = 0.0 if on and self.track is not None else 1.0
        if quiet or not self.isVisible():
            self.lift_anim.stop()
            self.lift = want
        elif want != self.lift:
            self.lift_anim.stop()
            self.lift_anim.setStartValue(self.lift)
            self.lift_anim.setEndValue(want)
            self.lift_anim.start()
        self._wake()
        if self.deck and self.isVisible():
            d = self.deck
            self.update(QRectF(d.led.x() - 12, d.led.y() - 12, 24, 24).toAlignedRect())

    def _wake(self):
        if (self.speed > 0.001 or self.target_speed > 0) and self.isVisible():
            if not self.spin.isActive():
                self.last = time.monotonic()
                self.spin.start()

    def _tick(self):
        now = time.monotonic()
        dt = min(0.2, now - self.last)
        self.last = now
        if not self.isVisible() or self.window().isMinimized():
            self.spin.stop()
            return
        self.frames = getattr(self, 'frames', 0) + 1
        if self.frames % 50 == 0 or self.spin.interval() != FPS_MS:   # every 2 s: is a full-screen game on?
            hidden = screen_taken()
            self.spin.setInterval(2000 if hidden else FPS_MS)
            if hidden:
                return
        tau = 0.45 if self.target_speed > self.speed else 1.1     # spins up quickly, slows down like a heavy platter
        self.speed += (self.target_speed - self.speed) * (1 - math.exp(-dt / tau))
        if self.target_speed == 0 and self.speed < 0.003:
            self.speed = 0.0
            self.spin.stop()
        self.angle = (self.angle + DEG_PER_S * self.speed * dt) % 360
        if self.deck and self.swap.state() != QVariantAnimation.State.Running:
            if self.back is None and self.track is not None:
                self._make_back()
            self.update(self._label_rect())

    # ---- the arm --------------------------------------------------------------------------------------------------
    def _phi_target(self):
        if self.deck is None:
            return 95.0
        if self.track is None:
            return self.deck.rest
        return self.deck.phi(self.deck.radius_at(self.frac))

    def _aim_arm(self, animate):
        target = self._phi_target()
        if abs(target - self.phi) < 0.01:
            return
        if animate and abs(target - self.phi) > 1.2 and self.isVisible():
            self.arm_anim.stop()
            self.arm_anim.setStartValue(self.phi)
            self.arm_anim.setEndValue(target)
            self.arm_anim.start()
        elif self.arm_anim.state() != QVariantAnimation.State.Running:
            self._set_phi(target)

    def _set_phi(self, phi):
        if self.deck is None:
            self.phi = phi
            return
        old = self.deck.arm_rect(self.phi)
        moved = abs(phi - self.phi) * math.pi / 180 * self.deck.L
        self.phi = phi
        if moved >= 0.4:
            self.update(QRegion(old).united(QRegion(self.deck.arm_rect(phi))))

    def _arm_step(self, v):
        self._set_phi(float(v))

    def _lift_step(self, v):
        self.lift = float(v)
        if self.deck:
            self.update(self.deck.arm_rect(self.phi))

    def _swap_step(self, v):
        """Taking one record off and putting the next on: the arm goes to its rest, the old record slides away, the
        new one slides in and the needle drops on its lead-in."""
        t = self.swap_t = float(v)
        d = self.deck
        if d is None:
            return
        lead = d.phi(d.r_out)
        ease = QEasingCurve(QEasingCurve.Type.InOutCubic)
        if t < 0.28:
            self.phi = self.phi + (d.rest - self.phi) * ease.valueForProgress(t / 0.28)
            self.lift = max(self.lift, min(1.0, t / 0.12))
        elif t > 0.8:
            self.phi = d.rest + (lead - d.rest) * ease.valueForProgress((t - 0.8) / 0.2)
        else:
            self.phi = d.rest
        self.update()

    def _swapped(self):
        self.old = None
        self.swap_t = 1.0
        self._aim_arm(animate=True)
        self._playing(self.engine.playing)
        self.update()

    # ---- pictures -------------------------------------------------------------------------------------------------
    def _disc_rect(self):
        d = self.deck
        return QRectF(d.c.x() - d.rp - 4, d.c.y() - d.rp - 4, 2 * d.rp + 8, 2 * d.rp + 8).toAlignedRect()

    def _label_rect(self):
        d = self.deck
        return QRectF(d.c.x() - d.rl - 3, d.c.y() - d.rl - 3, 2 * d.rl + 6, 2 * d.rl + 6).toAlignedRect()

    def _wall(self):
        return self.stage.wall(self)

    def _paint_stage(self, p, rect):
        d = self.deck
        light = 'night' if self.calm else self.k.get('light', 'gallery')
        spot = QPointF(d.rect.left() + d.h * 0.25, d.rect.top() - d.h * 0.3)       # the desk lamp, up and left
        draw_room(p, rect, self.size(), theme.scene.pal, light, spot, d.rect.width() * 1.15, self._wall(), self.stage)
        if self.track is not None and not self.calm:
            draw_sleeve(p, d.sleeve, d.S, SLEEVE_DEG, self.track, covers().image(self.track))
        draw_plinth(p, d, self.k.get('deck', 'walnut'))
        draw_platter(p, d, empty_logo=True)

    def _stage(self):
        if self.stage_px is None and theme.scene.anim.state() != QVariantAnimation.State.Running:
            ratio = self.devicePixelRatioF()
            px = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
            px.setDevicePixelRatio(ratio)
            q = QPainter(px)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint_stage(q, QRectF(0, 0, self.width(), self.height()))
            q.end()
            self.stage_px = px
        return self.stage_px

    def _disc(self):
        if self.disc_px is None and self.deck is not None:
            d = self.deck
            ratio = self.devicePixelRatioF()
            side = 2 * d.R + 4
            px = QPixmap(int(side * ratio), int(side * ratio))
            px.setDevicePixelRatio(ratio)
            px.fill(Qt.GlobalColor.transparent)
            q = QPainter(px)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw_record(q, QPointF(side / 2, side / 2), d.R, self.wave, Wax(self.k.get('record', 'black'), theme.scene.pal))
            q.end()
            self.disc_px = px
        return self.disc_px

    def _label(self):
        if self.label_px is None and self.deck is not None and self.track is not None:
            img = covers().image(self.track)
            self.label_px = label_pixmap(self.track, self.deck.rl, self.k.get('label', 'cover'), theme.scene.pal, img,
                                         self.devicePixelRatioF())
        return self.label_px

    # ---- paint ----------------------------------------------------------------------------------------------------
    def paintEvent(self, e):
        if self.deck is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        lr = self._label_rect()
        swapping = self.swap.state() == QVariantAnimation.State.Running
        if self.back is not None and lr.contains(e.rect()) and not swapping and self.track is not None:
            p.drawPixmap(lr.topLeft(), self.back)            # a turning record: only its label is drawn again
            self._record(p, self.deck, 0.0, None, self._label(), self.angle, QRectF(e.rect()))
            draw_spindle(p, self.deck)
            return
        if e.rect().intersects(lr):
            self.back = None                                  # something under the label changed
        self._scene(p, QRectF(e.rect()))

    def _make_back(self):
        """What lies under the label (the record, its marks, maybe the arm), kept so a turn costs one small copy."""
        lr = self._label_rect()
        ratio = self.devicePixelRatioF()
        px = QPixmap(int(lr.width() * ratio), int(lr.height() * ratio))
        px.setDevicePixelRatio(ratio)
        q = QPainter(px)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        q.translate(-lr.x(), -lr.y())
        self._scene(q, QRectF(lr), label=False)
        q.end()
        self.back = px

    def _scene(self, p, clip, label=True):
        d = self.deck
        stage = self._stage()
        if stage is not None:
            p.drawPixmap(clip, stage, QRectF(clip.x() * stage.devicePixelRatio(), clip.y() * stage.devicePixelRatio(),
                                             clip.width() * stage.devicePixelRatio(),
                                             clip.height() * stage.devicePixelRatio()))
        else:
            self._paint_stage(p, clip)
        if self.swap.state() == QVariantAnimation.State.Running and self.old is not None:
            t = self.swap_t
            far = d.c.x() + d.R + 40
            ease = QEasingCurve(QEasingCurve.Type.InOutCubic)
            if t < 0.5:
                dx = -far * ease.valueForProgress(min(1.0, max(0.0, (t - 0.12) / 0.38)))
                disc, lab, ang = self.old
                self._record(p, d, dx, disc, lab, ang, clip)
            if t > 0.42:
                dx = -far * (1 - ease.valueForProgress(min(1.0, (t - 0.42) / 0.38)))
                self._record(p, d, dx, self._disc(), self._label(), self.angle, clip)
        elif self.track is not None:
            self._record(p, d, 0.0, self._disc(), self._label() if label else None, self.angle, clip)
            if clip.intersects(QRectF(self._disc_rect())):
                self._marks(p, d)
        if label:
            draw_spindle(p, d)
        self._controls(p, d, clip)
        if clip.intersects(QRectF(d.arm_rect(self.phi, self.lift))):
            draw_arm(p, d, self.phi, self.lift, theme.scene.pal.acc)
        if self.hover_r >= 0 and self.track is not None:
            self._hover_time(p)
        if not self.calm and clip.intersects(QRectF(self.g['text_rect'])):
            self._words(p)

    def _record(self, p, d, dx, disc, label, angle, clip):
        c = QPointF(d.c.x() + dx, d.c.y())
        if disc is not None:
            side = disc.width() / disc.devicePixelRatio()
            r = QRectF(c.x() - side / 2, c.y() - side / 2, side, side)
            if r.intersects(clip):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 60))
                p.drawEllipse(QPointF(c.x() + d.h * 0.006, c.y() + d.h * 0.01), d.R, d.R)
                p.drawPixmap(r.topLeft(), disc)
        if label is not None:
            lr = QRectF(c.x() - d.rl, c.y() - d.rl, 2 * d.rl, 2 * d.rl)
            if lr.adjusted(-d.rl * 0.5, -d.rl * 0.5, d.rl * 0.5, d.rl * 0.5).intersects(clip):
                p.save()
                p.translate(c)
                p.rotate(angle)
                p.drawPixmap(QPointF(-d.rl, -d.rl), label)
                p.restore()

    def _marks(self, p, d):
        """The played grooves faintly lit, the needle's ring, the best part's gold ring, the ring under the mouse."""
        pal = theme.scene.pal
        r_now = d.radius_at(self.frac)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.frac > 0.002:
            played = ring(d.c, d.r_out, r_now)
            p.fillPath(played, alpha(pal.acc, 0.06))
            p.setPen(QPen(alpha(pal.acc, 0.55), 1.1))
            p.drawEllipse(d.c, r_now, r_now)
        if self.best is not None:
            rb = d.radius_at(self.best)
            p.setPen(QPen(alpha(theme.mix(pal.acc, QColor('#ffe2a8'), 0.6), 0.75 if self.frac < self.best else 0.35),
                          1.2, Qt.PenStyle.DashLine))
            p.drawEllipse(d.c, rb, rb)
        if self.hover_r >= 0:
            p.setPen(QPen(QColor(255, 255, 255, 140), 1.0))
            p.drawEllipse(d.c, self.hover_r, self.hover_r)

    def _hover_time(self, p):
        d = self.deck
        dur = self.engine.duration or self.track.duration
        f = max(0.0, min(1.0, d.frac_at(self.hover_r)))
        near_best = self.best is not None and abs(d.radius_at(self.best) - self.hover_r) < 5
        text = mmss(f * dur) + ('  ·  the best part' if near_best else '')
        p.setFont(theme.caps(9, 600, 1.4))
        fm = QFontMetrics(p.font())
        w = fm.horizontalAdvance(text) + 18
        r = QRectF(self.hover_pos.x() + 14, self.hover_pos.y() - 30, w, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 10, 12, 210))
        p.drawRoundedRect(r, 11, 11)
        p.setPen(QColor('#f4efe6'))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _hover_rect(self, pos):
        return QRectF(pos.x() + 6, pos.y() - 36, 220, 32).toAlignedRect()

    def _controls(self, p, d, clip):
        """The parts of the deck that change: the speed lamp, the START · STOP glow under the mouse, the fader."""
        on = self.engine.playing and self.track is not None
        led = d.led
        p.setPen(Qt.PenStyle.NoPen)
        if on:
            halo = QRadialGradient(led, d.h * 0.03)
            halo.setColorAt(0.0, QColor(255, 176, 80, 170))
            halo.setColorAt(1.0, QColor(255, 176, 80, 0))
            p.setBrush(halo)
            p.drawEllipse(led, d.h * 0.03, d.h * 0.03)
        p.setBrush(QColor('#ffb454') if on else QColor(70, 30, 20))
        p.drawEllipse(led, d.h * 0.007, d.h * 0.007)
        if self.button_hover:
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(d.button, d.h * 0.008, d.h * 0.008)
        k = d.knob(0.0 if self.engine.muted else self.engine.volume)
        if clip.intersects(k.adjusted(-8, -8, 8, 8)):
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRoundedRect(k.translated(d.h * 0.004, d.h * 0.007), 3, 3)
            g = QLinearGradient(k.topLeft(), k.bottomLeft())
            g.setColorAt(0.0, QColor('#e6e7eb'))
            g.setColorAt(1.0, QColor('#8b8d94'))
            p.setBrush(g)
            p.drawRoundedRect(k, 3, 3)
            p.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
            p.drawLine(QPointF(k.left() + 3, k.center().y()), QPointF(k.right() - 3, k.center().y()))

    def _words(self, p):
        """The wall label: set the way museums label a work."""
        g = self.g
        b = g.get('blocks', {})
        t = self.track
        pal = theme.scene.pal
        ink = QColor('#f1ebe2')
        soft = QColor(theme.MUTED)
        e = self.engine

        def put(key, font, color, text, flags=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter):
            r = b.get(key)
            if r is None or not text:
                return
            p.setFont(font)
            p.setPen(color)
            fm = QFontMetrics(font)
            if not flags & Qt.TextFlag.TextWordWrap:
                text = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width()))
            p.drawText(r, flags, text)

        if t is None:
            put('eyebrow', theme.caps(8.6, 700, 2.6), pal.acc, time.strftime('%A · %d %B').upper())
            put('title', g['title_font'], ink, g['title'], Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap)
            put('sub', theme.serif(12.5, 400, italic=True), soft, self.EMPTY, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap)
            return
        spinning = 'NOW SPINNING' if e.playing else 'ON THE DECK · PAUSED'
        if e.drift and not t.local:
            spinning += ' · PICKED FOR YOU'
        put('eyebrow', theme.caps(8.6, 700, 2.6), pal.acc, spinning)
        put('artist', theme.caps(12.5, 600, 3.0), ink, t.subtitle.upper())
        put('title', g['title_font'], ink, g['title'], Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap)
        put('album', theme.serif(12, 400, italic=False), soft, f'from {t.album}')
        put('medium', theme.serif(11.5, 400), QColor('#cfc8bd'), self.medium(t))
        dur = e.duration or t.duration
        put('dims', theme.serif(11.5, 400), QColor('#cfc8bd'), self.dims(dur))
        put('credit', theme.serif(11.5, 400, italic=True), soft, self.credit(t))
        r = b.get('acc')
        if r is not None:
            p.setFont(theme.caps(8.2, 600, 1.8))
            p.setPen(QColor(theme.DIM))
            p.drawText(QRectF(r.left(), r.top() + 4, r.width(), 18), Qt.AlignmentFlag.AlignVCenter,
                       accession(t.uid))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(pal.acc)
            p.drawRect(QRectF(r.left(), r.bottom() - 2, 34, 1.5))
        r = b.get('next')
        if r is not None and self.next_items:
            p.setFont(theme.caps(8.4, 700, 2.4))
            p.setPen(alpha(pal.star, 0.55))
            p.drawText(QRectF(r.left(), r.top(), r.width(), 22), Qt.AlignmentFlag.AlignVCenter, 'NEXT FROM THE CRATE')
            f1, f2 = theme.font(9.6, 600), theme.font(9)
            for rect, nt in self.next_items:
                sleeve = QRectF(rect.left(), rect.top(), 44, 44)
                disc = mini_record(nt, 40, self.k.get('record', 'black'), label=False)
                p.drawPixmap(QPointF(sleeve.left() + 9, sleeve.top() - 4), disc)       # a record peeks out
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 90))
                p.drawRect(sleeve.translated(1.5, 2.5))
                p.drawPixmap(sleeve.toAlignedRect(), covers().get(nt, 44, 2))
                x = sleeve.right() + 10
                tw = rect.right() - x
                p.setFont(f1)
                p.setPen(ink)
                p.drawText(QRectF(x, rect.top() + 2, tw, 20), Qt.AlignmentFlag.AlignVCenter,
                           QFontMetrics(f1).elidedText(nt.title, Qt.TextElideMode.ElideRight, int(tw)))
                p.setFont(f2)
                p.setPen(soft)
                p.drawText(QRectF(x, rect.top() + 22, tw, 18), Qt.AlignmentFlag.AlignVCenter,
                           QFontMetrics(f2).elidedText(nt.subtitle, Qt.TextElideMode.ElideRight, int(tw)))

    @staticmethod
    def medium(t):
        if t.local:
            ext = (t.path or '').rsplit('.', 1)[-1].upper() if '.' in (t.path or '') else 'audio'
            return f'Sound on vinyl, 33⅓ rpm, pressed from a {ext} file on this PC'
        where = {'youtube': 'YouTube', 'soundcloud': 'SoundCloud'}.get(t.source, 'the web')
        return f'Sound on vinyl, 33⅓ rpm, pressed from a {where} stream'

    @staticmethod
    def dims(ms):
        s = max(0, int(ms or 0) // 1000)
        if not s:
            return ''
        return f'{s // 60} min {s % 60:02d} s' if s < 3600 else f'{s // 3600} h {s // 60 % 60:02d} min'

    def credit(self, t):
        if t.local:
            return 'Collection of the listener'
        if t.liked:
            return 'From your loved records'
        if self.engine.drift:
            return 'Acquired for you by Drift'
        return 'On loan from ' + {'youtube': 'YouTube', 'soundcloud': 'SoundCloud'}.get(t.source, 'the web')

    # ---- mouse ----------------------------------------------------------------------------------------------------
    def _r_of(self, pos):
        d = self.deck
        return math.hypot(pos.x() - d.c.x(), pos.y() - d.c.y())

    def _on_grooves(self, pos):
        if self.deck is None or self.track is None:
            return False
        r = self._r_of(pos)
        return self.deck.r_in - 3 <= r <= self.deck.r_out + 4

    def _on_label(self, pos):
        return self.deck is not None and self.track is not None and self._r_of(pos) <= self.deck.rl

    def _on_fader(self, pos):
        d = self.deck
        return d is not None and d.slot.adjusted(-d.h * 0.04, -d.h * 0.02, d.h * 0.04, d.h * 0.02).contains(pos)

    def _snap(self, r):
        d = self.deck
        if self.best is not None and abs(d.radius_at(self.best) - r) < 5:
            return d.radius_at(self.best)
        return max(d.r_in, min(d.r_out, r))

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.fader:
            self._fade_to(pos.y())
            return
        if self.drag:
            self._drag_to(pos)
            return
        d = self.deck
        if d is None:
            return
        grooves = self._on_grooves(pos)
        btn = d.button.contains(pos)
        hand = grooves or btn or self._on_label(pos) or self._on_fader(pos) or \
            any(r.contains(pos) for r, _ in self.next_items)
        self.setCursor(Qt.CursorShape.PointingHandCursor if hand else Qt.CursorShape.ArrowCursor)
        self.setToolTip('Lyrics · double-click: calm view (F)' if self._on_label(pos) else
                        'Play / pause (Space)' if btn else 'Volume' if self._on_fader(pos) else '')
        if btn != self.button_hover:
            self.button_hover = btn
            self.update(d.button.adjusted(-2, -2, 2, 2).toAlignedRect())
        old = self._hover_rect(self.hover_pos)
        new_r = self._snap(self._r_of(pos)) if grooves else -1.0
        if new_r != self.hover_r or (grooves and pos != self.hover_pos):
            self.hover_r = new_r
            self.hover_pos = pos
            self.update(QRegion(self._disc_rect()).united(QRegion(old)).united(QRegion(self._hover_rect(pos))))

    def leaveEvent(self, e):
        if self.hover_r >= 0 or self.button_hover:
            self.hover_r = -1.0
            self.button_hover = False
            self.update()

    def mousePressEvent(self, e):
        pos = e.position()
        d = self.deck
        if d is None:
            return
        if e.button() == Qt.MouseButton.RightButton:
            for r, t in self.next_items:
                if r.contains(pos):
                    self.win.track_menu(t, self.engine.queue, e.globalPosition().toPoint())
                    return
            if self.track is not None and self._r_of(pos) <= d.R:
                self.win.track_menu(self.track, [self.track], e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if d.button.contains(pos):
            self.engine.toggle()
            return
        if self._on_fader(pos):
            self.fader = True
            self._fade_to(pos.y())
            return
        if self._on_grooves(pos):
            self.drag = True
            self.arm_anim.stop()
            self._drag_to(pos)
            return
        for r, t in self.next_items:
            if r.contains(pos):
                self.win.jump_to(t)
                return

    def _drag_to(self, pos):
        d = self.deck
        r = self._snap(self._r_of(pos))
        self.frac = max(0.0, min(1.0, d.frac_at(r)))
        old = self._hover_rect(self.hover_pos)
        self.hover_r, self.hover_pos = r, pos
        self._set_phi(d.phi(r))
        self.update(QRegion(self._disc_rect()).united(QRegion(old)).united(QRegion(self._hover_rect(pos))))

    def _fade_to(self, y):
        d = self.deck
        v = max(0.0, min(1.0, (d.slot.bottom() - y) / d.slot.height()))
        if self.engine.muted:
            self.engine.toggle_mute()
        self.engine.set_volume(v)
        self.win.bar.volume.set_value(v)
        self.win.bar._volume_icon(v)
        self.update(d.slot.adjusted(-d.h * 0.06, -d.h * 0.05, d.h * 0.06, d.h * 0.05).toAlignedRect())

    def mouseReleaseEvent(self, e):
        if self.fader:
            self.fader = False
            return
        if self.drag:
            self.drag = False
            self.engine.seek(self.frac * (self.engine.duration or 0))
            return
        if e.button() == Qt.MouseButton.LeftButton and self._on_label(e.position()):
            self.win.go('lyrics')

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        if self._on_label(pos) or (self.deck is not None and not self.deck.rect.contains(pos) and
                                   not QRectF(self.g['text_rect']).contains(pos)):
            self.win.go('home' if self.calm else 'calm')

    def wheelEvent(self, e):
        if self._on_fader(e.position()):
            v = self.engine.volume + (0.04 if e.angleDelta().y() > 0 else -0.04)
            self._fade_to(self.deck.volume_y(max(0.0, min(1.0, v))))

    # ---- visibility -----------------------------------------------------------------------------------------------
    def showEvent(self, e):
        self.refresh()
        self._wake()
        super().showEvent(e)

    def hideEvent(self, e):
        self.spin.stop()
        self.let_go()            # out of sight: let the pictures go
        super().hideEvent(e)


# ---- the stage ----------------------------------------------------------------------------------------------------
class VinylStage(Stage):
    id = 'vinyl'
    title = 'Vinyl'
    blurb = 'Every song is a record.'
    KNOBS = (
        Knob('record', 'Record', 'choice', 'black', (('black', 'Black'), ('smoke', 'Smoke'), ('clear', 'Clear'),
                                                     ('oxblood', 'Oxblood'), ('cover', 'Cover colour'),
                                                     ('sunburst', 'Sunburst'))),
        Knob('label', 'Label', 'choice', 'cover', (('cover', 'Cover art'), ('printed', 'Printed label'))),
        Knob('deck', 'Deck', 'choice', 'walnut', (('walnut', 'Walnut'), ('ash', 'Black ash'), ('lacquer', 'White lacquer'))),
        Knob('light', 'Light', 'choice', 'gallery', (('gallery', 'Gallery spotlight'), ('daylight', 'Daylight'),
                                                     ('night', 'Only the record'))),
        Knob('spin', 'Records turn', 'toggle', True, hint='Off: records stand still (less motion)'),
        Knob('wall', 'Picture on the wall', 'image', '', hint='Your own art, hung behind the deck'),
    )
    tabs = {'home': 'Deck', 'explore': 'Dig', 'library': 'Crate', 'liked': 'Loved', 'queue': 'Next',
            'playlists': 'Box sets'}
    words = {'library': 'The crate', 'liked': 'Loved records', 'queue': 'Up next', 'explore': 'Dig',
             'playlists': 'Box sets'}
    seek_style = 'groove'

    def __init__(self, sketch):
        super().__init__(sketch)
        self._wall = (None, None)

    def now_page(self, win):
        return Turntable(win, self)

    def track_list(self, numbered=True, grow=True):
        from ..crate import CrateList
        return CrateList(self, numbered=numbered, grow=grow)

    def record(self, track, size, label=True):
        return mini_record(track, size, self.k.get('record', 'black'), label=label)

    def wall(self, widget):
        path = self.asset('wall')
        if path is None:
            return None
        key = (str(path), widget.width(), widget.height())
        if self._wall[0] != key:
            self._wall = (key, scaled_wall(path, widget.size(), widget.devicePixelRatioF()))
        return self._wall[1]

    def paint_room(self, p, widget, rect):
        w = widget.width()
        draw_room(p, QRectF(rect), widget.size(), theme.scene.pal, self.k.get('light', 'gallery'),
                  QPointF(w * 0.5, -widget.height() * 0.25), max(w, widget.height()) * 0.9, self.wall(widget), self)

    def paint_top(self, p, widget, solid):
        if not solid:
            return
        base = wall_color(theme.scene.pal, self.k.get('light', 'gallery'))
        g = QLinearGradient(0, 0, 0, widget.height())
        g.setColorAt(0.0, alpha(base, 0.96))
        g.setColorAt(0.7, alpha(base, 0.85))
        g.setColorAt(1.0, alpha(base, 0.0))
        p.fillRect(widget.rect(), g)

    def paint_dock(self, p, dock):
        """The player strip is a shelf: the page falls into shadow, a lit edge, the deck's wood under it."""
        h = dock.height()
        base = wall_color(theme.scene.pal, self.k.get('light', 'gallery'))
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, alpha(base, 0.0))
        g.setColorAt(0.3, alpha(base.darker(130), 0.97))
        g.setColorAt(1.0, alpha(base.darker(170), 1.0))
        p.fillRect(dock.rect(), g)
        y = h * 0.3
        p.fillRect(QRectF(0, y, dock.width(), 1), QColor(255, 226, 190, 26))
        p.fillRect(QRectF(0, y + 1, dock.width(), 3), QColor(0, 0, 0, 50))

    def paint_mark(self, p, rect, color):
        c = rect.center()
        r = rect.width() * 0.75
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 12, 14))
        p.drawEllipse(c, r, r)
        p.setBrush(color)
        p.drawEllipse(c, r * 0.42, r * 0.42)

    def dock_cover(self, track, size):
        return self.record(track, size)

    @classmethod
    def thumbnail(cls, knobs, w, h, folder=None):
        from .landscape import _demo_palette, _Fake
        from PyQt6.QtGui import QImage
        ratio = 2.0
        px = QPixmap(int(w * ratio), int(h * ratio))
        px.setDevicePixelRatio(ratio)
        stage = cls(_Fake(knobs, folder))
        pal = stage.tune(_demo_palette())
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        dh = h * 0.7
        deck = Deck(QRectF((w - dh * RATIO) / 2 + dh * 0.1, h * 0.19, dh * RATIO, dh))
        wall = None
        if folder is not None and knobs.get('wall') and (folder / knobs['wall']).is_file():
            wall = scaled_wall(folder / knobs['wall'], QSize(w, h), ratio)
        draw_room(p, QRectF(0, 0, w, h), QSize(w, h), pal, knobs.get('light', 'gallery'),
                  QPointF(w / 2, -h * 0.2), w * 0.9, wall, stage)
        wave = [int(255 * v) for v in ([0.3] * 30 + [0.8] * 40 + [0.1] * 6 + [0.5] * 30 + [1.0] * 34 + [0.3] * 20)]
        img = QImage(64, 64, QImage.Format.Format_RGB32)
        q = QPainter(img)
        g = QLinearGradient(0, 0, 64, 64)
        g.setColorAt(0, QColor('#ff7a3d'))
        g.setColorAt(1, QColor('#5a1e6e'))
        q.fillRect(img.rect(), g)
        q.setBrush(QColor('#ffd166'))
        q.setPen(Qt.PenStyle.NoPen)
        q.drawEllipse(14, 24, 30, 30)
        q.end()
        from ...store import Track
        demo = Track(uid='demo', title='Harbour Lights', artist='The Low Tides')
        draw_sleeve(p, deck.sleeve, deck.S, SLEEVE_DEG, demo, img)
        draw_plinth(p, deck, knobs.get('deck', 'walnut'))
        draw_platter(p, deck, empty_logo=False)
        draw_record(p, deck.c, deck.R, wave, Wax(knobs.get('record', 'black'), pal))
        lab = label_pixmap(demo, deck.rl, knobs.get('label', 'cover'), pal, img, ratio)
        p.save()
        p.translate(deck.c)
        p.rotate(-18)
        p.drawPixmap(QPointF(-deck.rl, -deck.rl), lab)
        p.restore()
        draw_spindle(p, deck)
        draw_arm(p, deck, deck.phi(deck.radius_at(0.38)), 0.0, pal.acc)
        p.end()
        return px
