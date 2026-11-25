"""S13: approve / revoke writing, steps 1-10 of spec 12.3 with both failure classes, the
re-approval table in action, isolation and safety (spec 20 item 21).

Synthetic output folder from the real S3-S10 code; one real synthetic original in a temporary
library. Every failure test asserts the exit class and the state of the results file and backup.
"""
from __future__ import annotations
import hashlib, json, os, stat, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from photo_curator import approve_v3, atomic_v3, hashing_v3
from photo_curator.approve_v3 import (DURABILITY_MESSAGE, ORIGINAL_CHANGED, VERIFICATION_MESSAGE, ApproveFailure,
                                      ApproveRefusal, approve_item, backup_name, revoke_item)
from photo_curator.core import OUTPUT_MARKER
from photo_curator.locking_v3 import OutputBusyRefusal, acquire_lock
from photo_curator.preview_checks_v3 import expected_preview_name
from photo_curator.reader_v3 import read_results
from photo_curator.schema_v3 import APPROVAL_FIELDS
from tests.test_document_v3 import original
from tests.test_reader_v3_file import Output

NOW = datetime(2026, 11, 15, 8, 0, 0, 123456, tzinfo=timezone.utc)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fixture:
    """Output with frame 1 (needs review) bound to a real original in ``library``."""

    def __init__(self, root: Path):
        self.root = root
        self.work = root / 'out'
        self.work.mkdir()
        self.out = Output(self.work)
        self.library = root / 'library'
        self.library.mkdir()
        self.original = self.library / 'DSC00001.ARW'
        self.original.write_bytes(b'synthetic raw bytes for approval')
        self.index = next(k for k, r in enumerate(self.out.doc['results']) if r['original_path'] == original(1))
        r = self.out.doc['results'][self.index]
        new_preview = Path(r['preview_path']).with_name(expected_preview_name(str(self.original)))
        os.replace(r['preview_path'], new_preview)
        r.update(path=str(self.original), original_path=str(self.original), preview_path=str(new_preview),
                 original_sha256=sha(self.original.read_bytes()))
        self.out.doc['source_folder'] = str(self.library)
        self.out.rewrite()
        self.results = self.work / 'ratings-review.json'

    @property
    def item(self) -> dict:
        return json.loads(self.results.read_text(encoding='utf-8'))['results'][self.index]

    def preview_sha(self) -> str:
        return sha(Path(self.item['preview_path']).read_bytes())

    def approve(self, **kw):
        kw.setdefault('rating', self.item['rating'])
        kw.setdefault('preview_sha256_arg', self.preview_sha())
        kw.setdefault('now', NOW)
        return approve_item(self.results, str(self.original), **kw)

    def edit_item(self, **changes):
        doc = json.loads(self.results.read_text(encoding='utf-8'))
        doc['results'][self.index].update(changes)
        self.out.doc = doc
        self.out.rewrite()

    def backups(self) -> list[str]:
        return sorted(n for n in os.listdir(self.work) if n.endswith('.photo-curator.bak'))

    def temps(self) -> list[str]:
        return [n for n in os.listdir(self.work) if '.tmp-' in n]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s13-')
        self.addCleanup(tmp.cleanup)
        self.fx = Fixture(Path(os.path.realpath(tmp.name)))

    def assert_refused_unchanged(self, call, contains=''):
        before = self.fx.results.read_bytes()
        backups = self.fx.backups()
        with self.assertRaises(ApproveRefusal) as ctx:
            call()
        self.assertIn(contains, ctx.exception.message)
        self.assertEqual(self.fx.results.read_bytes(), before)
        self.assertEqual(self.fx.temps(), [])
        return ctx.exception, backups


class SuccessTest(Base):
    def test_approve_steps_1_to_10(self):
        old_raw = self.fx.results.read_bytes()
        old_doc = json.loads(old_raw)
        result = self.fx.approve()
        self.assertEqual((result.state_before, result.state_after), ('pending', 'approved'))
        self.assertEqual(Path(result.backup).name, Path(backup_name(str(self.fx.results), NOW)).name)
        self.assertRegex(Path(result.backup).name, r'^ratings-review\.json\.\d{8}T\d{6}\.\d{6}Z\.photo-curator\.bak$')
        self.assertEqual(Path(result.backup).read_bytes(), old_raw)             # backup = H0 bytes
        item = self.fx.item
        self.assertEqual((item['approved'], item['approved_rating'], item['approved_at']), (True, item['rating'], '2026-11-15T08:00:00Z'))
        self.assertEqual(item['approved_original_sha256'], item['original_sha256'])
        self.assertEqual(item['approved_preview_sha256'], self.fx.preview_sha())
        self.assertEqual(read_results(self.fx.results).items[self.fx.index].approval_state, 'approved')
        self.assertEqual(self.fx.temps(), [])
        acquire_lock(self.fx.work, 'exclusive').release()                       # lock released

    def test_isolation_only_target_approval_fields_and_editorial_suggestion(self):
        old = json.loads(self.fx.results.read_bytes())
        self.fx.approve()
        new = json.loads(self.fx.results.read_bytes())
        self.assertEqual({k: v for k, v in old.items() if k != 'results'}, {k: v for k, v in new.items() if k != 'results'})
        for k, (a, b) in enumerate(zip(old['results'], new['results'])):
            if k != self.fx.index:
                self.assertEqual(a, b)
            else:
                changed = {key for key in a if a[key] != b[key]}
                self.assertTrue(changed <= set(APPROVAL_FIELDS) | {'editorial_suggestion'}, changed)

    def test_revoke_approved_and_invalidated(self):
        self.fx.approve()
        result = revoke_item(self.fx.results, str(self.fx.original), now=NOW.replace(microsecond=1))
        self.assertEqual((result.state_before, result.state_after), ('approved', 'pending'))
        item = self.fx.item
        self.assertTrue(item['approved'] is False and all(item[f] is None for f in APPROVAL_FIELDS[1:]))
        self.assertEqual(len(self.fx.backups()), 2)
        self.fx.approve(now=NOW.replace(microsecond=2))
        self.fx.edit_item(approved_preview_path=str(self.fx.work / 'previews' / 'other.jpg'))   # binding check 4
        result = revoke_item(self.fx.results, str(self.fx.original), now=NOW.replace(microsecond=3))
        self.assertEqual(result.state_before, 'invalidated')

    def test_reapprove_invalidated_directly(self):
        self.fx.approve()
        self.fx.edit_item(approved_preview_path=str(self.fx.work / 'previews' / 'other.jpg'))
        result = self.fx.approve(now=NOW.replace(microsecond=9))
        self.assertEqual((result.state_before, result.state_after), ('invalidated', 'approved'))
        self.assertEqual(self.fx.item['approved_preview_path'], self.fx.item['preview_path'])


class TableRefusalTest(Base):
    def test_approve_on_approved_refused(self):
        self.fx.approve()
        self.assert_refused_unchanged(lambda: self.fx.approve(now=NOW.replace(microsecond=5)), 'already approved')

    def test_revoke_refused_on_pending_not_required_malformed(self):
        self.assert_refused_unchanged(lambda: revoke_item(self.fx.results, str(self.fx.original)), 'no-op')
        plain = next(r for r in self.fx.out.doc['results'] if r['needs_review'] is False)
        self.assert_refused_unchanged(lambda: revoke_item(self.fx.results, plain['original_path']), 'no-op')
        self.fx.edit_item(approved='true')
        self.assert_refused_unchanged(lambda: revoke_item(self.fx.results, str(self.fx.original)), 'repair')
        self.assert_refused_unchanged(lambda: self.fx.approve(), 'fix by hand')


class ApproveRefusalTest(Base):
    """12.3 refusals: no change, no backup, results byte-identical."""

    def test_refusals(self):
        cases = {
            'rating mismatch': (lambda: self.fx.approve(rating=5), 'differs'),
            'rating missing': (lambda: self.fx.approve(rating=None), 'missing'),
            'preview hash format': (lambda: self.fx.approve(preview_sha256_arg='ABC'), '64 lowercase hex'),
            'preview hash mismatch': (lambda: self.fx.approve(preview_sha256_arg='e' * 64), 'current preview'),
            'unknown original': (lambda: approve_item(self.fx.results, '/nowhere/X.ARW', rating=3,
                                                      preview_sha256_arg='e' * 64), 'matches 0 items'),
        }
        for label, (call, text) in cases.items():
            with self.subTest(label=label):
                _, backups = self.assert_refused_unchanged(call, text)
                self.assertEqual(self.fx.backups(), backups)

    def test_rating_zero(self):
        unevaluable = original(9)
        self.assert_refused_unchanged(lambda: approve_item(self.fx.results, unevaluable, rating=0,
                                                           preview_sha256_arg='e' * 64), 'rating 0')

    def test_preview_checks_fail(self):
        os.unlink(self.fx.work / 'previews' / OUTPUT_MARKER)
        preview_hash = self.fx.preview_sha()
        self.assert_refused_unchanged(lambda: self.fx.approve(preview_sha256_arg=preview_hash), 'PV2')

    def test_original_missing_changed_unreadable(self):
        self.fx.original.write_bytes(b'edited after analysis')
        self.assert_refused_unchanged(lambda: self.fx.approve(), ORIGINAL_CHANGED)
        self.fx.original.unlink()
        self.assert_refused_unchanged(lambda: self.fx.approve(), 'missing')
        self.fx.original.write_bytes(b'synthetic raw bytes for approval')
        with mock.patch.object(hashing_v3, 'sha256_file', side_effect=hashing_v3.HashError('x')):
            self.assert_refused_unchanged(lambda: self.fx.approve(), 'unreadable')

    def test_inconsistent_item(self):
        self.fx.edit_item(decision='strong')
        self.assert_refused_unchanged(lambda: self.fx.approve(), 'inconsistent')

    def test_symlinked_results_and_outside_output_folder(self):
        real = self.fx.work / 'real.json'
        os.replace(self.fx.results, real)
        os.symlink(real, self.fx.results)
        with self.assertRaises(ApproveRefusal) as ctx:
            self.fx.approve(preview_sha256_arg='e' * 64, rating=3)
        self.assertIn('symlink', ctx.exception.message)
        os.unlink(self.fx.results)
        os.replace(real, self.fx.results)
        os.unlink(self.fx.work / OUTPUT_MARKER)
        self.assert_refused_unchanged(lambda: self.fx.approve(), 'not inside a Photo Curator output folder')

    def test_lock_held(self):
        lock = acquire_lock(self.fx.work, 'shared')
        self.addCleanup(lock.release)
        before = self.fx.results.read_bytes()
        with self.assertRaises(OutputBusyRefusal):
            self.fx.approve()
        self.assertEqual(self.fx.results.read_bytes(), before)

    def test_no_bulk_input(self):
        import inspect
        params = inspect.signature(approve_item).parameters
        self.assertEqual(list(params)[:2], ['results_path', 'original_path'])
        self.assert_refused_unchanged(lambda: approve_item(self.fx.results, [str(self.fx.original)], rating=3,
                                                           preview_sha256_arg='e' * 64), 'matches 0 items')


class ConcurrentModificationTest(Base):
    def test_results_changed_between_read_and_replace(self):
        real_write = approve_v3.atomic_write
        other = self.fx.results.read_bytes() + b'\n'          # a valid edit by an outside program (H0 differs)

        def sneaky(path, data, **kw):
            Path(path).write_bytes(other)                                    # outside program writes
            return real_write(path, data, **kw)
        with mock.patch.object(approve_v3, 'atomic_write', side_effect=sneaky), self.assertRaises(ApproveRefusal) as ctx:
            self.fx.approve()
        self.assertIn('changed since it was read', ctx.exception.message)
        self.assertEqual(self.fx.results.read_bytes(), other)                    # other writer's content kept
        self.assertEqual(self.fx.temps(), [])
        self.assertEqual(len(self.fx.backups()), 1)


class BackupFailureTest(Base):
    def test_existing_backup_name(self):
        existing = Path(backup_name(str(self.fx.results), NOW))
        existing.write_bytes(b'older backup')
        self.assert_refused_unchanged(lambda: self.fx.approve(), 'backup could not be created')
        self.assertEqual(existing.read_bytes(), b'older backup')                 # never overwritten

    def test_backup_fsync_failure_removes_partial(self):
        with mock.patch.object(approve_v3.os, 'fsync', side_effect=OSError(5, 'Input/output error')):
            self.assert_refused_unchanged(lambda: self.fx.approve(), 'backup failed')
        self.assertEqual(self.fx.backups(), [])

    def test_backup_hash_mismatch_removes_partial(self):
        real = approve_v3._sha
        calls = []

        def wrong(data):
            calls.append(1)
            return 'f' * 64 if len(calls) == 2 else real(data)                   # 1: H0, 2: backup re-read
        with mock.patch.object(approve_v3, '_sha', side_effect=wrong):
            self.assert_refused_unchanged(lambda: self.fx.approve(), 'differs from H0')
        self.assertEqual(self.fx.backups(), [])

    def test_unwritable_folder(self):
        acquire_lock(self.fx.work, 'exclusive').release()    # lock file exists, so the lock can be taken
        os.chmod(self.fx.work, 0o555)
        self.addCleanup(os.chmod, self.fx.work, 0o755)
        if os.access(self.fx.work, os.W_OK):
            self.skipTest('running with permissions that ignore the mode')
        self.assert_refused_unchanged(lambda: self.fx.approve(), 'backup could not be created')


class BeforeReplacementFailureTest(Base):
    def test_each_failure(self):
        for label, target in (('temporary write', '_write_all'), ('temporary fsync', '_fsync_file'),
                              ('replace raises', '_replace')):
            with self.subTest(label=label):
                self.setUp()
                old = self.fx.results.read_bytes()
                with mock.patch.object(atomic_v3, target, side_effect=OSError(5, 'simulated')):
                    error, _ = self.assert_refused_unchanged(lambda: self.fx.approve(), 'not written')
                self.assertEqual(Path(error.backup).read_bytes(), old)            # backup kept, verified

    def test_read_back_validation_failure(self):
        old = self.fx.results.read_bytes()
        real = approve_v3.read_results
        calls = []

        def failing(path, **kw):
            calls.append(path)
            report = real(path, **kw)
            if str(path).count('.tmp-'):
                report.refusal = 'simulated read-back failure'
            return report
        with mock.patch.object(approve_v3, 'read_results', side_effect=failing):
            error, _ = self.assert_refused_unchanged(lambda: self.fx.approve(), 'simulated read-back failure')
        self.assertEqual(Path(error.backup).read_bytes(), old)


class AfterReplacementFailureTest(Base):
    def test_durability_failure_skips_step_10(self):
        old = self.fx.results.read_bytes()
        real = approve_v3.read_results
        calls = []
        replace = mock.Mock(wraps=atomic_v3._replace)
        with mock.patch.object(atomic_v3, '_fsync_dir', side_effect=OSError(5, 'simulated')), \
             mock.patch.object(atomic_v3, '_replace', replace), \
             mock.patch.object(approve_v3, 'read_results', side_effect=lambda p, **k: (calls.append(p), real(p, **k))[1]), \
             self.assertRaises(ApproveFailure) as ctx:
            self.fx.approve()
        self.assertEqual((ctx.exception.failure_class, ctx.exception.message), ('durability', DURABILITY_MESSAGE))
        self.assertEqual(len(calls), 2)                                            # load + read-back; no step 10
        self.assertEqual(replace.call_count, 1)                                    # no second replacement
        self.assertNotEqual(self.fx.results.read_bytes(), old)                     # holds the new document
        self.assertTrue(json.loads(self.fx.results.read_bytes())['results'][self.fx.index]['approved'])
        self.assertEqual(Path(ctx.exception.backup).read_bytes(), old)
        self.assertEqual(self.fx.temps(), [])

    def test_verification_failure_no_rollback(self):
        old = self.fx.results.read_bytes()
        real = approve_v3.read_results
        calls = []

        def third_fails(path, **kw):
            calls.append(path)
            report = real(path, **kw)
            if len(calls) == 3:
                report.refusal = 'simulated verification failure'
            return report
        with mock.patch.object(approve_v3, 'read_results', side_effect=third_fails), self.assertRaises(ApproveFailure) as ctx:
            self.fx.approve()
        self.assertEqual((ctx.exception.failure_class, ctx.exception.message), ('verification', VERIFICATION_MESSAGE))
        self.assertEqual(Path(ctx.exception.backup).read_bytes(), old)
        self.assertTrue(json.loads(self.fx.results.read_bytes())['results'][self.fx.index]['approved'])   # no rollback


class SafetyTest(Base):
    def test_original_only_opened_read_only_and_unchanged(self):
        before = (self.fx.original.read_bytes(), os.stat(self.fx.original).st_mtime_ns)
        real_open, flags = os.open, []
        with mock.patch.object(hashing_v3.os, 'open', side_effect=lambda p, f, *a: (flags.append((p, f)), real_open(p, f, *a))[1]):
            self.fx.approve()
        on_original = [f for p, f in flags if p == str(self.fx.original)]
        self.assertEqual(len(on_original), 1)                                      # hashed exactly once
        writes = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        self.assertTrue(all(f & writes == 0 for f in on_original))
        self.assertEqual((self.fx.original.read_bytes(), os.stat(self.fx.original).st_mtime_ns), before)
        self.assertFalse(list(self.fx.root.rglob('*.xmp*')))


if __name__ == '__main__':
    unittest.main()
