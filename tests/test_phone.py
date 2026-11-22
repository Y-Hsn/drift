"""The PC side of the phone shelf (drift/phone.py), offline. python -m unittest tests.test_phone"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DRIFT_DATA', tempfile.mkdtemp())     # never the real data folder
from drift import phone  # noqa: E402
from drift.store import Store, Track  # noqa: E402


class Shelf(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        phone.ROOT = Path(self.dir) / 'phone'
        phone.FILES = phone.ROOT / 'files'
        phone._hashes = None
        self.s = Store(os.path.join(self.dir, 'x.db'))

    def song(self, uid, size, ext='.m4a', **kw):
        path = os.path.join(self.dir, uid + ext)
        with open(path, 'wb') as f:
            f.write(os.urandom(size))
        return self.s.upsert(Track(uid, 'Song ' + uid, kw.pop('artist', 'Artist ' + uid), path=path, duration=180000, **kw))

    def test_order_budget_and_formats(self):
        mb = 1024 ** 2
        self.song('saved', 100 * mb)
        self.song('liked', 100 * mb, liked=1)
        self.song('vorbis', 1 * mb, ext='.ogg', liked=1)        # an iPhone cannot play it
        pid = self.s.new_playlist('Morning')
        self.s.add_to_playlist(pid, [self.song('morning', 100 * mb)])
        out = phone.shelf(self.s, 'dev1', 256 * mb)
        ids = [s['id'] for s in out['songs']]
        self.assertEqual(ids, ['liked', 'morning'])              # saved did not fit, ogg never goes
        self.assertEqual(out['playlists'][0]['name'], 'Morning')
        self.assertEqual(out['playlists'][0]['items'][0]['song'], 'morning')
        self.assertTrue(os.path.isfile(phone.ROOT / 'dev1' / 'shelf.json'))
        self.assertEqual(len(out['songs'][0]['sha256']), 64)
        self.assertEqual(out['rev'], phone.shelf(self.s, 'dev1', 256 * mb)['rev'])   # same shelf, same rev

    def test_events_once_in_order(self):
        self.song('a', 10)
        evs = [{'seq': 1, 'kind': 'play', 'song': 'a'}, {'seq': 2, 'kind': 'like', 'song': 'a', 'on': True},
               {'seq': 4, 'kind': 'skip', 'song': 'a'}]                         # 3 is missing: stop there
        self.assertEqual(phone.apply_events(self.s, 'dev1', evs), 2)
        self.assertEqual(phone.apply_events(self.s, 'dev1', evs), 2)           # sent again: counted once
        t = self.s.track('a')
        self.assertEqual((t.plays, t.liked, t.skips), (1, 1, 0))
        phone.apply_events(self.s, 'dev1', [{'seq': 3, 'kind': 'wish', 'query': ' Blue Love '}] + evs)
        self.assertEqual(self.s.track('a').skips, 1)
        self.assertEqual(phone._read(phone.ROOT / 'dev1' / 'wishes.json', [])[0]['query'], 'Blue Love')

    def test_skip_drops_radio_pick_not_wish(self):
        a, b = self.song('a', 10), self.song('b', 10)
        phone._write(phone.ROOT / 'dev1' / 'crate.json', [{'uid': 'a', 'path': a.path}, {'uid': 'b', 'path': b.path, 'wish': True}])
        phone.apply_events(self.s, 'dev1', [{'seq': 1, 'kind': 'skip', 'song': 'a'}, {'seq': 2, 'kind': 'skip', 'song': 'b'}])
        self.assertEqual([c['uid'] for c in phone._read(phone.ROOT / 'dev1' / 'crate.json', [])], ['b'])

    def test_streamed_song_is_learned_and_like_wishes_it(self):
        meta = {'title': 'Say My Name', 'artist': 'ODESZA', 'secs': 260, 'page': 'https://evil.example/x',
                'source': 'youtube', 'art': 'https://i.ytimg.com/vi/abcdefghijk/hq.jpg'}
        uid = 'yt:abcdefghijk'
        phone.apply_events(self.s, 'dev1', [{'seq': 1, 'kind': 'play', 'song': uid, 'meta': meta},
                                             {'seq': 2, 'kind': 'like', 'song': uid, 'on': True, 'meta': meta},
                                             {'seq': 3, 'kind': 'play', 'song': 'yt:bad', 'meta': meta},
                                             {'seq': 4, 'kind': 'play', 'song': 'au:x2', 'meta': meta}])
        t = self.s.track(uid)
        self.assertEqual((t.title, t.plays, t.liked, t.source, t.page),
                         ('Say My Name', 1, 1, 'youtube', 'https://www.youtube.com/watch?v=abcdefghijk'))
        self.assertIsNone(self.s.track('yt:bad'))                   # only real YouTube video ids
        self.assertIsNone(self.s.track('au:x2'))
        self.assertEqual(phone._read(phone.ROOT / 'dev1' / 'wishes.json', [])[0]['uid'], uid)

    def test_bad_device(self):
        with self.assertRaises(ValueError):
            phone.device_dir('../x')


if __name__ == '__main__':
    unittest.main()
