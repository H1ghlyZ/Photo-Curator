"""S17: calibration tooling, read-only (spec 16 metrics, 18 measurements, 19 invariant matrix;
spec 20 items 24-25 tooling only; decisions K-17a..K-17i). Synthetic complete v3 outputs in
temporary folders (fake Claude, real S3-S10 code); synthetic originals under /library do not
exist on disk, so any attempt to open one fails. No real photos, no M-26."""
from __future__ import annotations
import builtins, hashlib, importlib, io, json, os, shutil, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import calibration_v3, core, hashing_v3, locking_v3
from photo_curator.calibration_v3 import (UNAVAILABLE, CalibrationRefusal, batch_measurements, check_separation,
                                          compute_metrics, load_human_ratings, report_lines)
from photo_curator.locking_v3 import BUSY_MESSAGE, JOURNAL_NAME, LOCK_NAME, acquire_lock
from photo_curator.output_v3 import MANIFEST_NAME
from photo_curator.preview_checks_v3 import expected_preview_name
from photo_curator.schema_v3 import DECISIONS
from tests.test_document_v3 import SESSION, original
from tests.test_reader_v3_file import Output

RESULTS = 'ratings-review.json'
#            frame: (model rating, human rating, human capture type); frame 9 is the rating-0 frame
TABLE = {1: (3, 3, 'candid'), 2: (3, 4, 'candid'), 3: (3, 5, 'candid'), 4: (2, 3, 'candid'),
         5: (2, 2, 'group'), 6: (2, 4, 'group'), 7: (3, 2, 'group'), 8: (3, 1, 'group')}


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and p.name != LOCK_NAME}


def human_entry(rating, ctype='candid', reason='human judgement from the actual image'):
    return {'human_rating': rating, 'reason': reason, 'capture_type': ctype}


def error_entry(n, stage='original', error='original content changed during preview generation', **over):
    entry = {'stage': stage, 'error': error, 'original_path': original(n), 'session_id': SESSION,
             'burst_group': None, 'burst_sequence': None, 'capture_time': None, 'batch_index': None,
             'sent_to_model': False, 'result': None, 'preview_path': None}
    entry.update(over)
    return entry


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s17-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.out = self.output('cal')

    def output(self, name: str) -> Output:
        work = self.root / name
        work.mkdir()
        out = Output(work)
        lock = work / LOCK_NAME
        if lock.exists():
            lock.unlink()
        return out

    def results(self, out=None) -> Path:
        return (out or self.out).work / RESULTS

    def rate(self, out, n, rating):
        out.result(n).update(technical_score=rating, editorial_score=rating, rating=rating, decision=DECISIONS[rating])

    def human(self, mapping, name='human.json') -> Path:
        path = self.root / name
        path.write_text(json.dumps(mapping), encoding='utf-8')
        return path

    def table_output(self):
        for n, (m, _, _) in TABLE.items():
            self.rate(self.out, n, m)
        self.out.rewrite()
        mapping = {original(n): human_entry(h, c) for n, (_, h, c) in TABLE.items()}
        mapping[original(9)] = human_entry(3)                                    # rating-0 frame
        return mapping

    def relocate(self, out, prefix):
        """Move every original of ``out`` to ``prefix`` with distinct stored SHA-256 values."""
        mapping = {r['original_path']: prefix + r['original_path'] for r in out.doc['results']}
        for r in out.doc['results']:
            new = mapping[r['original_path']]
            old_preview = Path(r['preview_path'])
            new_preview = old_preview.with_name(expected_preview_name(new))
            os.replace(old_preview, new_preview)
            r.update(original_path=new, path=new, preview_path=str(new_preview),
                     original_sha256=hashlib.sha256(new.encode()).hexdigest())
            if r.get('near_duplicate_of') in mapping:
                r['near_duplicate_of'] = mapping[r['near_duplicate_of']]
        out.doc['source_folder'] = prefix + out.doc['source_folder']
        out.rewrite()
        return mapping


class HumanRatingsTest(Base):
    def test_valid_file(self):
        ratings = load_human_ratings(self.human({original(1): human_entry(4, 'group')}))
        self.assertEqual(ratings[original(1)].human_rating, 4)
        self.assertEqual(ratings[original(1)].capture_type, 'group')

    def test_refusals(self):
        cases = {
            'malformed json': b'{not json',
            'not utf-8': b'\xff\xfe',
            'top level list': b'[]',
            'empty object': b'{}',
            'duplicate key': ('{"%s": %s, "%s": %s}' % (original(1), json.dumps(human_entry(3)),
                                                        original(1), json.dumps(human_entry(4)))).encode(),
            'relative key': json.dumps({'DSC00001.ARW': human_entry(3)}).encode(),
            'traversal key': json.dumps({'/library/../library/DSC00001.ARW': human_entry(3)}).encode(),
            'trailing slash': json.dumps({original(1) + '/': human_entry(3)}).encode(),
            'rating 0': json.dumps({original(1): human_entry(0)}).encode(),
            'rating 6': json.dumps({original(1): human_entry(6)}).encode(),
            'rating bool': json.dumps({original(1): human_entry(True)}).encode(),
            'rating string': json.dumps({original(1): human_entry('3')}).encode(),
            'unknown capture type': json.dumps({original(1): human_entry(3, 'landscape')}).encode(),
            'empty reason': json.dumps({original(1): human_entry(3, reason='  ')}).encode(),
            'extra field': json.dumps({original(1): dict(human_entry(3), human_t=3)}).encode(),
            'missing field': json.dumps({original(1): {'human_rating': 3, 'reason': 'x'}}).encode(),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                path = self.root / 'bad.json'
                path.write_bytes(raw)
                with self.assertRaises(CalibrationRefusal) as ctx:
                    load_human_ratings(path)
                self.assertIn('human ratings', ctx.exception.message)

    def test_unreadable(self):
        with self.assertRaises(CalibrationRefusal):
            load_human_ratings(self.root / 'absent.json')


class MetricsTest(Base):
    def test_hand_computed_metrics(self):
        mapping = self.table_output()
        mapping[original(99)] = human_entry(4)                                   # not in the results
        m = compute_metrics(self.results(), self.human(mapping))
        self.assertEqual(len(m.population), 8)
        self.assertEqual((m.exact.numerator, m.exact.denominator), (2, 8))
        self.assertEqual((m.within_one.numerator, m.within_one.denominator), (5, 8))
        self.assertEqual((m.keeper_recall.numerator, m.keeper_recall.denominator), (3, 5))
        self.assertEqual((m.false_reject.numerator, m.false_reject.denominator), (2, 5))
        self.assertAlmostEqual(m.exact.value, 0.25)
        self.assertAlmostEqual(m.false_reject.value, 0.4)
        expected = {(h, mm): 0 for h in range(1, 6) for mm in range(1, 6)}
        for h, mm in ((3, 3), (4, 3), (5, 3), (3, 2), (2, 2), (4, 2), (2, 3), (1, 3)):
            expected[(h, mm)] += 1
        self.assertEqual(m.confusion, expected)
        cand_exact, cand_within = m.by_capture_type['candid']
        group_exact, group_within = m.by_capture_type['group']
        self.assertEqual((cand_exact.numerator, cand_exact.denominator, cand_within.numerator), (1, 4, 3))
        self.assertEqual((group_exact.numerator, group_exact.denominator, group_within.numerator), (1, 4, 2))
        self.assertEqual(m.rating_0, [original(9)])                              # kept apart
        self.assertEqual(m.unmatched_human, [original(99)])
        self.assertEqual(m.unlabeled, 0)
        self.assertEqual((m.te_agreement, m.te_reason), (UNAVAILABLE, calibration_v3.TE_UNAVAILABLE_REASON))
        self.assertEqual(sum(m.group_status.values()), 3)                        # b1, b2, b3
        self.assertTrue(set(m.group_status) <= {'validated', 'not_comparable', 'invalid'})
        self.assertEqual((m.rubric_version, m.evidence_rules_version, m.evidence_rules_changed), ('v3', 'v3.1', False))

    def test_no_keepers_gives_no_data_not_zero(self):
        self.rate(self.out, 1, 3)
        self.out.rewrite()
        m = compute_metrics(self.results(), self.human({original(1): human_entry(2)}))
        self.assertIsNone(m.keeper_recall.value)
        self.assertIsNone(m.false_reject.value)
        self.assertIn('n/a (no data)', m.keeper_recall.text())
        self.assertEqual(m.unlabeled, 8)

    def test_inconsistent_and_error_entries_kept_apart(self):
        self.out.result(3).update(rating=4, decision=DECISIONS[4])               # V34: hand-edited rating
        self.out.doc['errors'] = [error_entry(42)]
        self.out.rewrite()
        m = compute_metrics(self.results(), self.human({original(3): human_entry(4), original(42): human_entry(5),
                                                         original(1): human_entry(3)}))
        self.assertEqual([o for o, _ in m.inconsistent], [original(3)])
        self.assertIn('V34', m.inconsistent[0][1])
        self.assertEqual(m.errors_by_stage, {'original': 1})
        self.assertEqual(m.errors_matched, [(original(42), 'original')])
        self.assertEqual([p[0] for p in m.population], [original(1)])

    def test_v30_results_labelled_v43_not_pooled(self):
        self.out.doc['evidence_rules_version'] = 'v3.0'
        self.out.rewrite()
        m = compute_metrics(self.results(), self.human({original(1): human_entry(3)}))
        self.assertEqual(m.evidence_rules_version, 'v3.0')
        self.assertTrue(m.evidence_rules_changed)
        self.assertTrue(any(w.startswith('V43') for w in m.warnings))
        self.assertTrue(any(l.startswith('Warning: V43') for l in report_lines(metrics=m)))


class MetricsRefusalTest(Base):
    def test_v1_v2_refused(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.results().write_text(json.dumps({'schema_version': version, 'results': []}), encoding='utf-8')
                with self.assertRaises(CalibrationRefusal) as ctx:
                    compute_metrics(self.results(), self.human({original(1): human_entry(3)}))
                self.assertIn('schema version 3 only', ctx.exception.message)

    def test_incomplete_refused(self):
        os.unlink(self.out.work / MANIFEST_NAME)
        with self.assertRaises(CalibrationRefusal) as ctx:
            compute_metrics(self.results(), self.human({original(1): human_entry(3)}))
        self.assertIn('analysis output incomplete', ctx.exception.message)

    def test_journal_refused(self):
        (self.out.work / JOURNAL_NAME).write_bytes(b'{}')
        with self.assertRaises(CalibrationRefusal) as ctx:
            compute_metrics(self.results(), self.human({original(1): human_entry(3)}))
        self.assertIn('recover-archive', ctx.exception.message)

    def test_duplicate_original_refused(self):
        self.out.doc['errors'] = [error_entry(1)]                                 # same original as a result
        self.out.rewrite()
        with self.assertRaises(CalibrationRefusal) as ctx:
            compute_metrics(self.results(), self.human({original(1): human_entry(3)}))
        self.assertIn('duplicate original_path', ctx.exception.message)

    def test_human_file_inside_source_refused_including_symlink(self):
        source = self.root / 'src'
        source.mkdir()
        self.out.doc['source_folder'] = str(source)
        self.out.rewrite()
        inside = source / 'human.json'
        inside.write_text(json.dumps({original(1): human_entry(3)}), encoding='utf-8')
        link = self.root / 'link.json'
        link.symlink_to(inside)
        for path in (inside, link):
            with self.subTest(path=path.name):
                with self.assertRaises(CalibrationRefusal) as ctx:
                    compute_metrics(self.results(), path)
                self.assertIn('inside the source folder', ctx.exception.message)


class SeparationTest(Base):
    def setUp(self):
        super().setUp()
        self.ev = self.output('eval')
        self.relocate(self.out, '/cal')
        self.relocate(self.ev, '/eval')

    def test_separate(self):
        s = check_separation(self.results(), self.results(self.ev))
        self.assertEqual((s.verdict, s.path_overlap, s.hash_overlap, s.unverifiable), ('separate', [], [], []))

    def test_path_overlap(self):
        r = self.ev.doc['results'][0]                     # Output.result(n) no longer matches after relocate
        r['original_path'] = r['path'] = self.out.doc['results'][0]['original_path']
        preview = Path(r['preview_path'])
        r['preview_path'] = str(preview.rename(preview.with_name(expected_preview_name(r['original_path']))))
        self.ev.rewrite()
        s = check_separation(self.results(), self.results(self.ev))
        self.assertEqual(s.verdict, 'overlap')
        self.assertEqual(s.path_overlap, [self.out.doc['results'][0]['original_path']])

    def test_hash_overlap_is_a_copy(self):
        sha = self.out.doc['results'][0]['original_sha256']
        self.ev.doc['results'][3]['original_sha256'] = sha
        self.ev.rewrite()
        s = check_separation(self.results(), self.results(self.ev))
        self.assertEqual(s.verdict, 'overlap')
        self.assertEqual([h[0] for h in s.hash_overlap], [sha])
        self.assertEqual(s.path_overlap, [])

    def test_missing_sha_unverifiable(self):
        self.ev.doc['results'][2]['original_sha256'] = None
        self.ev.rewrite()
        s = check_separation(self.results(), self.results(self.ev))
        self.assertEqual(s.verdict, 'unverifiable')
        self.assertTrue(any('original_sha256' in why for _, _, why in s.unverifiable))

    def test_error_entries_unverifiable(self):
        self.ev.doc['errors'] = [error_entry(77, original_path='/eval' + original(77))]
        self.ev.rewrite()
        s = check_separation(self.results(), self.results(self.ev))
        self.assertEqual(s.verdict, 'unverifiable')
        self.assertTrue(any('error entry' in why for _, _, why in s.unverifiable))

    def test_duplicate_sha_within_one_set_is_information(self):
        self.ev.doc['results'][1]['original_sha256'] = self.ev.doc['results'][0]['original_sha256']
        self.ev.rewrite()
        s = check_separation(self.results(), self.results(self.ev))
        self.assertEqual(s.verdict, 'separate')
        self.assertEqual(len(s.duplicate_sha_within), 1)

    def test_same_file_twice_overlaps(self):
        s = check_separation(self.results(), self.results())
        self.assertEqual(s.verdict, 'overlap')

    def test_versions_may_differ(self):
        self.ev.doc['evidence_rules_version'] = 'v3.0'
        self.ev.rewrite()
        self.assertEqual(check_separation(self.results(), self.results(self.ev)).verdict, 'separate')


class BatchTest(Base):
    def test_counts_and_unavailable(self):
        self.out.doc['errors'] = [
            error_entry(50, stage='batch', error='claude call failed: invalid JSON', batch_index=1, sent_to_model=True),
            error_entry(51, stage='batch', error='claude call failed: invalid JSON', batch_index=1, sent_to_model=True),
            error_entry(52, stage='result', error='V1: no result for this original', batch_index=0, sent_to_model=True),
            error_entry(53, stage='result', error='V6: technical_score missing', batch_index=0, sent_to_model=True),
            error_entry(54, stage='preview', error='preview failed')]
        self.out.rewrite()
        report = batch_measurements([self.results()])
        [(key, files)] = report.groups.items()
        self.assertEqual(key, ('v3', 'v3.1'))
        f = files[0]
        self.assertEqual((f.batch_errors, f.batch_indexes, f.missing_results), (2, [1], 1))
        self.assertEqual(f.other_result_errors, {'V6': 1})
        self.assertEqual(f.other_stage_errors, {'preview': 1})
        self.assertEqual(f.largest_burst_group, 2)                                 # b1, b2, b3 have 2 frames
        self.assertEqual(f.max_burst_size, 12)
        self.assertEqual((f.batch_size, f.turns, f.duration, f.output_length), (UNAVAILABLE,) * 4)
        text = '\n'.join(report_lines(batches=report))
        self.assertIn('batch size: unavailable (batch size not stored in results)', text)
        self.assertNotIn('turns: 0', text)

    def test_versions_never_pooled(self):
        other = self.output('other')
        other.doc['evidence_rules_version'] = 'v3.0'
        other.rewrite()
        report = batch_measurements([self.results(), self.results(other)])
        self.assertEqual(set(report.groups), {('v3', 'v3.1'), ('v3', 'v3.0')})
        self.assertTrue(all(len(files) == 1 for files in report.groups.values()))

    def test_refusals(self):
        with self.assertRaises(CalibrationRefusal):
            batch_measurements([])
        with self.assertRaises(CalibrationRefusal):
            batch_measurements([self.results(), self.results()])


class LockTest(Base):
    def assertReleased(self, work):
        lock = acquire_lock(work, 'exclusive')                                    # non-blocking: fails if held
        lock.release()

    def calls(self):
        human = self.human({original(1): human_entry(3)})
        return {'compute_metrics': lambda: compute_metrics(self.results(), human),
                'check_separation': lambda: check_separation(self.results(), self.results()),
                'batch_measurements': lambda: batch_measurements([self.results()])}

    def test_shared_lock_taken_and_released_on_success(self):
        for name, call in self.calls().items():
            with self.subTest(call=name):
                modes, real = [], locking_v3.acquire_lock
                with mock.patch.object(locking_v3, 'acquire_lock', side_effect=lambda w, m: (modes.append(m), real(w, m))[1]):
                    call()
                self.assertTrue(modes and set(modes) == {'shared'})
                self.assertReleased(self.out.work)
                self.assertTrue((self.out.work / LOCK_NAME).is_file())           # only inside the fixture

    def test_released_on_refusal_and_exception(self):
        os.unlink(self.out.work / MANIFEST_NAME)
        for name, call in self.calls().items():
            with self.subTest(call=name, path='refusal'):
                with self.assertRaises(CalibrationRefusal):
                    call()
                self.assertReleased(self.out.work)
        for name, call in self.calls().items():
            with self.subTest(call=name, path='exception'):
                with mock.patch.object(calibration_v3, 'read_results', side_effect=RuntimeError('boom')), \
                     self.assertRaises(RuntimeError):
                    call()
                self.assertReleased(self.out.work)

    def test_busy_refused(self):
        holder = acquire_lock(self.out.work, 'exclusive')
        try:
            for name, call in self.calls().items():
                with self.subTest(call=name):
                    with self.assertRaises(CalibrationRefusal) as ctx:
                        call()
                    self.assertIn(BUSY_MESSAGE, ctx.exception.message)
        finally:
            holder.release()

    def test_network_filesystem_refused_before_lock(self):
        with mock.patch.object(locking_v3, 'filesystem_type', return_value='nfs'):
            for name, call in self.calls().items():
                with self.subTest(call=name), self.assertRaises(CalibrationRefusal):
                    call()
        self.assertFalse((self.out.work / LOCK_NAME).exists())

    def test_no_lock_artifact_outside_the_fixture(self):
        before = os.path.lexists(Path.cwd() / LOCK_NAME)
        for call in self.calls().values():
            call()
        self.assertEqual(os.path.lexists(Path.cwd() / LOCK_NAME), before)
        self.assertFalse(os.path.lexists(self.root / LOCK_NAME))


class SafetyTest(Base):
    def test_no_original_access_hash_metadata_write_delete_or_xmp(self):
        mapping = self.table_output()
        human = self.human(mapping)
        before = tree(self.root)
        boom = AssertionError('forbidden call')
        real_open = builtins.open

        def guarded_open(file, *a, **k):
            if str(file).startswith('/library'):
                raise AssertionError(f'original opened: {file}')
            return real_open(file, *a, **k)
        with mock.patch.object(core, 'capture_records', side_effect=boom), \
             mock.patch.object(core, 'run', side_effect=boom), \
             mock.patch.object(hashing_v3, 'sha256_file', side_effect=boom), \
             mock.patch.object(builtins, 'open', guarded_open), mock.patch.object(io, 'open', guarded_open), \
             mock.patch.object(os, 'unlink', side_effect=boom), mock.patch.object(os, 'remove', side_effect=boom), \
             mock.patch.object(os, 'rename', side_effect=boom), mock.patch.object(os, 'replace', side_effect=boom), \
             mock.patch.object(shutil, 'move', side_effect=boom):
            m = compute_metrics(self.results(), human)
            s = check_separation(self.results(), self.results())
            b = batch_measurements([self.results()])
            lines = report_lines(metrics=m, separation=s, batches=b)
        self.assertTrue(lines)
        self.assertEqual(tree(self.root), before)                                # only the lock file may appear
        self.assertFalse(list(self.root.rglob('*.xmp*')))

    def test_module_has_no_writer_or_hash_dependency(self):
        for name in ('hashing_v3', 'sidecar_v3', 'apply_v3', 'approve_v3', 'archive_v3', 'atomic_v3'):
            self.assertFalse(hasattr(calibration_v3, name), name)
        self.assertFalse(hasattr(calibration_v3, 'sha256_file'))

    def test_neutral_report_wording(self):
        m = compute_metrics(self.results(), self.human(self.table_output()))
        text = '\n'.join(report_lines(metrics=m, batches=batch_measurements([self.results()]))).lower()
        for claim in ('calibrated', 'recommend', 'should', 'keyword'):
            self.assertNotIn(claim, text)
        self.assertIn('exact agreement: 2/8 (25.0%)', text)
        self.assertIn('false-reject rate (human keepers rated <= 2): 2/5 (40.0%)', text)
        self.assertIn('t and e agreement: unavailable', text)


class SafetyInvariantMatrixTest(unittest.TestCase):
    """Spec 19 -> tests (R5 section 5). Each entry must name an existing test (method or class);
    entries marked 'S17' are also exercised directly above."""
    MATRIX = {
        1: [('tests.test_original_integrity_v3', 'AcceptTest', 'test_original_never_opened_for_writing_and_unchanged'),
            ('tests.test_apply_v3_order', 'CleanRunTest', 'test_originals_never_written'), 'S17'],
        2: [('tests.test_archive_cli', 'ArchiveCliTest', 'test_nothing_deleted'), 'S17'],
        3: [('tests.test_apply_v3_order', 'CleanRunTest', 'test_writes_not_required_items_and_skips_review_items'),
            ('tests.test_apply_v3_order', 'FileLevelTest', 'test_yes_required'),
            ('tests.test_apply_v3_order', 'PerItemTest', 'test_8_malformed_9_pending_10_invalidated'), 'S17'],
        4: [('tests.test_analyze_v3_e2e', 'HappyPathTest', 'test_complete_v3_output')],
        5: [('tests.test_apply_v3_order', 'PerItemTest', 'test_2_not_an_original_image'),
            ('tests.test_preview_checks_v3', 'EachCheckFailsAloneTest', 'test_pv6_preview_equal_to_original')],
        6: [('tests.test_output_safety', 'FindImagesExclusionTest', 'test_marked_custom_output_dir_excluded')],
        7: [('tests.test_apply_v3_order', 'PerItemTest', 'test_3_inside_generated_output')],
        8: [('tests.test_bursts_v3_preconditions', 'P1Test', 'test_group_across_sessions')],
        9: [('tests.test_preview_checks_v3', 'EachCheckFailsAloneTest', 'test_pv2_unmarked_previews'),
            ('tests.test_output_safety', 'MarkOutputDirTest', None)],
        10: [('tests.test_validate_v3_fields', 'V1Test', 'test_exactly_one_outcome_per_original_and_order'),
             ('tests.test_analyze_v3_e2e', 'FailureEntriesTest', 'test_batch_failure'), 'S17'],
        11: [('tests.test_validate_v3_rating', 'EvaluableRejectTest', None), 'S17'],
        12: [('tests.test_approve_v3_write', 'SuccessTest', 'test_isolation_only_target_approval_fields_and_editorial_suggestion')],
        13: [('tests.test_output_v3_refusal', 'ApprovalStateTest', 'test_invalidated_by_binding_checks_1_2_4')],
        14: [('tests.test_analyze_v3_e2e', 'RefusalTest', 'test_rerun_refused_with_full_12_7_text_before_previews_or_claude'),
             ('tests.test_archive_cli', 'ArchiveCliTest', 'test_nothing_deleted')],
        15: [('tests.test_coverage_v3', 'SafetyTest', 'test_no_metadata_no_original_hash_no_delete_or_move_no_write'), 'S17'],
        16: [('tests.test_apply_v3_order', 'Check12Test', 'test_unreadable_and_changed'),
             ('tests.test_apply_v3_order', 'Check12Test', 'test_approved_changed'), 'S17'],
        17: [('tests.test_apply_v3_order', 'FileLevelTest', 'test_0d_incomplete_output'),
             ('tests.test_startup_all_commands', 'JournalTest', 'test_every_command_but_recover_archive_refused_before_parsing'),
             'S17'],
        18: [('tests.test_coverage_v3', 'PreviewAccountingTest', 'test_orphan_after_analysis_listed_warned_not_deleted'),
             ('tests.test_output_v3_manifest', 'PreviewAccountingTest', None), 'S17'],
        19: [('tests.test_sidecar_v3', 'BeforeReplacementTest', 'test_each_failure_leaves_sidecar_unchanged_and_no_temp'),
             ('tests.test_sidecar_v3', 'AfterReplacementTest', 'test_verification_failure_no_rollback'),
             ('tests.test_sidecar_v3', 'OriginalSafetyTest', 'test_original_never_opened_for_writing')],
    }

    def test_every_invariant_is_mapped(self):
        self.assertEqual(set(self.MATRIX), set(range(1, 20)))
        for number, entries in self.MATRIX.items():
            self.assertTrue(any(isinstance(e, tuple) for e in entries), number)

    def test_every_named_test_exists(self):
        for number, entries in self.MATRIX.items():
            for entry in entries:
                if not isinstance(entry, tuple):
                    continue
                module, cls, method = entry
                with self.subTest(invariant=number, test=f'{module}.{cls}.{method}'):
                    klass = getattr(importlib.import_module(module), cls)
                    self.assertTrue(issubclass(klass, unittest.TestCase))
                    if method is None:
                        self.assertTrue(any(n.startswith('test_') for n in dir(klass)))
                    else:
                        self.assertTrue(callable(getattr(klass, method, None)))


if __name__ == '__main__':
    unittest.main()
