"""Drift radio ranking, refill scope and the tray helper, with a fixed catalogue."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from drift import helper, taste
from drift.engine import Engine
from drift.store import Store, Track


NOW = 100 * taste.DAY
APP = QApplication.instance() or QApplication([])


def song(uid, title, artist, **stats):
    return Track(f'yt:{uid}', title, artist, duration=200_000, page=f'https://example.com/{uid}', **stats)


class Catalog:
    def __init__(self, *rows):
        self.rows = {t.uid: picks for t, picks in rows}
        self.calls = []

    def __call__(self, track):
        self.calls.append(track.uid)
        return self.rows.get(track.uid, [])


class Ranking(unittest.TestCase):
    def test_agreement_and_rediscovery_stay_inside_the_seed_style(self):
        seed = song('seed', 'Blue', 'Alice')
        bridge = song('bridge', 'Fine', 'Nia', liked=1, last_played=NOW - 1800)
        old = song('old', 'Forgotten', 'Nia', liked=1, last_played=NOW - 18 * taste.DAY)
        unrelated = song('different', 'Crowd', 'Metal', liked=1, plays=40)
        agreement = song('two', 'Rain', 'Sol')
        other = song('one', 'Grey', 'Jo')
        third = song('three', 'Day', 'Owen')
        catalog = Catalog((seed, [bridge, other, agreement, third]), (bridge, [agreement, third]))
        picks = taste.Radio([bridge, old, unrelated], current=seed, now=NOW).plan(catalog, [])
        self.assertEqual(catalog.calls, [seed.uid, bridge.uid])
        self.assertEqual(picks[0], agreement)
        self.assertEqual(picks[2], old)
        self.assertNotIn(unrelated, picks)

    def test_early_skip_cools_the_recording_across_versions_and_keeps_same_title_by_other_artist(self):
        rejected = song('rejected', 'Home', 'Zoë')
        duplicate = song('copy', 'HOME (Official Video)', 'Zoe, A guest')
        same_title = song('other', 'Home', 'A different artist')
        played = song('anchor', 'Great', 'Lee')
        catalog = Catalog((played, [duplicate, same_title]))
        history = [(played.uid, NOW - 60, 'listen'), (rejected.uid, NOW - 1, 'skip')]
        radio = taste.Radio([rejected, played], history, current=rejected, now=NOW)
        self.assertEqual(radio.plan(catalog, []), [same_title])
        self.assertEqual(catalog.calls[0], played.uid)

    def test_positive_session_reanchors_an_unheard_automatic_pick(self):
        heard = song('heard', 'Quiet start', 'A')
        unproven = song('unproven', 'Next up', 'B')
        unproven.extra['radio'] = True
        radio = taste.Radio([heard], [(heard.uid, NOW - 4, 'listen')], current=unproven, now=NOW)
        self.assertEqual(radio.anchor([]), heard)
        radio = taste.Radio([heard], [(heard.uid, NOW - 4, 'listen')], seed=unproven, now=NOW)
        self.assertEqual(radio.anchor([]), unproven)

    def test_offline_reuses_small_library_without_repeating_current_when_another_song_exists(self):
        a = song('a', 'One', 'A', last_played=NOW - 1)
        b = song('b', 'Two', 'B', last_played=NOW - 2)
        self.assertEqual(taste.Radio([a, b], current=a, now=NOW).offline([a, b]), [b])
        self.assertEqual(taste.Radio([a], current=a, now=NOW).offline([a]), [a])
        self.assertEqual(taste.Radio([a, b], current=a, queue=[b], now=NOW).offline([a, b]), [])


class FakeCore(QObject):
    message = pyqtSignal(dict)
    ready = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.commands = []

    def send(self, cmd, **args):
        self.commands.append((cmd, args))


class Refill(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = Store(Path(self.folder.name) / 'test.db')
        self.addCleanup(self.store.db.close)

    def test_helper_loads_actual_tray_feedback_and_avoids_already_queued_recording(self):
        current = song('playing', 'Current', 'Someone')
        queued = song('upcoming', 'Home', 'Nia')
        duplicate = song('version', 'Home (Official Video)', 'Nia')
        seed = self.store.upsert(song('listened', 'Accepted', 'Alice'))
        pick = song('selected', 'Fine', 'Sol')
        (Path(self.folder.name) / 'radio-listening.json').write_text(json.dumps([
            {'uid': seed.uid, 'at': taste.time.time() - 5, 'kind': 'listen'},
        ]), encoding='utf-8')
        catalog = Catalog((seed, [duplicate, pick]))
        with patch.object(helper, 'Store', return_value=self.store), patch.object(helper.online, 'is_online', return_value=True), \
                patch.object(helper.online, 'related', side_effect=lambda t, n, **kw: catalog(t)), \
                patch.object(helper.online, 'stream', return_value=('https://example.com/audio', {})):
            result = helper.refill(helper.item_of(current, radio=True), [helper.item_of(queued)])
        self.assertEqual(catalog.calls[0], seed.uid)
        self.assertEqual([item['id'] for item in result['items']], [pick.uid])
        self.assertTrue(result['items'][0]['radio'])

    def test_manual_choice_cancels_stale_radio_results_and_keeps_manual_queue(self):
        first = song('first', 'Still', 'A')
        next_manual = song('manual', 'Next', 'B')
        wrong = song('wrong', 'Stale pick', 'A')
        right = song('right', 'Relevant pick', 'B')
        engine = Engine(self.store, core=FakeCore())
        self.addCleanup(lambda: engine.net_timer.stop())
        callbacks = []
        with patch('drift.engine.tasks.run', side_effect=lambda fn, *args, done, failed: callbacks.append(done)), \
                patch.object(engine, '_prefetch'):
            engine.play(first)
            self.assertEqual(len(callbacks), 1)
            engine.play(next_manual, [next_manual, first])
            callbacks.pop(0)([wrong])
            self.assertEqual(engine.queue, [first])
            callbacks.pop(0)([right])
        self.assertEqual(engine.queue, [first, right])
        self.assertFalse(engine.queue[0].extra.get('radio'))
        self.assertTrue(engine.queue[1].extra['radio'])

    def test_fast_skip_can_move_an_auto_pick_but_keeps_manual_next(self):
        engine = Engine(self.store, core=FakeCore())
        self.addCleanup(lambda: engine.net_timer.stop())
        current = song('current', 'Playing', 'A')
        a = engine._generated(song('a', 'First', 'A'))
        b = engine._generated(song('b', 'Second', 'B'))
        engine.current, engine.queue, engine._listened = current, [a, b], 4000
        engine._leave_current()
        self.assertEqual(engine.queue, [b, a])
        self.assertEqual(engine.core.commands[-1], ('leave', {'id': current.uid}))
        manual = song('manual', 'Queued by user', 'A')
        engine.queue, engine._listened = [manual, b], 4000
        engine._leave_current()
        self.assertEqual(engine.queue, [manual, b])
