"""Offline checks: names, the store, taste. python -m unittest tests.test_core"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DRIFT_DATA', tempfile.mkdtemp())     # never the real data folder
from drift import online, taste  # noqa: E402
from drift.library import uid_for  # noqa: E402
from drift.store import Store, Track, clean_title, matching, norm, split_artist  # noqa: E402


class Names(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(clean_title('07 - MAGHRIBI (lyric video) #27album'), 'MAGHRIBI')
        self.assertEqual(clean_title('Haram (Pablo II) Lyric Video'), 'Haram (Pablo II)')
        self.assertEqual(clean_title('Song [Official Music Video] [abcdefghijk]'), 'Song')
        self.assertEqual(clean_title('STALINE (VISUALIZER) #SALGOAT'), 'STALINE')

    def test_split(self):
        self.assertEqual(split_artist('ElGrandeToto - Blue Love (Paroles/Lyrics)', 'Some Lyrics Channel'),
                         ('Blue Love', 'ElGrandeToto'))
        self.assertEqual(split_artist('Mghayer', 'ElGrandeToto - Topic'), ('Mghayer', 'ElGrandeToto'))

    def test_norm(self):
        self.assertEqual(norm('Blue Love feat. Niro'), norm('blue love'))

    def test_uid(self):
        self.assertEqual(uid_for(r'C:\m\Artist - Song [abcdefghijk].m4a'), 'yt:abcdefghijk')
        self.assertTrue(uid_for(r'C:\m\Song.mp3').startswith('file:'))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = Store(os.path.join(self.dir, 'x.db'))

    def test_keep_stats_on_refresh(self):
        self.s.upsert(Track('yt:aaaaaaaaaaa', 'A', 'X'))
        self.s.set_field('yt:aaaaaaaaaaa', liked=1)
        self.s.played('yt:aaaaaaaaaaa')
        t = self.s.upsert(Track('yt:aaaaaaaaaaa', 'A new title', 'X'))
        self.assertEqual((t.liked, t.plays), (1, 1))
        self.assertEqual(self.s.track('yt:aaaaaaaaaaa').title, 'A new title')

    def test_settings(self):
        self.assertTrue(self.s.get('drift'))
        self.s.set('drift', False)
        self.assertFalse(self.s.get('drift'))

    def test_playlists(self):
        pid = self.s.new_playlist('Night')
        self.s.add_to_playlist(pid, [Track('yt:bbbbbbbbbbb', 'B'), Track('yt:ccccccccccc', 'C')])
        self.assertEqual([t.title for t in self.s.playlist_tracks(pid)], ['B', 'C'])
        self.s.remove_from_playlist(pid, 'yt:bbbbbbbbbbb')
        self.assertEqual([t.title for t in self.s.playlist_tracks(pid)], ['C'])
        self.s.drop_playlist(pid)
        self.assertEqual(self.s.playlists(), [])
        self.assertIsNotNone(self.s.track('yt:ccccccccccc'))   # the song stays


class TasteTests(unittest.TestCase):
    def test_accept(self):
        ok = Track('yt:1', 'Song', 'A', duration=200_000)
        self.assertTrue(taste.accept(ok, set(), set()))
        self.assertFalse(taste.accept(Track('yt:2', 'Best of 2024 full album', 'A', duration=200_000), set(), set()))
        self.assertFalse(taste.accept(Track('yt:3', 'Song', 'A', duration=3_600_000), set(), set()))
        self.assertFalse(taste.accept(ok, {norm('Song')}, set()))
        self.assertFalse(taste.accept(ok, set(), {norm('A')}))

    def test_likes_rank_first(self):
        now = time.time()
        tracks = [Track('a', 'x', 'Loved', liked=1, plays=3, last_played=now),
                  Track('b', 'y', 'Skipped', plays=0, skips=6, last_played=now),
                  Track('c', 'z', 'Meh', plays=1, last_played=now - 90 * 86400)]
        names = [a for a, _ in taste.top_artists(tracks)]
        self.assertEqual(names[0], 'Loved')
        self.assertNotIn('Skipped', names)
        self.assertIn(norm('Skipped'), taste.disliked(tracks))

    def test_offline_order_no_artist_twice(self):
        tracks = [Track(f'u{i}', f't{i}', 'A' if i % 2 else 'B', path='x') for i in range(10)]
        order = taste.offline_order(tracks, n=10)
        self.assertEqual(len(order), 10)
        self.assertTrue(all(order[i].artist != order[i + 1].artist for i in range(9)))


class Offline(unittest.TestCase):
    def test_search_this_pc(self):
        tracks = [Track('a', 'Breathe', 'Télépopmusik', album='Genetic World'), Track('b', 'Intro', 'The xx')]
        self.assertEqual([t.uid for t in matching(tracks, 'telepop breathe')], ['a'])
        self.assertEqual([t.uid for t in matching(tracks, 'GENETIC')], ['a'])
        self.assertEqual(len(matching(tracks, '')), 2)
        self.assertEqual(matching(tracks, 'nothing like it'), [])

    def test_lyrics_stay_for_offline(self):
        t = Track('yt:keeplyrics1', 'Song', 'Artist', duration=180000)
        calls = []
        real = online._lrclib
        online._lrclib = lambda track: calls.append(1) or [(1000, 'one'), (2000, 'two')]
        try:
            self.assertIsNone(online.lyrics_kept(t))
            self.assertEqual(online.lyrics(t), [(1000, 'one'), (2000, 'two')])
            self.assertEqual(online.lyrics(t), [(1000, 'one'), (2000, 'two')])     # the second time from disk
            self.assertEqual(online.lyrics_kept(t), [(1000, 'one'), (2000, 'two')])
            self.assertEqual(len(calls), 1)
        finally:
            online._lrclib = real


if __name__ == '__main__':
    unittest.main()


class PlayLog(unittest.TestCase):
    def test_failure_is_written_with_reason_and_capped(self):
        from pathlib import Path
        from drift import engine
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'play.log'
            t = Track(uid='yt:abc', title='Song A', source='youtube', page='https://music.youtube.com/watch?v=abc')
            for _ in range(engine.PLAY_LOG_LINES + 5):
                engine.note_play_failure(t, 'ERROR: Requested format\n is not available', True, log=log)
            lines = log.read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), engine.PLAY_LOG_LINES)
            self.assertIn('Song A', lines[-1])
            self.assertIn('Requested format is not available', lines[-1])
            self.assertIn('watch?v=abc', lines[-1])

    def test_never_picks_a_format_the_core_cannot_play(self):
        self.assertNotIn('opus', online.PLAYABLE)
        self.assertNotIn('best[', online.PLAYABLE.replace('bestaudio[', ''))


class JsFallback(unittest.TestCase):
    """YouTube can shut the one JavaScript-free route on a connection: then every song must go through a JS runtime."""

    def _fake(self, fail_without_js):
        calls = []

        class Y:
            def __init__(self, opts): self.opts = opts
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def prepare_filename(self, info): return 'x.m4a'
            def extract_info(self, url, download=False):
                calls.append(bool(self.opts.get('js_runtimes')))
                if fail_without_js and not self.opts.get('js_runtimes'):
                    raise RuntimeError('Requested format is not available')
                return {'url': 'https://a', 'id': 'v'}
        return calls, (lambda **o: Y(o))

    def setUp(self):
        self._saved = (online._ydl, online.js_runtimes, online._js_until, online._yt_blocked_until)
        online.js_runtimes = lambda: {'quickjs': {'path': 'qjs.exe'}}
        online._js_until = online._yt_blocked_until = 0.0

    def tearDown(self):
        online._ydl, online.js_runtimes, online._js_until, online._yt_blocked_until = self._saved

    def test_fast_route_first_when_it_works(self):
        calls, online._ydl = self._fake(False)
        online._extract('u')
        online._extract('u')
        self.assertEqual(calls, [False, False])

    def test_shut_route_falls_back_then_goes_straight_to_js(self):
        calls, online._ydl = self._fake(True)
        info, _ = online._extract('u')
        self.assertEqual(info['url'], 'https://a')
        online._extract('u')
        self.assertEqual(calls, [False, True, True])

    def test_bot_check_is_not_retried(self):
        calls, _ = self._fake(True)

        class Bot(Exception):
            pass

        def ydl(**o):
            class Y:
                def __enter__(s): return s
                def __exit__(s, *a): return False
                def extract_info(s, url, download=False):
                    calls.append(bool(o.get('js_runtimes')))
                    raise Bot("Sign in to confirm you're not a bot")
            return Y()
        online._ydl = ydl
        with self.assertRaises(Bot):
            online._extract('u')
        self.assertEqual(calls, [False])
