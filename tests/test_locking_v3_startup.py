"""S6: start-up order (spec 12.6), journal-check hook (13.1 check 0b, V47), temporary-file
leftovers (12.6, 12.8; D6-6), journal presence (D6-8) and refusal side effects.

Real flock on temporary folders with a synthetic mountinfo. Nothing is wired into the CLI.
"""
from __future__ import annotations
import builtins, json, os, stat, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import locking_v3
from photo_curator.locking_v3 import (JOURNAL_MESSAGE, LOCK_NAME, ArchiveInProgressRefusal, LockFileRefusal,
                                      NetworkFilesystemRefusal, OutputBusyRefusal, acquire_lock, startup_v3)

IS_ROOT = hasattr(os, 'geteuid') and os.geteuid() == 0


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s6-start-')
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        self.work = base / 'out'
        self.work.mkdir()
        (self.work / 'previews').mkdir()
        self.local = base / 'mountinfo-local'
        self.local.write_text('20 1 0:20 / / rw - ext4 /dev/x rw\n')
        self.network = base / 'mountinfo-nfs'
        self.network.write_text(f'20 1 0:20 / / rw - ext4 /dev/x rw\n21 20 0:21 / {self.work} rw - nfs4 srv:/x rw\n')

    def start(self, mode='exclusive', **kw):
        kw.setdefault('mountinfo_path', str(self.local))
        result = startup_v3(self.work, mode, **kw)
        self.addCleanup(result.lock.release)
        return result

    def listing(self):
        return sorted(str(p.relative_to(self.work)) for p in self.work.rglob('*'))

    def without_lock(self, names):
        return [n for n in names if n != LOCK_NAME]


class OrderTest(Base):
    def test_filesystem_then_lock_then_journal(self):
        calls = []
        real = {name: getattr(locking_v3, name) for name in ('check_filesystem', 'acquire_lock', 'check_no_archive_journal')}

        def recorder(name):
            def call(*args, **kwargs):
                calls.append(name)
                return real[name](*args, **kwargs)
            return call
        with mock.patch.multiple(locking_v3, **{name: recorder(name) for name in real}):
            result = self.start()
        self.assertEqual(calls, ['check_filesystem', 'acquire_lock', 'check_no_archive_journal'])
        self.assertFalse(result.lock.released)
        self.assertEqual(result.lock.mode, 'exclusive')

    def test_never_opens_results_or_previews(self):
        (self.work / 'ratings-review.json').write_text(json.dumps({'schema_version': 3}))
        (self.work / 'previews' / 'p.jpg').write_bytes(b'x')
        results = str(self.work / 'ratings-review.json')
        previews = str(self.work / 'previews')
        real_open, real_os_open = builtins.open, os.open

        def guard(path):
            text = str(path)
            if text == results or text == previews or text.startswith(previews + os.sep):
                raise AssertionError(f'start-up opened {path}')

        def fake_open(path, *a, **k):
            guard(path); return real_open(path, *a, **k)

        def fake_os_open(path, *a, **k):
            guard(path); return real_os_open(path, *a, **k)
        with mock.patch('builtins.open', fake_open), mock.patch.object(locking_v3.os, 'open', fake_os_open):
            for mode in ('shared', 'exclusive'):
                self.start(mode).lock.release()

    def test_invalid_mode_changes_nothing(self):
        before = self.listing()
        with self.assertRaises(ValueError):
            startup_v3(self.work, 'upgrade', mountinfo_path=str(self.local))
        self.assertEqual(self.listing(), before)


class RefusalTest(Base):
    def test_network_refusal_before_lock(self):
        before = self.listing()
        for mode in ('shared', 'exclusive'):
            with self.assertRaises(NetworkFilesystemRefusal):
                startup_v3(self.work, mode, mountinfo_path=str(self.network))
        self.assertEqual(self.listing(), before)
        self.assertFalse((self.work / LOCK_NAME).exists())

    def test_network_refusal_for_missing_work(self):                            # D6-2
        missing = self.work / 'not-yet'
        with self.assertRaises(NetworkFilesystemRefusal):
            startup_v3(missing, 'exclusive', mountinfo_path=str(self.network))
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):                                     # local: lock needs the folder
            startup_v3(missing, 'exclusive', mountinfo_path=str(self.local))
        self.assertFalse(missing.exists())

    def test_busy_changes_nothing(self):
        holder = acquire_lock(self.work, 'exclusive')
        self.addCleanup(holder.release)
        before = self.listing()
        for mode in ('shared', 'exclusive'):
            with self.assertRaises(OutputBusyRefusal):
                startup_v3(self.work, mode, mountinfo_path=str(self.local))
        self.assertEqual(self.listing(), before)

    def test_journal_refusal_releases_lock(self):
        (self.work / 'archive-in-progress.json').write_text('{}')
        before = self.listing()
        for mode in ('shared', 'exclusive'):
            with self.subTest(mode=mode):
                with self.assertRaises(ArchiveInProgressRefusal) as cm:
                    startup_v3(self.work, mode, mountinfo_path=str(self.local))
                self.assertEqual(str(cm.exception), JOURNAL_MESSAGE)
                acquire_lock(self.work, 'exclusive').release()                  # lock was released
        self.assertEqual(self.without_lock(self.listing()), before)
        self.assertEqual((self.work / 'archive-in-progress.json').read_text(), '{}')

    def test_journal_any_entry_type(self):                                      # D6-8
        journal = self.work / 'archive-in-progress.json'
        for make in (lambda: journal.mkdir(), lambda: journal.symlink_to(self.work / 'nowhere')):
            make()
            with self.assertRaises(ArchiveInProgressRefusal):
                startup_v3(self.work, 'shared', mountinfo_path=str(self.local))
            journal.rmdir() if journal.is_dir() and not journal.is_symlink() else journal.unlink()

    def test_recover_archive_is_exempt(self):
        (self.work / 'archive-in-progress.json').write_text('{}')
        self.assertFalse(self.start('shared', exempt_journal=True).lock.released)

    @unittest.skipIf(IS_ROOT, 'root bypasses file permissions')
    def test_readonly_lock_refuses_exclusive_before_journal(self):
        lock = self.work / LOCK_NAME
        lock.write_text(''); os.chmod(lock, 0o444)
        self.addCleanup(os.chmod, lock, 0o644)
        (self.work / 'archive-in-progress.json').write_text('{}')
        with self.assertRaises(LockFileRefusal):
            startup_v3(self.work, 'exclusive', mountinfo_path=str(self.local))
        with self.assertRaises(ArchiveInProgressRefusal):                       # shared reaches the journal
            startup_v3(self.work, 'shared', mountinfo_path=str(self.local))


class LeftoverTest(Base):
    def test_top_level_listed_never_read_or_deleted(self):
        names = {'ratings-review.json.tmp-123-abc': b'partial', 'ratings-review.csv.tmp-9-x': b'p',
                 'archive-dir.tmp-1': None, 'notes.txt': b'n', 'tmpfile': b't'}
        for name, data in names.items():
            path = self.work / name
            path.mkdir() if data is None else path.write_bytes(data)
        (self.work / 'previews' / 'p.jpg.tmp-1-z').write_bytes(b'nested')
        real_open = builtins.open

        def guard(path, *a, **k):
            if '.tmp-' in str(path):
                raise AssertionError(f'leftover opened: {path}')
            return real_open(path, *a, **k)
        with mock.patch('builtins.open', guard):
            result = self.start('shared')
        self.assertEqual(result.leftovers, ('archive-dir.tmp-1', 'ratings-review.csv.tmp-9-x',
                                            'ratings-review.json.tmp-123-abc'))
        self.assertEqual((self.work / 'ratings-review.json.tmp-123-abc').read_bytes(), b'partial')
        self.assertTrue((self.work / 'previews' / 'p.jpg.tmp-1-z').exists())

    def test_no_leftovers(self):
        self.assertEqual(self.start().leftovers, ())


class SchemaIndependenceTest(Base):
    def test_v1_v2_v3_folders_behave_identically(self):
        outcomes = []
        for version in (1, 2, 3):
            (self.work / 'ratings-review.json').write_text(json.dumps({'schema_version': version}))
            result = startup_v3(self.work, 'exclusive', mountinfo_path=str(self.local))
            outcomes.append((result.leftovers, result.lock.mode))
            result.lock.release()
        self.assertEqual(len(set(outcomes)), 1)
        self.assertTrue(stat.S_ISREG(os.lstat(self.work / LOCK_NAME).st_mode))


if __name__ == '__main__':
    unittest.main()
