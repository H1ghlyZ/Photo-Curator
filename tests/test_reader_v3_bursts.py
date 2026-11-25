"""S11: burst read-time checks V39, V41, V44 (spec 15.3, 8.2, 8.2.1; spec 20 items 8 and 13) with
the approved deviations B-11a (session/file scope at read time), D4-4b (monotone status) and the
V43 separation (non-evidence re-run derived from S4's failed_rules, S4 unchanged)."""
from __future__ import annotations
import copy, unittest

from photo_curator.reader_v3 import NEUTRAL_MISMATCH
from tests.test_document_v3 import SESSION, original
from tests.test_reader_v3_file import Base

GROUPS = ('b1', 'b2', 'b3')


def entry(**fields) -> dict:
    base = {'stage': 'result', 'error': 'V1: no result for this original', 'original_path': '/library/X.ARW',
            'session_id': SESSION, 'burst_group': None, 'burst_sequence': None, 'capture_time': None,
            'batch_index': 0, 'sent_to_model': True, 'result': None, 'preview_path': None}
    base.update(fields)
    return base


class Helpers(Base):
    def set_status(self, group: str, value) -> None:
        for r in self.out.doc['results']:
            if r['burst_group'] == group and r['evaluable'] is True:
                r['burst_ranking_status'] = value
        self.out.rewrite()

    def effective(self, report) -> dict:
        return {g: report.groups[g].effective for g in GROUPS}

    def items_of(self, report, group):
        return [report.items[k] for k, r in enumerate(self.out.doc['results']) if r['burst_group'] == group]


class V39Test(Helpers):
    def test_clean_statuses(self):
        report = self.out.read()
        self.assertEqual(self.effective(report), {'b1': 'validated', 'b2': 'not_comparable', 'b3': 'invalid'})
        self.assertEqual(report.groups['b1'].rerun, 'validated')

    def test_status_differs_from_rerun(self):
        self.set_status('b1', 'not_comparable')
        report = self.out.read()
        self.assertEqual(report.groups['b1'].effective, 'invalid')
        self.assertIn(NEUTRAL_MISMATCH, report.groups['b1'].reasons)
        self.assertTrue(all(i.eligibility_invalid for i in self.items_of(report, 'b1')))
        self.assertTrue(all(i.skip_message is None for i in self.items_of(report, 'b1')))   # ratings/apply unaffected

    def test_not_allowed_value_and_not_the_same_within_group(self):
        self.set_status('b1', 'Validated')
        self.assertEqual(self.out.read().groups['b1'].effective, 'invalid')
        self.setUp()
        first = next(r for r in self.out.doc['results'] if r['burst_group'] == 'b1')
        first['burst_ranking_status'] = 'invalid'
        self.out.rewrite()
        report = self.out.read()
        self.assertEqual(report.groups['b1'].effective, 'invalid')
        self.assertTrue(any('differs within the group' in r for r in report.groups['b1'].reasons))

    def test_stored_invalid_never_upgraded_d4_4b(self):
        self.out.doc['evidence_rules_version'] = 'v9.9'                     # B6 ignored on re-run
        self.out.rewrite()
        report = self.out.read()
        self.assertEqual(report.groups['b3'].rerun, 'validated')
        self.assertEqual(report.groups['b3'].effective, 'invalid')
        self.assertIn(NEUTRAL_MISMATCH, report.groups['b3'].reasons)


class V43SeparationTest(Helpers):
    """Same stored data, two reader outcomes: B6 (evidence) counts only when the versions match."""

    def test_evidence_rule_only_counts_with_matching_version(self):
        self.set_status('b3', 'validated')                                  # as if old rules accepted it
        before = copy.deepcopy(self.out.doc)
        same = self.out.read()
        self.assertEqual((same.groups['b3'].rerun, same.groups['b3'].effective), ('invalid', 'invalid'))
        self.out.doc['evidence_rules_version'] = 'v9.9'
        self.out.rewrite()
        changed = self.out.read()
        self.assertEqual((changed.groups['b3'].rerun, changed.groups['b3'].effective), ('validated', 'validated'))
        self.assertEqual(changed.groups['b2'].rerun, 'not_comparable')       # C2 structural part only
        for k, r in enumerate(self.out.doc['results']):                     # nothing stored was changed
            for key in ('rank_reason', 'not_comparable_evidence', 'review_reasons', 'burst_ranking_status'):
                self.assertEqual(r[key], before['results'][k][key])

    def test_non_evidence_failure_still_invalid_with_changed_version(self):
        self.out.doc['evidence_rules_version'] = 'v9.9'
        winner = next(r for r in self.out.doc['results'] if r['burst_group'] == 'b1' and r['best_in_burst'])
        winner['burst_rank'] = 2                                              # B4 broken
        self.out.rewrite()
        report = self.out.read()
        self.assertEqual(report.groups['b1'].rerun, 'invalid')
        self.assertEqual(report.groups['b1'].effective, 'invalid')


class V41Test(Helpers):
    def test_sessions_mixed_in_one_group(self):
        r = next(r for r in self.out.doc['results'] if r['burst_group'] == 'b1')
        r['session_id'] = 'OTHER/SESSION'
        self.out.rewrite()
        report = self.out.read()
        self.assertEqual(report.groups['b1'].effective, 'invalid')
        self.assertTrue(any(i.findings.get('V41') for i in self.items_of(report, 'b1')))

    def test_group_larger_than_max_burst_size(self):
        self.out.doc['max_burst_size'] = 2
        self.out.doc['errors'] = [entry(burst_group='b1', burst_sequence='b1')]
        self.out.rewrite()
        report = self.out.read()
        self.assertTrue(any('larger than max_burst_size' in r for r in report.groups['b1'].reasons))
        self.assertEqual(report.groups['b1'].effective, 'invalid')


class V44ReadTimeTest(Helpers):
    """Read-time B0 under B-11a; the whole file is never refused."""

    def read_with(self, *errors):
        self.out.doc['errors'] = list(errors)
        self.out.rewrite()
        report = self.out.read()
        self.assertIsNone(report.refusal)
        return report

    def test_known_identity_sent_frame_missing(self):
        report = self.read_with(entry(burst_group='b1', burst_sequence='b1'))
        self.assertEqual(self.effective(report), {'b1': 'invalid', 'b2': 'not_comparable', 'b3': 'invalid'})
        self.assertTrue(all('burst_frame_missing' in i.gallery_flags for i in self.items_of(report, 'b1')))
        self.assertTrue(all(i.skip_message is None for i in report.items))

    def test_not_sent_entries_never_trigger_b0(self):
        unsent = entry(stage='preview', batch_index=None, sent_to_model=False, burst_group='b1', burst_sequence='b1')
        report = self.read_with(unsent)
        self.assertEqual(report.groups['b1'].effective, 'validated')

    def test_missing_burst_group_invalidates_the_whole_session(self):
        unknown = entry()
        del unknown['burst_group']                                           # batch_index present
        report = self.read_with(unknown)
        self.assertEqual(self.effective(report), {'b1': 'invalid', 'b2': 'invalid', 'b3': 'invalid'})
        self.assertTrue(any("session 'DCIM/100MSDCF' invalid" in w for w in report.warnings))

    def test_unknown_identity_in_another_session_leaves_this_session(self):
        unknown = entry(session_id='OTHER/SESSION')
        del unknown['burst_group']
        report = self.read_with(unknown)
        self.assertEqual(report.groups['b1'].effective, 'validated')

    def test_missing_session_invalidates_the_whole_file(self):
        unknown = entry()
        del unknown['burst_group'], unknown['session_id'], unknown['batch_index']
        report = self.read_with(unknown)
        self.assertEqual(self.effective(report), {'b1': 'invalid', 'b2': 'invalid', 'b3': 'invalid'})
        self.assertTrue(any('whole file invalid' in w for w in report.warnings))

    def test_names_no_frame_and_sent_missing(self):
        for unknown in (entry(original_path=None), {k: v for k, v in entry().items() if k != 'sent_to_model'}):
            with self.subTest(keys=sorted(unknown)[:3]):
                self.setUp()
                report = self.read_with(unknown)
                self.assertEqual(report.groups['b1'].effective, 'invalid')

    def test_batch_scope_never_used(self):
        """B-11a: a batch_index pointing nowhere still widens to the session, never narrows."""
        unknown = entry(batch_index=999)
        del unknown['burst_group']
        report = self.read_with(unknown)
        self.assertEqual(self.effective(report), {'b1': 'invalid', 'b2': 'invalid', 'b3': 'invalid'})


if __name__ == '__main__':
    unittest.main()
