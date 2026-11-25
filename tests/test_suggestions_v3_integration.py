"""S5: end-to-end S3 -> S4 -> S5 on a mixed run: non-burst frames, validated, not_comparable,
invalid and all-unevaluable groups, missing and duplicate frames. Checks routing invariants,
independence from S3/S4, input immutability and fatal_flaws.

Pure functions only; synthetic values, no files, no tools.
"""
from __future__ import annotations
import copy, unittest

from photo_curator.bursts_v3 import apply_bursts_v3
from photo_curator.schema_v3 import BASE_MATRIX, DOCUMENTATION_SUGGESTIONS, EDITORIAL_SUGGESTIONS, REVIEW_CODES
from photo_curator.suggestions_v3 import S5_FIELDS, apply_suggestions_v3
from photo_curator.validate_v3 import FrameContext, validate_batch_v3

SHA = '6' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Background crowd on the left differs from the speaker frame'
GENERIC = 'Great photo, nice composition!'
NON_BURST = dict(burst_comparison=None, burst_rank=None, best_in_burst=None, co_best=None,
                 near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[])


def orig(n):
    return f'/lib/S/DSC{n:05d}.ARW'


def ctx(n, group=None, batch=0):
    return FrameContext(original_path=orig(n), preview_path=f'/out/previews/p{n:05d}.jpg', session_id='S',
                        burst_group=group, burst_sequence=group, capture_time=None, original_sha256=SHA,
                        batch_index=batch)


def model(n, t=3, e=3, burst=None, claim=False, **over):
    raw = {
        'path': orig(n), 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': t, 'editorial_score': e,
        'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]},
        'editorial': {'evidence': [GOOD2]}, 'decisive_evidence': [GOOD], 'proposed_rating': BASE_MATRIX[(t, e)],
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': claim, 'distinct_moment_evidence': [GOOD] if claim else [], 'confidence': 0.9,
    }
    raw.update(burst if burst is not None else NON_BURST)
    raw.update(over)
    return raw


def unevaluable(n):
    return {**model(n), 'evaluable': False, 'unevaluable_reason': 'preview is almost fully black',
            'capture_type': None, 'technical_score': None, 'editorial_score': None,
            'technical': {'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []},
            'editorial': {'evidence': []}, 'decisive_evidence': [], 'proposed_rating': 0}


def ranked(rank, best, reason=GOOD):
    return dict(burst_comparison='ranked', burst_rank=rank, best_in_burst=best, co_best=False,
                near_duplicate_of=None, rank_reason=reason, not_comparable_evidence=[])


def nc():
    return dict(burst_comparison='not_comparable', burst_rank=None, best_in_burst=None, co_best=None,
                near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[GOOD2])


def mixed_run(flaws=()):
    """Batch 0: non-burst 1-2, validated gV 3-5 (5 unevaluable), all-unevaluable gU 6-7.
    Batch 1: not_comparable gN 8-9, invalid gI 10-11, duplicate in gD 12-13, missing 14 (gM)."""
    b0 = {orig(1): ctx(1), orig(2): ctx(2), orig(3): ctx(3, 'gV'), orig(4): ctx(4, 'gV'), orig(5): ctx(5, 'gV'),
          orig(6): ctx(6, 'gU'), orig(7): ctx(7, 'gU')}
    p0 = [model(1, 4, 4, fatal_flaws=list(flaws)), model(2, 2, 2, claim=True),
          model(3, 4, 4, ranked(1, True)), model(4, 2, 3, ranked(2, False), claim=True), unevaluable(5),
          unevaluable(6), unevaluable(7)]
    b1 = {orig(n): ctx(n, g, 1) for n, g in ((8, 'gN'), (9, 'gN'), (10, 'gI'), (11, 'gI'), (12, 'gD'),
                                             (13, 'gD'), (14, 'gM'), (15, 'gM'))}
    p1 = [model(8, 4, 4, nc()), model(9, 5, 5, nc()),
          model(10, 4, 4, ranked(1, True)), model(11, 2, 2, ranked(2, False, reason=GENERIC), claim=True),
          model(12, 4, 4, ranked(1, True)), model(13, 3, 3, ranked(2, False)), model(13, 3, 3, ranked(2, False)),
          model(15, 4, 4, ranked(1, True))]
    accepted, errors, contexts = {}, [], {}
    for ctxs, photos in ((b0, p0), (b1, p1)):
        acc, errs = validate_batch_v3(sorted(ctxs), photos, ctxs)
        accepted.update(acc); errors.extend(errs); contexts.update(ctxs)
    s4, _ = apply_bursts_v3(accepted, errors, contexts)
    snapshot = copy.deepcopy((s4, errors))
    s5, summary = apply_suggestions_v3(s4, errors)
    return accepted, s4, s5, summary, errors, snapshot


class MixedRunTest(unittest.TestCase):
    def setUp(self):
        self.accepted, self.s4, self.s5, self.summary, self.errors, self.snapshot = mixed_run()

    def f(self, n):
        return self.s5[orig(n)].fields

    def test_invariants_for_every_result(self):
        self.assertEqual(set(self.s5), set(self.s4))
        for key, acc in self.s5.items():
            f = acc.fields
            self.assertIs(f['needs_review'], bool(f['review_reasons']), key)
            self.assertEqual(f['review_reasons'], sorted(set(f['review_reasons'])))
            self.assertEqual(frozenset(f['review_reasons']), acc.review_reasons)
            self.assertTrue(acc.review_reasons <= REVIEW_CODES)
            self.assertIn(f['documentation_suggestion'], DOCUMENTATION_SUGGESTIONS)
            self.assertIn(f['editorial_suggestion'], EDITORIAL_SUGGESTIONS)
            if acc.rating == 0:
                self.assertIs(f['needs_review'], True)
                self.assertIn('unevaluable', f['review_reasons'])
                self.assertEqual((f['documentation_suggestion'], f['editorial_suggestion']), ('unrated', 'not_suggested'))

    def test_expected_routing(self):
        expected = {
            1: ('retain', 'highlight', []),
            2: ('retain_low_rating', 'not_suggested', ['documentation_keep_low_rating']),
            3: ('retain', 'highlight', []),
            4: ('retain_low_rating', 'not_suggested', ['distinct_moment_highlight', 'documentation_keep_low_rating']),
            8: ('retain', 'pending_approval', ['burst_not_comparable']),
            9: ('retain', 'pending_approval', ['burst_not_comparable', 'rating_5']),
            10: ('retain', 'not_suggested', ['burst_ranking_invalid']),
            11: ('retain_low_rating', 'not_suggested',
                 ['burst_ranking_invalid', 'documentation_keep_low_rating', 'evidence_generic']),
            12: ('retain', 'not_suggested', ['burst_frame_missing', 'burst_ranking_invalid']),
            15: ('retain', 'not_suggested', ['burst_frame_missing', 'burst_ranking_invalid']),
        }
        for n, (doc, editorial, reasons) in expected.items():
            with self.subTest(n=n):
                self.assertEqual((self.f(n)['documentation_suggestion'], self.f(n)['editorial_suggestion'],
                                  self.f(n)['review_reasons']), (doc, editorial, reasons))
        self.assertNotIn(orig(13), self.s5)                                   # duplicate -> errors
        self.assertNotIn(orig(14), self.s5)                                   # missing -> errors

    def test_s3_s4_values_unchanged(self):
        for key, before in self.s4.items():
            after = self.s5[key]
            self.assertEqual((after.rating, after.decision, after.moment_override),
                             (before.rating, before.decision, before.moment_override))
            self.assertIs(after.distinct_moment_claim_valid, before.distinct_moment_claim_valid)
            self.assertEqual(after.burst_raw, before.burst_raw)
            self.assertTrue(before.review_reasons <= after.review_reasons)
            self.assertEqual({k: v for k, v in after.fields.items() if k not in S5_FIELDS}, before.fields)

    def test_inputs_not_mutated(self):
        self.assertEqual((self.s4, self.errors), self.snapshot)
        for acc in self.s4.values():
            self.assertFalse(set(S5_FIELDS) & set(acc.fields))

    def test_distribution(self):
        d = self.summary.distribution
        self.assertEqual(d['counts'], {'1': 0, '2': 3, '3': 0, '4': 6, '5': 1})
        self.assertEqual(d['denominator'], 10)
        self.assertEqual(d['rating_0'], 3)
        self.assertEqual(d['errors'], len(self.errors))
        self.assertIs(self.summary.calibration_drift_warning, True)


class FatalFlawsTest(unittest.TestCase):
    def test_content_has_no_effect(self):
        runs = [mixed_run(flaws)[2] for flaws in ((), ('every face is out of focus',))]
        strip = lambda s5: {k: (a.rating, a.review_reasons, a.fields['documentation_suggestion'],
                                a.fields['editorial_suggestion'], a.fields['needs_review']) for k, a in s5.items()}
        self.assertEqual(strip(runs[0]), strip(runs[1]))


if __name__ == '__main__':
    unittest.main()
