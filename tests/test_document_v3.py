"""S10: v3 results document assembly (spec 14.1, 14.4, 15.4 steps 10-15; decision M-3).

Runs are synthetic: the fake Claude answers, the real S3-S5 code validates, bursts and routes.
No originals exist; previews are small synthetic files in a temporary marked folder.
"""
from __future__ import annotations
import json, os, tempfile, unittest, uuid
from pathlib import Path

from photo_curator.core import OUTPUT_MARKER
from photo_curator.document_v3 import (DocumentError, RATING_STYLE_V3, assemble_document, build_document,
                                       build_result, document_bytes, reasons_from_model_text)
from photo_curator.output_v3 import check_completeness, write_analysis_outputs
from photo_curator.prompt_v3 import PromptFrame, build_prompt, parse_claude_output
from photo_curator.schema_v3 import RESULT_FIELDS
from photo_curator.suggestions_v3 import apply_suggestions_v3
from photo_curator.bursts_v3 import apply_bursts_v3
from photo_curator.validate_v3 import FrameContext, validate_batch_v3
from tests.fake_claude_v3 import FakeClaudeV3, evaluable_result, unevaluable_result

LIB = '/library/DCIM/100MSDCF'
SESSION = 'DCIM/100MSDCF'
SHA = 'd' * 64


def original(n: int) -> str:
    return f'{LIB}/DSC{n:05d}.ARW'


class Run:
    """A synthetic analysis: frames, contexts, fake answers, S3 output, assembled document."""

    def __init__(self, work: Path, groups: dict[int, str | None], overrides=None, **file_fields):
        self.work = work
        previews = work / 'previews'
        previews.mkdir(exist_ok=True)
        (work / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        (previews / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        self.frames, self.contexts = [], {}
        for n, group in groups.items():
            preview = previews / f'DSC{n:05d}__{n:010d}.jpg'
            preview.write_bytes(b'\xff\xd8 synthetic preview %d' % n)
            frame = PromptFrame(original(n), str(preview), SESSION, group, group)
            self.frames.append(frame)
            self.contexts[frame.original_path] = FrameContext(
                original_path=frame.original_path, preview_path=frame.preview_path, session_id=SESSION,
                burst_group=group, burst_sequence=group, capture_time=f'2026-11-15T10:14:{n:02d}',
                original_sha256=SHA, batch_index=0)
        fake = FakeClaudeV3(overrides or {})
        completed = fake(['claude', '-p', build_prompt(self.frames)])
        photos = parse_claude_output(completed.stdout)
        self.accepted, self.errors = validate_batch_v3([f.original_path for f in self.frames], photos, self.contexts)
        fields = dict(source_folder=LIB, max_burst_size=12, burst_window_seconds=1.0)
        fields.update(file_fields)
        self.doc = assemble_document(self.accepted, self.errors, self.contexts, **fields)

    def result(self, n: int) -> dict:
        return next(r for r in self.doc['results'] if r['original_path'] == original(n))


def standard_overrides() -> dict:
    """Group b2 not comparable, group b3 invalid (generic rank_reason), frame 9 unevaluable."""
    nc = 'This frame shows the venue entrance on the left, unrelated to the speaker frames'
    return {
        original(4): evaluable_result(original(4), burst_comparison='not_comparable', not_comparable_evidence=[nc]),
        original(5): evaluable_result(original(5), burst_comparison='not_comparable',
                                      not_comparable_evidence=['The background shows a different room with a hand on a table']),
        original(6): evaluable_result(original(6), burst_comparison='ranked', burst_rank=1, best_in_burst=True,
                                      co_best=False, near_duplicate_of=None, rank_reason='great photo'),
        original(7): evaluable_result(original(7), burst_comparison='ranked', burst_rank=2, best_in_burst=False,
                                      co_best=False, near_duplicate_of=None,
                                      rank_reason='The speaker on the left looks down, weaker expression'),
        original(9): unevaluable_result(original(9)),
    }


STANDARD_GROUPS = {1: None, 2: 'b1', 3: 'b1', 4: 'b2', 5: 'b2', 6: 'b3', 7: 'b3', 8: None, 9: None}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s10-doc-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))

    def standard(self, **file_fields) -> Run:
        return Run(self.work, STANDARD_GROUPS, standard_overrides(), **file_fields)


class ResultTest(Base):
    def test_keys_order_and_approval_defaults(self):
        run = self.standard()
        self.assertEqual(run.errors, [])
        for result in run.doc['results']:
            self.assertEqual(tuple(result), tuple(RESULT_FIELDS))
            self.assertIs(result['approved'], False)
            for key in ('approved_rating', 'approved_at', 'approved_original_path', 'approved_original_sha256',
                        'approved_preview_path', 'approved_preview_sha256'):
                self.assertIsNone(result[key])

    def test_code_fields_from_context(self):
        run = self.standard()
        r = run.result(2)
        ctx = run.contexts[original(2)]
        self.assertEqual((r['path'], r['original_path'], r['preview_path'], r['session_id'], r['capture_time'],
                          r['original_sha256'], r['burst_group'], r['burst_sequence']),
                         (ctx.original_path, ctx.original_path, ctx.preview_path, ctx.session_id, ctx.capture_time,
                          ctx.original_sha256, ctx.burst_group, ctx.burst_sequence))

    def test_s3_s5_values_unchanged(self):
        run = self.standard()
        after_s4, _ = apply_bursts_v3(run.accepted, run.errors, run.contexts)
        after_s5, _ = apply_suggestions_v3(after_s4, run.errors)
        for path, acc in after_s5.items():
            r = next(x for x in run.doc['results'] if x['original_path'] == path)
            for key, value in acc.fields.items():
                self.assertEqual(r[key], value, (path, key))

    def test_burst_statuses(self):
        run = self.standard()
        self.assertEqual(run.result(2)['burst_ranking_status'], 'validated')
        self.assertEqual(run.result(4)['burst_ranking_status'], 'not_comparable')
        self.assertEqual(run.result(6)['burst_ranking_status'], 'invalid')
        self.assertIsNone(run.result(1)['burst_ranking_status'])
        self.assertEqual(run.result(9)['rating'], 0)

    def test_reasons_are_model_text_only(self):
        run = self.standard()
        r = run.result(1)
        self.assertEqual(r['reasons'], r['technical']['evidence'] + r['editorial']['evidence'])
        self.assertEqual(run.result(9)['reasons'], [])
        fields = {'technical': {'evidence': ['a' * 25, 7], 'motion_evidence': ['m' * 25]},
                  'editorial': None, 'decisive_evidence': None, 'distinct_moment_evidence': ['d' * 25]}
        self.assertEqual(reasons_from_model_text(fields), ['a' * 25, 'm' * 25, 'd' * 25])

    def test_build_result_contract(self):
        run = self.standard()
        after_s4, _ = apply_bursts_v3(run.accepted, run.errors, run.contexts)
        with self.assertRaises(DocumentError):
            build_result(run.accepted[original(1)])              # S4/S5 not run
        after_s5, _ = apply_suggestions_v3(after_s4, run.errors)
        acc = after_s5[original(1)]
        import dataclasses
        tampered = dataclasses.replace(acc, fields=dict(acc.fields, approved=True))
        with self.assertRaises(DocumentError):
            build_result(tampered)
        with self.assertRaises(DocumentError):
            build_result('not accepted')


class DocumentTest(Base):
    def test_file_level_fields(self):
        run = self.standard()
        doc = run.doc
        self.assertEqual(list(doc)[:4], ['schema_version', 'rubric_version', 'evidence_rules_version', 'run_id'])
        self.assertEqual((doc['schema_version'], doc['rubric_version'], doc['evidence_rules_version']), (3, 'v3', 'v3.1'))
        self.assertEqual(uuid.UUID(doc['run_id']).version, 4)
        self.assertEqual((doc['confidence_threshold'], doc['max_burst_size'], doc['burst_scope']), (0.6, 12, 'per_session'))
        self.assertEqual((doc['rating_style'], doc['application'], doc['source_folder']), (RATING_STYLE_V3, 'preview_only', LIB))
        self.assertRegex(doc['created_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$')
        self.assertEqual(doc['distribution']['denominator'], 8)
        self.assertEqual(doc['distribution']['rating_0'], 1)
        self.assertIn(doc['calibration_drift_warning'], (True, False))

    def test_v30_output_read_by_v31_reader_gives_v43(self):          # M-27 (S16)
        from tests.test_reader_v3_file import Output                     # local: that module imports this one
        from photo_curator.reader_v3 import EVIDENCE_CHANGED_LABEL
        tmp = tempfile.TemporaryDirectory(prefix='pc-s16-v43-')
        self.addCleanup(tmp.cleanup)
        out = Output(Path(os.path.realpath(tmp.name)))
        out.doc['evidence_rules_version'] = 'v3.0'
        out.rewrite()
        report = out.read()
        self.assertIsNone(report.refusal)                                 # warned, never refused
        self.assertTrue(report.evidence_rules_changed)
        self.assertTrue(any(w.startswith('V43') and "'v3.0' != 'v3.1'" in w for w in report.warnings))
        self.assertTrue(all(any(EVIDENCE_CHANGED_LABEL in w for w in i.warnings) for i in report.items))

    def test_results_sorted_errors_kept(self):
        overrides = standard_overrides()
        overrides[original(8)] = None                                 # no answer -> error entry
        run = Run(self.work, STANDARD_GROUPS, overrides)
        paths = [r['original_path'] for r in run.doc['results']]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(run.doc['errors'], run.errors)
        self.assertEqual(len(run.doc['errors']), 1)

    def test_explicit_run_id_and_created_at(self):
        rid = str(uuid.uuid4())
        run = self.standard(run_id=rid, created_at='2026-11-15T10:00:00+0700')
        self.assertEqual((run.doc['run_id'], run.doc['created_at']), (rid, '2026-11-15T10:00:00+0700'))

    def test_invalid_file_fields_refused(self):
        for bad in ({'max_burst_size': 0}, {'max_burst_size': 13}, {'burst_window_seconds': -1},
                    {'run_id': 'not-a-uuid'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.standard(**bad)

    def test_bytes_round_trip_and_complete_output(self):
        run = self.standard()
        data = document_bytes(run.doc)
        self.assertEqual(json.loads(data), run.doc)
        write_analysis_outputs(self.work, results_bytes=data, csv_bytes=b'c\n', html_bytes=b'h\n')
        self.assertTrue(check_completeness(self.work).complete)

    def test_summary_type_checked(self):
        with self.assertRaises(DocumentError):
            build_document({}, [], {'distribution': {}}, source_folder=LIB, max_burst_size=12, burst_window_seconds=1.0)


if __name__ == '__main__':
    unittest.main()
