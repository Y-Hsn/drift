"""Sketches: the themes of Drift. Drift is the museum; a sketch is one of its rooms.

Drift ships Sunset, Vinyl and Paper Theatre. Each is built on a *stage*:
a whole layout made by Drift, in code. A sketch someone makes is a folder of choices and their own art, never code:
which stage it dresses, its colours, pictures, a typeface file, a texture. Because a sketch is only data it is safe
to share: importing one can never run anything.

    sketch.json   {"drift_sketch": 1, "name": "Night Wax", "by": "you", "about": "...", "stage": "vinyl",
                   "knobs": {"record": "smoke", "wall": "wall.jpg", "font": "Title.otf"}}
    wall.jpg      the files the knobs name sit beside it (pictures, one font)

Built-in sketches live in this file; your own live in DATA/sketches/<id>/. A .driftsketch file is that folder zipped.
new() starts a blank one (every knob written out at its default); the window redraws while you edit it.
The artist guide (guide.html) explains how to make one. New stages are Python modules that subclass Stage."""
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPixmap

from ...store import DATA
from .. import theme

FORMAT = 1
EXT = '.driftsketch'
FOLDER = DATA / 'sketches'
IMAGES = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
FONTS = ('.ttf', '.otf')
ALIASES = {'landscape': 'sunset'}   # ids from before Sunset had its name
MAX_IMAGE = 8 * 2**20         # one picture
MAX_FILE = 24 * 2**20         # a whole .driftsketch
MAX_FILES = 24
SCRIPT_MAX = 512 * 1024       # a sketch.js


@dataclass(frozen=True)
class Knob:
    """One choice a sketch makes. kind: 'choice' (one of options), 'toggle', 'color' ('' = from the cover) or
    'image' (a picture file in the sketch's folder, '' = none) or 'font' (a .ttf/.otf there, '' = none)."""
    key: str
    label: str
    kind: str
    default: object = ''
    options: tuple = ()        # ((value, label), ...) for 'choice'
    hint: str = ''

    def clean(self, value):
        """The value if it is a valid one for this knob, else the default (an imported sketch can hold anything)."""
        if self.kind == 'choice':
            return value if value in [v for v, _ in self.options] else self.default
        if self.kind == 'toggle':
            return value if isinstance(value, bool) else self.default
        if self.kind == 'color':
            return value if isinstance(value, str) and (value == '' or (re.fullmatch(r'#[0-9a-fA-F]{6}', value))) \
                else self.default
        if self.kind in ('image', 'font'):
            kinds = IMAGES if self.kind == 'image' else FONTS
            ok = isinstance(value, str) and re.fullmatch(r'[\w\- .]{1,80}', value or '') and \
                Path(value).suffix.lower() in kinds and '..' not in value
            return value if ok else ''
        return self.default

    @property
    def is_file(self):
        return self.kind in ('image', 'font')


# Every stage has these: the mood of the colours, a fixed accent, the typeface of titles (or your own font file)
# and a texture laid over the whole room.
SHARED = (
    Knob('mood', 'Colours', 'choice', 'cover', (('cover', 'From each cover'), ('mono', 'Black and white'),
                                                ('warm', 'Warm'), ('cool', 'Cool'))),
    Knob('accent', 'Accent', 'color', '', hint='Empty: the accent comes from each cover'),
    Knob('type', 'Titles', 'choice', 'Sitka', (('Sitka', 'Gallery serif'), ('Constantia', 'Bookish'),
                                               ('Bahnschrift', 'Modernist'), ('Courier New', 'Typewriter'))),
    Knob('font', 'Title font file', 'font', '', hint='A .ttf/.otf in the folder; used instead of "type"'),
    Knob('texture', 'Texture', 'image', '', hint='A small picture tiled over the room (paper, canvas, film)'),
    Knob('texture_strength', 'Texture strength', 'choice', 'medium',
         (('light', 'Light'), ('medium', 'Medium'), ('strong', 'Strong'))),
)
TEXTURE = {'light': 0.10, 'medium': 0.2, 'strong': 0.36}


class Stage:
    """A whole layout of Drift. The window asks its stage for: the Now page, how song lists look, the ground of every
    page, the top strip, the player strip, the small mark under the open tab, and the words of the index. Everything
    else (search, the engine, keys, menus) is the same app for every stage."""
    id = ''
    title = ''
    blurb = ''
    KNOBS = ()
    tabs = {'home': 'Now', 'explore': 'Explore', 'library': 'Library', 'liked': 'Liked', 'queue': 'Queue',
            'playlists': 'Lists'}
    words = {}                 # page headings: 'library', 'liked', 'queue', 'explore', 'playlists'
    seek_style = 'shore'       # the player strip's seek bar: 'shore' (hills) or 'groove' (a record's groove)
    now_dock = 'lake'          # the player on Now: 'lake' (controls only, placed by the page) or 'strip'

    def __init__(self, sketch):
        self.sketch = sketch
        self.k = sketch.full()

    @classmethod
    def knobs(cls):
        return tuple(cls.KNOBS) + SHARED

    def word(self, key, default):
        return self.words.get(key, default)

    def asset(self, key):
        """The file a knob names, as a path, or None."""
        name = self.k.get(key)
        if not name or getattr(self.sketch, 'folder', None) is None:
            return None
        p = self.sketch.folder / name
        return p if p.is_file() else None

    def texture(self):
        """(tile pixmap, opacity) of the sketch's texture, or None. A tile is kept to 512 px at most."""
        path = self.asset('texture')
        if path is None:
            return None
        if getattr(self, '_tex', (None,))[0] != path:
            from PyQt6.QtGui import QImage
            img = QImage(str(path))
            if img.isNull():
                self._tex = (path, None)
            else:
                if max(img.width(), img.height()) > 512:
                    img = img.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                self._tex = (path, QPixmap.fromImage(img))
        px = self._tex[1]
        return None if px is None else (px, TEXTURE.get(self.k.get('texture_strength'), 0.2))

    def paint_texture(self, p, rect):
        """The sketch's texture over a rect (nothing when it has none)."""
        t = self.texture()
        if t is None:
            return
        px, a = t
        r = QRectF(rect)
        p.save()
        p.setOpacity(a)
        p.drawTiledPixmap(r, px, QPointF(r.x() % px.width(), r.y() % px.height()))
        p.restore()

    # ---- building ---------------------------------------------------------------------------------------------
    def now_page(self, win):
        raise NotImplementedError

    def track_list(self, numbered=True, grow=True):
        from ..lists import TrackList
        return TrackList(numbered=numbered, grow=grow)

    # ---- colours ----------------------------------------------------------------------------------------------
    def tune(self, pal):
        """A song's palette as this sketch sees it (mood, fixed accent)."""
        mood, acc = self.k.get('mood', 'cover'), self.k.get('accent', '')
        if mood == 'cover' and not acc:
            return pal
        out = {}
        for name in theme.Palette.NAMES:
            c = QColor(getattr(pal, name))
            h, s, l, a = c.hslHueF(), c.hslSaturationF(), c.lightnessF(), c.alphaF()
            if mood == 'mono':
                c = QColor.fromHslF(0.62, min(s, 0.06), l, a)
            elif mood in ('warm', 'cool'):
                target = 0.075 if mood == 'warm' else 0.6
                h = h if h >= 0 else target
                d = ((target - h + 0.5) % 1.0) - 0.5
                c = QColor.fromHslF((h + d * 0.6) % 1.0, min(1.0, s * 0.9 + 0.05), l, a)
            out[name] = c
        if acc:
            out['acc'] = QColor(acc)
            out['star'] = theme.mix(QColor(acc), QColor('#ffffff'), 0.75)
            out['lit'] = theme.mix(out['lit'], QColor(acc), 0.25)
        elif mood == 'mono':
            out['acc'] = QColor('#e8e8ea')
            out['star'] = QColor('#ffffff')
        return theme.Palette(night=pal.night, **out)

    # ---- painting ---------------------------------------------------------------------------------------------
    def paint_room(self, p, widget, rect):
        p.fillRect(rect, theme.scene.pal.sky_top)

    def paint_top(self, p, widget, solid):
        pass

    def paint_dock(self, p, dock):
        pass

    def paint_mark(self, p, rect, color):
        """The small mark under the open tab (default: a small sun)."""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(rect)

    def dock_cover(self, track, size):
        from ..covers import shared as covers
        if track is None:
            return theme.placeholder('drift', size, size // 2)
        return covers().get(track, size, size // 2)

    @classmethod
    def thumbnail(cls, knobs, w, h, folder=None):
        """A small picture of a sketch for the gallery (a made-up song, so every sketch is judged alike)."""
        px = QPixmap(w, h)
        px.fill(QColor('#101014'))
        return px


@dataclass
class Sketch:
    id: str
    name: str
    stage: str
    by: str = ''
    about: str = ''
    knobs: dict = field(default_factory=dict)
    folder: Path | None = None        # where its pictures are (user sketches)
    builtin: bool = False
    extra: dict = field(default_factory=dict)   # canvas sketches: script, words, player

    def stage_cls(self):
        return STAGES.get(ALIASES.get(self.stage, self.stage)) or STAGES['sunset']

    def full(self):
        """Every knob of its stage, with this sketch's values over the defaults (and nothing else)."""
        out = {}
        for k in self.stage_cls().knobs():
            out[k.key] = k.clean(self.knobs.get(k.key, k.default))
        return out

    def make(self):
        return self.stage_cls()(self)

    def manifest(self):
        out = {'drift_sketch': FORMAT, 'id': self.id, 'name': self.name, 'by': self.by, 'about': self.about,
               'stage': self.stage}
        out.update(self.extra)
        out['knobs'] = self.full()
        return out

    def thumbnail(self, w, h):
        cls = self.stage_cls()
        if cls.id == 'canvas':
            return cls.thumbnail(self.full(), w, h, self.folder, sketch=self)
        return cls.thumbnail(self.full(), w, h, self.folder)


def _stages():
    from .canvas import CanvasStage
    from .landscape import SunsetStage
    from .theatre import PaperTheatreStage
    from .vinyl import VinylStage
    return {s.id: s for s in (SunsetStage, VinylStage, PaperTheatreStage, CanvasStage)}


class _Stages(dict):
    """id -> Stage class, filled the first time it is asked (the stages import the UI)."""

    def _fill(self):
        if not dict.__len__(self):
            self.update(_stages())

    def get(self, k, d=None):
        self._fill()
        return dict.get(self, k, d)

    def __getitem__(self, k):
        self._fill()
        return dict.__getitem__(self, k)

    def __contains__(self, k):
        self._fill()
        return dict.__contains__(self, k)

    def values(self):
        self._fill()
        return dict.values(self)


STAGES = _Stages()

BUILTIN = (
    Sketch('sunset', 'Sunset', 'sunset', 'Drift',
           'Every song is a landscape: its loudness is the hills, its cover the sun crossing the sky.', builtin=True),
    Sketch('vinyl', 'Vinyl', 'vinyl', 'Drift',
           'A listening room. Every song is a record: its grooves are the song, the cover is the label.',
           builtin=True),
    Sketch('paper-theatre', 'Paper Theatre', 'paper-theatre', 'Drift',
           'Pull the brass cue rail to move a cut-paper set built around the song’s cover.', builtin=True),
)


def slug(name):
    s = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')[:40]
    return s or 'sketch'


def read(data, folder=None, builtin=False):
    """A Sketch from a manifest (dict), checked: unknown stages, keys and values are refused or dropped."""
    if not isinstance(data, dict) or data.get('drift_sketch') not in (1,):
        raise ValueError('This is not a Drift sketch (or it is from a newer Drift).')
    stage = ALIASES.get(data.get('stage'), data.get('stage'))
    if stage not in STAGES:
        raise ValueError(f'This sketch needs the stage "{stage}", which this Drift does not have yet.')
    name = str(data.get('name') or '').strip()[:48] or 'Untitled sketch'
    sid = slug(str(data.get('id') or name))
    knobs = data.get('knobs') if isinstance(data.get('knobs'), dict) else {}
    s = Sketch(sid, name, stage, str(data.get('by') or '')[:48], str(data.get('about') or '')[:240], {},
               folder, builtin)
    s.knobs = {k.key: k.clean(knobs[k.key]) for k in s.stage_cls().knobs() if k.key in knobs}
    if stage == 'canvas':
        s.extra = _canvas_extra(data)
    return s


TAB_KEYS = ('home', 'explore', 'library', 'liked', 'queue', 'playlists')


def _canvas_extra(data):
    """The canvas part of a manifest, checked: the script's file name, the words of the index, the player."""
    script = data.get('script')
    ok = isinstance(script, str) and re.fullmatch(r'[\w\- .]{1,80}', script) and script.lower().endswith('.js') \
        and '..' not in script
    words = data.get('words') if isinstance(data.get('words'), dict) else {}
    words = {k: str(v).strip()[:24] for k, v in words.items() if k in TAB_KEYS and str(v).strip()}
    player = data.get('player') if data.get('player') in ('bottom', 'hidden') else 'bottom'
    out = {'script': script if ok else 'sketch.js', 'player': player}
    if words:
        out['words'] = words
    return out


def user_sketches():
    out = []
    if not FOLDER.is_dir():
        return out
    for d in sorted(FOLDER.iterdir()):
        f = d / 'sketch.json'
        if d.is_dir() and f.is_file():
            try:
                out.append(read(json.loads(f.read_text(encoding='utf-8')), d))
            except (ValueError, OSError):
                continue          # a broken sketch is skipped, never fatal
    return out


def available():
    return list(BUILTIN) + user_sketches()


def find(sid):
    return next((s for s in available() if s.id == sid), None)


def active(store):
    sid = store.get('sketch', 'sunset')
    return find(ALIASES.get(sid, sid)) or BUILTIN[0]


def _free_id(sid, keep=None):
    taken = {s.id for s in available()} - {keep}
    out, n = sid, 2
    while out in taken:
        out, n = f'{sid}-{n}', n + 1
    return out


def save(sketch, pictures=None, replace=None, files=()):
    """Write a user sketch to DATA/sketches/<id>/. pictures: knob -> a picture file to copy in. files: files copied
    in under their own names (a canvas sketch's script, pictures, font). replace: the id it was saved under before
    (editing keeps its folder). Returns the saved Sketch."""
    sid = _free_id(slug(sketch.id or sketch.name), keep=replace)
    folder = FOLDER / sid
    old = FOLDER / replace if replace else None
    if old and old.is_dir() and old != folder:
        shutil.move(str(old), str(folder))
    folder.mkdir(parents=True, exist_ok=True)
    knobs = dict(sketch.knobs)
    for key, src in (pictures or {}).items():
        src = Path(src)
        if src.is_file() and src.suffix.lower() in IMAGES + FONTS and src.stat().st_size <= MAX_IMAGE:
            name = f'{key}{src.suffix.lower()}'
            if src.resolve() != (folder / name).resolve():
                for stale in folder.glob(f'{key}.*'):
                    stale.unlink(missing_ok=True)
                shutil.copyfile(src, folder / name)
            knobs[key] = name
    for src in files:
        src = Path(src)
        if src.is_file() and src.resolve() != (folder / src.name).resolve():
            shutil.copyfile(src, folder / src.name)
    out = Sketch(sid, sketch.name, sketch.stage, sketch.by, sketch.about, knobs, folder, extra=dict(sketch.extra))
    out.knobs = {k: v for k, v in out.full().items()}
    (folder / 'sketch.json').write_text(json.dumps(out.manifest(), indent=2, ensure_ascii=False), encoding='utf-8')
    _readme()
    return out


def export(sketch, path):
    """sketch -> a .driftsketch file (a zip: sketch.json and its pictures)."""
    path = Path(path)
    if path.suffix.lower() != EXT:
        path = path.with_suffix(EXT)
    knobs = sketch.full()
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('sketch.json', json.dumps(sketch.manifest(), indent=2, ensure_ascii=False))
        done = set()
        if sketch.stage == 'canvas' and sketch.folder is not None:
            for f in _canvas_files(sketch):
                z.write(f, f.name)
                done.add(f.name)
        for k in sketch.stage_cls().knobs():
            name = knobs.get(k.key) if k.is_file else None
            if name and name not in done and sketch.folder is not None and (sketch.folder / name).is_file():
                z.write(sketch.folder / name, name)
                done.add(name)
    return path


def import_file(path):
    """A .driftsketch (or a sketch.json) -> a new user sketch. Only the manifest and the pictures it names are
    taken; sizes are capped. Raises ValueError with a sentence for the person when it cannot be used."""
    path = Path(path)
    if path.stat().st_size > MAX_FILE:
        raise ValueError('That file is too big to be a sketch.')
    pictures = {}
    if path.suffix.lower() == '.json':
        data = json.loads(path.read_text(encoding='utf-8'))
        sketch = read(data)
        for k in sketch.stage_cls().knobs():
            v = sketch.knobs.get(k.key)
            if k.is_file and v and (path.parent / v).is_file() and _real(path.parent / v, k.kind):
                pictures[k.key] = path.parent / v
        images = {k.key for k in sketch.stage_cls().knobs() if k.is_file}
        sketch.knobs = {k: v for k, v in sketch.knobs.items() if k not in images}
        files = _take(sketch, {f.name: f for f in path.parent.iterdir() if f.is_file()},
                      lambda f: f.stat().st_size, lambda f, _tmp: f) if sketch.stage == 'canvas' else []
        return save(sketch, pictures, files=files)
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError('That file is not a Drift sketch.')
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError('That sketch holds too many files.')
        by_name = {Path(i.filename).name: i for i in infos if not i.is_dir()}   # flat, any folder inside ignored
        if 'sketch.json' not in by_name:
            raise ValueError('That file has no sketch.json inside.')
        info = by_name['sketch.json']
        if info.file_size > 256 * 1024:
            raise ValueError('That sketch.json is far too big.')
        try:
            data = json.loads(z.read(info).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError('That sketch.json could not be read.')
        sketch = read(data)
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix='driftsketch-'))
        try:
            for k in sketch.stage_cls().knobs():
                v = sketch.knobs.get(k.key)
                i = by_name.get(v) if k.is_file and v else None
                if i is not None and i.file_size <= MAX_IMAGE:
                    out = tmp / Path(v).name
                    out.write_bytes(z.read(i))
                    if _real(out, k.kind):                # only real pictures and fonts come in
                        pictures[k.key] = out
            images = {k.key for k in sketch.stage_cls().knobs() if k.is_file}
            sketch.knobs = {k: v for k, v in sketch.knobs.items() if k not in images}   # only pictures that came in
            files = []
            if sketch.stage == 'canvas':
                def unpack(i, where):
                    out = where / Path(i.filename).name
                    out.write_bytes(z.read(i))
                    return out
                files = _take(sketch, by_name, lambda i: i.file_size, unpack, tmp)
            return save(sketch, pictures, files=files)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _take(sketch, found, size_of, fetch, tmp=None):
    """A canvas sketch's files out of what an import found (name -> entry): its script (plain text, 512 KB at most)
    and every real picture and font. Anything else is left behind."""
    out = []
    script = sketch.extra.get('script', 'sketch.js')
    for name, entry in sorted(found.items()):
        if name == 'sketch.json' or not re.fullmatch(r'[\w\- .]{1,80}', name) or len(out) >= MAX_FILES:
            continue
        suf = Path(name).suffix.lower()
        if name == script:
            if size_of(entry) > SCRIPT_MAX:
                raise ValueError('That sketch\'s script is bigger than 512 KB.')
            f = fetch(entry, tmp)
            try:
                f.read_bytes().decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('That sketch\'s script is not plain text.')
            out.append(f)
        elif suf in IMAGES + FONTS and size_of(entry) <= MAX_IMAGE:
            f = fetch(entry, tmp)
            if _real(f, 'image' if suf in IMAGES else 'font'):
                out.append(f)
    if not any(f.name == script for f in out):
        raise ValueError('That sketch has no script (sketch.js) inside.')
    return out


def _canvas_files(sketch):
    """The script + every picture and font in a canvas sketch's folder (flat, capped)."""
    out = []
    script = sketch.extra.get('script', 'sketch.js')
    for f in sorted(sketch.folder.iterdir()):
        if not f.is_file() or f.name == 'sketch.json' or len(out) >= MAX_FILES - 1:
            continue
        suf = f.suffix.lower()
        if (f.name == script and f.stat().st_size <= SCRIPT_MAX) or \
                (suf in IMAGES + FONTS and f.stat().st_size <= MAX_IMAGE):
            out.append(f)
    return out


def _real(path, kind):
    """True when a file really is a picture (kind 'image') or a font Windows can use (kind 'font')."""
    if kind == 'image':
        from PyQt6.QtGui import QImage
        return not QImage(str(path)).isNull()
    from PyQt6.QtGui import QFontDatabase
    fid = QFontDatabase.addApplicationFont(str(path))
    if fid < 0:
        return False
    QFontDatabase.removeApplicationFont(fid)
    return True


def new(name):
    """A new sketch of your own: a blank canvas. Its folder gets sketch.json and sketch.js, a short working
    drawing with notes that the artist replaces with their own room (guide.html explains every call)."""
    name = (name or '').strip()[:48] or 'Untitled sketch'
    s = Sketch(slug(name), name, 'canvas', '', '', {},
               extra={'script': 'sketch.js', 'player': 'bottom',
                      'words': {'home': 'Now', 'explore': 'Explore', 'library': 'Library', 'liked': 'Liked',
                                'queue': 'Queue', 'playlists': 'Lists'}})
    out = save(s)
    shutil.copyfile(Path(__file__).with_name('starter.js'), out.folder / 'sketch.js')
    return out


def remove(sketch):
    """A user sketch's folder goes to the Recycle Bin (it can be put back from there)."""
    if sketch.builtin or sketch.folder is None:
        return
    from ... import library
    library.recycle(str(sketch.folder))


def apply(sketch, stage):
    """Make the shared look follow a sketch before its window is built: typeface and colour mood."""
    theme.SERIF = font_family(stage) or sketch.full().get('type') or 'Sitka'
    theme.tuner = stage.tune
    theme.scene.cache.clear()
    pal = stage.tune(theme.DEFAULT_PALETTE)
    theme.scene.pal = theme.scene.start = theme.scene.target = pal
    theme.accent.set(pal.acc)


_fonts = {}


def font_family(stage):
    """The family name of the sketch's own font file (loaded once per file), or ''."""
    path = stage.asset('font')
    if path is None:
        return ''
    key = (str(path), path.stat().st_mtime)
    if key not in _fonts:
        from PyQt6.QtGui import QFontDatabase
        fid = QFontDatabase.addApplicationFont(str(path))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        _fonts[key] = fams[0] if fams else ''
    return _fonts[key]


def _readme():
    """The sketches folder explains itself to whoever opens it: README.md + the artist guide."""
    FOLDER.mkdir(parents=True, exist_ok=True)
    for name in ('README.md', 'guide.html', 'starter.js'):
        src = Path(__file__).with_name(name)
        dst = FOLDER / name
        try:
            if src.is_file() and (not dst.is_file() or dst.read_bytes() != src.read_bytes()):
                shutil.copyfile(src, dst)
        except OSError:
            pass


def open_folder(sketch=None):
    _readme()
    import os
    os.startfile(str(sketch.folder if sketch is not None and sketch.folder else FOLDER))


def open_guide():
    _readme()
    import os
    os.startfile(str(FOLDER / 'guide.html'))
