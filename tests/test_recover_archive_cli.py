"""W-B: ``recover-archive`` through the CLI (spec 12.9, CLI-9; spec 20 item 16b CLI). Partial archive
states come from a real ``archive_existing`` run with one failing rename; temporary folders only."""
from __future__ import annotations
import os, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from photo_curator import archive_v3
from photo_curator.archive_v3 import ArchiveFailure, archive_existing
from photo_curator.locking_v3 import JOURNAL_NAME, acquire_lock
from tests.test_archive_v3 import FOLDER, NOW, make_output, tree
from tests.test_cli_wiring_v3 import run_main


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-wb-rec-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        make_output(self.work)
        self.original = tree(self.work)

    def partial(self, fail_at: int = 3):
        real, calls = os.rename, []

        def rename(src, dst):
            calls.append(src)
            if len(calls) == fail_at:
                raise OSError(5, 'Input/output error')
            real(src, dst)
        lock = acquire_lock(self.work, 'exclusive')
        try:
            with mock.patch.object(archive_v3.os, 'rename', side_effect=rename), self.assertRaises(ArchiveFailure):
                archive_existing(self.work, lock, now=NOW)
        finally:
            lock.release()


class NoJournalTest(Base):
    def test_report_zero_and_complete_rollback_refused(self):
        code, out, _ = run_main('recover-archive', str(self.work))
        self.assertEqual(code, 0)
        self.assertIn('no archive recovery pending', out)
        for flag in ('--complete', '--rollback'):
            with self.subTest(flag=flag):
                code, _, err = run_main('recover-archive', str(self.work), flag)
                self.assertEqual(code, 1)
                self.assertIn('no archive in progress', err)


class PartialArchiveTest(Base):
    def test_report_shows_states_and_changes_nothing(self):
        self.partial()
        before = {k: v for k, v in tree(self.work).items() if k != 'ratings-review.json.lock'}
        code, out, _ = run_main('recover-archive', str(self.work))
        self.assertEqual(code, 0)
        self.assertIn('archive recovery pending', out)
        self.assertIn('done', out)
        self.assertIn('pending', out)
        self.assertEqual({k: v for k, v in tree(self.work).items() if k != 'ratings-review.json.lock'}, before)

    def test_complete(self):
        self.partial()
        code, out, err = run_main('recover-archive', str(self.work), '--complete')
        self.assertEqual(code, 0, err)
        self.assertIn('journal moved to', out)
        self.assertFalse((self.work / JOURNAL_NAME).exists())
        self.assertTrue((self.work / FOLDER / 'archive-journal.json').exists())

    def test_rollback(self):
        self.partial()
        code, out, err = run_main('recover-archive', str(self.work), '--rollback')
        self.assertEqual(code, 0, err)
        after = tree(self.work)
        for rel, digest in self.original.items():
            self.assertEqual(after.get(rel), digest, rel)
        self.assertTrue((self.work / FOLDER / 'archive-journal.rolled-back.json').exists())

    def test_refusal_exits_1_and_moves_nothing(self):
        self.partial()
        journal = archive_v3.json.loads((self.work / JOURNAL_NAME).read_text(encoding='utf-8'))
        (self.work / journal['entries'][0]['source']).mkdir()                       # conflict
        before = {k: v for k, v in tree(self.work).items() if k != 'ratings-review.json.lock'}
        code, _, err = run_main('recover-archive', str(self.work), '--complete')
        self.assertEqual(code, 1)
        self.assertIn('state conflict', err)
        self.assertEqual({k: v for k, v in tree(self.work).items() if k != 'ratings-review.json.lock'}, before)

    def test_other_commands_blocked_while_journal_exists(self):
        self.partial()
        code, _, err = run_main('review', str(self.work / 'ratings-review.json'))
        self.assertEqual(code, 1)
        self.assertIn('recover-archive', err)


if __name__ == '__main__':
    unittest.main()
