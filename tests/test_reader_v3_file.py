"""S11: file-level read-time checks V30, V31, V32, V43, V46, V47 (spec 15.3; spec 20 item 13).

``Output`` builds a complete synthetic v3 output folder through the real S3-S10 code (fake
Claude) with PV5-correct preview names, so the reader sees exactly what analyze writes.
"""
from __future__ import annotations
import hashlib, json, os, tempfile, unittest
from pathlib import Path

from photo_curator.artifacts_v3 import csv_bytes, gallery_bytes
from photo_curator.document_v3 import document_bytes
from photo_curator.locking_v3 import JOURNAL_MESSAGE, JOURNAL_NAME
from photo_curator.output_v3 import MANIFEST_NAME, write_analysis_outputs
from photo_curator.preview_checks_v3 import expected_preview_name
from photo_curator.reader_v3 import EVIDENCE_CHANGED_LABEL, read_results
from tests.fake_claude_v3 import evaluable_result
from tests.test_document_v3 import STANDARD_GROUPS, Run, original, standard_overrides

RESULTS = 'ratings-review.json'


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Output:
    """A complete v3 output in ``work``; ``rewrite`` keeps it complete after an edit."""

    def __init__(self, work: Path, overrides=None):
        self.work = work
        overrides = standard_overrides() if overrides is None else overrides
        overrides.setdefault(original(1), evaluable_result(original(1), confidence=0.4))   # needs review
        run = Run(work, STANDARD_GROUPS, overrides)
        self.doc = run.doc
        for r in self.doc['results']:                                    # PV5-correct names
            old = Path(r['preview_path'])
            new = old.with_name(expected_preview_name(r['original_path']))
            os.replace(old, new)
            r['preview_path'] = str(new)
        self.rewrite()

    def result(self, n: int) -> dict:
        return next(r for r in self.doc['results'] if r['original_path'] == original(n))

    def rewrite(self, doc=None, *, raw: bytes | None = None) -> None:
        doc = self.doc if doc is None else doc
        data = raw if raw is not None else (json.dumps(doc, indent=2) + '\n').encode('utf-8')
        for name in os.listdir(self.work):
            if name.startswith('ratings-review.') or '.tmp-' in name:
                os.unlink(self.work / name)
        write_analysis_outputs(self.work, results_bytes=data, csv_bytes=csv_bytes(doc), html_bytes=gallery_bytes(doc))

    def approve(self, n: int) -> dict:
        r = self.result(n)
        r.update(approved=True, approved_rating=r['rating'], approved_at='2026-11-15T07:12:03Z',
                 approved_original_path=r['original_path'], approved_original_sha256=r['original_sha256'],
                 approved_preview_path=r['preview_path'], approved_preview_sha256=sha(r['preview_path']))
        self.rewrite()
        return r

    def read(self, **kw):
        return read_results(self.work / RESULTS, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s11-rd-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        self.out = Output(self.work)


class CleanFileTest(Base):
    def test_clean_output_has_no_findings(self):
        report = self.out.read()
        self.assertIsNone(report.refusal)
        self.assertEqual(report.schema_version, 3)
        problems = {(i.index, row): p for i in report.items for row, p in i.findings.items()
                    if row not in ('V39',)}
        self.assertEqual(problems, {})
        self.assertTrue(all(i.skip_message is None for i in report.items))
        self.assertFalse(report.evidence_rules_changed)

    def test_reader_never_writes(self):
        before = {n: sha(self.work / n) for n in os.listdir(self.work) if (self.work / n).is_file()}
        self.out.read()
        after = {n: sha(self.work / n) for n in os.listdir(self.work) if (self.work / n).is_file()}
        self.assertEqual(after, before)


class V30Test(Base):
    def test_invalid_json_and_unknown_schema_refused(self):
        for raw in (b'{oops', b'[]', json.dumps({'schema_version': 7, 'results': []}).encode()):
            with self.subTest(raw=raw[:15]):
                (self.work / RESULTS).write_bytes(raw)
                report = self.out.read()
                self.assertEqual(report.refusal_row, 'V30')

    def test_v1_v2_accepted_with_v33_only(self):
        for version in (1, 2):
            with self.subTest(version=version):
                (self.work / RESULTS).write_text(json.dumps({'schema_version': version, 'results': [
                    {'path': '/lib/A.JPG', 'rating': 3}]}), encoding='utf-8')
                report = self.out.read()
                self.assertIsNone(report.refusal)
                self.assertEqual(report.items[0].findings, {})


class V31Test(Base):
    def test_each_file_level_field(self):
        cases = {'rubric': {'rubric_version': 'v2'}, 'evidence version': {'evidence_rules_version': ' '},
                 'run_id': {'run_id': 'x'}, 'threshold': {'confidence_threshold': 0.5},
                 'max burst': {'max_burst_size': 13}, 'scope': {'burst_scope': 'global'},
                 'window': {'burst_window_seconds': -1}, 'errors': {'errors': [{'stage': 'x', 'error': 'e'}]}}
        for label, change in cases.items():
            with self.subTest(label=label):
                (self.work / RESULTS).write_text(json.dumps(dict(self.out.doc, **change)), encoding='utf-8')
                self.assertEqual(self.out.read().refusal_row, 'V31')


class V32Test(Base):
    def test_distribution_mismatch_is_display_only(self):
        self.out.doc['distribution'] = dict(self.out.doc['distribution'], denominator=99)
        self.out.doc['calibration_drift_warning'] = 'yes'
        self.out.rewrite()
        report = self.out.read()
        self.assertIsNone(report.refusal)
        self.assertTrue(any(w.startswith('V32: stored distribution') for w in report.warnings))
        self.assertTrue(any('calibration_drift_warning' in w for w in report.warnings))
        self.assertEqual(report.warnings_distribution['denominator'], 8)
        self.assertTrue(all(i.skip_message is None for i in report.items))


class V43Test(Base):
    def test_evidence_rules_version_mismatch(self):
        self.out.doc['evidence_rules_version'] = 'v9.9'
        self.out.rewrite()
        report = self.out.read()
        self.assertIsNone(report.refusal)
        self.assertTrue(report.evidence_rules_changed)
        self.assertTrue(any(EVIDENCE_CHANGED_LABEL in w for w in report.warnings))
        item = report.items[0]
        self.assertTrue(any(EVIDENCE_CHANGED_LABEL in w for w in item.warnings))
        self.assertTrue(all(i.skip_message is None for i in report.items))       # apply unaffected


class V46Test(Base):
    def test_incomplete_output_refused(self):
        os.unlink(self.work / MANIFEST_NAME)
        report = self.out.read()
        self.assertEqual(report.refusal_row, 'V46')
        self.assertIn('analysis output incomplete', report.refusal)

    def test_recount_difference_is_warning_only(self):
        (self.work / 'previews' / 'extra.jpg').write_bytes(b'x')
        report = self.out.read()
        self.assertIsNone(report.refusal)
        self.assertTrue(any(w.startswith('V46:') and 'orphan' in w for w in report.warnings))
        self.assertTrue((self.work / 'previews' / 'extra.jpg').exists())


class V47Test(Base):
    def test_journal_refuses_before_parsing_any_version(self):
        (self.work / JOURNAL_NAME).write_bytes(b'{}')
        for raw in (b'{not json', json.dumps({'schema_version': 1, 'results': []}).encode()):
            with self.subTest(raw=raw[:10]):
                (self.work / RESULTS).write_bytes(raw)
                report = self.out.read()
                self.assertEqual((report.refusal_row, report.refusal), ('V47', JOURNAL_MESSAGE))
                self.assertIsNone(report.doc)


if __name__ == '__main__':
    unittest.main()
