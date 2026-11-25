"""S5: documentation_suggestion (spec 7.1), including independence from burst status.

Pure functions only; synthetic values, no files, no tools.
"""
from __future__ import annotations
import unittest

from photo_curator.bursts_v3 import apply_bursts_v3
from photo_curator.schema_v3 import BASE_MATRIX
from photo_curator.suggestions_v3 import apply_suggestions_v3, documentation_suggestion_v3
from photo_curator.validate_v3 import FrameContext, validate_batch_v3

SHA = '8' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Background crowd on the left differs from the speaker frame'
GENERIC = 'Great photo, nice composition!'
NON_BURST = dict(burst_comparison=None, burst_rank=None, best_in_burst=None, co_best=None,
                 near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[])


def orig(n):
    return f'/lib/S/DSC{n:05d}.ARW'


def ctx(n, group='g1'):
    return FrameContext(original_path=orig(n), preview_path=f'/out/previews/p{n:05d}.jpg', session_id='S',
                        burst_group=group, burst_sequence=group, capture_time=None, original_sha256=SHA,
                        batch_index=0)


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


def ranked(rank, best, reason=GOOD):
    return dict(burst_comparison='ranked', burst_rank=rank, best_in_burst=best, co_best=False,
                near_duplicate_of=None, rank_reason=reason, not_comparable_evidence=[])


def nc():
    return dict(burst_comparison='not_comparable', burst_rank=None, best_in_burst=None, co_best=None,
                near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[GOOD2])


def pipeline(ctxs, photos):
    accepted, errors = validate_batch_v3(sorted(ctxs), photos, ctxs)
    s4, _ = apply_bursts_v3(accepted, errors, ctxs)
    s5, _ = apply_suggestions_v3(s4, errors)
    return s5


class DocumentationTableTest(unittest.TestCase):
    def test_every_rating_and_claim(self):
        expected = {
            (0, False): ('unrated', set()), (0, True): ('unrated', set()),
            (1, False): ('not_suggested', set()), (1, True): ('retain_low_rating', {'documentation_keep_low_rating'}),
            (2, False): ('not_suggested', set()), (2, True): ('retain_low_rating', {'documentation_keep_low_rating'}),
            (3, False): ('retain', set()), (3, True): ('retain', set()),
            (4, False): ('retain', set()), (4, True): ('retain', set()),
            (5, False): ('retain', set()), (5, True): ('retain', set()),
        }
        for (rating, claim), (suggestion, codes) in expected.items():
            with self.subTest(rating=rating, claim=claim):
                self.assertEqual(documentation_suggestion_v3(rating, claim), (suggestion, frozenset(codes)))

    def test_invalid_rating(self):
        for rating in (-1, 6, True, '3', 3.0, None):
            with self.assertRaises(ValueError):
                documentation_suggestion_v3(rating, False)


class DocumentationPipelineTest(unittest.TestCase):
    def test_t1_with_valid_claim_is_retained(self):                          # 7.1: T = 1, E >= 2
        out = pipeline({orig(1): ctx(1, None)}, [model(1, 1, 3, claim=True)])[orig(1)]
        self.assertEqual(out.rating, 1)
        self.assertEqual(out.fields['documentation_suggestion'], 'retain_low_rating')
        self.assertIn('documentation_keep_low_rating', out.fields['review_reasons'])

    def test_e1_claim_never_retains(self):                                   # 7.1: E = 1
        out = pipeline({orig(1): ctx(1, None)}, [model(1, 3, 1, claim=True)])[orig(1)]
        self.assertEqual(out.fields['documentation_suggestion'], 'not_suggested')
        self.assertIn('distinct_moment_invalid', out.fields['review_reasons'])
        self.assertNotIn('documentation_keep_low_rating', out.fields['review_reasons'])

    def test_generic_claim_evidence_never_retains(self):
        out = pipeline({orig(1): ctx(1, None)},
                       [model(1, 2, 2, distinct_moment=True, distinct_moment_evidence=[GENERIC])])[orig(1)]
        self.assertEqual(out.fields['documentation_suggestion'], 'not_suggested')

    def test_independent_of_burst_status(self):
        scenarios = {
            'validated': [model(1, 4, 4, ranked(1, True)), model(2, 2, 2, ranked(2, False), claim=True)],
            'not_comparable': [model(1, 4, 4, nc()), model(2, 2, 2, nc(), claim=True)],
            'invalid': [model(1, 4, 4, ranked(1, True, reason=GENERIC)), model(2, 2, 2, ranked(2, False), claim=True)],
        }
        for status, photos in scenarios.items():
            with self.subTest(status=status):
                out = pipeline({orig(n): ctx(n) for n in (1, 2)}, photos)[orig(2)]
                self.assertEqual(out.fields['burst_ranking_status'], status)
                self.assertEqual(out.fields['documentation_suggestion'], 'retain_low_rating')
                self.assertIn('documentation_keep_low_rating', out.fields['review_reasons'])


if __name__ == '__main__':
    unittest.main()
