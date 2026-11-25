"""S15: read-only coverage report (spec 7.3, 12.8, V37, V40, V46, 14.7; spec 20 item 22) with the
approved decisions P-15a (gaps), P-15b (recomputed suggestions), P-15c (unknown capture type) and
P-15d (data + lines). Synthetic complete v3 output (fake Claude, real S3-S10 code)."""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import core, hashing_v3
from photo_curator.coverage_v3 import (NOT_VERIFIED, ORPHAN_LABEL, UNKNOWN_CAPTURE_TYPE, UNKNOWN_SESSION,
                                       CoverageRefusal, build_report)
from photo_curator.locking_v3 import JOURNAL_NAME
from photo_curator.output_v3 import MANIFEST_NAME
from photo_curator.preview_checks_v3 import expected_preview_name
from photo_curator.schema_v3 import DECISIONS
from tests.test_document_v3 import SESSION, original
from tests.test_reader_v3_file import Output


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s15-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        self.out = Output(self.work)
        self.results = self.work / 'ratings-review.json'

    def edit(self, n: int, rating: int | None = None, **changes):
        r = self.out.result(n)
        if rating is not None:
            r.update(technical_score=rating, editorial_score=rating, rating=rating, decision=DECISIONS[rating])
        r.update(changes)

    def save(self):
        self.out.rewrite()

    def report(self):
        return build_report(self.results)


class RefusalTest(Base):
    def test_v1_v2_refused(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.results.write_text(json.dumps({'schema_version': version, 'results': []}), encoding='utf-8')
                with self.assertRaises(CoverageRefusal) as ctx:
                    self.report()
                self.assertIn('schema version 3 only', ctx.exception.message)

    def test_incomplete_output_refused(self):
        os.unlink(self.work / MANIFEST_NAME)
        with self.assertRaises(CoverageRefusal) as ctx:
            self.report()
        self.assertIn('analysis output incomplete', ctx.exception.message)

    def test_archive_in_progress_refused(self):
        (self.work / JOURNAL_NAME).write_bytes(b'{}')
        with self.assertRaises(CoverageRefusal):
            self.report()


class SessionCountTest(Base):
    def test_counts_per_session(self):
        report = self.report()
        c = report.sessions[SESSION]
        self.assertEqual((c.total, c.evaluable, c.rating_0, c.errors), (9, 8, 1, 0))
        self.assertEqual(c.ratings, {1: 0, 2: 0, 3: 8, 4: 0, 5: 0})

    def test_second_session_and_unknown_session_errors(self):
        self.edit(8, session_id='DCIM/101MSDCF')
        self.out.doc['errors'] = [
            {'stage': 'original', 'error': 'original content changed during preview generation',
             'original_path': '/library/X.ARW', 'session_id': None, 'burst_group': None, 'burst_sequence': None,
             'capture_time': None, 'batch_index': None, 'sent_to_model': False, 'result': None, 'preview_path': None},
            {'stage': 'result', 'error': 'V1: no result', 'original_path': '/library/Y.ARW', 'session_id': SESSION,
             'burst_group': None, 'burst_sequence': None, 'capture_time': None, 'batch_index': 0,
             'sent_to_model': True, 'result': None, 'preview_path': None}]
        self.save()
        report = self.report()
        self.assertEqual(report.sessions['DCIM/101MSDCF'].total, 1)
        self.assertEqual(report.sessions[UNKNOWN_SESSION].errors, 1)                 # never dropped
        self.assertEqual(report.sessions[SESSION].errors, 1)
        self.assertEqual(sum(c.errors for c in report.sessions.values()), len(self.out.doc['errors']))
        self.assertTrue(any(line.startswith(f'  {UNKNOWN_SESSION}: total 1') for line in report.lines))


class CaptureTypeAndSuggestionTest(Base):
    def test_capture_type_and_type_by_rating(self):
        self.edit(8, capture_type=None, review_reasons=sorted(set(self.out.result(8)['review_reasons']) | {'field_invalid'}),
                  needs_review=True)
        self.edit(2, capture_type='group')
        self.save()
        report = self.report()
        self.assertEqual(report.capture_types[UNKNOWN_CAPTURE_TYPE], 1)                # P-15c: not dropped
        self.assertEqual(report.capture_types['group'], 1)
        self.assertEqual(report.capture_type_rating[('group', 3)], 1)
        self.assertEqual(report.capture_type_rating[('candid', 0)], 1)               # the unevaluable frame
        self.assertEqual(sum(report.capture_types.values()), 9)

    def test_suggestions_are_recomputed_not_trusted(self):
        honest = self.report()
        self.edit(8, documentation_suggestion='retain_low_rating', editorial_suggestion='hero_candidate')
        self.save()
        report = self.report()
        self.assertEqual(report.documentation_suggestions, honest.documentation_suggestions)
        self.assertEqual(report.editorial_suggestions, honest.editorial_suggestions)
        self.assertTrue(any('V37' in w and 'DSC00008.ARW' in w for w in report.warnings))


class GapTest(Base):
    """P-15a: gaps only between consecutive usable photos, >= 10 minutes inclusive."""

    def times(self, mapping):
        for n, value in mapping.items():
            self.edit(n, capture_time=value)

    def test_boundaries_and_ordering(self):
        # stored in frame order; capture order differs (frame 5 earliest)
        self.times({5: '2026-11-15T10:00:00', 2: '2026-11-15T10:09:59', 3: '2026-11-15T10:19:59',
                    4: '2026-11-15T10:40:00', 6: '2026-11-15T10:41:00', 7: '2026-11-15T10:42:00',
                    8: '2026-11-15T10:43:00', 1: '2026-11-15T10:44:00',
                    9: '2026-11-15T09:00:00'})                                        # rating 0: bounds only
        self.save()
        report = self.report()
        gaps = [(g.start, g.end) for g in report.gaps]
        self.assertNotIn(('2026-11-15T10:00:00', '2026-11-15T10:09:59'), gaps)        # 9:59 < 10
        self.assertIn(('2026-11-15T10:09:59', '2026-11-15T10:19:59'), gaps)           # exactly 10:00
        self.assertIn(('2026-11-15T10:19:59', '2026-11-15T10:40:00'), gaps)           # > 10
        self.assertEqual(len(gaps), 2)
        self.assertEqual(report.observation[SESSION], ('2026-11-15T09:00:00', '2026-11-15T10:44:00'))
        self.assertFalse(any(g.start == '2026-11-15T09:00:00' for g in report.gaps))  # no gap before first usable
        self.assertIn('no rating >= 3 image between 2026-11-15T10:09:59 and 2026-11-15T10:19:59 in DCIM/100MSDCF',
                      '\n'.join(report.lines))

    def test_no_gap_after_last_usable(self):
        self.edit(8, rating=2, capture_time='2026-11-15T12:00:00')                      # rating 2 late in the day
        self.times({n: f'2026-11-15T10:0{n}:00' for n in (1, 2, 3, 4, 5, 6, 7)})
        self.save()
        report = self.report()
        self.assertEqual(report.gaps, [])
        self.assertEqual(report.observation[SESSION][1], '2026-11-15T12:00:00')

    def test_session_without_usable_photo(self):
        self.edit(9, session_id='DCIM/102MSDCF', capture_time='2026-11-15T08:00:00')     # only the rating-0 frame
        self.save()
        report = self.report()
        self.assertIn('DCIM/102MSDCF', report.sessions_without_usable)
        self.assertFalse(any(g.session == 'DCIM/102MSDCF' for g in report.gaps))
        self.assertIn('  DCIM/102MSDCF: no rating >= 3 image with a capture time', report.lines)

    def test_mixed_offsets_no_gaps_at_all(self):
        self.times({2: '2026-11-15T10:00:00+07:00', 3: '2026-11-15T11:00:00'})
        self.save()
        report = self.report()
        self.assertEqual(report.gaps, [])
        self.assertCountEqual(report.mixed_offsets[SESSION], ['+07:00', 'no offset'])
        self.assertTrue(any(w.startswith(f'mixed time offsets in session {SESSION}; timeline gaps not computed')
                            for w in report.warnings))

    def test_same_offset_handled_normally(self):
        self.times({n: f'2026-11-15T{10 + (n - 1) // 6:02d}:{10 * ((n - 1) % 6):02d}:00+07:00' for n in range(1, 9)})
        self.edit(9, capture_time='2026-11-15T10:05:00+07:00')
        self.save()
        self.assertEqual(len(self.report().gaps), 7)


class UntimedTest(Base):
    def test_null_and_invalid_listed_with_unverified_hash(self):
        self.edit(2, capture_time=None)
        self.edit(3, capture_time='2026-13-01T10:00:00')                                 # V40 invalid
        self.save()
        report = self.report()
        listed = {Path(u['original_path']).name for u in report.untimed}
        self.assertEqual(listed, {'DSC00002.ARW', 'DSC00003.ARW'})
        text = '\n'.join(report.lines)
        self.assertIn(f'({NOT_VERIFIED})', text)


class PreviewAccountingTest(Base):
    def test_manifest_counts_and_recount(self):
        report = self.report()
        self.assertEqual(report.manifest_counts, report.recount)
        self.assertEqual(report.orphans, [])

    def test_orphan_after_analysis_listed_warned_not_deleted(self):
        extra = self.work / 'previews' / 'hand-placed.jpg'
        extra.write_bytes(b'x')
        report = self.report()
        self.assertIn('hand-placed.jpg', report.orphans)
        self.assertTrue(any('recount' in w and 'differs' in w for w in report.warnings))
        self.assertIn(ORPHAN_LABEL + ':', report.lines)
        self.assertTrue(extra.exists())

    def test_stage_original_orphan_annotated(self):
        lost = '/library/DSC00042.ARW'
        (self.work / 'previews' / expected_preview_name(lost)).write_bytes(b'x')
        self.out.doc['errors'] = [{'stage': 'original', 'error': 'original content changed during preview generation',
                                   'original_path': lost, 'session_id': SESSION, 'burst_group': None, 'burst_sequence': None,
                                   'capture_time': None, 'batch_index': None, 'sent_to_model': False, 'result': None,
                                   'preview_path': None}]
        self.save()
        report = self.report()
        line = next(l for l in report.lines if expected_preview_name(lost) in l)
        self.assertIn('matches the stage = original error for DSC00042.ARW', line)


class SafetyTest(Base):
    def test_no_metadata_no_original_hash_no_delete_or_move_no_write(self):
        before = tree(self.work)
        boom = AssertionError('forbidden call')
        with mock.patch.object(core, 'capture_records', side_effect=boom), \
             mock.patch.object(core, 'run', side_effect=boom), \
             mock.patch.object(hashing_v3, 'sha256_file', side_effect=boom), \
             mock.patch.object(os, 'unlink', side_effect=boom), mock.patch.object(os, 'remove', side_effect=boom), \
             mock.patch.object(os, 'rename', side_effect=boom), mock.patch.object(os, 'replace', side_effect=boom), \
             mock.patch.object(shutil, 'move', side_effect=boom):
            report = self.report()
        self.assertTrue(report.lines)
        self.assertEqual(tree(self.work), before)                                        # nothing written

    def test_neutral_wording(self):
        text = '\n'.join(self.report().lines).lower()
        for claim in ('missing', 'incomplete', 'recommend', 'should', 'lacks'):
            self.assertNotIn(claim, text)


if __name__ == '__main__':
    unittest.main()
