"""Sketches: reading, checking, saving, export -> import round trip, and files that must be refused.
python -m unittest tests.test_sketches (offline, scratch data folder)"""
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault('DRIFT_DATA', tempfile.mkdtemp())
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from drift.ui import sketches  # noqa: E402
import drift.ui.sketches.canvas  # noqa: E402,F401


def slug_dir(name):
    return sketches.slug(name)


def picture(path):
    img = QImage(40, 30, QImage.Format.Format_RGB32)
    img.fill(QColor('#884422'))
    img.save(str(path))
    return path


class SketchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_builtins_are_sound(self):
        ids = [s.id for s in sketches.BUILTIN]
        self.assertEqual(len(ids), len(set(ids)))
        for s in sketches.BUILTIN:
            full = s.full()
            for k in s.stage_cls().knobs():
                self.assertEqual(k.clean(full[k.key]), full[k.key])
            px = s.thumbnail(120, 80)
            self.assertFalse(px.isNull())

    def test_bad_values_fall_back(self):
        s = sketches.read({'drift_sketch': 1, 'name': 'X', 'stage': 'vinyl',
                           'knobs': {'record': 'gold', 'spin': 'yes', 'accent': 'red', 'wall': '../../evil.png',
                                     'nonsense': 1}})
        full = s.full()
        self.assertEqual(full['record'], 'black')
        self.assertIs(full['spin'], True)
        self.assertEqual(full['accent'], '')
        self.assertEqual(full['wall'], '')
        self.assertNotIn('nonsense', full)

    def test_unknown_stage_refused(self):
        with self.assertRaises(ValueError):
            sketches.read({'drift_sketch': 1, 'name': 'X', 'stage': 'hologram'})
        with self.assertRaises(ValueError):
            sketches.read({'drift_sketch': 9, 'name': 'X', 'stage': 'vinyl'})

    def test_round_trip(self):
        draft = sketches.Sketch('night-wax', 'Night Wax', 'vinyl', 'Tester', 'Smoke and walnut',
                                {'record': 'smoke', 'deck': 'ash', 'mood': 'cool'})
        saved = sketches.save(draft, {'wall': picture(self.tmp / 'art.png')})
        self.assertEqual(saved.full()['wall'], 'wall.png')
        self.assertTrue((saved.folder / 'wall.png').is_file())
        out = sketches.export(saved, self.tmp / 'night')
        self.assertEqual(out.suffix, sketches.EXT)
        with zipfile.ZipFile(out) as z:
            self.assertEqual(sorted(z.namelist()), ['sketch.json', 'wall.png'])
        again = sketches.import_file(out)
        self.assertNotEqual(again.id, saved.id)            # a second copy gets its own id
        self.assertEqual(again.full()['record'], 'smoke')
        self.assertTrue((again.folder / 'wall.png').is_file())
        self.assertIn(again.id, [s.id for s in sketches.available()])
        self.assertFalse(again.thumbnail(100, 64).isNull())

    def test_hostile_zip(self):
        bad = self.tmp / 'bad.driftsketch'
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('sketch.json', json.dumps({'drift_sketch': 1, 'name': 'Trojan', 'stage': 'landscape',
                                                  'knobs': {'grain': 'heavy'}}))
            z.writestr('../../outside.txt', 'no')
            z.writestr('run.py', 'print(1)')
        s = sketches.import_file(bad)
        self.assertEqual(sorted(p.name for p in s.folder.iterdir()), ['sketch.json'])
        self.assertFalse((sketches.FOLDER.parent.parent / 'outside.txt').exists())
        notzip = self.tmp / 'x.driftsketch'
        notzip.write_bytes(b'hello')
        with self.assertRaises(ValueError):
            sketches.import_file(notzip)

    def test_fake_picture_refused(self):
        bad = self.tmp / 'pic.driftsketch'
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('sketch.json', json.dumps({'drift_sketch': 1, 'name': 'Pic', 'stage': 'vinyl',
                                                  'knobs': {'wall': 'wall.png'}}))
            z.writestr('wall.png', b'MZ not a picture')
        s = sketches.import_file(bad)
        self.assertEqual(s.full()['wall'], '')

    def test_builtins_and_old_name(self):
        self.assertEqual([s.id for s in sketches.BUILTIN], ['sunset', 'vinyl', 'paper-theatre'])
        old = sketches.read({'drift_sketch': 1, 'name': 'Old', 'stage': 'landscape'})
        self.assertEqual(old.stage, 'sunset')

        class Store(dict):
            def get(self, k, d=None):
                return dict.get(self, k, d)
        self.assertEqual(sketches.active(Store(sketch='landscape')).id, 'sunset')
        self.assertEqual(sketches.active(Store(sketch='aurora')).id, 'sunset')

    def test_theatre_cues_follow_the_measurement(self):
        from drift.ui.sketches.theatre_set import CueSheet
        flat = CueSheet([80] * 160)
        self.assertEqual(flat.cues, (0, .25, .5, .75))
        measured = CueSheet([15] * 32 + [230] * 59 + [50] * 19 + [180] * 50)
        self.assertLess(abs(measured.cues[1] - .2), .03)
        self.assertEqual(measured.act(.35), 1)
        self.assertNotEqual(measured.blocking(.38), measured.blocking(.7))

    def test_theatre_rail_previews_then_seeks_once(self):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        from drift.ui.sketches.theatre import CueRail
        rail = CueRail(None)
        rail.resize(400, 56)
        rail.show()
        app.processEvents()
        seek = []
        rail.seek.connect(seek.append)
        rail.clock(12000, 100000)
        QTest.mousePress(rail, Qt.MouseButton.LeftButton, pos=QPoint(100, 27))
        QTest.mouseMove(rail, QPoint(200, 27))
        rail.clock(15000, 100000)
        self.assertEqual(rail.sliderPosition(), 50000)
        self.assertEqual(seek, [])
        QTest.mouseRelease(rail, Qt.MouseButton.LeftButton, pos=QPoint(200, 27))
        self.assertEqual(seek, [50000])
        QTest.keyClick(rail, Qt.Key.Key_Left)
        self.assertEqual(seek, [50000, 45000])
        rail.close()

    def test_new_is_blank_and_complete(self):
        s = sketches.new('Blank')
        data = json.loads((s.folder / 'sketch.json').read_text(encoding='utf-8'))
        self.assertEqual(data['stage'], 'canvas')
        self.assertEqual(data['script'], 'sketch.js')

    def test_sunset_colours_picture_texture(self):
        src = self.tmp / 'src'
        src.mkdir()
        picture(src / 'sky.png')
        picture(src / 'paper.png')
        (src / 'fake.ttf').write_bytes(b'not a font at all')
        (src / 'sketch.json').write_text(json.dumps({
            'drift_sketch': 1, 'name': 'Hokusai', 'stage': 'sunset',
            'knobs': {'sky': '#10243e', 'hills': '#1d3b2a', 'backdrop': 'sky.png', 'texture': 'paper.png',
                      'font': 'fake.ttf'}}), encoding='utf-8')
        s = sketches.import_file(src / 'sketch.json')
        full = s.full()
        self.assertEqual(full['backdrop'], 'backdrop.png')
        self.assertEqual(full['texture'], 'texture.png')
        self.assertEqual(full['font'], '')                 # not a real font: left out
        stage = s.make()
        pal = stage.tune(sketches.theme.DEFAULT_PALETTE)
        self.assertEqual(pal.sky_top.name(), '#10243e')
        self.assertEqual(pal.near.name(), '#1d3b2a')
        self.assertIsNotNone(stage.texture())
        self.assertIsNotNone(stage.backdrop(200, 100, 1.0))
        self.assertFalse(s.thumbnail(120, 80).isNull())
        out = sketches.export(s, self.tmp / 'hok')
        with zipfile.ZipFile(out) as z:
            self.assertEqual(sorted(z.namelist()), ['backdrop.png', 'sketch.json', 'texture.png'])

    # ---- canvas sketches (sketch.js) ---------------------------------------------------------------------------
    def _canvas(self, js, name='Test Canvas', extra_files=()):
        src = self.tmp / slug_dir(name)
        src.mkdir()
        (src / 'sketch.js').write_text(js, encoding='utf-8')
        for f in extra_files:
            picture(src / f)
        (src / 'sketch.json').write_text(json.dumps({'drift_sketch': 1, 'name': name, 'stage': 'canvas',
                                                     'script': 'sketch.js', 'words': {'home': 'Easel', 'bad': 'x'}}),
                                         encoding='utf-8')
        return sketches.import_file(src / 'sketch.json')

    def test_new_is_a_blank_canvas_that_draws(self):
        s = sketches.new('My Room')
        self.assertEqual(s.stage, 'canvas')
        self.assertTrue((s.folder / 'sketch.js').is_file())
        stage = s.make()
        rt = stage.runtime()
        self.assertIsNone(stage.error)
        song = {'has': True, 'title': 'T', 'artist': 'A', 'cover': '', 'shape': [0.5] * 160, 'progress': 0.3,
                'line': 'la', 'calm': False, 'colors': sketches.canvas.song_colors(sketches.theme.DEFAULT_PALETTE)}
        cmds, acts = rt.call('draw', 800, 600, 48, None, song, 0.0, {})
        self.assertGreater(len(cmds), 5)
        self.assertFalse(s.thumbnail(120, 80).isNull())
        cmds, acts = rt.call('press', 800, 600, 48, None, {}, 0.0, {}, [400.0, rt.js.evaluate('seekBar.y').toNumber()])
        self.assertEqual(acts[0][0], 'seek')
        self.assertAlmostEqual(acts[0][1], 0.5, places=2)

    def test_sample_sketches_draw(self):
        """tests/sketches: Sunset redrawn as a sketch, a spinning record. Both draw with and without a song, and
        every colour they make is a real one (a missing value must not turn into a wrong colour)."""
        colors = sketches.canvas.song_colors(sketches.theme.DEFAULT_PALETTE)
        full = {'has': True, 'title': 'T', 'artist': 'A', 'cover': '', 'shape': [0.5] * 160, 'progress': 0.4,
                'best': 0.6, 'duration': 200, 'line': 'la', 'nextLine': 'lo', 'calm': False, 'night': 1,
                'next': [{'title': 'N', 'artist': 'M'}], 'colors': colors}
        empty = {'has': False, 'title': '', 'artist': '', 'cover': '', 'shape': [], 'progress': 0, 'best': -1,
                 'duration': 0, 'line': '', 'nextLine': '', 'calm': False, 'night': 1, 'next': [], 'colors': colors}
        for folder in sorted((Path(__file__).parent / 'sketches').iterdir()):
            with self.subTest(folder.name):
                s = sketches.import_file(folder / 'sketch.json')
                stage = s.make()
                rt = stage.runtime()
                self.assertIsNone(stage.error)
                for song in (empty, full):
                    cmds, _a = rt.call('draw', 1100, 700, 48, None, song, 1.0, {})
                    self.assertGreater(len(cmds), 5)
                    bad = [a for c in cmds for a in c[1:] if isinstance(a, str) and a.startswith('#')
                           and 'n' in a.lower()]          # '#ffffffNaN' was a missing value, drawn as magenta
                    self.assertEqual(bad, [])
        self.assertEqual(rt.js.evaluate('drift.rgba(255, 255, 255, NaN)').toString(), '#ffffff00')

    def test_canvas_is_sealed(self):
        s = self._canvas('var seen = [typeof XMLHttpRequest, typeof require, typeof Qt, typeof console, '
                         'typeof setTimeout, typeof fetch, typeof print].join(",");'
                         'function draw(g, song) { g.text(seen, 0, 0); }')
        cmds, _a = s.make().runtime().call('draw', 100, 100, 48, None, {}, 0.0, {})
        self.assertEqual(cmds[0][1], ','.join(['undefined'] * 7))
        self.assertEqual(s.make().tabs['home'], 'Easel')

    def test_runaway_script_is_stopped(self):
        s = self._canvas('function draw(g) { while (true) {} }')
        rt = s.make().runtime()
        from drift.ui.sketches.canvas import SketchError
        import time
        t = time.perf_counter()
        with self.assertRaises(SketchError):
            rt.call('draw', 100, 100, 48, None, {}, 0.0, {})
        self.assertLess(time.perf_counter() - t, 1.5)
        self.assertEqual(rt.js.evaluate('1 + 1').toInt(), 2)           # the box still works after a stop

    def test_script_error_has_a_line(self):
        s = self._canvas('function draw(g) {\n  g.rect(0, 0, 10, 10);\n  nope();\n}')
        from drift.ui.sketches.canvas import SketchError
        with self.assertRaises(SketchError) as e:
            s.make().runtime().call('draw', 100, 100, 48, None, {}, 0.0, {})
        self.assertEqual(e.exception.line, 3)

    def test_canvas_round_trip(self):
        s = self._canvas('function draw(g) { g.image("paper.png", 0, 0, 50, 50); }', 'Paper', ['paper.png'])
        self.assertTrue((s.folder / 'paper.png').is_file())
        out = sketches.export(s, self.tmp / 'paper')
        with zipfile.ZipFile(out) as z:
            self.assertEqual(sorted(z.namelist()), ['paper.png', 'sketch.js', 'sketch.json'])
        again = sketches.import_file(out)
        stage = again.make()
        self.assertIsNotNone(stage.picture('paper.png'))
        self.assertIsNone(stage.picture('../sketch.json'))
        bad = self.tmp / 'noscript.driftsketch'
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('sketch.json', json.dumps({'drift_sketch': 1, 'name': 'N', 'stage': 'canvas'}))
        with self.assertRaises(ValueError):
            sketches.import_file(bad)

    def test_tune(self):
        stage = sketches.Sketch('t', 'T', 'landscape', knobs={'mood': 'mono', 'accent': '#ff0000'}).make()
        pal = stage.tune(sketches.theme.DEFAULT_PALETTE)
        self.assertEqual(pal.acc.name(), '#ff0000')
        self.assertLess(pal.sky_mid.hslSaturationF(), 0.1)


if __name__ == '__main__':
    unittest.main()
