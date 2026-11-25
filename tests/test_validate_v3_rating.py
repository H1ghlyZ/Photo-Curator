"""S3: rating matrix, overrides, unevaluable shape, decision, spec 6.6 and V26.

Pure validator only (photo_curator.validate_v3); synthetic values, no files, no tools.
"""
from __future__ import annotations
import copy, unittest

from photo_curator.schema_v3 import BASE_MATRIX, DECISIONS, OVERRIDE_CELLS, REVIEW_CODES
from photo_curator.validate_v3 import Accepted, FrameContext, Rejected, validate_frame_v3

ORIG = '/library/DCIM/100MSDCF/DSC00001.ARW'
PREV = '/out/previews/DSC00001__abcdef0123.jpg'
SHA = 'a' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Eyes of the speaker meet the audience on the left'


def ctx(burst_group=None, **over):
    base = dict(original_path=ORIG, preview_path=PREV, session_id='DCIM/100MSDCF',
                burst_group=burst_group, burst_sequence=burst_group,
                capture_time='2026-11-15T10:14:03.250', original_sha256=SHA, batch_index=0)
    base.update(over)
    return FrameContext(**base)


def evaluable(t=3, e=3, **over):
    raw = {
        'path': ORIG, 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': t, 'editorial_score': e,
        'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]},
        'editorial': {'evidence': [GOOD2]}, 'decisive_evidence': [GOOD],
        'proposed_rating': BASE_MATRIX[(t, e)], 'proposed_moment_override': False,
        'flags': [], 'fatal_flaws': [], 'uncertain_checks': [], 'distinct_moment': False,
        'distinct_moment_evidence': [], 'burst_comparison': None, 'burst_rank': None,
        'best_in_burst': None, 'co_best': None, 'near_duplicate_of': None, 'rank_reason': None,
        'not_comparable_evidence': [], 'confidence': 0.9,
    }
    raw.update(over)
    return raw


def unevaluable(**over):
    raw = {
        'path': ORIG, 'evaluable': False, 'unevaluable_reason': 'preview is almost fully black',
        'capture_type': None, 'technical_score': None, 'editorial_score': None,
        'technical': {'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []},
        'editorial': {'evidence': []}, 'decisive_evidence': [], 'proposed_rating': 0,
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': False, 'distinct_moment_evidence': [], 'burst_comparison': None,
        'burst_rank': None, 'best_in_burst': None, 'co_best': None, 'near_duplicate_of': None,
        'rank_reason': None, 'not_comparable_evidence': [], 'confidence': 0.9,
    }
    raw.update(over)
    return raw


def expected_clean_codes(t, e, rating):
    codes = set()
    if rating == 5:
        codes.add('rating_5')
    if abs(t - e) >= 3:
        codes.add('score_gap')
    return codes


class Base(unittest.TestCase):
    def ok(self, raw, context=None) -> Accepted:
        out = validate_frame_v3(raw, context or ctx())
        self.assertIsInstance(out, Accepted, getattr(out, 'entry', None))
        self.assertTrue(out.review_reasons <= REVIEW_CODES)
        return out

    def rejected(self, raw, context=None) -> Rejected:
        out = validate_frame_v3(raw, context or ctx())
        self.assertIsInstance(out, Rejected)
        self.assertEqual(out.entry['stage'], 'result')
        self.assertEqual(out.entry['original_path'], ORIG)
        return out


class MatrixTest(Base):
    def test_all_25_cells(self):
        self.assertEqual(len(BASE_MATRIX), 25)
        for (t, e), rating in BASE_MATRIX.items():
            with self.subTest(t=t, e=e):
                out = self.ok(evaluable(t, e))
                self.assertEqual(out.rating, rating)
                self.assertEqual(out.decision, DECISIONS[rating])
                self.assertIs(out.moment_override, False)
                self.assertEqual(set(out.review_reasons), expected_clean_codes(t, e, rating))
                self.assertEqual(out.fields['rating'], rating)
                self.assertEqual(out.fields['decision'], DECISIONS[rating])

    def test_decision_mapping(self):
        self.assertEqual(DECISIONS, {0: 'unreadable', 1: 'reject', 2: 'weak', 3: 'usable',
                                     4: 'strong', 5: 'exceptional'})


class OverrideTest(Base):
    def test_three_valid_cells(self):
        for (t, e), capped in OVERRIDE_CELLS.items():
            with self.subTest(t=t, e=e):
                out = self.ok(evaluable(t, e, proposed_moment_override=True, proposed_rating=capped))
                self.assertEqual(out.rating, capped)
                self.assertIs(out.moment_override, True)
                self.assertIn('moment_override', out.review_reasons)
                self.assertNotIn('override_invalid', out.review_reasons)
                self.assertNotIn('rating_rule_mismatch', out.review_reasons)

    def test_t3_e5_valid_override(self):
        out = self.ok(evaluable(3, 5, proposed_moment_override=True, proposed_rating=4))
        self.assertEqual((out.rating, out.decision), (4, 'strong'))

    def test_every_other_cell_is_invalid(self):
        for (t, e), base in BASE_MATRIX.items():
            if (t, e) in OVERRIDE_CELLS:
                continue
            with self.subTest(t=t, e=e):
                out = self.ok(evaluable(t, e, proposed_moment_override=True))
                self.assertEqual(out.rating, base)
                self.assertIs(out.moment_override, False)
                self.assertIn('override_invalid', out.review_reasons)
                self.assertNotIn('moment_override', out.review_reasons)

    def test_t1_never_overridable(self):
        for e in range(1, 6):
            out = self.ok(evaluable(1, e, proposed_moment_override=True))
            self.assertEqual(out.rating, 1)
            self.assertIn('override_invalid', out.review_reasons)

    def test_t4_e4_without_proposal_has_no_override_invalid(self):
        out = self.ok(evaluable(4, 4))
        self.assertEqual(out.rating, 4)
        self.assertNotIn('override_invalid', out.review_reasons)

    def test_t2_e4_with_proposal_missing_evidence(self):
        raw = evaluable(2, 4, proposed_moment_override=True, proposed_rating=3)
        del raw['decisive_evidence']
        out = self.ok(raw)
        self.assertEqual(out.rating, 2)
        self.assertIs(out.moment_override, False)
        self.assertTrue({'override_invalid', 'evidence_missing', 'rating_rule_mismatch'} <= out.review_reasons)
        self.assertIsNone(out.fields['decisive_evidence'])

    def test_valid_cell_with_empty_evidence(self):
        out = self.ok(evaluable(2, 5, proposed_moment_override=True, proposed_rating=3, decisive_evidence=[]))
        self.assertEqual(out.rating, 2)
        self.assertTrue({'override_invalid', 'evidence_missing'} <= out.review_reasons)
        self.assertEqual(out.fields['decisive_evidence'], [])

    def test_valid_cell_with_generic_evidence(self):
        out = self.ok(evaluable(3, 5, proposed_moment_override=True, proposed_rating=4,
                                decisive_evidence=['Great photo, nice composition!']))
        self.assertEqual(out.rating, 3)
        self.assertTrue({'override_invalid', 'evidence_generic'} <= out.review_reasons)
        self.assertEqual(out.fields['decisive_evidence'], ['Great photo, nice composition!'])

    def test_proposal_null_or_false_never_override_invalid(self):
        for value in (None, False, 'yes', 1):
            with self.subTest(value=value):
                out = self.ok(evaluable(2, 4, proposed_moment_override=value, decisive_evidence=[]))
                self.assertEqual(out.rating, 2)
                self.assertNotIn('override_invalid', out.review_reasons)
                self.assertIs(out.moment_override, False)

    def test_invalid_proposal_type_is_withheld(self):
        for value in (None, 'true', 1, 0):
            with self.subTest(value=value):
                out = self.ok(evaluable(2, 4, proposed_moment_override=value))
                self.assertIsNone(out.fields['proposed_moment_override'])
                self.assertIn('field_invalid', out.review_reasons)
        raw = evaluable(2, 4); del raw['proposed_moment_override']
        out = self.ok(raw)
        self.assertIsNone(out.fields['proposed_moment_override'])
        self.assertIn('field_invalid', out.review_reasons)

    def test_codes_are_unique_when_v12a_and_v12b_both_fail(self):
        # (T4,E5): proposal on an invalid cell (V12a fails) and final rating 5 (V12b fails).
        out = self.ok(evaluable(4, 5, proposed_moment_override=True, decisive_evidence=[]))
        self.assertEqual(out.rating, 5)
        self.assertEqual(set(out.review_reasons),
                         {'evidence_missing', 'override_invalid', 'rating_5'})
        self.assertIsInstance(out.review_reasons, frozenset)


class ProposedRatingTest(Base):
    def test_mismatch(self):
        out = self.ok(evaluable(4, 4, proposed_rating=3))
        self.assertIn('rating_rule_mismatch', out.review_reasons)
        self.assertEqual(out.fields['proposed_rating'], 3)

    def test_missing_or_invalid_is_null_and_mismatch(self):
        for value in (None, '4', 4.0, True, 6, -1):
            with self.subTest(value=value):
                out = self.ok(evaluable(4, 4, proposed_rating=value))
                self.assertIsNone(out.fields['proposed_rating'])
                self.assertIn('rating_rule_mismatch', out.review_reasons)
        raw = evaluable(4, 4); del raw['proposed_rating']
        out = self.ok(raw)
        self.assertIsNone(out.fields['proposed_rating'])
        self.assertIn('rating_rule_mismatch', out.review_reasons)

    def test_model_rating_ignored_and_never_substituted(self):           # V26
        raw = evaluable(4, 4, rating=1); del raw['proposed_rating']
        out = self.ok(raw)
        self.assertEqual(out.rating, 4)
        self.assertIsNone(out.fields['proposed_rating'])
        self.assertIn('rating_rule_mismatch', out.review_reasons)
        self.assertEqual(out.fields['rating'], 4)

    def test_model_decision_ignored(self):                               # D3-5 (C field)
        out = self.ok(evaluable(4, 4, decision='exceptional', moment_override=True))
        self.assertEqual(out.fields['decision'], 'strong')
        self.assertIs(out.fields['moment_override'], False)
        self.assertEqual(set(out.review_reasons), set())


class UnevaluableTest(Base):
    def test_valid_unevaluable_shape(self):
        out = self.ok(unevaluable())
        self.assertEqual((out.rating, out.decision), (0, 'unreadable'))
        self.assertIs(out.moment_override, False)
        self.assertEqual(set(out.review_reasons), {'unevaluable'})
        f = out.fields
        self.assertIsNone(f['technical_score']); self.assertIsNone(f['editorial_score'])
        self.assertEqual(f['technical'], {'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []})
        self.assertEqual(f['editorial'], {'evidence': []})
        for key in ('decisive_evidence', 'distinct_moment_evidence', 'not_comparable_evidence', 'fatal_flaws'):
            self.assertEqual(f[key], [])
        for key in ('burst_comparison', 'burst_rank', 'best_in_burst', 'co_best', 'near_duplicate_of', 'rank_reason'):
            self.assertIsNone(f[key])
        self.assertIs(f['proposed_moment_override'], False); self.assertIs(f['distinct_moment'], False)
        self.assertIsNone(out.burst_raw)
        self.assertIs(out.distinct_moment_claim_valid, False)

    def test_inside_burst_with_null_burst_fields(self):
        out = self.ok(unevaluable(), ctx(burst_group='burst-A'))
        self.assertEqual(out.rating, 0)
        self.assertIsNone(out.burst_raw)
        self.assertEqual(out.fields['burst_group'], 'burst-A')
        self.assertIsNone(out.fields['burst_comparison'])

    def test_proposed_rating_other_than_zero(self):
        out = self.ok(unevaluable(proposed_rating=2))
        self.assertIn('rating_rule_mismatch', out.review_reasons)
        out = self.ok(unevaluable(proposed_rating='x'))
        self.assertIsNone(out.fields['proposed_rating'])
        self.assertIn('rating_rule_mismatch', out.review_reasons)

    def test_v4_exceptions_validated_as_any_result(self):
        out = self.ok(unevaluable(capture_type='venue', flags=['clutter'], uncertain_checks=['focus'], confidence=0.3))
        self.assertEqual(out.fields['capture_type'], 'venue')
        self.assertTrue({'unevaluable', 'critical_defect_uncertain', 'low_confidence'} <= out.review_reasons)
        out = self.ok(unevaluable(capture_type='selfie', flags=['none'], uncertain_checks='focus', confidence=2))
        self.assertIsNone(out.fields['capture_type']); self.assertIsNone(out.fields['flags'])
        self.assertIsNone(out.fields['uncertain_checks']); self.assertIsNone(out.fields['confidence'])
        self.assertIn('field_invalid', out.review_reasons)
        self.assertNotIn('motion_conflict', out.review_reasons)          # 10.1 not applied
        self.assertNotIn('score_gap', out.review_reasons)                # D3-6

    def test_v3_reason(self):
        for value in ('', '   ', None, 5):
            with self.subTest(value=value):
                self.rejected(unevaluable(unevaluable_reason=value))
        raw = unevaluable(); del raw['unevaluable_reason']
        self.rejected(raw)

    def test_v4_contradictions_reject(self):
        tech = unevaluable()['technical']
        cases = {
            'technical_score': {'technical_score': 1},
            'editorial_score': {'editorial_score': 0},
            'motion': {'technical': {**tech, 'motion': 'frozen'}},
            'motion_evidence': {'technical': {**tech, 'motion_evidence': [GOOD]}},
            'technical.evidence': {'technical': {**tech, 'evidence': None}},
            'recoverable': {'technical': {**tech, 'recoverable_in_raw': ['highlights']}},
            'technical missing': {'technical': None},
            'editorial null': {'editorial': None},
            'editorial.evidence': {'editorial': {'evidence': [GOOD]}},
            'decisive': {'decisive_evidence': [GOOD]},
            'distinct evidence': {'distinct_moment_evidence': [GOOD]},
            'not_comparable': {'not_comparable_evidence': [GOOD]},
            'override': {'proposed_moment_override': True},
            'override null': {'proposed_moment_override': None},
            'distinct': {'distinct_moment': True},
            'burst_comparison': {'burst_comparison': 'ranked'},
            'burst_rank': {'burst_rank': 1},
            'best_in_burst': {'best_in_burst': False},
            'co_best': {'co_best': False},
            'near_duplicate_of': {'near_duplicate_of': ORIG},
            'rank_reason': {'rank_reason': GOOD},
            'fatal non-empty': {'fatal_flaws': ['preview unreadable']},
            'fatal null': {'fatal_flaws': None},
        }
        for label, over in cases.items():
            with self.subTest(label=label):
                self.rejected(unevaluable(**over), ctx(burst_group='burst-A'))

    def test_v4_missing_keys_reject(self):
        for key in ('technical_score', 'fatal_flaws', 'burst_rank', 'distinct_moment', 'decisive_evidence'):
            with self.subTest(key=key):
                raw = unevaluable(); del raw[key]
                self.rejected(raw)

    def test_rejected_entry_keeps_raw_result(self):
        raw = unevaluable(fatal_flaws=['corrupt preview'])
        out = self.rejected(raw)
        self.assertEqual(out.entry['result'], raw)
        self.assertEqual(out.entry['preview_path'], PREV)


class EvaluableRejectTest(Base):
    def test_v2(self):
        for value in (None, 'true', 1):
            with self.subTest(value=value):
                self.rejected(evaluable(evaluable=value))
        raw = evaluable(); del raw['evaluable']
        self.rejected(raw)

    def test_v6_scores(self):
        for value in (0, 6, None, '3', 3.0, True):
            for key in ('technical_score', 'editorial_score'):
                with self.subTest(key=key, value=value):
                    self.rejected(evaluable(**{key: value}))

    def test_v8_technical_and_motion(self):
        self.rejected(evaluable(technical=None))
        self.rejected(evaluable(technical=['frozen']))
        tech = evaluable()['technical']
        for motion in (None, 'blurry', 'Frozen', 1):
            with self.subTest(motion=motion):
                self.rejected(evaluable(technical={**tech, 'motion': motion}))
        no_motion = dict(tech); del no_motion['motion']
        self.rejected(evaluable(technical=no_motion))

    def test_input_not_mutated(self):
        raw = evaluable(4, 4, rating=2, foo='bar')
        before = copy.deepcopy(raw)
        validate_frame_v3(raw, ctx())
        self.assertEqual(raw, before)


class ScoreGapAndRatingFiveTest(Base):
    def test_score_gap_threshold(self):
        self.assertIn('score_gap', self.ok(evaluable(5, 2)).review_reasons)
        self.assertIn('score_gap', self.ok(evaluable(1, 4)).review_reasons)
        self.assertNotIn('score_gap', self.ok(evaluable(5, 3)).review_reasons)

    def test_rating_5(self):
        self.assertIn('rating_5', self.ok(evaluable(5, 5)).review_reasons)
        self.assertNotIn('rating_5', self.ok(evaluable(4, 4)).review_reasons)


class DuplicateFlagNoEffectTest(Base):                                          # R-4, spec 3.3, 88
    """"duplicate" is redundancy only: it never changes T, E, rating, decision, the override or the
    review reasons, and it is stored exactly as returned."""

    KEYS = ('technical_score', 'editorial_score', 'rating', 'decision', 'moment_override', 'flags')

    def assert_same_except_flags(self, raw, base_flags):
        without = self.ok(copy.deepcopy({**raw, 'flags': list(base_flags)}))
        with_dup = self.ok(copy.deepcopy({**raw, 'flags': list(base_flags) + ['duplicate']}))
        self.assertEqual((with_dup.rating, with_dup.decision, with_dup.moment_override),
                         (without.rating, without.decision, without.moment_override))
        self.assertEqual(with_dup.review_reasons, without.review_reasons)
        for key in self.KEYS[:-1]:
            self.assertEqual(with_dup.fields[key], without.fields[key], key)
        self.assertEqual(with_dup.fields['flags'], list(base_flags) + ['duplicate'])

    def test_all_25_cells(self):
        for (t, e) in BASE_MATRIX:
            for base_flags in ([], ['closed_eyes']):
                with self.subTest(t=t, e=e, base_flags=base_flags):
                    self.assert_same_except_flags(evaluable(t, e), base_flags)

    def test_override_cells(self):
        for (t, e), capped in OVERRIDE_CELLS.items():
            with self.subTest(t=t, e=e):
                self.assert_same_except_flags(evaluable(t, e, proposed_moment_override=True, proposed_rating=capped), [])

    def test_rating_mismatch_unaffected(self):
        self.assert_same_except_flags(evaluable(3, 3, proposed_rating=2), [])


if __name__ == '__main__':
    unittest.main()
