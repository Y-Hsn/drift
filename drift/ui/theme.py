"""Drift's look. Each song's cover gives a palette (a dusk sky, three ranges of hills, a lake, the sun's light) that
paints its landscape on Now and the ground of every other page; it glides to the next song's. Surfaces are thin veils
of white over that ground, so they share its colour. Type: Sitka (serif) for titles, Bahnschrift (condensed DIN) for
spaced capitals and numbers, Segoe for the rest. Also the stylesheet, line icons and covers."""
import colorsys
import hashlib

from PyQt6.QtCore import QByteArray, QEasingCurve, QObject, QRectF, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PyQt6.QtSvg import QSvgRenderer

BG = '#0b0c10'
RAISED = '#1a1b22'
HOVER = '#22232c'
LINE = '#1d1e26'
TEXT = '#ececf1'
MUTED = '#8d8e9b'
DIM = '#5c5d69'
DEFAULT_ACCENT = '#8fa6ff'
VEIL = 'rgba(255,255,255,0.045)'        # a surface: a thin veil over the room
VEIL_HI = 'rgba(255,255,255,0.08)'
EDGE = 'rgba(255,255,255,0.06)'

FAMILY = 'Segoe UI Variable Text'
INK, INK_MUTED = '#1b1712', '#6a6258'    # Drift's own words over a light room (canvas sketches)
SERIF = 'Sitka'            # Windows' own optical-size serif (its Banner cut, made for big type)
CAPS = 'Bahnschrift'       # Windows' DIN: condensed, spaced capitals for labels and numbers


def font(size=10, weight=400, display=False):
    """Body text in Segoe; display=True sets headings in the serif."""
    if display:
        return serif(size, min(weight, 700))
    f = QFont()
    f.setFamilies([FAMILY, 'Segoe UI', 'Arial'])
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def serif(size=20, weight=600, italic=False):
    f = QFont()
    f.setFamilies([SERIF, 'Georgia', 'Cambria'])
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    f.setItalic(italic)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def caps(size=9, weight=600, spacing=2.0, condensed=True):
    """Spaced capitals (the text is upper-cased by the caller) in condensed DIN."""
    f = QFont()
    f.setFamilies([CAPS, FAMILY, 'Arial'])
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    if condensed:
        f.setStretch(75)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


class _Accent(QObject):
    changed = pyqtSignal(QColor)

    def __init__(self):
        super().__init__()
        self.color = QColor(DEFAULT_ACCENT)

    def set(self, color):
        color = QColor(color)
        if color != self.color:
            self.color = color
            self.changed.emit(color)


accent = _Accent()


def ink_on(color):
    """Black or white text, whichever reads on `color`."""
    c = QColor(color)
    return QColor('#0b0b0d') if (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) > 150 else QColor('#ffffff')


def accent_from(image):
    """The cover's most vivid colour that still reads on the dark room (falls back to the default accent)."""
    if image is None or image.isNull():
        return QColor(DEFAULT_ACCENT)
    small = image.scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    best, score = None, 0.0
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            h, l, s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
            if l < 0.18 or l > 0.92:
                continue
            sc = s * (1 - abs(l - 0.55))
            if sc > score:
                best, score = (h, l, s), sc
    if not best or score < 0.12:
        return QColor(DEFAULT_ACCENT)
    h, l, s = best
    r, g, b = colorsys.hls_to_rgb(h, max(0.58, min(0.72, l)), max(0.55, min(0.85, s)))
    return QColor.fromRgbF(r, g, b)


# ---- the landscape's colours ----------------------------------------------------------------------------------------
def mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t)


def _hsl(h, s, l):
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return QColor.fromRgbF(r, g, b)


class Palette:
    """Every colour of one song's landscape, from its cover: a dusk sky (dark above, the cover's glow at the
    horizon), three ranges of hills from far and hazy to near and dark, the lake, and the accent (the sun's light)."""
    NAMES = ('sky_top', 'sky_mid', 'glow', 'far', 'mid', 'near', 'lit', 'lake_top', 'lake_bottom', 'acc', 'star')

    def __init__(self, **c):
        for k in self.NAMES:
            setattr(self, k, QColor(c[k]))
        self.night = c.get('night', 1.0)

    def blend(self, other, t):
        return Palette(night=self.night + (other.night - self.night) * t,
                       **{k: mix(getattr(self, k), getattr(other, k), t) for k in self.NAMES})


def _dominant(image):
    """The cover's main colour (h, s, l): the fullest hue family, averaged; grey covers give s ~ 0."""
    small = image.scaled(14, 14, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    buckets = {}
    total = [0.0, 0.0, 0.0, 0]
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            h, l, s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
            total[0] += c.redF()
            total[1] += c.greenF()
            total[2] += c.blueF()
            total[3] += 1
            if s < 0.18 or l < 0.08 or l > 0.92:
                continue
            k = int(h * 12) % 12
            b = buckets.setdefault(k, [0.0, 0.0, 0.0, 0.0])
            w = s * (1 - abs(l - 0.5))
            b[0] += c.redF() * w
            b[1] += c.greenF() * w
            b[2] += c.blueF() * w
            b[3] += w
    lum = (total[0] + total[1] + total[2]) / (3 * max(1, total[3]))
    if buckets:
        b = max(buckets.values(), key=lambda v: v[3])
        if b[3] > 4.0:
            h, l, s = colorsys.rgb_to_hls(b[0] / b[3], b[1] / b[3], b[2] / b[3])
            return h, s, l, lum
    n = max(1, total[3])
    h, l, s = colorsys.rgb_to_hls(total[0] / n, total[1] / n, total[2] / n)
    return h, s * 0.5, l, lum


def palette_of(image, seed=''):
    """The landscape of a cover (or, without one, of a colour drawn from the artist's name)."""
    if image is not None and not image.isNull():
        acc = accent_from(image)
        h, s, _l, lum = _dominant(image)
    else:
        acc = QColor.fromHsl(tint(seed).hslHue(), 160, 170)
        h, s, lum = tint(seed).hslHueF(), 0.5, 0.3
    ha = acc.hslHueF() if acc.hslHueF() >= 0 else h
    sat = min(0.62, 0.12 + s * 0.75)
    glow = _hsl(ha, 0.55, 0.30 + 0.06 * lum)
    return Palette(
        sky_top=_hsl(h, sat * 0.8, 0.055),
        sky_mid=mix(_hsl(h, sat, 0.12), _hsl(ha, 0.45, 0.16), 0.5),
        glow=glow,
        far=mix(glow, _hsl(h, sat * 0.6, 0.20), 0.55),
        mid=_hsl((h + ha) / 2 if abs(h - ha) < 0.5 else h, sat * 0.7, 0.115),
        near=_hsl(h, sat * 0.6, 0.052),
        lit=mix(_hsl(h, sat * 0.6, 0.07), acc, 0.34),
        lake_top=mix(glow, _hsl(h, sat, 0.05), 0.55),
        lake_bottom=_hsl(h, sat * 0.7, 0.03),
        acc=acc,
        star=mix(acc, QColor('#ffffff'), 0.75),
        night=max(0.35, 1.0 - lum),
    )


tuner = None      # the sketch's view of a palette (mood, fixed accent); set by sketches.apply

DEFAULT_PALETTE = Palette(sky_top='#05070f', sky_mid='#0f1730', glow='#3a4f86', far='#25305a', mid='#121a33',
                          near='#070a14', lit='#2c3d6e', lake_top='#1d2850', lake_bottom='#03050b',
                          acc=DEFAULT_ACCENT, star='#dfe6ff', night=1.0)


class _Scene(QObject):
    """The palette of what plays, gliding (900 ms) to the next song's. Every page's ground follows it."""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.pal = DEFAULT_PALETTE
        self.start = self.target = DEFAULT_PALETTE
        self.anim = QVariantAnimation(self, duration=900, easingCurve=QEasingCurve(QEasingCurve.Type.InOutSine))
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._step)
        self.cache = {}

    def of(self, uid, image, seed):
        """One song's palette (kept for the last 40 songs)."""
        key = (uid, image is not None)
        if key not in self.cache:
            if len(self.cache) > 40:
                self.cache.clear()
            pal = palette_of(image, seed)
            self.cache[key] = tuner(pal) if tuner else pal
        return self.cache[key]

    def follow(self, pal):
        if pal is self.target:
            return
        self.start, self.target = self.pal, pal
        self.anim.stop()
        self.anim.start()

    def _step(self, t):
        self.pal = self.start.blend(self.target, float(t))
        self.changed.emit()


scene = _Scene()


_grain = None


def grain():
    """A 128 px tile of fine film grain (drawn over the grounds, so flat colours feel printed)."""
    global _grain
    if _grain is None:
        import random
        r = random.Random(7)
        n = 128
        data = bytearray(n * n * 4)
        for i in range(n * n):
            a = r.randrange(0, 15)
            v = a if r.random() < 0.5 else 0      # premultiplied: light specks or dark ones
            data[i * 4:i * 4 + 4] = bytes((v, v, v, a))
        _grain = QPixmap.fromImage(QImage(bytes(data), n, n, n * 4, QImage.Format.Format_ARGB32_Premultiplied).copy())
    return _grain


def stylesheet(acc):
    a = QColor(acc).name()
    return f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; }}
QMainWindow {{ background: {BG}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px 8px; border-radius: 6px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #34343d; border-radius: 3px; min-height: 40px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: #4a4a55; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QScrollBar:horizontal {{ height: 0; }}
QLineEdit {{ font-size: 10.5pt; background: {VEIL}; border: 1px solid {EDGE}; border-radius: 18px; padding: 8px 16px 8px 40px;
            selection-background-color: {a}; }}
QLineEdit:focus {{ border: 1px solid {a}; background: rgba(0,0,0,0.25); }}
QMenu {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
QPushButton {{ background: transparent; border: none; }}
QPushButton#Chip {{ background: {VEIL}; border: 1px solid {EDGE}; border-radius: 15px; padding: 0 14px; color: {TEXT}; font-size: 9.5pt; }}
QPushButton#Chip:hover {{ background: {VEIL_HI}; }}
QPushButton#Chip:checked {{ background: {TEXT}; color: #0b0b0d; }}
QPushButton#Pill {{ background: {a}; color: {ink_on(a).name()}; border-radius: 17px; padding: 0 22px; font-weight: 600; }}
QPushButton#Pill:hover {{ background: {QColor(a).lighter(112).name()}; }}
QPushButton#Ghost {{ background: {VEIL}; border: 1px solid {EDGE}; border-radius: 17px; padding: 0 20px; font-weight: 600; }}
QPushButton#Ghost:hover {{ background: {VEIL_HI}; }}
QPushButton#Ghost:disabled, QPushButton#Pill:disabled {{ color: {DIM}; }}
QPushButton#Tab {{ padding: 6px 8px; color: {MUTED}; }}
QPushButton#Tab:hover {{ color: {TEXT}; }}
QPushButton#Tab:checked {{ color: {TEXT}; }}
QListView {{ background: transparent; border: none; }}
QLabel#Eyebrow, QLabel#Net {{ color: {a}; background: transparent; }}
"""


# ---- icons ---------------------------------------------------------------------------------------------------
_P = {
    'home': '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
    'explore': '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
    'search': '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    'library': '<path d="M4 4v16M9 4v16M14 4.5l5 15.5"/>',
    'heart': '<path d="M12 20s-7.5-4.6-9-9.3C2 7.4 4.3 4.5 7.4 4.5c1.9 0 3.5 1 4.6 2.6 1.1-1.6 2.7-2.6 4.6-2.6 3.1 0 5.4 2.9 4.4 6.2C19.5 15.4 12 20 12 20z"/>',
    'heartfill': '<path fill="currentColor" d="M12 20s-7.5-4.6-9-9.3C2 7.4 4.3 4.5 7.4 4.5c1.9 0 3.5 1 4.6 2.6 1.1-1.6 2.7-2.6 4.6-2.6 3.1 0 5.4 2.9 4.4 6.2C19.5 15.4 12 20 12 20z"/>',
    'queue': '<path d="M4 6h11M4 11h11M4 16h7M17 14v6l4-3z"/>',
    'play': '<path fill="currentColor" stroke="none" d="M8 5.2v13.6a.8.8 0 0 0 1.2.7l10.8-6.8a.8.8 0 0 0 0-1.4L9.2 4.5A.8.8 0 0 0 8 5.2z"/>',
    'next': '<path fill="currentColor" stroke="none" d="M5 6.2v11.6a.8.8 0 0 0 1.2.7l8.6-5.8a.8.8 0 0 0 0-1.4L6.2 5.5A.8.8 0 0 0 5 6.2z"/><rect fill="currentColor" stroke="none" x="16.5" y="5.5" width="2.5" height="13" rx="1"/>',
    'prev': '<path fill="currentColor" stroke="none" d="M19 6.2v11.6a.8.8 0 0 1-1.2.7l-8.6-5.8a.8.8 0 0 1 0-1.4l8.6-5.8A.8.8 0 0 1 19 6.2z"/><rect fill="currentColor" stroke="none" x="5" y="5.5" width="2.5" height="13" rx="1"/>',
    'shuffle': '<path d="M3 7h3.5c2 0 3.2 1 4.5 3l2 4c1.3 2 2.5 3 4.5 3H21M3 17h3.5c1.4 0 2.4-.5 3.3-1.5M14.2 8.5C15 7.5 16 7 17.5 7H21M18 4l3 3-3 3M18 14l3 3-3 3"/>',
    'repeat': '<path d="M4 11V9a3 3 0 0 1 3-3h12M16 3l3 3-3 3M20 13v2a3 3 0 0 1-3 3H5M8 21l-3-3 3-3"/>',
    'repeat1': '<path d="M4 11V9a3 3 0 0 1 3-3h12M16 3l3 3-3 3M20 13v2a3 3 0 0 1-3 3H5M8 21l-3-3 3-3"/><path d="M11.5 10.5 13 9.5v5"/>',
    'volume': '<path d="M4 9.5h3.5L12 5v14l-4.5-4.5H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    'mute': '<path d="M4 9.5h3.5L12 5v14l-4.5-4.5H4z"/><path d="m16 9.5 5 5M21 9.5l-5 5"/>',
    'download': '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
    'saved': '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.7L16.5 9"/>',
    'radio': '<circle cx="12" cy="12" r="2"/><path d="M8.2 8.2a5.4 5.4 0 0 0 0 7.6M15.8 8.2a5.4 5.4 0 0 1 0 7.6M5.4 5.4a9.3 9.3 0 0 0 0 13.2M18.6 5.4a9.3 9.3 0 0 1 0 13.2"/>',
    'more': '<circle fill="currentColor" stroke="none" cx="5.5" cy="12" r="1.6"/><circle fill="currentColor" stroke="none" cx="12" cy="12" r="1.6"/><circle fill="currentColor" stroke="none" cx="18.5" cy="12" r="1.6"/>',
    'settings': '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/>',
    'lyrics': '<path d="M5 5h14v10H9l-4 4z"/><path d="M9 9h6M9 12h4"/>',
    'offline': '<path d="M2 8.5a15 15 0 0 1 4.5-2.7M10 5a15 15 0 0 1 12 3.5M5 12a10 10 0 0 1 3.5-2M13.5 9.7A10 10 0 0 1 19 12M8.5 15.5a5 5 0 0 1 7 0"/><circle fill="currentColor" stroke="none" cx="12" cy="19" r="1.3"/><path d="m3 3 18 18"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'folder': '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    'close': '<path d="m6 6 12 12M18 6 6 18"/>',
    'music': '<path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
}
_cache = {}


def pixmap(name, color=TEXT, size=20, stroke=1.8, ratio=2.0):
    key = (name, QColor(color).name(QColor.NameFormat.HexArgb), size, stroke, ratio)
    if key in _cache:
        return _cache[key]
    c = QColor(color).name()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" color="{c}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_P[name]}</svg>')
    svg = svg.replace('currentColor', c)
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(p)
    p.end()
    px.setDevicePixelRatio(ratio)
    if len(_cache) > 300:
        _cache.clear()
    _cache[key] = px
    return px


def icon(name, color=TEXT, size=20, stroke=1.8):
    return QIcon(pixmap(name, color, size, stroke))


# ---- covers ----------------------------------------------------------------------------------------------------
def tint(seed):
    h = int(hashlib.md5((seed or '?').encode()).hexdigest()[:6], 16)
    return QColor.fromHsl(h % 360, 90 + h % 60, 70 + (h >> 8) % 40)


def placeholder(seed, size, radius=6, ratio=2.0):
    """A cover for songs without one: two calm tones and a note, unique per artist."""
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    px.setDevicePixelRatio(ratio)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(path)
    base = tint(seed)
    p.fillRect(QRectF(0, 0, size, size), base.darker(260))
    p.setBrush(base.darker(150))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(size * 0.35, -size * 0.25, size * 0.95, size * 0.95))
    note = pixmap('music', QColor(255, 255, 255, 150), int(size * 0.38), 1.6)
    p.drawPixmap(int(size * 0.31), int(size * 0.31), note)
    p.end()
    return px


def rounded(image, size, radius=6, ratio=2.0):
    """Centre-cropped square cover with rounded corners."""
    if isinstance(image, QPixmap):
        image = image.toImage()
    side = min(image.width(), image.height())
    if side <= 0:
        return None
    sq = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    sq = sq.scaled(int(size * ratio), int(size * ratio), Qt.AspectRatioMode.IgnoreAspectRatio,
                   Qt.TransformationMode.SmoothTransformation)
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    px.setDevicePixelRatio(ratio)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(path)
    p.drawImage(QRectF(0, 0, size, size), sq)
    p.end()
    return px


def image_from(data):
    if not data:
        return None
    img = QImage.fromData(data)
    if img.isNull():
        return None
    # YouTube thumbnails are 4:3 with black bars: crop to the 16:9 picture first
    if img.width() and abs(img.height() / img.width() - 0.75) < 0.02:
        h = int(img.width() * 9 / 16)
        img = img.copy(0, (img.height() - h) // 2, img.width(), h)
    return img
