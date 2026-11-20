"""Stage 'canvas': a room drawn by the sketch itself. This is how anyone makes a theme Drift has never had (ink on paper,
a darkroom, a tape deck...): the sketch's folder holds sketch.js, a small drawing program in plain JavaScript, plus its
pictures and font.

Safety: sketch.js runs in a sealed box (Qt's QJSEngine with no extensions). It has no files, no internet, no system,
no timers of its own. All it can do is describe a picture (the `g` commands below, recorded in JavaScript and handed
to Drift in one array per frame) and ask Drift for a few things (`drift.seek`, `drift.next`...). Every call is
watched: one that runs longer than WATCHDOG seconds is stopped, and a sketch that keeps doing it is paused with a
message. Pictures it names must be files in its own folder.

The script's side (the full reference is guide.html):
    var fps = 0;                       // 0: redraw when something changes; up to 30 for motion
    function draw(g, song, t) {}       // the Now page; t = seconds since the room opened
    function room(g, song) {}          // optional: behind every other page (cached)
    function press(x, y, song) {}      // optional: mouse; also drag, release, hover, wheel(dy), doubleClick
"""
import json
import threading
import time

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath,
                         QPen, QPixmap, QRadialGradient)
from PyQt6.QtWidgets import QWidget

from .. import theme
from . import IMAGES, FONTS, Stage

SCRIPT_MAX = 512 * 1024      # a sketch.js
WATCHDOG = 0.25              # seconds one call may take
STRIKES = 3                  # stopped calls before the sketch is paused
MAX_FPS = 30
MAX_LAYERS = 8
TABS = ('home', 'explore', 'library', 'liked', 'queue', 'playlists')

# The script's toolbox. Everything here is plain JavaScript: it records what to draw; Drift paints it afterwards.
PRELUDE = r'''
var __global = this, __cmds = [], __acts = [], __cached = {}, __song = {};
function __c(a) { __cmds.push(a); if (__cmds.length > 30000) throw new Error('A picture can have 30000 drawing steps at most.'); }
function __n(v, d) { v = +v; return isFinite(v) ? v : (d || 0); }
function __s(v) { return v == null ? null : String(v); }
function __pts(p) {
  var out = [];
  for (var i = 0; i < p.length; i++) {
    if (p[i] != null && typeof p[i] === 'object') { out.push(__n(p[i][0] !== undefined ? p[i][0] : p[i].x), __n(p[i][1] !== undefined ? p[i][1] : p[i].y)); }
    else out.push(__n(p[i]));
  }
  return out;
}
function __stops(s) { var o = []; for (var i = 0; i < s.length; i++) o.push([__n(s[i][0]), String(s[i][1])]); return o; }
var g = {
  width: 0, height: 0, top: 48, player: null,
  clear: function (c) { __c(['clear', __s(c)]); return g; },
  fill: function (c) { __c(['fill', __s(c)]); return g; },
  noFill: function () { __c(['fill', null]); return g; },
  stroke: function (c, w) { __c(['stroke', __s(c), __n(w, 1)]); return g; },
  noStroke: function () { __c(['stroke', null, 0]); return g; },
  linear: function (x1, y1, x2, y2, stops) { __c(['lin', __n(x1), __n(y1), __n(x2), __n(y2), __stops(stops)]); return g; },
  radial: function (x, y, r, stops) { __c(['rad', __n(x), __n(y), __n(r), __stops(stops)]); return g; },
  rect: function (x, y, w, h, r) { __c(['rect', __n(x), __n(y), __n(w), __n(h), __n(r)]); return g; },
  circle: function (x, y, r) { __c(['ell', __n(x), __n(y), __n(r), __n(r)]); return g; },
  ellipse: function (x, y, rx, ry) { __c(['ell', __n(x), __n(y), __n(rx), __n(ry)]); return g; },
  line: function (x1, y1, x2, y2) { __c(['line', __n(x1), __n(y1), __n(x2), __n(y2)]); return g; },
  poly: function (pts, closed) { __c(['poly', __pts(pts), !!closed]); return g; },
  curve: function (pts, closed) { __c(['curve', __pts(pts), !!closed]); return g; },
  arc: function (x, y, r, from, to) { __c(['arc', __n(x), __n(y), __n(r), __n(from), __n(to)]); return g; },
  path: function (d) { __c(['path', String(d)]); return g; },
  text: function (s, x, y, o) {
    o = o || {};
    __c(['text', String(s == null ? '' : s).slice(0, 2000), __n(x), __n(y), __n(o.size, 14), String(o.font || 'body'),
         __n(o.weight, 400), !!o.italic, String(o.align || 'left'), String(o.baseline || 'top'), __n(o.width, 0),
         __n(o.spacing, 0), !!o.caps]);
    return g;
  },
  image: function (name, x, y, w, h, o) {
    o = o || {};
    __c(['img', String(name), __n(x), __n(y), __n(w), __n(h), __n(o.opacity, 1), String(o.fit || 'cover'), __n(o.radius, 0)]);
    return g;
  },
  save: function () { __c(['save']); return g; },
  restore: function () { __c(['restore']); return g; },
  translate: function (x, y) { __c(['tr', __n(x), __n(y)]); return g; },
  rotate: function (deg) { __c(['rot', __n(deg)]); return g; },
  scale: function (x, y) { __c(['sc', __n(x, 1), __n(y === undefined ? x : y, 1)]); return g; },
  alpha: function (a) { __c(['alpha', __n(a, 1)]); return g; },
  blend: function (m) { __c(['blend', String(m || 'normal')]); return g; },
  clip: function (x, y, w, h, r) { __c(['clip', __n(x), __n(y), __n(w), __n(h), __n(r)]); return g; },
  texture: function (name, opacity) { __c(['tex', String(name), __n(opacity, 1)]); return g; },
  layer: function (key, fn) {
    key = String(key);
    if (__cached[key]) { __c(['use', key]); return g; }
    var outer = __cmds; __cmds = [];
    try { fn(g); } finally { var inner = __cmds; __cmds = outer; }
    __c(['layer', key, inner]);
    return g;
  }
};
function __hex(v) { v = Math.max(0, Math.min(255, Math.round(+v || 0))); return (v < 16 ? '0' : '') + v.toString(16); }  // NaN -> 0
function __rgba(c) {
  c = String(c).trim();
  var m = /^rgba?\(([^)]*)\)$/.exec(c);
  if (m) { var p = m[1].split(','); return [+p[0], +p[1], +p[2], p.length > 3 ? +p[3] : 1]; }
  if (c[0] === '#') {
    var h = c.slice(1);
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), a];
  }
  throw new Error('Colours in drift.mix/alpha/lighter must be "#rrggbb" or "rgba(r,g,b,a)", not "' + c + '"');
}
function __out(c) { return '#' + __hex(c[0]) + __hex(c[1]) + __hex(c[2]) + (c[3] >= 1 ? '' : __hex(c[3] * 255)); }
var drift = {
  seek: function (f) { __acts.push(['seek', __n(f)]); },
  playPause: function () { __acts.push(['toggle']); },
  next: function () { __acts.push(['next']); },
  previous: function () { __acts.push(['previous']); },
  like: function () { __acts.push(['like']); },
  lyrics: function () { __acts.push(['lyrics']); },
  calm: function () { __acts.push(['calm']); },
  play: function (i) { __acts.push(['jump', Math.floor(__n(i))]); },
  cursor: function (k) { __acts.push(['cursor', String(k || 'arrow')]); },
  tip: function (s) { __acts.push(['tip', String(s == null ? '' : s).slice(0, 200)]); },
  redraw: function () { __acts.push(['redraw']); },
  rgba: function (r, g_, b, a) { return __out([r, g_, b, a === undefined ? 1 : a]); },
  mix: function (a, b, t) { a = __rgba(a); b = __rgba(b); return __out([0, 1, 2, 3].map(function (i) { return a[i] + (b[i] - a[i]) * t; })); },
  alpha: function (c, a) { c = __rgba(c); c[3] = a; return __out(c); },
  lighter: function (c, t) { return drift.mix(c, '#ffffff', t); },
  darker: function (c, t) { return drift.mix(c, '#000000', t); },
  clamp: function (v, a, b) { return Math.max(a, Math.min(b, v)); },
  lerp: function (a, b, t) { return a + (b - a) * t; },
  ease: function (t) { t = Math.max(0, Math.min(1, t)); return t * t * (3 - 2 * t); },
  random: function (seed) {
    var h = 1779033703 ^ String(seed).length;
    for (var i = 0; i < String(seed).length; i++) { h = Math.imul(h ^ String(seed).charCodeAt(i), 3432918353); h = h << 13 | h >>> 19; }
    return function () { h = Math.imul(h ^ h >>> 16, 2246822507); h = Math.imul(h ^ h >>> 13, 3266489909); h ^= h >>> 16; return (h >>> 0) / 4294967296; };
  },
  noise: function (x, y, z) {
    function hsh(i, j, k) { var n = Math.imul(i, 374761393) + Math.imul(j, 668265263) + Math.imul(k, 2147483647); n = Math.imul(n ^ n >>> 13, 1274126177); return ((n ^ n >>> 16) >>> 0) / 4294967296; }
    function sm(t) { return t * t * (3 - 2 * t); }
    y = y || 0; z = z || 0;
    var xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z), xf = sm(x - xi), yf = sm(y - yi), zf = sm(z - zi);
    function L(a, b, t) { return a + (b - a) * t; }
    function face(k) { return L(L(hsh(xi, yi, k), hsh(xi + 1, yi, k), xf), L(hsh(xi, yi + 1, k), hsh(xi + 1, yi + 1, k), xf), yf); }
    return L(face(zi), face(zi + 1), zf);
  },
  measure: function (s, o) { o = o || {}; return __host.measure(String(s), __n(o.size, 14), String(o.font || 'body'), __n(o.weight, 400), !!o.italic, __n(o.spacing, 0), !!o.caps); },
  imageSize: function (name) { var v = __host.imageSize(String(name)); return { width: v[0], height: v[1] }; }
};
function __call(name, args) {
  __cmds = []; __acts = [];
  var f = __global[name];
  if (typeof f !== 'function') return [null, []];
  f.apply(null, args);
  return [__cmds, __acts];
}
function __frame(name, w, h, top, player, changes, t, cached, extra) {
  g.width = w; g.height = h; g.top = top; g.player = player;
  for (var k in changes) __song[k] = changes[k];
  __cached = cached;
  var args = [g, __song, t];
  if (extra) args = extra.concat([__song]);
  return __call(name, args);
}
'''


class SketchError(Exception):
    def __init__(self, text, line=0):
        super().__init__(text)
        self.text, self.line = text, line


class Runtime:
    """One sketch.js in its sealed box."""

    def __init__(self, source, host):
        from PyQt6.QtQml import QJSEngine
        self.js = QJSEngine()                   # no installExtensions: no console, no timers, no files, no network
        self.host = host
        QJSEngine.setObjectOwnership(host, QJSEngine.ObjectOwnership.CppOwnership)   # Drift keeps it, not JS
        self.js.globalObject().setProperty('__host', self.js.newQObject(host))
        self.strikes = 0
        self._run(lambda: self.js.evaluate(PRELUDE, 'drift-prelude.js'))
        self._run(lambda: self.js.evaluate(source, 'sketch.js'))
        glob = self.js.globalObject()
        self.fps = max(0, min(MAX_FPS, int(glob.property('fps').toNumber() or 0))) if glob.hasProperty('fps') else 0
        self.has = {n: glob.property(n).isCallable() for n in
                    ('draw', 'room', 'press', 'drag', 'release', 'hover', 'wheel', 'doubleClick')}

    def _run(self, fn):
        """fn() with the watchdog; a JS error or a stopped call becomes SketchError."""
        stopped = threading.Event()

        def stop():
            stopped.set()
            self.js.setInterrupted(True)
        dog = threading.Timer(WATCHDOG, stop)
        dog.daemon = True
        dog.start()
        try:
            v = fn()
        finally:
            dog.cancel()
            if self.js.isInterrupted():
                self.js.setInterrupted(False)
        if stopped.is_set():
            self.strikes += 1
            raise SketchError(f'sketch.js took longer than {int(WATCHDOG * 1000)} ms and was stopped. '
                              'Draw less in one picture, or keep what does not change in g.layer().')
        if v.isError():
            line = v.property('lineNumber').toInt() if v.hasProperty('lineNumber') else 0
            raise SketchError(v.toString(), line)
        return v

    def call(self, name, w, h, top, player, changes, t, cached, extra=None):
        """-> (commands, actions) of one function of the sketch."""
        js = self.js
        # evaluate(), not QJSValue.call(): only evaluate lets go of Python while it runs, so the watchdog can stop it.
        # Both ways as JSON text: plain JS arrays are ~10x faster for the script than wrapped Python lists, and one
        # string back is faster than converting every command.
        js.globalObject().setProperty('__args', json.dumps([name, w, h, top, player, changes, t, cached, extra]))
        v = self._run(lambda: js.evaluate('JSON.stringify(__frame.apply(null, JSON.parse(__args)))'))
        try:
            out = json.loads(v.toString()) or [None, []]
        except ValueError:
            out = [None, []]
        return out[0], out[1] or []


# ---- painting the commands ----------------------------------------------------------------------------------------
_BLEND = {'normal': QPainter.CompositionMode.CompositionMode_SourceOver,
          'multiply': QPainter.CompositionMode.CompositionMode_Multiply,
          'screen': QPainter.CompositionMode.CompositionMode_Screen,
          'overlay': QPainter.CompositionMode.CompositionMode_Overlay,
          'add': QPainter.CompositionMode.CompositionMode_Plus,
          'darken': QPainter.CompositionMode.CompositionMode_Darken,
          'lighten': QPainter.CompositionMode.CompositionMode_Lighten,
          'soft-light': QPainter.CompositionMode.CompositionMode_SoftLight,
          'color-dodge': QPainter.CompositionMode.CompositionMode_ColorDodge,
          'difference': QPainter.CompositionMode.CompositionMode_Difference}
_colors = {}


def color(c):
    """'#rgb', '#rrggbb', '#rrggbbaa' (CSS order), 'rgba(r,g,b,a)' or a colour name -> QColor."""
    if c is None:
        return None
    q = _colors.get(c)
    if q is None:
        s = str(c).strip()
        if s.startswith('#') and len(s) == 9:
            q = QColor(s[:7])
            q.setAlpha(int(s[7:9], 16))
        elif s.startswith('rgb'):
            try:
                parts = [float(v) for v in s[s.index('(') + 1:s.rindex(')')].split(',')]
                q = QColor(int(parts[0]), int(parts[1]), int(parts[2]))
                q.setAlphaF(max(0.0, min(1.0, parts[3])) if len(parts) > 3 else 1.0)
            except (ValueError, IndexError):
                q = QColor()
        else:
            q = QColor(s)
        if not q.isValid():
            q = QColor(255, 0, 255)       # a wrong colour shows, loudly, instead of failing
        if len(_colors) > 2000:
            _colors.clear()
        _colors[c] = q
    return q


def smooth_path(pts, closed):
    """A curve through points (Catmull-Rom as cubic Béziers)."""
    path = QPainterPath()
    P = [QPointF(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
    if len(P) < 2:
        return path
    path.moveTo(P[0])
    n = len(P)
    rng = range(n if closed else n - 1)
    for i in rng:
        p0 = P[(i - 1) % n] if closed or i > 0 else P[i]
        p1, p2 = P[i], P[(i + 1) % n]
        p3 = P[(i + 2) % n] if closed or i + 2 < n else p2
        path.cubicTo(p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2)
    if closed:
        path.closeSubpath()
    return path


def svg_path(d):
    """A small SVG path reader: M L H V C Q Z (upper case absolute, lower case relative)."""
    import re
    path = QPainterPath()
    tokens = re.findall(r'[MLHVCQZmlhvcqz]|-?\d*\.?\d+(?:e-?\d+)?', d[:20000])
    i, cmd, x, y = 0, 'M', 0.0, 0.0

    def num():
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in 'Zz':
                path.closeSubpath()
                continue
        rel = cmd.islower()
        C = cmd.upper()
        try:
            if C in 'ML':
                nx, ny = num(), num()
                x, y = (x + nx, y + ny) if rel else (nx, ny)
                (path.moveTo if C == 'M' else path.lineTo)(x, y)
                if C == 'M':
                    cmd = 'l' if rel else 'L'
            elif C == 'H':
                nx = num()
                x = x + nx if rel else nx
                path.lineTo(x, y)
            elif C == 'V':
                ny = num()
                y = y + ny if rel else ny
                path.lineTo(x, y)
            elif C == 'C':
                v = [num() for _ in range(6)]
                if rel:
                    v = [v[k] + (x if k % 2 == 0 else y) for k in range(6)]
                path.cubicTo(v[0], v[1], v[2], v[3], v[4], v[5])
                x, y = v[4], v[5]
            elif C == 'Q':
                v = [num() for _ in range(4)]
                if rel:
                    v = [v[k] + (x if k % 2 == 0 else y) for k in range(4)]
                path.quadTo(v[0], v[1], v[2], v[3])
                x, y = v[2], v[3]
            else:
                i += 1
        except (IndexError, ValueError):
            break
    return path


class Painter:
    """Plays the commands of one picture onto a QPainter."""

    def __init__(self, stage):
        self.stage = stage
        self.layers = {}        # key -> QPixmap (the size of the canvas)
        self.images = {}        # (name, w, h, fit, radius, ratio) -> QPixmap
        self.cover = None       # the current song's cover (QImage) or None

    def forget(self):
        self.layers.clear()
        self.images.clear()

    def font(self, size, family, weight, italic, spacing, caps):
        if family == 'title':
            f = theme.serif(size, int(weight), italic)
        elif family == 'caps':
            f = theme.caps(size, int(weight), spacing)
        else:
            f = QFont()
            if family not in ('body', ''):
                fam = self.stage.font_file(family) if family.lower().endswith(FONTS) else family
                f.setFamilies([fam, theme.FAMILY] if fam else [theme.FAMILY])
            else:
                f.setFamilies([theme.FAMILY])
            f.setPointSizeF(max(1.0, size))
            f.setWeight(QFont.Weight(max(100, min(900, int(weight)))))
            f.setItalic(italic)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        if caps:
            f.setCapitalization(QFont.Capitalization.AllUppercase)
        return f

    def image(self, name):
        """The QImage a name means: 'cover' (the song's cover) or a picture file in the sketch's folder."""
        if name == 'cover':
            return self.cover
        return self.stage.picture(name)

    def scaled(self, name, w, h, fit, radius, ratio):
        key = (name, int(w), int(h), fit, int(radius), ratio, id(self.cover) if name == 'cover' else 0)
        px = self.images.get(key)
        if px is None:
            img = self.image(name)
            if img is None or img.isNull() or w < 1 or h < 1:
                return None
            W, H = max(1, int(w * ratio)), max(1, int(h * ratio))
            if fit == 'fill':
                img = img.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            elif fit == 'contain':
                img = img.scaled(W, H, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            else:
                img = img.scaled(W, H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
                img = img.copy((img.width() - W) // 2, (img.height() - H) // 2, W, H)
            px = QPixmap.fromImage(img)
            if radius > 0:
                out = QPixmap(px.size())
                out.fill(Qt.GlobalColor.transparent)
                q = QPainter(out)
                q.setRenderHint(QPainter.RenderHint.Antialiasing)
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(0, 0, px.width(), px.height()), radius * ratio, radius * ratio)
                q.setClipPath(clip)
                q.drawPixmap(0, 0, px)
                q.end()
                px = out
            px.setDevicePixelRatio(ratio)
            if len(self.images) > 32:
                self.images.pop(next(iter(self.images)))
            self.images[key] = px
        return px

    def play(self, p, cmds, size, ratio):
        brush, pen = QColor('#ffffff'), None
        st = []
        base = p.transform()          # layers are in page coordinates: drawn back under this, not the sketch's moves
        for c in cmds or ():
            if not c:
                continue
            op = c[0]
            try:
                if op == 'fill':
                    brush = color(c[1])
                elif op == 'stroke':
                    if c[1] is None or c[2] <= 0:
                        pen = None
                    else:
                        pen = QPen(color(c[1]), c[2])
                        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                elif op in ('lin', 'rad'):
                    grad = QLinearGradient(c[1], c[2], c[3], c[4]) if op == 'lin' else QRadialGradient(c[1], c[2], max(0.1, c[3]))
                    for pos, col in c[-1]:
                        grad.setColorAt(max(0.0, min(1.0, pos)), color(col))
                    brush = grad
                elif op == 'clear':
                    p.save()
                    p.resetTransform()
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    p.fillRect(QRectF(0, 0, size.width(), size.height()), color(c[1] or '#00000000'))
                    p.restore()
                elif op in ('rect', 'ell', 'line', 'poly', 'curve', 'arc', 'path'):
                    self._shape(p, op, c, brush, pen)
                elif op == 'text':
                    self._text(p, c, brush, pen)
                elif op == 'img':
                    px = self.scaled(c[1], c[4], c[5], c[7], c[8], ratio)
                    if px is not None:
                        o = p.opacity()
                        p.setOpacity(o * max(0.0, min(1.0, c[6])))
                        p.drawPixmap(QRectF(c[2], c[3], c[4], c[5]), px, QRectF(0, 0, px.width(), px.height()))
                        p.setOpacity(o)
                elif op == 'tex':
                    img = self.image(c[1])
                    if img is not None and not img.isNull():
                        o = p.opacity()
                        p.setOpacity(o * max(0.0, min(1.0, c[2])))
                        px = self.scaled(c[1], img.width(), img.height(), 'fill', 0, 1.0)
                        p.drawTiledPixmap(QRectF(0, 0, size.width(), size.height()), px)
                        p.setOpacity(o)
                elif op == 'save':
                    p.save()
                    st.append((brush, pen))
                elif op == 'restore':
                    if st:
                        p.restore()
                        brush, pen = st.pop()
                elif op == 'tr':
                    p.translate(c[1], c[2])
                elif op == 'rot':
                    p.rotate(c[1])
                elif op == 'sc':
                    p.scale(c[1], c[2])
                elif op == 'alpha':
                    p.setOpacity(max(0.0, min(1.0, c[1])))
                elif op == 'blend':
                    p.setCompositionMode(_BLEND.get(c[1], _BLEND['normal']))
                elif op == 'clip':
                    path = QPainterPath()
                    path.addRoundedRect(QRectF(c[1], c[2], c[3], c[4]), c[5], c[5])
                    p.setClipPath(path, Qt.ClipOperation.IntersectClip)
                elif op == 'layer':
                    px = QPixmap(QSize(max(1, int(size.width() * ratio)), max(1, int(size.height() * ratio))))
                    px.setDevicePixelRatio(ratio)
                    px.fill(Qt.GlobalColor.transparent)
                    q = QPainter(px)
                    q.setRenderHint(QPainter.RenderHint.Antialiasing)
                    q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    self.play(q, c[2], size, ratio)
                    q.end()
                    if len(self.layers) >= MAX_LAYERS:
                        self.layers.pop(next(iter(self.layers)))
                    self.layers[c[1]] = px
                    self._layer(p, px, base, size)
                elif op == 'use':
                    px = self.layers.get(c[1])
                    if px is not None:
                        self._layer(p, px, base, size)
            except (TypeError, IndexError, ValueError):
                continue            # one malformed step is skipped, the picture goes on
        while st:
            p.restore()
            st.pop()

    @staticmethod
    def _layer(p, px, base, size):
        p.save()
        p.setTransform(base)
        p.drawPixmap(QRectF(0, 0, size.width(), size.height()), px, QRectF(0, 0, px.width(), px.height()))
        p.restore()

    @staticmethod
    def _shape(p, op, c, brush, pen):
        p.setPen(pen if pen is not None else Qt.PenStyle.NoPen)
        p.setBrush(brush if brush is not None and op not in ('line',) else Qt.BrushStyle.NoBrush)
        if op == 'rect':
            r = QRectF(c[1], c[2], c[3], c[4])
            if c[5] > 0:
                p.drawRoundedRect(r, c[5], c[5])
            else:
                p.drawRect(r)
        elif op == 'ell':
            p.drawEllipse(QPointF(c[1], c[2]), c[3], c[4])
        elif op == 'line':
            if pen is not None:
                p.drawLine(QPointF(c[1], c[2]), QPointF(c[3], c[4]))
        elif op in ('poly', 'curve'):
            pts, closed = c[1], c[2]
            if op == 'curve':
                path = smooth_path(pts, closed)
            else:
                path = QPainterPath()
                if len(pts) >= 2:
                    path.moveTo(pts[0], pts[1])
                    for i in range(2, len(pts) - 1, 2):
                        path.lineTo(pts[i], pts[i + 1])
                    if closed:
                        path.closeSubpath()
            if not closed:
                p.setBrush(Qt.BrushStyle.NoBrush)        # an open line is only stroked
            p.drawPath(path)
        elif op == 'arc':
            r = QRectF(c[1] - c[3], c[2] - c[3], 2 * c[3], 2 * c[3])
            path = QPainterPath()
            path.arcMoveTo(r, -c[4])
            path.arcTo(r, -c[4], -(c[5] - c[4]))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        elif op == 'path':
            p.drawPath(svg_path(c[1]))

    def _text(self, p, c, brush, pen):
        _op, s, x, y, size, fam, weight, italic, align, base, width, spacing, caps = c
        f = self.font(size, fam, weight, italic, spacing, caps)
        fm = QFontMetricsF(f)
        if width > 0:
            s = fm.elidedText(s, Qt.TextElideMode.ElideRight, width)
        w = fm.horizontalAdvance(s.upper() if caps else s)
        if align == 'center':
            x -= w / 2
        elif align == 'right':
            x -= w
        if base == 'top':
            y += fm.ascent()
        elif base == 'middle':
            y += (fm.ascent() - fm.descent()) / 2
        elif base == 'bottom':
            y -= fm.descent()
        p.setFont(f)
        col = brush if isinstance(brush, QColor) else QColor('#ffffff')
        if pen is not None:
            path = QPainterPath()
            path.addText(QPointF(x, y), f, s)
            p.setPen(pen)
            p.setBrush(col if brush is not None else Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            p.setPen(col)
            p.drawText(QPointF(x, y), s)


# ---- the script's view of Drift -----------------------------------------------------------------------------------
from PyQt6.QtCore import QObject, pyqtSlot  # noqa: E402


class Host(QObject):
    """The only things the script can ask synchronously: how wide a text is, how big a picture is."""

    def __init__(self, painter, parent=None):
        super().__init__(parent)
        self.painter = painter

    @pyqtSlot(str, float, str, float, bool, float, bool, result=float)
    def measure(self, s, size, family, weight, italic, spacing, caps):
        f = self.painter.font(size, family, weight, italic, spacing, caps)
        return QFontMetricsF(f).horizontalAdvance(s[:2000].upper() if caps else s[:2000])

    @pyqtSlot(str, result='QVariantList')
    def imageSize(self, name):
        img = self.painter.image(name)
        return [0, 0] if img is None or img.isNull() else [img.width(), img.height()]


COLOR_NAMES = {'background': 'sky_top', 'surface': 'sky_mid', 'glow': 'glow', 'soft': 'far', 'mid': 'mid',
               'dark': 'near', 'lit': 'lit', 'water': 'lake_bottom', 'shallow': 'lake_top', 'accent': 'acc',
               'light': 'star'}


def song_colors(pal):
    return {k: QColor(getattr(pal, v)).name() for k, v in COLOR_NAMES.items()}


class SketchCanvas(QWidget):
    """The Now page of a canvas sketch: asks sketch.js for a picture when something changes (or fps times a second
    while it moves), paints it, passes the mouse on."""

    def __init__(self, win, stage):
        super().__init__()
        self.win, self.engine, self.stage = win, win.engine, stage
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.calm = False
        self.started = time.monotonic()
        self.painter = stage.painter
        self.rt = stage.runtime()
        self.error = stage.error
        self.cmds = None
        self.dirty = {}               # song fields that changed since the script last saw them
        self.sent = {}
        self.pressed = False
        self.light_top = False        # the room is light under Drift's menu: the window writes it in dark ink
        self.judge_size = None
        self.uid = None
        self.lines = []
        self.timer = QTimer(self, timeout=self._tick)
        e = self.engine
        e.track_changed.connect(lambda _t: self.refresh())
        e.queue_changed.connect(self._queue)
        e.position_changed.connect(self._position)
        e.wave_ready.connect(lambda uid, w: self._wave(uid, w))
        e.track_updated.connect(lambda t: self._liked(t))
        e.playing_changed.connect(lambda on: self._change(playing=bool(on)))
        theme.scene.changed.connect(self._palette)
        from ..covers import shared as covers
        from ..lyricsfeed import shared as lyrics
        covers().ready.connect(self._cover)
        lyrics().ready.connect(self._lyrics)
        self.refresh()

    # ---- what the script knows --------------------------------------------------------------------------------
    def _change(self, **kw):
        for k, v in kw.items():
            if self.sent.get(k, object()) != v:
                self.dirty[k] = v
        if self.dirty:
            self._ask()

    def refresh(self):
        e = self.engine
        t = e.current
        new = (t.uid if t else None) != self.uid
        self.uid = t.uid if t else None
        from ..covers import shared as covers
        if new:
            self.painter.cover = covers().image(t) if t else None
            self.painter.images.clear()
            self.lines = []
            if t is not None and self.win.store.get('whisper', True):
                from ..lyricsfeed import shared as lyrics
                feed = lyrics()
                self.lines = (feed.lines(t) if e.online or feed.known(t) else None) or []
        uid, wave = e.wave
        shape = [v / 255.0 for v in wave] if t and uid == t.uid and wave else []
        from ..widgets import best_part
        best = best_part(list(wave)) if shape else None
        self._change(has=t is not None, title=(t.title if t else '') or '', artist=(t.artist if t else '') or '',
                     album=(getattr(t, 'album', '') if t else '') or '', liked=bool(t and t.liked),
                     cover='cover' if self.painter.cover is not None else '', shape=shape,
                     best=-1 if best is None else float(best), calm=self.calm, playing=bool(e.playing),
                     colors=song_colors(theme.scene.pal), night=float(theme.scene.pal.night),
                     next=self._next(), **self._clock(e.position, e.duration))

    def _next(self):
        e = self.engine
        return [{'title': x.title or '', 'artist': x.artist or ''} for x in list(e.queue or [])[:5]]

    def _clock(self, ms, dur):
        from ..lyricsfeed import line_at
        k = line_at(self.lines, ms) if self.lines else -1
        line = self.lines[k][1] if k >= 0 else ''
        nxt = self.lines[k + 1][1] if 0 <= k + 1 < len(self.lines) else ''
        return {'position': round(ms / 1000.0, 2), 'duration': round((dur or 0) / 1000.0, 2),
                'progress': max(0.0, min(1.0, ms / dur)) if dur else 0.0, 'line': line or '', 'nextLine': nxt or ''}

    def _position(self, ms, dur):
        if self.isVisible() and not self.pressed:
            self._change(playing=bool(self.engine.playing), **self._clock(ms, dur))

    def _queue(self):
        self._change(next=self._next())

    def _wave(self, uid, wave):
        if uid == self.uid:
            self.refresh()

    def _liked(self, t):
        if t is not None and t.uid == self.uid:
            self._change(liked=bool(t.liked))

    def _cover(self, uid):
        if uid == self.uid:
            self.uid = None              # take it as new: the cover image changes
            self.refresh()

    def _lyrics(self, uid):
        if uid == self.uid:
            self.uid = None
            self.refresh()

    def _palette(self):
        self.stage.room_px = None
        self._change(colors=song_colors(theme.scene.pal), night=float(theme.scene.pal.night))

    def set_calm(self, on):
        if on != self.calm:
            self.calm = on
            self._change(calm=on)

    def let_go(self):
        self.painter.layers.clear()

    def dock_rect(self):
        if self.stage.player == 'hidden':
            return QRect(0, self.height() + 10, self.width(), 1)
        from ..window import STRIP
        return QRect(0, max(0, self.height() - STRIP), self.width(), STRIP)

    def player_rect(self):
        if self.stage.player == 'hidden' or self.calm or self.engine.current is None:
            return None
        r = self.dock_rect()
        return {'x': r.x(), 'y': r.y(), 'width': r.width(), 'height': r.height()}

    # ---- asking the script ------------------------------------------------------------------------------------
    def _ask(self, extra_fn=None, extra=None):
        """Run draw (or an event function) now; repaint if a picture came back."""
        if self.rt is None or not self.isVisible():
            return
        changes, self.dirty = self.dirty, {}
        self.sent.update(changes)
        try:
            cmds, acts = self.rt.call(extra_fn or 'draw', self.width(), self.height(), self._top(), self.player_rect(),
                                      changes, time.monotonic() - self.started, {k: True for k in self.painter.layers},
                                      extra)
        except SketchError as e:
            self._fail(e)
            return
        if extra_fn is not None:
            self._act(acts)
            if cmds:        # an event function may draw too: then that is the picture
                self.cmds = cmds
                self.update()
            elif acts:
                self._ask()
            return
        self.cmds = cmds
        self._act(acts)
        self.update()
        if changes.get('title') is not None or changes.get('colors') is not None or self.judge_size != self.size():
            self._judge()

    def _judge(self):
        """How light the picture is under Drift's menu (drawn small, off screen; only when the song, its colours
        or the size change). A light top gets Drift's words in dark ink."""
        self.judge_size = self.size()
        if not self.cmds or self.width() < 8:
            return
        W, H = self.width(), self.height()
        img = QImage(max(1, W // 8), max(1, H // 8), QImage.Format.Format_RGB32)
        img.fill(theme.scene.pal.sky_top)
        side = Painter(self.stage)
        side.cover = self.painter.cover
        side.layers = dict(self.painter.layers)
        q = QPainter(img)
        q.scale(img.width() / W, img.height() / H)
        side.play(q, self.cmds, self.size(), 0.125)
        q.end()
        rows = max(1, int(self._top() / 8) or 6)
        total, n = 0.0, 0
        for y in range(min(rows, img.height())):
            for x in range(0, img.width(), 2):
                c = img.pixelColor(x, y)
                total += 0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()
                n += 1
        light = n > 0 and total / n > 0.55
        if light != self.light_top:
            self.light_top = light
            self.win._place()

    def _top(self):
        from ..window import TOP
        return 0 if self.calm else TOP

    def _act(self, acts):
        e = self.engine
        for a in acts or ():
            if not a:
                continue
            k = a[0]
            if k == 'seek' and e.duration:
                e.seek(max(0.0, min(1.0, float(a[1]))) * e.duration)
            elif k == 'toggle':
                e.toggle()
            elif k == 'next':
                e.next()
            elif k == 'previous':
                e.previous()
            elif k == 'like' and e.current is not None:
                e.like(e.current)
            elif k == 'lyrics':
                self.win.go('lyrics')
            elif k == 'calm':
                self.win.go('home' if self.calm else 'calm')
            elif k == 'jump':
                q = list(e.queue or [])
                if 0 <= int(a[1]) < len(q):
                    self.win.jump_to(q[int(a[1])])
            elif k == 'cursor':
                shapes = {'hand': Qt.CursorShape.PointingHandCursor, 'arrow': Qt.CursorShape.ArrowCursor,
                          'grab': Qt.CursorShape.OpenHandCursor, 'grabbing': Qt.CursorShape.ClosedHandCursor,
                          'cross': Qt.CursorShape.CrossCursor, 'text': Qt.CursorShape.IBeamCursor}
                self.setCursor(shapes.get(a[1], Qt.CursorShape.ArrowCursor))
            elif k == 'tip':
                self.setToolTip(a[1])
            elif k == 'redraw':
                QTimer.singleShot(0, self._ask)

    def _fail(self, e):
        self.stage.error = self.error = e
        if self.rt is not None and (self.rt.strikes >= STRIKES or 'took longer' not in e.text):
            self.timer.stop()
            if self.rt.strikes >= STRIKES or not self.cmds:
                self.rt = None
        self.update()

    def _tick(self):
        from .vinyl import screen_taken
        if not self.isVisible() or self.window().isMinimized() or screen_taken():
            return
        self._ask()

    def _motion(self):
        fps = self.rt.fps if self.rt is not None else 0
        if fps and self.isVisible():
            self.timer.start(int(1000 / fps))
        else:
            self.timer.stop()

    def showEvent(self, e):
        super().showEvent(e)
        self._motion()
        QTimer.singleShot(0, self._ask)

    def hideEvent(self, e):
        self.timer.stop()
        self.painter.layers.clear()
        super().hideEvent(e)

    def resizeEvent(self, e):
        self.painter.layers.clear()
        super().resizeEvent(e)
        QTimer.singleShot(0, self._ask)

    # ---- painting ---------------------------------------------------------------------------------------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.fillRect(self.rect(), theme.scene.pal.sky_top)
        if self.cmds:
            self.painter.play(p, self.cmds, self.size(), self.devicePixelRatioF())
        if self.error is not None:
            self._paint_error(p)
        p.end()

    def _paint_error(self, p):
        e = self.error
        w = min(560, self.width() - 48)
        r = QRectF((self.width() - w) / 2, max(70, self.height() * 0.3), w, 150)
        p.resetTransform()
        p.setOpacity(1.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 10, 16, 230))
        p.drawRoundedRect(r, 14, 14)
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.serif(15, 600))
        where = f'  ·  sketch.js line {e.line}' if e.line else ''
        p.drawText(r.adjusted(20, 16, -20, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   f'This sketch has a problem{where}')
        p.setFont(QFont(theme.FAMILY, 10))
        p.setPen(QColor(theme.MUTED))
        p.drawText(r.adjusted(20, 48, -20, -14), Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop,
                   f'{e.text}\n\nFix sketch.js and save: Drift tries again at once.')

    # ---- the mouse --------------------------------------------------------------------------------------------
    def _event(self, name, *args):
        if self.rt is not None and self.rt.has.get(name):
            self._ask(name, [float(a) for a in args])
            return True
        return False

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.pressed = True
            self._event('press', e.position().x(), e.position().y())

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        self._event('drag' if self.pressed else 'hover', x, y)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.pressed = False
            self._event('release', e.position().x(), e.position().y())

    def mouseDoubleClickEvent(self, e):
        if not self._event('doubleClick', e.position().x(), e.position().y()):
            self.win.go('home' if self.calm else 'calm')          # the default: calm view

    def wheelEvent(self, e):
        if not self._event('wheel', e.angleDelta().y() / 120.0):
            super().wheelEvent(e)


class CanvasStage(Stage):
    """A room drawn by sketch.js (see the module text)."""
    id = 'canvas'
    title = 'Canvas'
    blurb = 'A room you draw yourself.'
    KNOBS = ()
    now_dock = 'strip'

    def __init__(self, sketch):
        super().__init__(sketch)
        ex = getattr(sketch, 'extra', {}) or {}
        self.player = ex.get('player', 'bottom')
        words = ex.get('words') or {}
        self.tabs = dict(Stage.tabs, **{k: v for k, v in words.items() if k in TABS})
        self.words = {k: v for k, v in words.items() if k in ('library', 'liked', 'queue', 'explore', 'playlists')}
        self.painter = Painter(self)
        self.error = None
        self._rt = None
        self._pictures = {}
        self.room_px = None

    # ---- the files of the sketch ------------------------------------------------------------------------------
    def script_path(self):
        folder = getattr(self.sketch, 'folder', None)
        name = (getattr(self.sketch, 'extra', {}) or {}).get('script', 'sketch.js')
        p = folder / name if folder is not None else None
        return p if p is not None and p.is_file() else None

    def runtime(self):
        """The sketch's script, started once (None + self.error when it cannot run)."""
        if self._rt is None and self.error is None:
            path = self.script_path()
            try:
                if path is None:
                    raise SketchError('This sketch has no sketch.js.')
                if path.stat().st_size > SCRIPT_MAX:
                    raise SketchError('sketch.js is bigger than 512 KB.')
                src = path.read_text(encoding='utf-8', errors='replace')
                self._rt = Runtime(src, Host(self.painter))
            except SketchError as e:
                self.error = e
            except OSError as e:
                self.error = SketchError(f'sketch.js could not be read: {e}')
        return self._rt

    def picture(self, name):
        """A picture file of the sketch's folder by its file name (nothing outside the folder), loaded once."""
        if name in self._pictures:
            return self._pictures[name]
        img = None
        folder = getattr(self.sketch, 'folder', None)
        import re
        from pathlib import Path
        if folder is not None and re.fullmatch(r'[\w\- .]{1,80}', name) and '..' not in name and \
                Path(name).suffix.lower() in IMAGES:
            p = folder / name
            if p.is_file() and p.stat().st_size <= 8 * 2**20:
                img = QImage(str(p))
                img = None if img.isNull() else img
        if len(self._pictures) > 24:
            self._pictures.clear()
        self._pictures[name] = img
        return img

    def font_file(self, name):
        from . import font_family

        class _One:
            def __init__(s, stage, n):
                s.stage, s.n = stage, n

            def asset(s, _k):
                folder = getattr(s.stage.sketch, 'folder', None)
                import re
                if folder is None or not re.fullmatch(r'[\w\- .]{1,80}', s.n) or '..' in s.n:
                    return None
                p = folder / s.n
                return p if p.is_file() else None
        return font_family(_One(self, name)) or ''

    # ---- building ---------------------------------------------------------------------------------------------
    def now_page(self, win):
        return SketchCanvas(win, self)

    # ---- painting the rest of the room ------------------------------------------------------------------------
    def paint_room(self, p, widget, rect):
        pal = theme.scene.pal
        rt = self.runtime()
        if rt is not None and rt.has.get('room'):
            size = widget.size()
            ratio = widget.devicePixelRatioF()
            key = (size.width(), size.height(), ratio, id(pal))
            if self.room_px is None or self.room_px[0] != key:
                px = QPixmap(QSize(max(1, int(size.width() * ratio)), max(1, int(size.height() * ratio))))
                px.setDevicePixelRatio(ratio)
                px.fill(pal.sky_top)
                try:
                    cmds, _a = rt.call('room', size.width(), size.height(), 48, None,
                                       {'colors': song_colors(pal), 'night': float(pal.night)}, 0.0, {})
                    q = QPainter(px)
                    q.setRenderHint(QPainter.RenderHint.Antialiasing)
                    Painter(self).play(q, cmds, size, ratio)
                    q.end()
                except SketchError:
                    pass
                self.room_px = (key, px)
            p.drawPixmap(QRectF(rect), self.room_px[1], QRectF(rect.x() * ratio, rect.y() * ratio, rect.width() * ratio,
                                                       rect.height() * ratio))
        else:
            p.fillRect(rect, pal.sky_top)
        self.paint_texture(p, rect)

    def paint_top(self, p, widget, solid):
        if not solid:
            return
        c0 = QColor(theme.scene.pal.sky_top)
        c1 = QColor(c0)
        c1.setAlphaF(0.0)
        g = QLinearGradient(0, 0, 0, widget.height())
        g.setColorAt(0.0, c0)
        g.setColorAt(0.7, c0)
        g.setColorAt(1.0, c1)
        p.fillRect(widget.rect(), g)

    def paint_dock(self, p, dock):
        c1 = QColor(theme.scene.pal.sky_top)
        c0 = QColor(c1)
        c0.setAlphaF(0.0)
        g = QLinearGradient(0, 0, 0, dock.height())
        g.setColorAt(0.0, c0)
        g.setColorAt(0.42, c1)
        g.setColorAt(1.0, c1)
        p.fillRect(dock.rect(), g)

    @classmethod
    def thumbnail(cls, knobs, w, h, folder=None, sketch=None):
        """The sketch's own drawing, at card size, with a made-up song (so every sketch is judged alike)."""
        ratio = 2.0
        px = QPixmap(int(w * ratio), int(h * ratio))
        px.setDevicePixelRatio(ratio)
        from .landscape import _demo_palette
        pal = _demo_palette()
        px.fill(pal.sky_top)
        if sketch is None:
            return px
        stage = cls(sketch)
        img = QImage(64, 64, QImage.Format.Format_RGB32)
        q = QPainter(img)
        grad = QLinearGradient(0, 0, 64, 64)
        grad.setColorAt(0, QColor('#ff7a3d'))
        grad.setColorAt(1, QColor('#5a1e6e'))
        q.fillRect(img.rect(), grad)
        q.end()
        stage.painter.cover = img
        import math
        shape = [0.35 + 0.45 * abs(math.sin(i / 9.0)) * (0.6 + 0.4 * math.sin(i / 23.0)) for i in range(160)]
        song = {'has': True, 'title': 'Harbour Lights', 'artist': 'The Low Tides', 'album': '', 'liked': False, 'cover': 'cover',
                'shape': shape, 'best': 0.62, 'calm': False, 'playing': True, 'colors': song_colors(pal),
                'night': float(pal.night), 'next': [{'title': 'Copper Sky', 'artist': 'Mira Vale'}],
                'position': 96.0, 'duration': 243.0, 'progress': 0.4, 'line': 'Waiting in a car', 'nextLine': ''}
        rt = stage.runtime()
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if rt is not None and rt.has.get('draw'):
            W, H = 1280, 800                         # drawn as a real window, then shrunk to the card
            try:
                cmds, _a = rt.call('draw', W, H, 48, None, song, 1.0, {})
                p.scale(w / W, h / H)
                stage.painter.play(p, cmds, QSize(W, H), ratio * w / W)
            except SketchError:
                pass
        p.end()
        return px
