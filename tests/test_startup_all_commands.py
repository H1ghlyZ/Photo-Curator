"""W-B: the 12.6 start-up order for every lock-taking command (spec 20 item 16: start-up order,
network filesystems, busy, journal, v1/v2 folders; 14.7 differences 2-5)."""
from __future__ import annotations
import hashlib, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import locking_v3
from photo_curator.locking_v3 import BUSY_MESSAGE, JOURNAL_MESSAGE, JOURNAL_NAME, LOCK_NAME, acquire_lock
from tests.test_apply_v3_order import ApplyFixture
from tests.test_cli_wiring_v3 import run_main


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-wb-start-')
        self.addCleanup(tmp.cleanup)
        self.fx = ApplyFixture(Path(os.path.realpath(tmp.name)))
        self.lock = self.fx.work / LOCK_NAME
        if self.lock.exists():
            self.lock.unlink()

    def commands(self):
        r = self.fx.doc()['results'][self.fx.index(1)]
        preview_sha = hashlib.sha256(Path(r['preview_path']).read_bytes()).hexdigest()
        results, original, work = str(self.fx.results), str(self.fx.paths[1]), str(self.fx.work)
        return {
            'review': (('review', results), 'shared'),
            'gallery': (('gallery', results), 'exclusive'),
            'apply': (('apply', results, '--yes'), 'exclusive'),
            'approve --show': (('approve', results, original, '--show'), 'shared'),
            'approve --rating': (('approve', results, original, '--rating', '3', '--preview-sha256', preview_sha), 'exclusive'),
            'approve --revoke': (('approve', results, original, '--revoke'), 'exclusive'),
            'recover-archive': (('recover-archive', work), 'shared'),
            'recover-archive --complete': (('recover-archive', work, '--complete'), 'exclusive'),
            'recover-archive --rollback': (('recover-archive', work, '--rollback'), 'exclusive'),
        }

    def listing(self):
        return sorted(os.listdir(self.fx.work))


class NetworkFilesystemTest(Base):
    def test_refused_before_the_lock_for_every_command(self):
        for name, (argv, _) in self.commands().items():
            with self.subTest(command=name):
                before = self.listing()
                with mock.patch.object(locking_v3, 'filesystem_type', return_value='nfs'):
                    code, _, err = run_main(*argv)
                self.assertEqual(code, 1)
                self.assertIn('network filesystem (nfs)', err)
                self.assertEqual(self.listing(), before)                          # not even the lock file
                self.assertFalse(self.lock.exists())


class BusyTest(Base):
    def test_refused_under_contention(self):
        for name, (argv, mode) in self.commands().items():
            with self.subTest(command=name):
                holder = acquire_lock(self.fx.work, 'exclusive' if mode == 'shared' else 'shared')
                try:
                    code, _, err = run_main(*argv)
                finally:
                    holder.release()
                self.assertEqual(code, 1)
                self.assertIn(BUSY_MESSAGE, err)

    def test_shared_commands_run_together(self):
        holder = acquire_lock(self.fx.work, 'shared')
        self.addCleanup(holder.release)
        for argv in (('review', str(self.fx.results)), ('approve', str(self.fx.results), str(self.fx.paths[1]), '--show'),
                     ('recover-archive', str(self.fx.work))):
            with self.subTest(argv=argv[0]):
                code, _, err = run_main(*argv)
                self.assertEqual(code, 0, err)


class JournalTest(Base):
    def test_every_command_but_recover_archive_refused_before_parsing(self):
        commands = self.commands()                                                # built while the file is valid
        (self.fx.work / JOURNAL_NAME).write_bytes(b'{}')
        self.fx.results.write_bytes(b'{not json')                                 # proves no parse before 0b
        for name, (argv, _) in commands.items():
            if name.startswith('recover-archive'):
                continue
            with self.subTest(command=name):
                code, _, err = run_main(*argv)
                self.assertEqual(code, 1)
                self.assertIn(JOURNAL_MESSAGE, err)
        self.assertFalse(list(self.fx.library.glob('*.xmp')))

    def test_recover_archive_is_exempt(self):
        (self.fx.work / JOURNAL_NAME).write_bytes(b'{}')
        code, out, _ = run_main('recover-archive', str(self.fx.work))
        self.assertEqual(code, 0)
        self.assertIn('journal malformed', out)


class OrderTest(Base):
    def record(self):
        order = []
        real = {name: getattr(locking_v3, name) for name in ('check_filesystem', 'acquire_lock', 'check_no_archive_journal')}
        labels = {'check_filesystem': 'fs', 'acquire_lock': 'lock', 'check_no_archive_journal': 'journal'}
        patches = [mock.patch.object(locking_v3, name, side_effect=(lambda n: lambda *a, **k: (order.append(labels[n]), real[n](*a, **k))[1])(name))
                   for name in real]
        return order, patches

    def test_fs_then_lock_then_journal_for_every_command(self):
        for name, (argv, mode) in self.commands().items():
            with self.subTest(command=name):
                order, patches = self.record()
                for p in patches:
                    p.start()
                try:
                    run_main(*argv)
                except ValueError:                                                # gallery on v3: D-1 after start-up
                    pass
                finally:
                    for p in patches:
                        p.stop()
                expected = ['fs', 'lock'] if name.startswith('recover-archive') else ['fs', 'lock', 'journal']
                self.assertEqual(order[:len(expected)], expected)
                if name.startswith('recover-archive'):
                    self.assertNotIn('journal', order)                            # exempt_journal


class LegacyFolderTest(unittest.TestCase):
    """v1/v2 folders (14.7 per-command table): start-up first; approve refuses after it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-wb-legacy-')
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name))
        self.work = root / 'out'
        self.work.mkdir()
        (self.work / '.photo-curator-output').write_text('m', encoding='utf-8')
        self.original = root / 'lib' / 'A.JPG'
        self.original.parent.mkdir()
        self.original.write_bytes(b'x')

    def write(self, version):
        path = self.work / 'ratings-review.json'
        r = {'path': str(self.original), 'rating': 3, 'decision': 'usable', 'confidence': 0.7, 'reasons': [], 'flags': []}
        if version == 2:
            r.update(original_path=str(self.original), session_id='s')
        path.write_text(json.dumps({'schema_version': version, 'results': [r], 'errors': []}), encoding='utf-8')
        return path

    def test_lock_created_and_approve_refuses_v1_v2(self):
        for version in (1, 2):
            with self.subTest(version=version):
                results = self.write(version)
                lock = self.work / LOCK_NAME
                if lock.exists():
                    lock.unlink()
                code, _, _ = run_main('review', str(results))
                self.assertEqual(code, 0)
                self.assertTrue(lock.exists())                                   # 14.7 difference 3
                before = sorted(os.listdir(self.work))
                for argv in (('approve', str(results), str(self.original), '--show'),
                             ('approve', str(results), str(self.original), '--revoke')):
                    code, _, err = run_main(*argv)
                    self.assertEqual(code, 1)
                    self.assertIn('schema version 3', err)
                    self.assertEqual(sorted(os.listdir(self.work)), before)      # no backup, temp or change

    def test_journal_refuses_v1_v2_apply(self):
        for version in (1, 2):
            with self.subTest(version=version):
                results = self.write(version)
                (self.work / JOURNAL_NAME).write_bytes(b'{}')
                code, _, err = run_main('apply', str(results), '--yes')
                (self.work / JOURNAL_NAME).unlink()
                self.assertEqual(code, 1)
                self.assertIn(JOURNAL_MESSAGE, err)
                self.assertFalse(Path(str(self.original) + '.xmp').exists())


if __name__ == '__main__':
    unittest.main()
