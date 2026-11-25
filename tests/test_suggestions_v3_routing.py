"""S5: review routing (spec 11), approval state at analysis (12.1 rows 2-3), the 7.2 editorial
table for every approval state, the unevaluable contract (D5-11) and the distribution (17).

Pure functions only; synthetic values, no files, no tools.
"""
from __future__ import annotations
import dataclasses, unittest

from photo_curator.bursts_v3 import apply_bursts_v3
from photo_curator.schema_v3 import APPROVAL_STATES, BASE_MATRIX, REVIEW_CODES
from photo_curator.suggestions_v3 import (analysis_approval_state_v3, apply_suggestions_v3,
                                          distribution_v3, editorial_suggestion_v3)
from photo_curator.validate_v3 import FrameContext, validate_batch_v3

SHA = '7' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Background crowd on the left differs from the speaker frame'
NON_BURST = dict(burst_comparison=None, burst_rank=None, best_in_burst=None, co_best=None,
                 near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[])


def orig(n):
    return f'/lib/S/DSC{n:05d}.ARW'


def ctx(n, group=None):
    return FrameContext(original_path=orig(n), preview_path=f'/out/previews/p{n:05d}.jpg', session_id='S',
                        burst_group=group, burst_sequence=group, capture_time=None, original_sha256=SHA,
                        batch_index=0)


def model(n, t=3, e=3, burst=None, **over):
    raw = {
        'path': orig(n), 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': t, 'editorial_score': e,
        'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]},
        'editorial': {'evidence': [GOOD2]}, 'decisive_evidence': [GOOD], 'proposed_rating': BASE_MATRIX[(t, e)],
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': False, 'distinct_moment_evidence': [], 'confidence': 0.9,
    }
    raw.update(burst if burst is not None else NON_BURST)
    raw.update(over)
    return raw


def unevaluable(n):
    return {**model(n), 'evaluable': False, 'unevaluable_reason': 'preview is almost fully black',
            'capture_type': None, 'technical_score': None, 'editorial_score': None,
            'technical': {'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []},
            'editorial': {'evidence': []}, 'decisive_evidence': [], 'proposed_rating': 0}


def stages(ctxs, photos):
    accepted, errors = validate_batch_v3(sorted(ctxs), photos, ctxs)
    s4, _ = apply_bursts_v3(accepted, errors, ctxs)
    return s4, errors


def pipeline(ctxs, photos):
    s4, errors = stages(ctxs, photos)
    return apply_suggestions_v3(s4, errors)


class RoutingTest(unittest.TestCase):
    def test_reasons_sorted_unique_and_needs_review(self):
        out, _ = pipeline({orig(1): ctx(1)}, [model(1, 5, 2, confidence=0.1, uncertain_checks=['focus', 'eyes'])])
        f = out[orig(1)].fields
        self.assertEqual(f['review_reasons'], sorted(set(f['review_reasons'])))
        self.assertEqual(set(f['review_reasons']),
                         {'low_confidence', 'critical_defect_uncertain', 'score_gap'})
        self.assertIs(f['needs_review'], True)
        self.assertEqual(frozenset(f['review_reasons']), out[orig(1)].review_reasons)
        self.assertTrue(set(f['review_reasons']) <= REVIEW_CODES)

    def test_no_reasons_no_review(self):
        out, _ = pipeline({orig(1): ctx(1)}, [model(1, 3, 3)])
        self.assertEqual(out[orig(1)].fields['review_reasons'], [])
        self.assertIs(out[orig(1)].fields['needs_review'], False)

    def test_rating_5_always_pending(self):
        out, _ = pipeline({orig(1): ctx(1)}, [model(1, 5, 5)])
        self.assertEqual(out[orig(1)].fields['review_reasons'], ['rating_5'])
        self.assertEqual(out[orig(1)].fields['editorial_suggestion'], 'pending_approval')

    def test_override_4_pending_before_approval(self):
        out, _ = pipeline({orig(1): ctx(1)}, [model(1, 3, 5, proposed_moment_override=True, proposed_rating=4)])
        f = out[orig(1)].fields
        self.assertEqual((out[orig(1)].rating, f['review_reasons']), (4, ['moment_override']))
        self.assertEqual(f['editorial_suggestion'], 'pending_approval')
        self.assertEqual(editorial_suggestion_v3(4, True, 'approved'), 'highlight')     # after approval

    def test_weak_burst_best_only_routes(self):
        ctxs = {orig(n): ctx(n, 'g1') for n in (1, 2)}
        burst = lambda rank, best: dict(burst_comparison='ranked', burst_rank=rank, best_in_burst=best,
                                        co_best=False, near_duplicate_of=None, rank_reason=GOOD,
                                        not_comparable_evidence=[])
        out, _ = pipeline(ctxs, [model(1, 2, 2, burst(1, True)), model(2, 2, 2, burst(2, False))])
        f = out[orig(1)].fields
        self.assertEqual(f['review_reasons'], ['weak_burst_best'])
        self.assertEqual(f['editorial_suggestion'], 'not_suggested')


class ApprovalStateTest(unittest.TestCase):
    def test_analysis_state(self):
        self.assertEqual(analysis_approval_state_v3(False), 'not_required')
        self.assertEqual(analysis_approval_state_v3(True), 'pending')
        with self.assertRaises(ValueError):
            analysis_approval_state_v3(1)


class EditorialTableTest(unittest.TestCase):                                 # 7.2, 20 item 10
    def test_every_state(self):
        expected = {
            'malformed': ('pending_approval', 'pending_approval'),
            'not_required': ('not_suggested', 'highlight'),       # hero + not_required: literal (D5-7)
            'pending': ('pending_approval', 'pending_approval'),
            'invalidated': ('pending_approval', 'pending_approval'),
            'approved': ('hero_candidate', 'highlight'),
        }
        self.assertEqual(set(expected), set(APPROVAL_STATES))
        for state, (hero, highlight) in expected.items():
            with self.subTest(state=state):
                self.assertEqual(editorial_suggestion_v3(5, True, state), hero)
                self.assertEqual(editorial_suggestion_v3(4, True, state), highlight)
                for rating in (0, 1, 2, 3):
                    self.assertEqual(editorial_suggestion_v3(rating, True, state), 'not_suggested')
                for rating in (4, 5):
                    self.assertEqual(editorial_suggestion_v3(rating, False, state), 'not_suggested')

    def test_unknown_state(self):
        with self.assertRaises(ValueError):
            editorial_suggestion_v3(4, True, 'unknown')


class UnevaluableTest(unittest.TestCase):                                    # 6.4, 11, D5-11
    def test_unevaluable_always_needs_review(self):
        ctxs = {orig(1): ctx(1), orig(2): ctx(2, 'g1'), orig(3): ctx(3, 'g1')}
        out, _ = pipeline(ctxs, [unevaluable(1), unevaluable(2), unevaluable(3)])
        for n in (1, 2, 3):
            f = out[orig(n)].fields
            self.assertIs(f['needs_review'], True)
            self.assertNotEqual(f['review_reasons'], [])
            self.assertIn('unevaluable', f['review_reasons'])
            self.assertEqual((f['documentation_suggestion'], f['editorial_suggestion']), ('unrated', 'not_suggested'))

    def test_rating_0_without_unevaluable_code_is_refused(self):
        s4, errors = stages({orig(1): ctx(1)}, [unevaluable(1)])
        broken = dataclasses.replace(s4[orig(1)], review_reasons=frozenset())
        with self.assertRaises(ValueError):
            apply_suggestions_v3({orig(1): broken}, errors)


class ContractTest(unittest.TestCase):
    def test_contract_violations(self):
        s4, errors = stages({orig(1): ctx(1)}, [model(1)])
        acc = s4[orig(1)]
        no_status = dataclasses.replace(acc, fields={k: v for k, v in acc.fields.items() if k != 'burst_ranking_status'})
        with self.assertRaises(ValueError):
            apply_suggestions_v3({orig(1): no_status}, errors)              # not through S4
        done, _ = apply_suggestions_v3(s4, errors)
        with self.assertRaises(ValueError):
            apply_suggestions_v3(done, errors)                               # already through S5
        with self.assertRaises(ValueError):
            apply_suggestions_v3(s4, {'x': 1})
        with self.assertRaises(ValueError):
            apply_suggestions_v3({orig(1): 'x'}, errors)


class DistributionTest(unittest.TestCase):                                   # 17, D5-4, D5-5
    def run_ratings(self, cells, unevaluables=0, extra_errors=0):
        n, photos, ctxs = 0, [], {}
        for t, e in cells:
            n += 1; ctxs[orig(n)] = ctx(n); photos.append(model(n, t, e))
        for _ in range(unevaluables):
            n += 1; ctxs[orig(n)] = ctx(n); photos.append(unevaluable(n))
        for _ in range(extra_errors):
            n += 1; ctxs[orig(n)] = ctx(n)                                   # no result -> error entry
        return pipeline(ctxs, photos)[1]

    def test_counts_percentages_and_separate_counts(self):
        summary = self.run_ratings([(1, 1), (2, 2), (3, 3), (3, 3), (4, 4)], unevaluables=2, extra_errors=3)
        d = summary.distribution
        self.assertEqual(d['denominator'], 5)
        self.assertEqual(d['counts'], {'1': 1, '2': 1, '3': 2, '4': 1, '5': 0})
        self.assertEqual(d['percentages'], {'1': 20.0, '2': 20.0, '3': 40.0, '4': 20.0, '5': 0.0})
        self.assertEqual((d['rating_0'], d['errors']), (2, 3))
        self.assertIs(summary.calibration_drift_warning, True)               # 20% > 15%

    def test_drift_boundary(self):
        exactly = self.run_ratings([(4, 4)] * 3 + [(3, 3)] * 17)            # 3/20 = 15%
        self.assertIs(exactly.calibration_drift_warning, False)
        above = self.run_ratings([(4, 4), (5, 5)] * 2 + [(3, 3)] * 22)     # 4/26 > 15%
        self.assertIs(above.calibration_drift_warning, True)

    def test_unrounded_percentages(self):
        d = self.run_ratings([(3, 3), (3, 3), (4, 4)]).distribution
        self.assertEqual(d['percentages']['3'], 100 * 2 / 3)

    def test_no_evaluable_rated_results(self):
        summary = self.run_ratings([], unevaluables=2, extra_errors=1)
        self.assertEqual(summary.distribution['denominator'], 0)
        self.assertEqual(summary.distribution['percentages'], {str(r): None for r in range(1, 6)})
        self.assertIsNone(summary.calibration_drift_warning)
        self.assertEqual((summary.distribution['rating_0'], summary.distribution['errors']), (2, 1))

    def test_errors_count_entries_not_originals(self):                       # D5-5
        summary = distribution_v3({}, [{'a': 1}, {'b': 2}, {'c': 3}])
        self.assertEqual(summary.distribution['errors'], 3)
        with self.assertRaises(ValueError):
            distribution_v3({}, 'x')


if __name__ == '__main__':
    unittest.main()
