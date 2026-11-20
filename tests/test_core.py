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
