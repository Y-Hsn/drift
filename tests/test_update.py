"""Auto-update: newer-release check, checked download + unzip, and the real PowerShell swap on a scratch folder."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from drift import update  # noqa: E402


def release(tag, digest='sha256:' + '0' * 64, name=update.ASSET):
    return {'tag_name': tag, 'body': 'fixes', 'assets': [{'name': name, 'size': 10, 'digest': digest,
                                                          'browser_download_url': 'https://x/Drift-windows.zip'}]}


class Answer(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def drift_zip(path, files):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    data = Path(path).read_bytes()
    return {'version': '9.9.9', 'url': Path(path).as_uri(), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


WHOLE = {'Drift/Drift.exe': b'new exe', 'Drift/drift-core.exe': b'new core', 'Drift/qjs.exe': b'qjs',
         'Drift/_internal/base_library.zip': b'new lib'}


class Latest(unittest.TestCase):
    def check(self, rel, current='1.0.2'):
        with mock.patch.object(update, '_get', return_value=Answer(json.dumps(rel).encode())):
            return update.latest(current=current)

    def test_newer_release_is_found(self):
        info = self.check(release('v1.0.10'))
        self.assertEqual(info['version'], '1.0.10')        # 10 > 2: numbers, not text
        self.assertEqual(len(info['sha256']), 64)

    def test_same_or_older_is_ignored(self):
        self.assertIsNone(self.check(release('v1.0.2')))
        self.assertIsNone(self.check(release('v1.0.1')))

    def test_unverifiable_or_missing_zip_is_ignored(self):
        self.assertIsNone(self.check(release('v2.0.0', digest='')))
        self.assertIsNone(self.check(release('v2.0.0', name='other.zip')))
        self.assertIsNone(self.check({**release('v2.0.0'), 'prerelease': True}))


class FetchAndSwap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patch = mock.patch.object(update, 'folder', return_value=self.root / 'data' / 'update')
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_good_zip_unpacks(self):
        info = drift_zip(self.root / 'r.zip', WHOLE)
        seen = []
        new = update.fetch(info, seen.append)
        self.assertEqual((new / 'Drift.exe').read_bytes(), b'new exe')
        self.assertAlmostEqual(seen[-1], 1.0)

    def test_tampered_zip_is_refused(self):
        info = drift_zip(self.root / 'r.zip', WHOLE)
        info['sha256'] = '1' * 64
        with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
            update.fetch(info)

    def test_zip_slip_and_partial_zip_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            update.fetch(drift_zip(self.root / 'a.zip', {**WHOLE, '../evil.txt': b'x'}))
        with self.assertRaisesRegex(RuntimeError, 'whole Drift'):
            update.fetch(drift_zip(self.root / 'b.zip', {'Drift/Drift.exe': b'only'}))

    def make_app(self):
        app = self.root / 'My Apps' / "Bob's Drift"         # spaces and a quote in the path
        (app / '_internal').mkdir(parents=True)
        for n, d in (('Drift.exe', b'old exe'), ('drift-core.exe', b'old core'), ('_internal/old.pyd', b'old lib')):
            (app / n).write_bytes(d)
        (app / 'my notes.txt').write_bytes(b'mine')
        return app

    def test_swap_replaces_program_files_and_keeps_the_rest(self):
        app = self.make_app()
        new = update.fetch(drift_zip(self.root / 'r.zip', WHOLE))
        update.apply(new, '9.9.9', [], app=app, start=False, wait=True)
        self.assertEqual((app / 'Drift.exe').read_bytes(), b'new exe')
        self.assertEqual((app / 'qjs.exe').read_bytes(), b'qjs')
        self.assertTrue((app / '_internal' / 'base_library.zip').is_file())
        self.assertFalse((app / '_internal' / 'old.pyd').exists())      # no old pieces mixed in
        self.assertEqual((app / 'my notes.txt').read_bytes(), b'mine')
        self.assertFalse((app / '.drift-old').exists())
        self.assertIn('updated to 9.9.9', (self.root / 'data' / 'update.log').read_text())

    def test_failed_swap_puts_the_old_drift_back(self):
        app = self.make_app()
        new = update.fetch(drift_zip(self.root / 'r.zip', WHOLE))
        held = open(app / '_internal' / 'old.pyd', 'rb')                # a file Windows will not let go of
        try:
            update.apply(new, '9.9.9', [], app=app, start=False, wait=True)
        finally:
            held.close()
        self.assertEqual((app / 'Drift.exe').read_bytes(), b'old exe')
        self.assertEqual((app / '_internal' / 'old.pyd').read_bytes(), b'old lib')
        self.assertIn('old Drift put back', (self.root / 'data' / 'update.log').read_text())


if __name__ == '__main__':
    unittest.main()
