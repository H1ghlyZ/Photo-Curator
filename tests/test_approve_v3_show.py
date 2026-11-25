"""S13: ``approve --show [--open-preview]`` (spec 12.3 step 1, 12.6, M-14; spec 20 item 21 '--show').

The viewer is always a test double; the default viewer is checked through a fake ``core.run``.
"""
from __future__ import annotations
import hashlib, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import approve_v3, core
from photo_curator.approve_v3 import ORIGINAL_CHANGED, ApproveRefusal, show_item
from photo_curator.core import OUTPUT_MARKER
from photo_curator.locking_v3 import (JOURNAL_NAME, LOCK_NAME, ArchiveInProgressRefusal, OutputBusyRefusal,
                                      acquire_lock)
from photo_curator.output_v3 import MANIFEST_NAME
from tests.test_approve_v3_write import NOW, Fixture


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s13-show-')
        self.addCleanup(tmp.cleanup)
        self.fx = Fixture(Path(os.path.realpath(tmp.name)))

    def show(self, **kw):
        return show_item(self.fx.results, str(self.fx.original), **kw)


class ContentTest(Base):
    def test_prints_paths_hashes_checks_and_state(self):
        report = self.show()
        text = '\n'.join(report.lines)
        item = self.fx.item
        self.assertIn(f'original: {self.fx.original}', text)
        self.assertIn(f'original SHA-256 at analysis: {item["original_sha256"]}', text)
        self.assertIn(f'original SHA-256 now: {item["original_sha256"]}', text)
        self.assertTrue(report.original_matches)
        self.assertIn(f'preview: {item["preview_path"]}', text)
        for check in ('PV1', 'PV2', 'PV3', 'PV4', 'PV5', 'PV6'):
            self.assertIn(f'  {check}: ok', text)
        self.assertIn(f'preview SHA-256 now: {self.fx.preview_sha()}', text)
        self.assertIn(f'rating: {item["rating"]}', text)
        self.assertIn('approval state: pending', text)
        self.assertIn('review reasons:', text)
        self.assertIn('capture_time:', text)
        self.assertTrue(any(line.startswith('  evidence: ') for line in report.lines))

    def test_original_changed_is_shown(self):
        self.fx.original.write_bytes(b'edited')
        report = self.show()
        self.assertFalse(report.original_matches)
        self.assertIn(ORIGINAL_CHANGED, '\n'.join(report.lines))

    def test_binding_checks_in_diagnostic_order(self):
        self.fx.approve()
        self.fx.edit_item(approved_preview_path=str(self.fx.work / 'previews' / 'other.jpg'))   # check 4
        self.fx.original.write_bytes(b'edited')                                                  # check 3
        report = self.show()
        self.assertEqual(report.state.failing_check, 3)                           # --show hashes: 3 first
        self.assertIn('binding check 3', '\n'.join(report.lines))


class WritesNothingTest(Base):
    def test_only_the_lock_file_may_appear(self):
        lock = self.fx.work / LOCK_NAME
        if lock.exists():
            lock.unlink()
        before = tree(self.fx.root)
        self.show()
        after = tree(self.fx.root)
        self.assertEqual({k: v for k, v in after.items() if k != f'out/{LOCK_NAME}'}, before)
        self.assertTrue(lock.exists())
        again_before = tree(self.fx.root)
        self.show()
        self.assertEqual(tree(self.fx.root), again_before)                       # lock present: fully unchanged


class LockTest(Base):
    def test_refused_while_exclusive_holder_active(self):
        lock = acquire_lock(self.fx.work, 'exclusive')
        self.addCleanup(lock.release)
        with self.assertRaises(OutputBusyRefusal):
            self.show()

    def test_runs_alongside_another_shared_holder(self):
        lock = acquire_lock(self.fx.work, 'shared')
        self.addCleanup(lock.release)
        self.assertTrue(self.show().original_matches)

    def test_blocks_approve_while_running(self):
        seen = {}

        def viewer(path):                                                         # runs while --show holds the lock
            with self.assertRaises(OutputBusyRefusal):
                self.fx.approve()
            seen['preview'] = path
        self.show(open_preview=True, opener=viewer)
        self.assertEqual(seen['preview'], self.fx.item['preview_path'])


class OpenPreviewTest(Base):
    def test_valid_preview_opened_never_the_original(self):
        opened = []
        report = self.show(open_preview=True, opener=opened.append)
        self.assertTrue(report.opened)
        self.assertEqual(opened, [self.fx.item['preview_path']])
        self.assertNotIn(str(self.fx.original), opened)

    def test_refuses_to_open_when_a_preview_check_fails(self):
        os.unlink(self.fx.work / 'previews' / OUTPUT_MARKER)
        opened = []
        report = self.show(open_preview=True, opener=opened.append)
        self.assertEqual(opened, [])
        self.assertFalse(report.opened)
        self.assertIn('PV2', report.open_refusal)

    def test_default_viewer_is_xdg_open_on_the_preview(self):
        with mock.patch.object(core, 'run') as run:
            self.show(open_preview=True)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['xdg-open', self.fx.item['preview_path']])


class RefusalTest(Base):
    def test_incomplete_output(self):
        os.unlink(self.fx.work / MANIFEST_NAME)
        with self.assertRaises(ApproveRefusal) as ctx:
            self.show()
        self.assertIn('analysis output incomplete', ctx.exception.message)

    def test_archive_in_progress(self):
        (self.fx.work / JOURNAL_NAME).write_bytes(b'{}')
        with self.assertRaises(ArchiveInProgressRefusal):
            self.show()

    def test_ambiguous_or_unknown_original(self):
        with self.assertRaises(ApproveRefusal):
            show_item(self.fx.results, '/nowhere/X.ARW')


if __name__ == '__main__':
    unittest.main()
