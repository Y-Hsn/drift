"""Offline tests for release checks, staging, and PowerShell swaps on scratch files."""
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

    def test_marker_and_payload_are_verified(self):
        info = drift_zip(self.root / 'r.zip', WHOLE)
        new = update.fetch(info)
        stage = update.folder() / info['version']
        marker = stage / update.MARKER
        saved = json.loads(marker.read_text(encoding='utf-8'))
        self.assertEqual(saved['sha256'], info['sha256'])
        self.assertEqual(saved['version'], info['version'])

        marker.write_text(json.dumps({**saved, 'version': '8.0.0'}), encoding='utf-8')
        self.assertIsNone(update.staged())
        marker.write_text(json.dumps(saved), encoding='utf-8')
        (new / '_internal' / 'base_library.zip').write_bytes(b'tampered')
        self.assertIsNone(update.staged())
        self.assertEqual(update.fetch(info), new)
        (stage / update.ASSET).write_bytes(b'tampered')
        self.assertIsNone(update.staged())

    def test_startup_reuses_stage_without_network_or_delay(self):
        from drift.ui import window

        info = drift_zip(self.root / 'r.zip', WHOLE)
        new = update.fetch(info)
        win = mock.Mock(quitting=False)

        def run(fn, *, done, failed):
            done(fn())

        with (mock.patch.object(update, 'found', None),
              mock.patch.object(update, 'can_update', return_value=True),
              mock.patch.object(update, '_get', side_effect=AssertionError('unexpected network request')),
              mock.patch.object(window, 'live', return_value=win),
              mock.patch.object(window.tasks, 'run', side_effect=run),
              mock.patch.object(window, 'QTimer') as timer):
            window.Window._check_update(win, cached_only=True)
            win._update_ready.assert_called_once_with(
                {'version': info['version'], 'sha256': info['sha256'], 'path': new})
            timer.singleShot.assert_not_called()
            win.store.set.assert_not_called()

    def test_new_stage_cleans_older_versions(self):
        info = drift_zip(self.root / 'r.zip', WHOLE)
        old = update.fetch({**info, 'version': '9.9.8'})
        self.assertTrue(old.is_dir())
        new = update.fetch(info)
        self.assertFalse((update.folder() / '9.9.8').exists())
        self.assertEqual(update.staged()['path'], new)
        self.assertIsNone(update.staged(current=info['version']))
        self.assertFalse((update.folder() / info['version']).exists())

    def test_startup_finishes_an_interrupted_unpack_offline(self):
        info = drift_zip(self.root / 'r.zip', WHOLE)
        with mock.patch.object(update, '_unpack', side_effect=OSError('interrupted')):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                update.fetch(info)
        stage = update.folder() / info['version']
        self.assertFalse((stage / update.MARKER).exists())
        with mock.patch.object(update, '_get', side_effect=AssertionError('unexpected network request')):
            ready = update.staged()
        self.assertEqual((ready['path'] / 'Drift.exe').read_bytes(), b'new exe')
        self.assertTrue((stage / update.MARKER).is_file())

    def test_tap_applies_the_stage_and_preserves_playback_intent(self):
        from drift import core
        from drift.ui import window

        info = drift_zip(self.root / 'r.zip', WHOLE)
        new = update.fetch(info)
        win = mock.Mock(quitting=False)
        win.engine.playing = True
        with (mock.patch.object(update, 'found', update.staged()),
              mock.patch.object(update, 'apply') as apply,
              mock.patch.object(core, 'read_port', return_value=(4321, 'token', 123)),
              mock.patch.object(window.tasks, 'run') as run):
            window.Window.update_now(win)
        apply.assert_called_once_with(new, info['version'], [os.getpid(), 123])
        win.store.set.assert_called_once_with('resume_after_update', True)
        win.quit.assert_called_once_with(keep_music=False)
        run.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows staging lock')
    def test_staging_refuses_a_second_download(self):
        info = drift_zip(self.root / 'r.zip', WHOLE)
        with update._staging(), mock.patch.object(update, '_get') as request:
            with self.assertRaisesRegex(RuntimeError, 'already being prepared'):
                update.fetch(info)
            request.assert_not_called()

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
        self.assertFalse(list(app.glob('.drift-old-*')))
        self.assertIn('updated to 9.9.9', (update.folder().parent / 'update.log').read_text())
        self.assertFalse((update.folder() / '9.9.9').exists())

        script = (update.folder() / 'swap.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('$sameVolume = $true', script)
        self.assertIn('if ($sameVolume)', script)
        self.assertIn('Move-Item -LiteralPath "$new\\$n" -Destination $app', script)
        self.assertIn('Copy-Item -LiteralPath "$new\\$n" -Destination $app -Recurse -Force', script)
        self.assertIn('Start-Sleep -Milliseconds 75', script)
        self.assertIn('Move-Item -LiteralPath "$old\\$n" -Destination "$app\\$n"', script)

    def test_copy_fallback_installs_the_update(self):
        app = self.make_app()
        new = update.fetch(drift_zip(self.root / 'r.zip', WHOLE))
        # Exercise the copy branch without needing a second drive.
        with mock.patch.object(update, 'SWAP', update.SWAP.replace('$' + '{same_volume}', '$false')):
            update.apply(new, '9.9.9', [], app=app, start=False, wait=True)
        self.assertEqual((app / 'Drift.exe').read_bytes(), b'new exe')
        self.assertEqual((app / '_internal' / 'base_library.zip').read_bytes(), b'new lib')

    def test_failure_after_swap_restores_old_files_and_the_stage(self):
        app = self.make_app()
        new = update.fetch(drift_zip(self.root / 'r.zip', WHOLE))
        fail_after_swap = update.SWAP.replace('\n    Restart-Drift\n', '\n    throw "restart failed"\n', 1)
        with mock.patch.object(update, 'SWAP', fail_after_swap):
            update.apply(new, '9.9.9', [], app=app, start=False, wait=True)
        self.assertEqual((app / 'Drift.exe').read_bytes(), b'old exe')
        self.assertEqual((app / '_internal' / 'old.pyd').read_bytes(), b'old lib')
        self.assertFalse((app / 'qjs.exe').exists())
        self.assertEqual(update.staged()['path'], new)

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
        self.assertIn('old Drift put back', (update.folder().parent / 'update.log').read_text())


if __name__ == '__main__':
    unittest.main()
