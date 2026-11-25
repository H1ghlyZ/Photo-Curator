"""S7b: recover-archive library (spec 12.9 recovery, no-journal rules, schema boundary; 12.6 lock
rows; spec 20 item 16b, library part).

Partial archive states are produced with a real ``archive_existing`` run whose rename fails at
a chosen position. Synthetic temporary folders only; nothing is wired into the CLI.
"""
from __future__ import annotations
import json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import archive_v3
from photo_curator.archive_v3 import (ARCHIVED_JOURNAL, NO_ARCHIVE_MESSAGE, ROLLED_BACK_JOURNAL, ArchiveFailure,
                                      RecoveryFailure, RecoveryRefusal, archive_existing, recover_complete,
                                      recover_report, recover_rollback)
from photo_curator.locking_v3 import (JOURNAL_NAME, LOCK_NAME, ArchiveInProgressRefusal, NetworkFilesystemRefusal,
                                      OutputBusyRefusal, acquire_lock, startup_v3)

from tests.test_archive_v3 import FOLDER, LISTED, NOW, make_output, tree


def without_lock(snapshot: dict) -> dict:
    return {k: v for k, v in snapshot.items() if k != LOCK_NAME}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s7b-rec-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        make_output(self.work)
        self.original = tree(self.work)

    def partial(self, fail_at: int) -> ArchiveFailure:
        """Archive with the ``fail_at``-th rename failing (1-based); returns the failure."""
        real, calls = os.rename, []

        def rename(src, dst):
            calls.append(src)
            if len(calls) == fail_at:
                raise OSError(5, 'Input/output error')
            real(src, dst)
        lock = acquire_lock(self.work, 'exclusive')
        try:
            with mock.patch.object(archive_v3.os, 'rename', side_effect=rename), \
                 self.assertRaises(ArchiveFailure) as ctx:
                archive_existing(self.work, lock, now=NOW)
        finally:
            lock.release()
        return ctx.exception

    def journal(self) -> dict:
        return json.loads((self.work / JOURNAL_NAME).read_text(encoding='utf-8'))

    def write_journal(self, journal) -> None:
        (self.work / JOURNAL_NAME).write_text(json.dumps(journal), encoding='utf-8')


class ReportTest(Base):
    def test_states_and_nothing_changed(self):
        failure = self.partial(3)
        before = tree(self.work)
        report = recover_report(self.work)
        self.assertTrue(report.journal_present and report.well_formed and report.archive_folder_ok)
        states = {e.source: e.state for e in report.entries}
        self.assertEqual({s for s, st in states.items() if st == 'done'}, set(failure.done))
        self.assertEqual({s for s, st in states.items() if st == 'pending'}, set(failure.pending))
        self.assertTrue(all(e.verified for e in report.entries))
        self.assertEqual(without_lock(tree(self.work)), without_lock(before))   # only the lock file may appear

    def test_conflict_and_lost_reported(self):
        self.partial(3)
        journal = self.journal()
        done, pending = journal['entries'][0], journal['entries'][2]
        (self.work / done['source']).mkdir()                                   # conflict
        (self.work / pending['source']).unlink()                               # lost
        states = {e.source: e.state for e in recover_report(self.work).entries}
        self.assertEqual(states[done['source']], 'conflict')
        self.assertEqual(states[pending['source']], 'lost')

    def test_takes_shared_lock(self):
        self.partial(2)
        reader = acquire_lock(self.work, 'shared')
        self.addCleanup(reader.release)
        recover_report(self.work)                                              # runs alongside a reader
        reader.release()
        writer = acquire_lock(self.work, 'exclusive')
        self.addCleanup(writer.release)
        with self.assertRaises(OutputBusyRefusal):
            recover_report(self.work)

    def test_malformed_journal_reported_not_acted_on(self):
        self.partial(2)
        self.write_journal({'archive_folder': '../../etc', 'entries': []})
        before = tree(self.work)
        report = recover_report(self.work)
        self.assertFalse(report.well_formed)
        self.assertTrue(report.problems)
        self.assertEqual(without_lock(tree(self.work)), without_lock(before))


class NoJournalTest(Base):
    def check_no_journal(self):
        before = tree(self.work)
        report = recover_report(self.work)
        self.assertFalse(report.journal_present)
        self.assertEqual(report.message, f'no archive recovery pending in {self.work}')
        for action in (recover_complete, recover_rollback):
            with self.assertRaises(RecoveryRefusal) as ctx:
                action(self.work)
            self.assertEqual(ctx.exception.message, NO_ARCHIVE_MESSAGE)
        self.assertEqual(without_lock(tree(self.work)), without_lock(before))

    def test_no_archive_history(self):
        self.check_no_journal()

    def test_after_completed_archive_journal_not_read(self):
        lock = acquire_lock(self.work, 'exclusive')
        archive_existing(self.work, lock, now=NOW)
        lock.release()
        with mock.patch.object(archive_v3, '_read_journal_bytes', side_effect=AssertionError('read')):
            self.check_no_journal()

    def test_after_rolled_back_archive(self):
        self.partial(3)
        recover_rollback(self.work)
        self.assertTrue((self.work / FOLDER / ROLLED_BACK_JOURNAL).exists())
        with mock.patch.object(archive_v3, '_read_journal_bytes', side_effect=AssertionError('read')):
            self.check_no_journal()


class CompleteTest(Base):
    def test_complete_from_each_partial_state(self):
        for fail_at in range(1, len(LISTED) + 1):
            with self.subTest(fail_at=fail_at):
                self.setUp()
                failure = self.partial(fail_at)
                real, moved = os.rename, []
                with mock.patch.object(archive_v3.os, 'rename', side_effect=lambda s, d: (moved.append(s), real(s, d))):
                    result = recover_complete(self.work)
                self.assertEqual(list(result.moved), list(failure.pending))      # only pending entries
                self.assertEqual(result.journal_moved_to, f'{FOLDER}/{ARCHIVED_JOURNAL}')
                self.assertFalse((self.work / JOURNAL_NAME).exists())
                after = tree(self.work)
                for rel, digest in self.original.items():
                    where = f'{FOLDER}/{rel}' if rel.split('/')[0] in LISTED else rel
                    self.assertEqual(after.get(where), digest, rel)
                startup_v3(self.work, 'exclusive').lock.release()                # folder unblocked

    def test_complete_with_nothing_pending_runs_step6(self):
        self.partial(1)
        lock = acquire_lock(self.work, 'exclusive')
        lock.release()
        for entry in self.journal()['entries']:
            os.rename(self.work / entry['source'], self.work / entry['target'])
        result = recover_complete(self.work)
        self.assertEqual(result.moved, ())
        self.assertTrue((self.work / FOLDER / ARCHIVED_JOURNAL).exists())


class RollbackTest(Base):
    def test_rollback_from_each_partial_state(self):
        for fail_at in range(1, len(LISTED) + 1):
            with self.subTest(fail_at=fail_at):
                self.setUp()
                failure = self.partial(fail_at)
                result = recover_rollback(self.work)
                self.assertEqual(sorted(result.moved), sorted(failure.done))      # only done entries
                self.assertEqual(list(result.moved), list(reversed(failure.done)))
                after = tree(self.work)
                for rel, digest in self.original.items():                        # everything back
                    self.assertEqual(after.get(rel), digest, rel)
                self.assertTrue((self.work / FOLDER).is_dir())                   # archive folder kept
                self.assertTrue((self.work / FOLDER / ROLLED_BACK_JOURNAL).is_file())
                self.assertFalse((self.work / JOURNAL_NAME).exists())


class LockTest(Base):
    def test_complete_and_rollback_refused_while_reader_active(self):
        self.partial(3)
        reader = acquire_lock(self.work, 'shared')
        self.addCleanup(reader.release)
        before = tree(self.work)
        for action in (recover_complete, recover_rollback):
            with self.assertRaises(OutputBusyRefusal):
                action(self.work)
        self.assertEqual(tree(self.work), before)

    def test_journal_blocks_every_other_command(self):
        self.partial(3)
        for mode in ('shared', 'exclusive'):
            with self.assertRaises(ArchiveInProgressRefusal):
                startup_v3(self.work, mode)
        recover_report(self.work)                                    # recover-archive is exempt

    def test_network_filesystem_refused_before_any_change(self):
        self.partial(3)
        before = tree(self.work)
        info = Path(tempfile.mkdtemp(prefix='pc-s7b-mi-')) / 'mountinfo'
        self.addCleanup(lambda: (info.unlink(), info.parent.rmdir()))
        info.write_text(f'20 1 0:20 / / rw - ext4 src rw\n21 1 0:21 / {self.work} rw - nfs4 srv:/x rw\n',
                        encoding='utf-8')
        for action in (recover_report, recover_complete, recover_rollback):
            with self.assertRaises(NetworkFilesystemRefusal):
                action(self.work, mountinfo_path=str(info))
        self.assertEqual(tree(self.work), before)


class RefusalBeforeAnyMoveTest(Base):
    def assert_refused(self, contains: str):
        before = tree(self.work)
        rename = mock.Mock(side_effect=AssertionError('renamed'))
        for action in (recover_complete, recover_rollback):
            with mock.patch.object(archive_v3.os, 'rename', rename), self.assertRaises(RecoveryRefusal) as ctx:
                action(self.work)
            self.assertIn(contains, ctx.exception.message)
        rename.assert_not_called()
        self.assertEqual(without_lock(tree(self.work)), without_lock(before))

    def test_conflict(self):
        self.partial(3)
        (self.work / self.journal()['entries'][0]['source']).mkdir()
        self.assert_refused('state conflict')

    def test_lost(self):
        self.partial(3)
        (self.work / self.journal()['entries'][2]['source']).unlink()
        self.assert_refused('state lost')

    def test_malformed_journal(self):
        self.partial(3)
        good = self.journal()
        for bad in ({'archive_folder': good['archive_folder']},
                    dict(good, entries=[dict(good['entries'][1], target='/etc/passwd')]),
                    'not an object'):
            with self.subTest(bad=str(bad)[:40]):
                self.write_journal(bad)
                self.assert_refused('journal malformed')
        (self.work / JOURNAL_NAME).write_bytes(b'{oops')
        self.assert_refused('journal does not parse')

    def test_hash_mismatch(self):
        self.partial(3)
        journal = self.journal()
        done_file = next(e for e in journal['entries'][:2] if e['kind'] == 'file')
        pending_file = next(e for e in journal['entries'][2:] if e['kind'] == 'file')
        (self.work / done_file['target']).write_bytes(b'changed')
        (self.work / pending_file['source']).write_bytes(b'changed')
        self.assert_refused('SHA-256 differs from journal')

    def test_previews_listing_mismatch(self):
        self.partial(3)                                   # previews (entry 1) already done
        (self.work / FOLDER / 'previews' / 'extra.jpg').write_bytes(b'x')
        before = tree(self.work)
        with self.assertRaises(RecoveryRefusal) as ctx:
            recover_rollback(self.work)
        self.assertIn('previews listing differs from journal', ctx.exception.message)
        self.assertEqual(without_lock(tree(self.work)), without_lock(before))

    def test_archive_folder_missing(self):
        self.partial(1)                                   # nothing moved yet
        os.remove(self.work / FOLDER / '.photo-curator-output')
        os.rmdir(self.work / FOLDER)
        self.assert_refused('archive folder')


class FailureDuringRecoveryTest(Base):
    def test_rename_failure_stops_and_leaves_journal(self):
        self.partial(2)                                   # 1 done, 6 pending
        real, calls = os.rename, []

        def rename(src, dst):
            calls.append(src)
            if len(calls) == 2:
                raise OSError(5, 'Input/output error')
            real(src, dst)
        with mock.patch.object(archive_v3.os, 'rename', side_effect=rename), \
             self.assertRaises(RecoveryFailure) as ctx:
            recover_complete(self.work)
        self.assertEqual(len(ctx.exception.moved), 1)
        self.assertEqual(len(calls), 2)                    # no retry
        self.assertTrue((self.work / JOURNAL_NAME).exists())
        states = [e.state for e in recover_report(self.work).entries]
        self.assertEqual(states.count('done'), 2)
        self.assertEqual(states.count('pending'), 5)


class SchemaBoundaryTest(Base):
    VARIANTS = {
        'v1': b'{"schema_version": 1, "results": []}',
        'v2': b'{"schema_version": 2, "results": []}',
        'v3': b'{"schema_version": 3, "results": []}',
        'invalid json': b'{not json',
        'unknown version': b'{"schema_version": 99}',
    }

    def test_same_result_for_every_results_file(self):
        outcomes = {}
        for label, results in self.VARIANTS.items():
            for manifest in ('missing', 'corrupt'):
                with self.subTest(results=label, manifest=manifest):
                    self.setUp()
                    (self.work / 'ratings-review.json').write_bytes(results)
                    if manifest == 'missing':
                        (self.work / 'ratings-review.complete.json').unlink()
                    else:
                        (self.work / 'ratings-review.complete.json').write_bytes(b'\x00garbage')
                    self.partial(3)
                    journal_bytes = (self.work / JOURNAL_NAME).read_bytes()
                    parsed = []
                    real_loads = json.loads
                    with mock.patch.object(archive_v3.json, 'loads',
                                           side_effect=lambda s, *a, **k: (parsed.append(s), real_loads(s, *a, **k))[1]):
                        report = recover_report(self.work)
                        result = recover_complete(self.work)
                    self.assertEqual(parsed, [journal_bytes.decode('utf-8')] * 2)   # only the journal is parsed
                    self.assertTrue(all(e.verified for e in report.entries))
                    archived = self.work / FOLDER / 'ratings-review.json'
                    self.assertEqual(archived.read_bytes(), results)             # moved, never changed
                    outcomes[(label, manifest)] = (tuple(e.state for e in report.entries), len(result.moved))
        # A missing manifest is simply not on the move list, so compare across results-file
        # variants for each manifest condition.
        for manifest in ('missing', 'corrupt'):
            same = {v for (label, m), v in outcomes.items() if m == manifest}
            self.assertEqual(len(same), 1, manifest)


if __name__ == '__main__':
    unittest.main()
