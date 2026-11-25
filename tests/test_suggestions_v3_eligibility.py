"""S5: highlight eligibility (spec 7.2), burst paths, D5-3 (non-burst field_invalid), the
D5-10 monotone status matrix, and distinct_moment_highlight (D5-2).

Pure functions only; synthetic values, no files, no tools.
"""
from __future__ import annotations
import itertools, unittest

from photo_curator.bursts_v3 import apply_bursts_v3
from photo_curator.schema_v3 import BASE_MATRIX
from photo_curator.suggestions_v3 import (NOT_GIVEN, apply_suggestions_v3, burst_path_v3,
                                          effective_burst_status_v3, highlight_eligible_v3)
from photo_curator.validate_v3 import FrameContext, validate_batch_v3

SHA = '9' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Background crowd on the left differs from the speaker frame'
GENERIC = 'Great photo, nice composition!'
NON_BURST = dict(burst_comparison=None, burst_rank=None, best_in_burst=None, co_best=None,
                 near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[])


def orig(n):
    return f'/lib/S/DSC{n:05d}.ARW'


def ctx(n, group='g1', batch=0):
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


def ranked(rank, best, nd=None, reason=GOOD):
    return dict(burst_comparison='ranked', burst_rank=rank, best_in_burst=best, co_best=False,
                near_duplicate_of=nd, rank_reason=reason, not_comparable_evidence=[])


def nc():
    return dict(burst_comparison='not_comparable', burst_rank=None, best_in_burst=None, co_best=None,
                near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[GOOD2])


def pipeline(ctxs, photos):
    accepted, errors = validate_batch_v3(sorted(ctxs), photos, ctxs)
    s4, _ = apply_bursts_v3(accepted, errors, ctxs)
    s5, _ = apply_suggestions_v3(s4, errors)
    return s5


def fields(status=None, group=None, best=None, nd=None):
    return {'burst_ranking_status': status, 'burst_group': group, 'best_in_burst': best, 'near_duplicate_of': nd}


class EffectiveStatusTest(unittest.TestCase):                                 # D5-10
    STORED = (None, 'validated', 'not_comparable', 'invalid', 'bogus')
    REVAL = (NOT_GIVEN, None, 'validated', 'not_comparable', 'invalid')
    EXPECTED = {
        None: (None, None, 'invalid', 'invalid', 'invalid'),
        'validated': ('validated', 'invalid', 'validated', 'invalid', 'invalid'),
        'not_comparable': ('not_comparable', 'invalid', 'invalid', 'not_comparable', 'invalid'),
        'invalid': ('invalid',) * 5,
        'bogus': ('invalid',) * 5,
    }

    def test_full_matrix(self):
        for stored in self.STORED:
            for reval, expected in zip(self.REVAL, self.EXPECTED[stored]):
                with self.subTest(stored=stored, reval=reval):
                    self.assertEqual(effective_burst_status_v3(stored, reval), expected)

    def test_never_looser_than_stored(self):
        for stored, reval in itertools.product(self.STORED, self.REVAL):
            result = effective_burst_status_v3(stored, reval)
            allowed = {'invalid'} | ({stored} if stored in (None, 'validated', 'not_comparable') else set())
            self.assertIn(result, allowed, (stored, reval))

    def test_invalid_revalidated_value_raises(self):
        for stored in self.STORED:
            for reval in ('x', 5, True, ['validated']):
                with self.assertRaises(ValueError):
                    effective_burst_status_v3(stored, reval)


class BurstPathTest(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(burst_path_v3(fields(), False), 'single')
        self.assertEqual(burst_path_v3(fields('validated', 'g', True), False), 'winner')
        self.assertEqual(burst_path_v3(fields('validated', 'g', True), True), 'winner')       # winner first
        self.assertEqual(burst_path_v3(fields('validated', 'g', False), True), 'distinct_moment')
        self.assertIsNone(burst_path_v3(fields('validated', 'g', False), False))
        self.assertEqual(burst_path_v3(fields('not_comparable', 'g'), False), 'not_comparable')
        self.assertIsNone(burst_path_v3(fields('invalid', 'g', True), True))
        self.assertIsNone(burst_path_v3(fields(None, 'g'), True))                              # unevaluable

    def test_revalidation_only_tightens(self):
        self.assertIsNone(burst_path_v3(fields('validated', 'g', True), False, 'invalid'))
        self.assertIsNone(burst_path_v3(fields('validated', 'g', True), False, 'not_comparable'))
        self.assertIsNone(burst_path_v3(fields('invalid', 'g', True), False, 'validated'))
        self.assertIsNone(burst_path_v3(fields('invalid', 'g'), False, 'not_comparable'))
        self.assertEqual(burst_path_v3(fields('validated', 'g', True), False, 'validated'), 'winner')

    def test_contract(self):
        with self.assertRaises(ValueError):
            burst_path_v3({'burst_group': None}, False)                  # burst_ranking_status missing
        with self.assertRaises(ValueError):
            burst_path_v3({'burst_ranking_status': None}, False)         # burst_group missing
        with self.assertRaises(ValueError):
            burst_path_v3(fields(), False, 'validated')                  # non-burst revalidated
        self.assertEqual(burst_path_v3(fields(), False, None), 'single')
        self.assertIsNone(burst_path_v3(fields('validated'), False))     # non-burst with a status: corrupt


class EligibilityTest(unittest.TestCase):
    def test_conditions(self):
        f = fields('validated', 'g', True)
        self.assertTrue(highlight_eligible_v3(f, [], False))
        self.assertTrue(highlight_eligible_v3(f, ['co_best_tie', 'field_invalid'], False))     # burst frame
        for code in ('evidence_missing', 'evidence_generic', 'near_duplicate_invalid'):
            self.assertFalse(highlight_eligible_v3(f, [code], False), code)
        self.assertFalse(highlight_eligible_v3(fields('validated', 'g', False, nd=orig(1)), [], True))
        self.assertFalse(highlight_eligible_v3(fields('invalid', 'g', True), [], True))

    def test_d5_3_non_burst_field_invalid(self):
        self.assertTrue(highlight_eligible_v3(fields(), [], False))
        self.assertTrue(highlight_eligible_v3(fields(), ['low_confidence'], False))
        self.assertFalse(highlight_eligible_v3(fields(), ['field_invalid'], False))

    def test_stored_invalid_never_eligible(self):
        for reval in (NOT_GIVEN, None, 'validated', 'not_comparable', 'invalid'):
            self.assertFalse(highlight_eligible_v3(fields('invalid', 'g', True), [], True, reval))

    def test_near_duplicate_missing_field_raises(self):
        with self.assertRaises(ValueError):
            highlight_eligible_v3({'burst_ranking_status': None, 'burst_group': None}, [], False)


class PipelineEligibilityTest(unittest.TestCase):
    def test_single_rating_4(self):
        out = pipeline({orig(1): ctx(1, None)}, [model(1, 4, 4)])[orig(1)]
        self.assertEqual(out.fields['editorial_suggestion'], 'highlight')
        self.assertEqual(out.fields['review_reasons'], [])

    def test_d5_3_withdrawn_near_duplicate_blocks_highlight(self):
        out = pipeline({orig(1): ctx(1, None)}, [model(1, 4, 4, near_duplicate_of=orig(9))])[orig(1)]
        self.assertIsNone(out.fields['near_duplicate_of'])
        self.assertEqual(out.fields['review_reasons'], ['field_invalid'])
        self.assertEqual(out.fields['editorial_suggestion'], 'not_suggested')
        self.assertEqual(out.fields['documentation_suggestion'], 'retain')

    def test_d5_3_unrelated_field_invalid_false_positive(self):
        out = pipeline({orig(1): ctx(1, None)}, [model(1, 4, 4, confidence=None)])[orig(1)]
        self.assertEqual(out.fields['review_reasons'], ['field_invalid'])
        self.assertEqual(out.fields['editorial_suggestion'], 'not_suggested')

    def test_winner_and_near_duplicate_in_validated_group(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        out = pipeline(ctxs, [model(1, 4, 4, ranked(1, True)), model(2, 4, 4, ranked(2, False, nd=orig(1)))])
        self.assertEqual(out[orig(1)].fields['editorial_suggestion'], 'highlight')
        self.assertEqual(out[orig(2)].fields['editorial_suggestion'], 'not_suggested')   # near-duplicate

    def test_winner_in_invalid_group(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        out = pipeline(ctxs, [model(1, 4, 4, ranked(1, True)), model(2, 4, 4, ranked(2, False, reason=GENERIC))])
        self.assertEqual(out[orig(1)].fields['burst_ranking_status'], 'invalid')
        self.assertEqual(out[orig(1)].fields['editorial_suggestion'], 'not_suggested')

    def test_not_comparable_path(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        out = pipeline(ctxs, [model(1, 4, 4, nc()), model(2, 3, 3, nc())])
        self.assertEqual(out[orig(1)].fields['review_reasons'], ['burst_not_comparable'])
        self.assertEqual(out[orig(1)].fields['editorial_suggestion'], 'pending_approval')
        self.assertEqual(out[orig(2)].fields['editorial_suggestion'], 'not_suggested')

    def test_evidence_from_s4_blocks(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        out = pipeline(ctxs, [model(1, 4, 4, {**nc(), 'not_comparable_evidence': [GENERIC]}), model(2, 4, 4, nc())])
        self.assertIn('evidence_generic', out[orig(1)].fields['review_reasons'])
        self.assertEqual(out[orig(1)].fields['editorial_suggestion'], 'not_suggested')


class DistinctMomentHighlightTest(unittest.TestCase):                          # D5-2
    def group(self, t2, e2, claim, reason2=GOOD):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        return pipeline(ctxs, [model(1, 4, 4, ranked(1, True)),
                               model(2, t2, e2, ranked(2, False, reason=reason2), claim=claim)])

    def test_non_winner_with_valid_claim_rating_4(self):
        out = self.group(4, 4, True)[orig(2)]
        self.assertEqual(out.fields['review_reasons'], ['distinct_moment_highlight'])
        self.assertEqual(out.fields['editorial_suggestion'], 'pending_approval')

    def test_literal_eligibility_regardless_of_rating(self):
        out = self.group(3, 3, True)[orig(2)]
        self.assertEqual(out.fields['review_reasons'], ['distinct_moment_highlight'])
        self.assertIs(out.fields['needs_review'], True)
        self.assertEqual(out.fields['editorial_suggestion'], 'not_suggested')     # rating 3 never qualifies

    def test_no_claim_no_code(self):
        out = self.group(4, 4, False)[orig(2)]
        self.assertEqual(out.fields['review_reasons'], [])
        self.assertEqual(out.fields['editorial_suggestion'], 'not_suggested')

    def test_invalid_group_low_rating_keeps_documentation_only(self):             # 20 item 6
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        out = pipeline(ctxs, [model(1, 4, 4, ranked(1, True)),
                              model(2, 2, 2, ranked(2, False, reason=GENERIC), claim=True)])[orig(2)]
        self.assertEqual(out.fields['documentation_suggestion'], 'retain_low_rating')
        self.assertNotIn('distinct_moment_highlight', out.fields['review_reasons'])
        self.assertEqual(out.fields['editorial_suggestion'], 'not_suggested')
        self.assertEqual(set(out.fields['review_reasons']),
                         {'burst_ranking_invalid', 'evidence_generic', 'documentation_keep_low_rating'})


class DuplicateFlagEligibilityTest(unittest.TestCase):                           # R-4, spec 3.3, 7.2
    """The "duplicate" flag never affects highlight eligibility on its own; a valid
    "near_duplicate_of" still blocks it (7.2)."""

    SUGGESTION_KEYS = ('editorial_suggestion', 'documentation_suggestion', 'review_reasons', 'needs_review')

    def assert_flag_ignored(self, ctxs, photos, n):
        plain = pipeline(ctxs, photos)[orig(n)]
        flagged = pipeline(ctxs, [{**p, 'flags': ['duplicate']} if p['path'] == orig(n) else p
                                  for p in photos])[orig(n)]
        for key in self.SUGGESTION_KEYS:
            self.assertEqual(flagged.fields[key], plain.fields[key], key)
        self.assertEqual(flagged.fields['flags'], ['duplicate'])
        return flagged

    def test_single_frame_highlight_unaffected(self):
        out = self.assert_flag_ignored({orig(1): ctx(1, None)}, [model(1, 4, 4)], 1)
        self.assertEqual(out.fields['editorial_suggestion'], 'highlight')

    def test_winner_highlight_unaffected(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        out = self.assert_flag_ignored(ctxs, [model(1, 4, 4, ranked(1, True)), model(2, 3, 3, ranked(2, False))], 1)
        self.assertEqual(out.fields['editorial_suggestion'], 'highlight')

    def test_distinct_moment_path_unaffected(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        out = self.assert_flag_ignored(ctxs, [model(1, 4, 4, ranked(1, True)),
                                              model(2, 4, 4, ranked(2, False), claim=True)], 2)
        self.assertEqual(out.fields['review_reasons'], ['distinct_moment_highlight'])
        self.assertEqual(out.fields['editorial_suggestion'], 'pending_approval')

    def test_valid_link_blocks_distinct_moment_path_with_or_without_flag(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        for flags in ([], ['duplicate']):
            with self.subTest(flags=flags):
                out = pipeline(ctxs, [model(1, 4, 4, ranked(1, True)),
                                      model(2, 4, 4, ranked(2, False, nd=orig(1)), claim=True, flags=flags)])[orig(2)]
                self.assertEqual(out.fields['near_duplicate_of'], orig(1))
                self.assertEqual(out.fields['burst_ranking_status'], 'validated')
                self.assertEqual(out.fields['review_reasons'], [])                 # no distinct_moment_highlight
                self.assertEqual(out.fields['editorial_suggestion'], 'not_suggested')
                self.assertEqual(out.fields['flags'], flags)

    def test_eligibility_function_ignores_flags(self):
        f = fields('validated', 'g', True)
        self.assertTrue(highlight_eligible_v3({**f, 'flags': ['duplicate']}, [], False))
        self.assertFalse(highlight_eligible_v3({**fields('validated', 'g', False, nd=orig(1)), 'flags': []}, [], True))


if __name__ == '__main__':
    unittest.main()
