"""W-C: ``photo-curator coverage <results>`` through the CLI (CLI-10; R5 lock matrix: shared, not
journal-exempt; spec 7.3, 12.6, 12.8, 14.7, V46; spec 20 items 16 and 22 CLI). Synthetic complete
v3 output in temporary folders only; no real library, no XMP."""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import coverage_v3, core, hashing_v3, locking_v3
from photo_curator.coverage_v3 import build_report
from photo_curator.locking_v3 import BUSY_MESSAGE, JOURNAL_MESSAGE, JOURNAL_NAME, LOCK_NAME, acquire_lock
from photo_curator.output_v3 import MANIFEST_NAME
from tests.test_cli_wiring_v3 import run_main, subcommands
from tests.test_reader_v3_file import Output


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and p.name != LOCK_NAME}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-wc-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.work = self.root / 'out'
        self.work.mkdir()
        self.out = Output(self.work)
        self.results = self.work / 'ratings-review.json'
        self.lock = self.work / LOCK_NAME
        if self.lock.exists():
            self.lock.unlink()

    def coverage(self, path=None):
        return run_main('coverage', str(path or self.results))


class HelpTest(unittest.TestCase):
    def test_coverage_listed_with_help_text(self):
        self.assertIn('coverage', subcommands())
        _, out, _ = run_main('--help')
        self.assertIn('Read-only coverage report from v3 results (facts only)', out)


class SuccessTest(Base):
    def test_stdout_equals_report_lines_and_nothing_but_the_lock_written(self):
        before = tree(self.root)
        expected = build_report(self.results).lines
        code, out, err = self.coverage()
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), expected)
        self.assertEqual(err, '')
        self.assertEqual(tree(self.root), before)
        self.assertTrue(self.lock.is_file())                                      # the only side effect
        self.assertFalse(list(self.root.rglob('*.xmp*')))

    def test_orphan_and_recount_mismatch_are_warnings_exit_0_not_deleted(self):
        extra = self.work / 'previews' / 'hand-placed.jpg'
        extra.write_bytes(b'x')
        code, out, err = self.coverage()
        self.assertEqual(code, 0, err)
        self.assertIn('hand-placed.jpg', out)
        self.assertIn('Warning: preview recount', out)
        self.assertEqual(err, '')
        self.assertTrue(extra.exists())


class RefusalTest(Base):
    def assertRefused(self, fragment, path=None):
        before = tree(self.root)
        code, out, err = self.coverage(path)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertTrue(err.startswith('photo-curator: '), err)
        self.assertIn(fragment, err)
        self.assertEqual(tree(self.root), before)
        return err

    def test_manifest_missing(self):
        os.unlink(self.work / MANIFEST_NAME)
        self.assertRefused('analysis output incomplete')

    def test_csv_mismatch(self):
        csv = next(self.work.glob('*.csv'))
        csv.write_bytes(csv.read_bytes() + b'tampered\n')
        self.assertRefused('analysis output incomplete')

    def test_malformed_json(self):
        self.results.write_bytes(b'{not json')
        self.assertRefused('photo-curator: ')

    def test_v1_v2_refused_after_startup(self):
        legacy = self.root / 'legacy'
        legacy.mkdir()
        for version in (1, 2):
            with self.subTest(version=version):
                path = legacy / 'ratings-review.json'
                path.write_text(json.dumps({'schema_version': version, 'results': [], 'errors': []}), encoding='utf-8')
                listing = sorted(os.listdir(legacy))
                self.assertRefused('schema version 3 only', path)
                self.assertTrue((legacy / LOCK_NAME).is_file())                   # start-up ran first
                self.assertEqual(sorted(n for n in os.listdir(legacy) if n != LOCK_NAME),
                                 [n for n in listing if n != LOCK_NAME])


class StartupTest(Base):
    def test_network_filesystem_refused_before_the_lock(self):
        before = sorted(os.listdir(self.work))
        with mock.patch.object(locking_v3, 'filesystem_type', return_value='nfs'):
            code, _, err = self.coverage()
        self.assertEqual(code, 1)
        self.assertIn('network filesystem (nfs)', err)
        self.assertFalse(self.lock.exists())
        self.assertEqual(sorted(os.listdir(self.work)), before)

    def test_busy_under_exclusive_holder(self):
        holder = acquire_lock(self.work, 'exclusive')
        try:
            code, out, err = self.coverage()
        finally:
            holder.release()
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn(BUSY_MESSAGE, err)

    def test_runs_together_with_a_shared_holder(self):
        holder = acquire_lock(self.work, 'shared')
        self.addCleanup(holder.release)
        code, _, err = self.coverage()
        self.assertEqual(code, 0, err)

    def test_takes_the_shared_lock(self):
        modes = []
        real = locking_v3.acquire_lock
        with mock.patch.object(locking_v3, 'acquire_lock', side_effect=lambda w, m: (modes.append(m), real(w, m))[1]):
            code, _, err = self.coverage()
        self.assertEqual(code, 0, err)
        self.assertEqual(modes, ['shared'])

    def test_journal_refused_before_the_results_file_is_parsed(self):
        (self.work / JOURNAL_NAME).write_bytes(b'{}')
        self.results.write_bytes(b'{not json')                                     # proves no parse first
        code, out, err = self.coverage()
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn(JOURNAL_MESSAGE, err)

    def test_order_filesystem_lock_journal_then_read(self):
        order = []
        names = {'check_filesystem': 'fs', 'acquire_lock': 'lock', 'check_no_archive_journal': 'journal'}
        real = {n: getattr(locking_v3, n) for n in names}
        real_read = coverage_v3.read_results

        def wrap(name):
            return lambda *a, **k: (order.append(names[name]), real[name](*a, **k))[1]
        patches = [mock.patch.object(locking_v3, n, side_effect=wrap(n)) for n in names]
        patches.append(mock.patch.object(coverage_v3, 'read_results',
                                         side_effect=lambda *a, **k: (order.append('read'), real_read(*a, **k))[1]))
        for p in patches:
            p.start()
        try:
            code, _, err = self.coverage()
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(code, 0, err)
        self.assertEqual(order, ['fs', 'lock', 'journal', 'read'])


class SafetyTest(Base):
    def test_no_metadata_no_original_hash_no_delete_move_or_xmp(self):
        before = tree(self.root)
        boom = AssertionError('forbidden call')
        with mock.patch.object(core, 'capture_records', side_effect=boom), \
             mock.patch.object(core, 'run', side_effect=boom), \
             mock.patch.object(hashing_v3, 'sha256_file', side_effect=boom), \
             mock.patch.object(os, 'unlink', side_effect=boom), mock.patch.object(os, 'remove', side_effect=boom), \
             mock.patch.object(os, 'rename', side_effect=boom), mock.patch.object(os, 'replace', side_effect=boom), \
             mock.patch.object(shutil, 'move', side_effect=boom):
            code, out, err = self.coverage()
        self.assertEqual(code, 0, err)
        self.assertTrue(out)
        self.assertEqual(tree(self.root), before)
        self.assertFalse(list(self.root.rglob('*.xmp*')))


if __name__ == '__main__':
    unittest.main()
