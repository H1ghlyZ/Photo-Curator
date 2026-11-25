"""S7a: incomplete-output detection and refusals (spec 12.8, 12.7 refusal list, V30/V31, V46;
spec 20 item 16a first paragraph).

The commands that refuse (apply, approve, --show, review, coverage report, analyze) are wired
in later steps; here the library refusals they will call are tested: ``require_complete`` and
``refuse_existing_output``. Every check is read-only: listings and hashes are compared before
and after, and nothing is repaired. Synthetic temporary folders only.
"""
from __future__ import annotations
import contextlib, hashlib, json, os, tempfile, unittest, uuid
from pathlib import Path
from unittest import mock

from photo_curator import atomic_v3, output_v3
from photo_curator.core import OUTPUT_MARKER
from photo_curator.output_v3 import (ARTIFACT_ORDER, CSV_NAME, HTML_NAME, INCOMPLETE_MESSAGE, JOURNAL_NAME,
                                     MANIFEST_NAME, RESULTS_NAME, ApprovalSummary, ExistingOutputRefusal,
                                     IncompleteOutputRefusal, OutputFolderUnreadableRefusal,
                                     approval_state_without_files,
                                     existing_output_entries, refuse_existing_output, require_complete,
                                     write_analysis_outputs)

RUN_ID = str(uuid.UUID(int=0xABCDEF, version=4))
OTHER_RUN_ID = str(uuid.UUID(int=0x123456, version=4))


class Crash(BaseException):
    """Simulated process crash."""


def results_doc(work: Path, run_id=RUN_ID) -> dict:
    return {'schema_version': 3, 'rubric_version': 'v3', 'evidence_rules_version': 'v3.0', 'run_id': run_id,
            'confidence_threshold': 0.6, 'max_burst_size': 12, 'burst_window_seconds': 1.0,
            'burst_scope': 'per_session',
            'results': [{'path': '/lib/A.ARW', 'original_path': '/lib/A.ARW',
                         'preview_path': str(work / 'previews' / 'A__1.jpg'), 'rating': 3, 'approved': False,
                         'approved_rating': None}],
            'errors': []}


def encode(doc) -> bytes:
    return (json.dumps(doc, indent=2) + '\n').encode('utf-8')


def snapshot(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                out[rel] = 'link:' + os.readlink(full)
            elif os.path.isfile(full):
                out[rel] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
            else:
                out[rel] = 'dir'
    return out


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s7a-ref-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        (self.work / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        previews = self.work / 'previews'
        previews.mkdir()
        (previews / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        (previews / 'A__1.jpg').write_bytes(b'\xff\xd8 preview')

    def write_complete(self, doc=None):
        write_analysis_outputs(self.work, results_bytes=encode(doc or results_doc(self.work)),
                               csv_bytes=b'path,rating\n/lib/A.ARW,3\n', html_bytes=b'<html>A</html>\n')

    def assert_refused_read_only(self, *must_mention):
        before = snapshot(self.work)
        with self.assertRaises(IncompleteOutputRefusal) as ctx:
            require_complete(self.work)
        self.assertTrue(ctx.exception.message.startswith(INCOMPLETE_MESSAGE + ': '))
        for text in must_mention:
            self.assertIn(text, ctx.exception.message)
        with self.assertRaises(ExistingOutputRefusal):
            refuse_existing_output(self.work)            # analyze into the folder refuses
        self.assertEqual(snapshot(self.work), before)    # nothing repaired, nothing written
        return ctx.exception


class CrashTest(Base):
    def test_crash_after_each_of_the_first_three_writes(self):
        for stop_after in (RESULTS_NAME, CSV_NAME, HTML_NAME):
            with self.subTest(stop_after=stop_after):
                for name in ARTIFACT_ORDER:
                    (self.work / name).unlink(missing_ok=True)

                def hook(name, stop_after=stop_after):
                    if name == stop_after:
                        raise Crash()
                with self.assertRaises(Crash):
                    write_analysis_outputs(self.work, results_bytes=encode(results_doc(self.work)),
                                           csv_bytes=b'c\n', html_bytes=b'h\n', _test_after_artifact=hook)
                self.assertFalse((self.work / MANIFEST_NAME).exists())
                self.assert_refused_read_only(MANIFEST_NAME)

    def test_crash_during_each_of_the_four_writes(self):
        for during in ARTIFACT_ORDER:
            with self.subTest(during=during):
                for name in os.listdir(self.work):
                    if name in ARTIFACT_ORDER or '.tmp-' in name:
                        (self.work / name).unlink()
                real = atomic_v3._replace
                target = str(self.work / during)

                def replace(src, dst, target=target):
                    if dst == target:
                        raise Crash()
                    real(src, dst)
                with mock.patch.object(atomic_v3, '_replace', side_effect=replace), self.assertRaises(Crash):
                    self.write_complete()
                self.assertFalse((self.work / during).exists())
                self.assertTrue([n for n in os.listdir(self.work) if '.tmp-' in n])   # listed leftover
                error = self.assert_refused_read_only()
                self.assertFalse(error.completeness.complete)

    def test_complete_after_all_four_writes(self):
        self.write_complete()
        result = require_complete(self.work)
        self.assertTrue(result.applicable and result.complete)
        self.assertEqual(result.problems, ())


class MismatchTest(Base):
    def test_wrong_run_id(self):
        self.write_complete()
        manifest = json.loads((self.work / MANIFEST_NAME).read_text(encoding='utf-8'))
        manifest['run_id'] = OTHER_RUN_ID
        (self.work / MANIFEST_NAME).write_text(json.dumps(manifest), encoding='utf-8')
        self.assert_refused_read_only('run_id does not match')

    def test_csv_or_gallery_hash_mismatch(self):
        for name in (CSV_NAME, HTML_NAME):
            with self.subTest(name=name):
                self.write_complete()
                (self.work / name).write_bytes(b'edited by hand\n')
                self.assert_refused_read_only(f'{name} SHA-256 does not match')
                for n in ARTIFACT_ORDER:
                    (self.work / n).unlink()

    def test_missing_csv_or_gallery(self):
        for name in (CSV_NAME, HTML_NAME):
            with self.subTest(name=name):
                self.write_complete()
                (self.work / name).unlink()
                self.assert_refused_read_only(f'{name} missing')
                for n in ARTIFACT_ORDER:
                    (self.work / n).unlink(missing_ok=True)

    def test_manifest_not_regular_or_unparseable(self):
        self.write_complete()
        real = self.work / 'elsewhere.json'
        os.replace(self.work / MANIFEST_NAME, real)
        os.symlink(real, self.work / MANIFEST_NAME)
        self.assert_refused_read_only(f'{MANIFEST_NAME} not a regular file')
        os.unlink(self.work / MANIFEST_NAME)
        (self.work / MANIFEST_NAME).write_text('{not json', encoding='utf-8')
        self.assert_refused_read_only(f'{MANIFEST_NAME} does not parse')

    def test_results_failing_v30_or_v31(self):
        self.write_complete()
        for label, raw in (('invalid json', b'{oops'), ('schema 7', encode(dict(results_doc(self.work), schema_version=7))),
                           ('rubric', encode(dict(results_doc(self.work), rubric_version='v2'))),
                           ('threshold', encode(dict(results_doc(self.work), confidence_threshold=0.5))),
                           ('bad error entry', encode(dict(results_doc(self.work), errors=[{'stage': 'x'}])))):
            with self.subTest(label=label):
                (self.work / RESULTS_NAME).write_bytes(raw)
                self.assert_refused_read_only()

    def test_results_missing(self):
        self.write_complete()
        (self.work / RESULTS_NAME).unlink()
        self.assert_refused_read_only(f'{RESULTS_NAME} missing')


class UnreadableFileTest(Base):
    """R-1: every read failure of the results file, manifest, CSV or gallery gives the defined
    refusal; no raw OSError escapes."""

    def deny(self, name: str, *, at: str = 'open', errno_: int = 13):
        target = str(self.work / name)
        if at == 'open':
            real = os.open
            return mock.patch.object(output_v3.os, 'open', side_effect=lambda p, *a, **k: (
                (_ for _ in ()).throw(PermissionError(errno_, os.strerror(errno_), p)) if p == target else real(p, *a, **k)))
        if at == 'lstat':
            real = os.lstat
            return mock.patch.object(output_v3.os, 'lstat', side_effect=lambda p, *a, **k: (
                (_ for _ in ()).throw(PermissionError(errno_, os.strerror(errno_), p)) if p == target else real(p, *a, **k)))
        raise AssertionError(at)

    def test_permission_denied_on_each_artifact(self):
        for name in (RESULTS_NAME, MANIFEST_NAME, CSV_NAME, HTML_NAME):
            for at in ('open', 'lstat'):
                with self.subTest(name=name, at=at):
                    for n in ARTIFACT_ORDER:
                        (self.work / n).unlink(missing_ok=True)
                    self.write_complete()
                    with self.deny(name, at=at):
                        error = self.assert_refused_read_only(f'{name} unreadable (Permission denied)')
                    self.assertFalse(error.completeness.complete)

    def test_read_error_after_open(self):
        self.write_complete()
        with mock.patch.object(output_v3.os, 'read', side_effect=OSError(5, 'Input/output error')):
            self.assert_refused_read_only(f'{RESULTS_NAME} unreadable (Input/output error)')

    def test_replaced_by_non_regular_between_lstat_and_open(self):
        self.write_complete()
        fake = os.stat_result((0o010644, 0, 0, 1, 0, 0, 0, 0, 0, 0))            # S_IFIFO
        with mock.patch.object(output_v3.os, 'fstat', return_value=fake):
            self.assert_refused_read_only(f'{RESULTS_NAME} not a regular file')

    def test_existing_output_refusal_when_results_unreadable(self):
        self.write_complete()
        with self.deny(RESULTS_NAME), self.assertRaises(ExistingOutputRefusal) as ctx:
            refuse_existing_output(self.work)
        self.assertIsNone(ctx.exception.approvals)
        self.assertIn(f'unknown ({RESULTS_NAME} unreadable (Permission denied))', ctx.exception.message)
        self.assertIn(RESULTS_NAME, ctx.exception.found)

    def test_unlistable_previews_counts_as_found(self):
        real = os.listdir
        target = str(self.work / 'previews')
        os.unlink(self.work / 'previews' / 'A__1.jpg')
        os.unlink(self.work / 'previews' / OUTPUT_MARKER)
        with mock.patch.object(output_v3.os, 'listdir', side_effect=lambda p='.': (
                (_ for _ in ()).throw(PermissionError(13, 'Permission denied', p)) if os.fspath(p) == target else real(p))):
            self.assertEqual(existing_output_entries(self.work), ('previews',))


class CloseAndListingFailureTest(Base):
    """Review round 2: a failing os.close and an unlistable <work> are refused, never raised raw."""

    def tracked_close_failure(self):
        """os.open / os.close wrappers: every descriptor is really closed, then close reports EIO."""
        opened, closed = [], []
        real_open, real_close = os.open, os.close

        def fake_open(path, *a, **k):
            fd = real_open(path, *a, **k)
            opened.append(fd)
            return fd

        def fake_close(fd):
            real_close(fd)
            closed.append(fd)
            raise OSError(5, 'Input/output error')
        patches = contextlib.ExitStack()
        patches.enter_context(mock.patch.object(output_v3.os, 'open', side_effect=fake_open))
        patches.enter_context(mock.patch.object(output_v3.os, 'close', side_effect=fake_close))
        return patches, opened, closed

    def test_close_failure_makes_output_incomplete(self):
        self.write_complete()
        patches, opened, closed = self.tracked_close_failure()
        with patches:
            error = self.assert_refused_read_only(f'{RESULTS_NAME} unreadable (Input/output error)')
        self.assertFalse(error.completeness.complete)
        self.assertTrue(opened)
        self.assertEqual(sorted(opened), sorted(closed))          # each descriptor closed exactly once

    def test_close_failure_on_manifest_only(self):
        self.write_complete()
        real_close = os.close
        target = str(self.work / MANIFEST_NAME)
        fds = {}
        real_open = os.open

        def fake_open(path, *a, **k):
            fd = real_open(path, *a, **k)
            fds[fd] = path
            return fd

        def fake_close(fd):
            real_close(fd)
            if fds.get(fd) == target:
                raise OSError(5, 'Input/output error')
        with mock.patch.object(output_v3.os, 'open', side_effect=fake_open), \
             mock.patch.object(output_v3.os, 'close', side_effect=fake_close):
            self.assert_refused_read_only(f'{MANIFEST_NAME} unreadable (Input/output error)')

    def test_close_failure_gives_unknown_approvals(self):
        self.write_complete()
        patches, opened, closed = self.tracked_close_failure()
        with patches, self.assertRaises(ExistingOutputRefusal) as ctx:
            refuse_existing_output(self.work)
        self.assertIsNone(ctx.exception.approvals)
        self.assertIn(f'unknown ({RESULTS_NAME} unreadable (Input/output error))', ctx.exception.message)
        self.assertEqual(sorted(opened), sorted(closed))

    def test_unlistable_work_folder_refused(self):
        self.write_complete()
        before = snapshot(self.work)
        real = os.listdir
        target = os.fspath(self.work)
        with mock.patch.object(output_v3.os, 'listdir', side_effect=lambda p='.': (
                (_ for _ in ()).throw(PermissionError(13, 'Permission denied', p)) if os.fspath(p) == target else real(p))):
            with self.assertRaises(OutputFolderUnreadableRefusal) as ctx:
                existing_output_entries(self.work)
            with self.assertRaises(ExistingOutputRefusal) as ctx2:          # same refusal family for analyze
                refuse_existing_output(self.work)
        for error in (ctx.exception, ctx2.exception):
            self.assertIsInstance(error, OutputFolderUnreadableRefusal)
            self.assertEqual(error.message, 'output folder cannot be listed (Permission denied); refusing, '
                                            'because existing analysis output cannot be ruled out')
            self.assertEqual(str(error), error.message)
            self.assertEqual(error.found, ())
            self.assertIsNone(error.approvals)
            self.assertIsNone(error.__cause__)
        self.assertEqual(snapshot(self.work), before)

    def test_unlistable_empty_work_folder_not_treated_as_empty(self):
        for name in os.listdir(self.work / 'previews'):
            os.unlink(self.work / 'previews' / name)
        (self.work / 'previews').rmdir()
        refuse_existing_output(self.work)                                   # really empty: no refusal
        real = os.listdir
        target = os.fspath(self.work)
        with mock.patch.object(output_v3.os, 'listdir', side_effect=lambda p='.': (
                (_ for _ in ()).throw(OSError(5, 'Input/output error', p)) if os.fspath(p) == target else real(p))):
            with self.assertRaises(OutputFolderUnreadableRefusal) as ctx:
                refuse_existing_output(self.work)
        self.assertIn('(Input/output error)', ctx.exception.message)


class ApproveDoesNotBreakCompletenessTest(Base):
    def test_results_file_changed_by_approve_stays_complete(self):
        self.write_complete()
        doc = json.loads((self.work / RESULTS_NAME).read_text(encoding='utf-8'))
        doc['results'][0].update(approved=True, approved_rating=3)
        atomic_v3.atomic_write(self.work / RESULTS_NAME, encode(doc))     # what approve will do (12.3)
        result = require_complete(self.work)
        self.assertTrue(result.complete)


class LegacyTest(Base):
    def test_v1_v2_not_applicable(self):
        for version in (1, 2):
            with self.subTest(version=version):
                (self.work / RESULTS_NAME).write_bytes(encode({'schema_version': version, 'results': []}))
                result = require_complete(self.work)
                self.assertFalse(result.applicable)
                self.assertEqual(result.schema_version, version)


class ExistingOutputTest(Base):
    def test_clean_folder_not_refused(self):
        previews = self.work / 'previews'
        for name in os.listdir(previews):
            os.unlink(previews / name)
        self.assertEqual(existing_output_entries(self.work), ())       # empty previews folder: no entry
        refuse_existing_output(self.work)
        previews.rmdir()
        self.assertEqual(existing_output_entries(self.work), ())       # only the folder marker
        refuse_existing_output(self.work)

    def test_previews_with_only_marker_refused_literal_any_entry(self):
        os.unlink(self.work / 'previews' / 'A__1.jpg')
        self.assertIn('previews', existing_output_entries(self.work))

    def test_each_refusal_list_item(self):
        names = {RESULTS_NAME: b'{}', CSV_NAME: b'', HTML_NAME: b'', MANIFEST_NAME: b'{}',
                 'ratings-review.json.20261115T100000.000000Z.photo-curator.bak': b'{}',
                 'ratings-review.csv.tmp-1-ab': b'', JOURNAL_NAME: b'{}'}
        for name, data in names.items():
            with self.subTest(name=name):
                (self.work / name).write_bytes(data)
                self.assertIn(name, existing_output_entries(self.work))
                with self.assertRaises(ExistingOutputRefusal) as ctx:
                    refuse_existing_output(self.work)
                self.assertIn(name, ctx.exception.message)
                (self.work / name).unlink()

    def test_journal_points_to_recover_archive(self):
        (self.work / JOURNAL_NAME).write_bytes(b'{}')
        with self.assertRaises(ExistingOutputRefusal) as ctx:
            refuse_existing_output(self.work)
        self.assertIn('recover-archive', ctx.exception.message)

    def test_previews_with_entries_refused(self):
        # setUp leaves A__1.jpg in previews: any entry counts.
        self.assertIn('previews', existing_output_entries(self.work))

    def test_backup_pattern_is_the_spec_glob(self):
        # R-2: ratings-review.json.*.photo-curator.bak, where * may be empty.
        for name in ('ratings-review.json..photo-curator.bak',
                     'ratings-review.json.20261115T100000.000000Z.photo-curator.bak',
                     'ratings-review.json.x.y.photo-curator.bak'):
            with self.subTest(name=name):
                (self.work / name).write_bytes(b'{}')
                self.assertIn(name, existing_output_entries(self.work))
                (self.work / name).unlink()
        for name in ('ratings-review.json.photo-curator.bak', 'ratings-review.json.x.photo-curator.bak.old',
                     'Ratings-review.json.x.photo-curator.bak'):
            with self.subTest(not_matching=name):
                (self.work / name).write_bytes(b'{}')
                self.assertNotIn(name, existing_output_entries(self.work))
                (self.work / name).unlink()

    def test_tmp_pattern_is_the_spec_glob(self):
        for name, expected in (('x.tmp-', True), ('.tmp-1', True), ('a.b.tmp-9-z', True), ('x.tmp', False), ('xtmp-1', False)):
            with self.subTest(name=name):
                (self.work / name).write_bytes(b'')
                self.assertEqual(name in existing_output_entries(self.work), expected)
                (self.work / name).unlink()


V4 = str(uuid.UUID(int=0xABCDEF, version=4))
SHA_O, SHA_P = 'a' * 64, 'b' * 64


def item(**overrides) -> dict:
    base = {'path': '/lib/A.ARW', 'original_path': '/lib/A.ARW', 'preview_path': '/w/previews/A__1.jpg',
            'rating': 3, 'needs_review': True, 'original_sha256': SHA_O,
            'approved': True, 'approved_rating': 3, 'approved_at': '2026-11-15T07:12:03Z',
            'approved_original_path': '/lib/A.ARW', 'approved_original_sha256': SHA_O,
            'approved_preview_path': '/w/previews/A__1.jpg', 'approved_preview_sha256': SHA_P}
    base.update(overrides)
    return base


NOT_APPROVED = dict(approved=False, approved_rating=None, approved_at=None, approved_original_path=None,
                    approved_original_sha256=None, approved_preview_path=None, approved_preview_sha256=None)


class ApprovalStateTest(unittest.TestCase):
    """Point 7: approvals are classified by 12.2 structure and binding checks 1, 2, 4 only."""

    def test_passing(self):
        self.assertEqual(approval_state_without_files(item()), 'passing')

    def test_not_approved(self):
        self.assertEqual(approval_state_without_files(item(**NOT_APPROVED)), 'not_approved')
        self.assertEqual(approval_state_without_files(item(needs_review=False, **NOT_APPROVED)), 'not_approved')

    def test_malformed_each_rule(self):
        cases = {
            'approved string': item(approved='true'),
            'approved int': item(approved=1),
            'missing field': {k: v for k, v in item().items() if k != 'approved_at'},
            'rating bool': item(approved_rating=True),
            'rating 0': item(approved_rating=0),
            'rating 6': item(approved_rating=6),
            'approved_at without Z': item(approved_at='2026-11-15T07:12:03'),
            'approved_at unparseable': item(approved_at='yesterdayZ'),
            'relative original path': item(approved_original_path='A.ARW'),
            'relative preview path': item(approved_preview_path='previews/A__1.jpg'),
            'uppercase hash': item(approved_preview_sha256='B' * 64),
            'short hash': item(approved_original_sha256='a' * 63, original_sha256='a' * 63),
            'hash differs from original_sha256': item(approved_original_sha256='c' * 64),
            'false with non-null field': item(**dict(NOT_APPROVED, approved_rating=3)),
            'approval on needs_review false': item(needs_review=False),
            'approval on rating 0 item': item(rating=0),
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                self.assertEqual(approval_state_without_files(value), 'malformed')

    def test_invalidated_by_binding_checks_1_2_4(self):
        self.assertEqual(approval_state_without_files(item(rating=4)), 'invalidated')                       # 1
        self.assertEqual(approval_state_without_files(item(original_path='/lib/B.ARW')), 'invalidated')     # 2
        self.assertEqual(approval_state_without_files(item(preview_path='/w/previews/B__2.jpg')), 'invalidated')  # 4

    def test_undetermined(self):
        self.assertEqual(approval_state_without_files('x'), 'undetermined')
        self.assertEqual(approval_state_without_files(item(needs_review='yes')), 'undetermined')
        self.assertEqual(approval_state_without_files(item(rating=None)), 'undetermined')

    def test_no_file_is_opened(self):
        with mock.patch.object(output_v3.os, 'open', side_effect=AssertionError('opened')), \
             mock.patch('builtins.open', side_effect=AssertionError('opened')):
            self.assertEqual(approval_state_without_files(item()), 'passing')


class ApprovalSummaryRefusalTest(Base):
    def refusal(self) -> ExistingOutputRefusal:
        with self.assertRaises(ExistingOutputRefusal) as ctx:
            refuse_existing_output(self.work)
        return ctx.exception

    def test_summary_in_message(self):
        doc = results_doc(self.work)
        doc['results'] = [item(), item(), item(rating=4), item(approved='true'), 'x', item(**NOT_APPROVED)]
        (self.work / RESULTS_NAME).write_bytes(encode(doc))
        error = self.refusal()
        self.assertEqual(error.approvals, ApprovalSummary(passing=2, invalidated=1, malformed=1, undetermined=1))
        self.assertIn('2 passing structural and binding checks 1, 2, 4 (preview checks and original content '
                      'not verified), 1 invalidated, 1 malformed, 1 undetermined', error.message)

    def test_unknown_when_results_unparseable_or_not_a_list(self):
        for raw, reason in ((b'{broken', 'not valid JSON'), (encode(dict(results_doc(self.work), results={})),
                                                             'results is not a list')):
            with self.subTest(reason=reason):
                (self.work / RESULTS_NAME).write_bytes(raw)
                error = self.refusal()
                self.assertIsNone(error.approvals)
                self.assertIn('unknown (', error.message)
                self.assertIn(reason, error.message)

    def test_v1_v2_have_no_approvals(self):
        for version in (1, 2):
            with self.subTest(version=version):
                (self.work / RESULTS_NAME).write_bytes(encode({'schema_version': version,
                                                               'results': [{'path': '/lib/A.ARW', 'approved': True}]}))
                self.assertEqual(self.refusal().approvals, ApprovalSummary())

    def test_no_results_file(self):
        # only previews present: approvals are zero, not unknown
        self.assertEqual(self.refusal().approvals, ApprovalSummary())


if __name__ == '__main__':
    unittest.main()
