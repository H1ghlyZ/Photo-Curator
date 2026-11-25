"""S4: C1 categories (D4-1, D4-1a), C2 (D4-4), B1-B6 (D4-2, D4-3), V29 in ranked groups,
group status and group codes (spec 8.2, 8.3; D4-5).

Pure functions only; synthetic values, no files, no tools. Every stored value and review-code
set is asserted exactly.
"""
from __future__ import annotations
import unittest

from photo_curator.bursts_v3 import apply_bursts_v3
from photo_curator.schema_v3 import BASE_MATRIX, REVIEW_CODES
from photo_curator.validate_v3 import Accepted, FrameContext, validate_frame_v3

SHA = 'd' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Background crowd on the left differs from the speaker frame'
GENERIC = 'Great photo, nice composition!'
DEL = object()                     # marker: remove this key from the model result
BURST_KEYS = ('burst_comparison', 'burst_rank', 'best_in_burst', 'co_best',
              'near_duplicate_of', 'rank_reason', 'not_comparable_evidence')
INVALID = {'burst_ranking_invalid'}


def orig(n):
    return f'/lib/S/DSC{n:05d}.ARW'


def ctx(n, group='g1', batch=0, session='S'):
    return FrameContext(original_path=orig(n), preview_path=f'/out/previews/p{n:05d}.jpg', session_id=session,
                        burst_group=group, burst_sequence=group, capture_time=None, original_sha256=SHA,
                        batch_index=batch)


def model(n, t=3, e=3, **over):
    raw = {
        'path': orig(n), 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': t, 'editorial_score': e,
        'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]},
        'editorial': {'evidence': [GOOD2]}, 'decisive_evidence': [GOOD], 'proposed_rating': BASE_MATRIX[(t, e)],
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': False, 'distinct_moment_evidence': [], 'confidence': 0.9,
    }
    raw.update(over)
    return {k: v for k, v in raw.items() if v is not DEL}


def ranked(rank, best, co=False, nd=None, reason=GOOD, nce=()):
    return dict(burst_comparison='ranked', burst_rank=rank, best_in_burst=best, co_best=co,
                near_duplicate_of=nd, rank_reason=reason, not_comparable_evidence=list(nce))


def nc(evidence=(GOOD2,)):
    return dict(burst_comparison='not_comparable', burst_rank=None, best_in_burst=None, co_best=None,
                near_duplicate_of=None, rank_reason=None, not_comparable_evidence=list(evidence))


class Base(unittest.TestCase):
    def run_group(self, frames, errors=()):
        """frames: {n: model overrides}. All frames in group g1, batch 0."""
        contexts = {orig(n): ctx(n) for n in frames}
        accepted = {}
        for n, over in frames.items():
            out = validate_frame_v3(model(n, **over), contexts[orig(n)])
            self.assertIsInstance(out, Accepted, getattr(out, 'entry', None))
            self.assertEqual(out.review_reasons, frozenset(), f'fixture {n} has S3 codes')
            accepted[orig(n)] = out
        result, outcomes = apply_bursts_v3(accepted, list(errors), contexts)
        for acc in result.values():
            self.assertTrue(acc.review_reasons <= REVIEW_CODES)
        return result, outcomes['g1']

    def codes(self, result, n):
        return set(result[orig(n)].review_reasons)

    def field(self, result, n, key):
        return result[orig(n)].fields[key]


class ValidatedTest(Base):
    def test_single_winner(self):
        res, out = self.run_group({1: ranked(1, True), 2: ranked(2, False, nd=orig(1)), 3: ranked(3, False)})
        self.assertEqual(out.status, 'validated')
        self.assertEqual(out.winners, (orig(1),))
        for n in (1, 2, 3):
            self.assertEqual(self.codes(res, n), set())
            self.assertEqual(self.field(res, n, 'burst_ranking_status'), 'validated')
        self.assertEqual(self.field(res, 2, 'near_duplicate_of'), orig(1))
        self.assertEqual(self.field(res, 3, 'burst_rank'), 3)
        self.assertEqual(self.field(res, 1, 'not_comparable_evidence'), [])

    def test_co_best_tie(self):
        res, out = self.run_group({1: ranked(1, True, True), 2: ranked(1, True, True), 3: ranked(3, False)})
        self.assertEqual(out.status, 'validated')
        self.assertEqual(self.codes(res, 1), {'co_best_tie'})
        self.assertEqual(self.codes(res, 2), {'co_best_tie'})
        self.assertEqual(self.codes(res, 3), set())

    def test_single_ranked_frame(self):
        res, out = self.run_group({1: ranked(1, True, reason='Only readable frame; the speaker eyes are open')})
        self.assertEqual(out.status, 'validated')
        self.assertEqual(self.codes(res, 1), set())


class WinnerTest(Base):                                                      # B1-B3, D4-2, D4-3
    CLEARED = {'burst_winner_invalid', 'burst_rank_invalid', 'burst_ranking_invalid'}

    def assert_b3(self, frames):
        res, out = self.run_group(frames)
        self.assertEqual(out.status, 'invalid')
        self.assertEqual(out.winners, ())
        self.assertTrue({'B3', 'B4'} <= out.failed_rules)
        for n in frames:
            self.assertEqual(self.codes(res, n), self.CLEARED)
            self.assertIs(self.field(res, n, 'best_in_burst'), False)
            self.assertIs(self.field(res, n, 'co_best'), False)
            self.assertIsNone(self.field(res, n, 'burst_rank'))

    def test_no_winner(self):
        self.assert_b3({1: ranked(1, False), 2: ranked(2, False)})

    def test_two_winners_without_co_best(self):
        self.assert_b3({1: ranked(1, True), 2: ranked(1, True)})

    def test_co_best_on_single_winner(self):
        self.assert_b3({1: ranked(1, True, True), 2: ranked(2, False)})

    def test_non_winner_with_co_best(self):                                  # D4-3
        self.assert_b3({1: ranked(1, True), 2: ranked(2, False, True)})

    def test_winner_with_rank_two(self):                                     # D4-2
        self.assert_b3({1: ranked(2, True), 2: ranked(1, False)})

    def test_tie_with_rank_two(self):                                        # D4-2
        self.assert_b3({1: ranked(1, True, True), 2: ranked(2, True, True)})

    def test_wrong_types(self):
        self.assert_b3({1: ranked(1, 'yes'), 2: ranked(2, False)})
        self.assert_b3({1: ranked(1, True, None), 2: ranked(2, False)})
        self.assert_b3({1: {**ranked(1, True), 'best_in_burst': DEL}, 2: ranked(2, False)})


class RankTest(Base):                                                        # B4
    def assert_b4(self, frames):
        res, out = self.run_group(frames)
        self.assertEqual(out.status, 'invalid')
        self.assertEqual(out.failed_rules, frozenset({'B4'}))
        for n in frames:
            self.assertEqual(self.codes(res, n), {'burst_rank_invalid', 'burst_ranking_invalid'})
            self.assertIsNone(self.field(res, n, 'burst_rank'))

    def test_competition_ranking(self):
        res, out = self.run_group({1: ranked(1, True, True), 2: ranked(1, True, True), 3: ranked(3, False)})
        self.assertEqual(out.status, 'validated')
        self.assert_b4({1: ranked(1, True, True), 2: ranked(1, True, True), 3: ranked(2, False)})   # 1, 1, 2
        self.assert_b4({1: ranked(1, True), 2: ranked(2, False), 3: ranked(2, False)})               # 1, 2, 2

    def test_bad_rank_types(self):
        self.assert_b4({1: ranked(1, True), 2: ranked('2', False)})
        self.assert_b4({1: ranked(1, True), 2: ranked(True, False)})
        self.assert_b4({1: ranked(1, True), 2: ranked(3, False)})
        self.assert_b4({1: ranked(1, True), 2: {**ranked(2, False), 'burst_rank': DEL}})


class NearDuplicateTest(Base):                                               # B5
    def test_invalid_targets(self):
        cases = {
            'worse rank': ranked(2, False, nd=orig(3)),
            'self': ranked(2, False, nd=orig(2)),
            'outside group': ranked(2, False, nd='/lib/S/DSC09999.ARW'),
            'wrong type': ranked(2, False, nd=1),
            'absent': {**ranked(2, False), 'near_duplicate_of': DEL},
        }
        for label, frame2 in cases.items():
            with self.subTest(label=label):
                res, out = self.run_group({1: ranked(1, True), 2: frame2, 3: ranked(3, False)})
                self.assertEqual(out.status, 'invalid')
                self.assertEqual(out.failed_rules, frozenset({'B5'}))
                self.assertEqual(self.codes(res, 2), {'near_duplicate_invalid', 'burst_ranking_invalid'})
                self.assertIsNone(self.field(res, 2, 'near_duplicate_of'))
                self.assertEqual(self.codes(res, 1), INVALID)
                self.assertEqual(self.codes(res, 3), INVALID)

    def test_b3_b4_b5_chain(self):
        res, out = self.run_group({1: ranked(1, False), 2: ranked(2, False, nd=orig(1))})
        self.assertEqual(out.failed_rules, frozenset({'B3', 'B4', 'B5'}))
        self.assertIsNone(self.field(res, 2, 'near_duplicate_of'))
        self.assertEqual(self.codes(res, 2), {'burst_winner_invalid', 'burst_rank_invalid',
                                              'near_duplicate_invalid', 'burst_ranking_invalid'})


class RankReasonTest(Base):                                                  # B6
    def test_failures_on_winner_and_non_winner(self):
        cases = [(DEL, None, 'evidence_missing'), ('', None, 'evidence_missing'),
                 ('   ', None, 'evidence_missing'), (5, None, 'evidence_missing'),
                 (GENERIC, GENERIC, 'evidence_generic'), ('speaker', 'speaker', 'evidence_generic')]
        for target in (1, 2):
            for value, stored, code in cases:
                with self.subTest(target=target, value=value):
                    frames = {1: ranked(1, True), 2: ranked(2, False), 3: ranked(3, False)}
                    frames[target] = {**frames[target], 'rank_reason': value}
                    res, out = self.run_group(frames)
                    self.assertEqual(out.status, 'invalid')
                    self.assertEqual(out.failed_rules, frozenset({'B6'}))
                    self.assertEqual(self.field(res, target, 'rank_reason'), stored)
                    for n in (1, 2, 3):
                        expected = INVALID | ({code} if n == target else set())
                        self.assertEqual(self.codes(res, n), expected)

    def test_tie_with_b6_failure_has_no_co_best_tie(self):
        res, out = self.run_group({1: ranked(1, True, True), 2: ranked(1, True, True, reason=GENERIC)})
        self.assertEqual(out.status, 'invalid')
        self.assertEqual(self.codes(res, 1), INVALID)
        self.assertEqual(self.codes(res, 2), INVALID | {'evidence_generic'})


class RankedNceTest(Base):                                                   # V29 ranked, D4-4
    def test_non_empty_list_kept(self):
        res, out = self.run_group({1: ranked(1, True, nce=[GOOD]), 2: ranked(2, False)})
        self.assertEqual(out.status, 'validated')
        self.assertEqual(self.field(res, 1, 'not_comparable_evidence'), [GOOD])
        self.assertEqual(self.codes(res, 1), {'field_invalid'})

    def test_missing_or_not_list_is_null(self):
        for value in (DEL, 'text', None, {'a': 1}):
            with self.subTest(value=value):
                res, out = self.run_group({1: {**ranked(1, True), 'not_comparable_evidence': value},
                                           2: ranked(2, False)})
                self.assertEqual(out.status, 'validated')
                self.assertIsNone(self.field(res, 1, 'not_comparable_evidence'))
                self.assertEqual(self.codes(res, 1), {'field_invalid'})


class C2Test(Base):
    def test_valid_two_and_five(self):
        for size in (2, 5):
            with self.subTest(size=size):
                res, out = self.run_group({n: nc() for n in range(1, size + 1)})
                self.assertEqual(out.status, 'not_comparable')
                for n in range(1, size + 1):
                    self.assertEqual(self.codes(res, n), {'burst_not_comparable'})
                    for key in ('burst_rank', 'best_in_burst', 'co_best', 'near_duplicate_of', 'rank_reason'):
                        self.assertIsNone(self.field(res, n, key))
                    self.assertEqual(self.field(res, n, 'not_comparable_evidence'), [GOOD2])

    def test_single_frame(self):
        res, out = self.run_group({1: nc()})
        self.assertEqual(out.status, 'invalid')
        self.assertEqual(self.codes(res, 1), {'burst_comparison_invalid', 'burst_ranking_invalid'})

    def test_evidence_failures(self):
        bci = {'burst_comparison_invalid', 'burst_ranking_invalid'}
        cases = [(DEL, None, 'evidence_missing'), ('text', None, 'evidence_missing'), ([1], None, 'evidence_missing'),
                 ([], [], 'evidence_missing'), ([GENERIC], [GENERIC], 'evidence_generic')]
        for value, stored, code in cases:
            with self.subTest(value=value):
                res, out = self.run_group({1: {**nc(), 'not_comparable_evidence': value}, 2: nc()})
                self.assertEqual(out.status, 'invalid')
                self.assertEqual(self.field(res, 1, 'not_comparable_evidence'), stored)
                self.assertEqual(self.codes(res, 1), bci | {code})
                self.assertEqual(self.codes(res, 2), bci)

    def test_ranking_fields_set_are_kept(self):
        res, out = self.run_group({1: {**nc(), 'burst_rank': 1, 'best_in_burst': True}, 2: nc()})
        self.assertEqual(out.status, 'invalid')
        self.assertEqual(self.field(res, 1, 'burst_rank'), 1)
        self.assertIs(self.field(res, 1, 'best_in_burst'), True)
        self.assertEqual(self.codes(res, 1), {'burst_comparison_invalid', 'burst_ranking_invalid'})

    def test_absent_ranking_field_is_not_null(self):                          # D4-4
        for key in ('burst_rank', 'best_in_burst', 'co_best', 'near_duplicate_of', 'rank_reason'):
            with self.subTest(key=key):
                res, out = self.run_group({1: {**nc(), key: DEL}, 2: nc()})
                self.assertEqual(out.status, 'invalid')
                self.assertEqual(out.failed_rules, frozenset({'C2'}))
                self.assertIsNone(self.field(res, 1, key))
                self.assertEqual(self.codes(res, 1), {'burst_comparison_invalid', 'burst_ranking_invalid'})


class C1CategoryTest(Base):                                                   # C1, D4-1, D4-1a
    BCI = {'burst_comparison_invalid', 'burst_ranking_invalid'}

    def test_invalid_values_make_category_x(self):
        for value in (DEL, 'Ranked', 'none', None, True, 1, []):
            with self.subTest(value=value):
                x = {**ranked(3, False, nd=orig(9), reason=GENERIC, nce=['x']), 'burst_comparison': value}
                res, out = self.run_group({1: ranked(1, True), 2: ranked(2, False), 3: x})
                self.assertEqual(out.status, 'invalid')
                self.assertEqual(out.categories, {orig(1): 'R', orig(2): 'R', orig(3): 'X'})
                self.assertEqual(out.failed_rules, frozenset({'C1'}))     # R subset (ranks 1, 2) passes
                self.assertIsNone(self.field(res, 3, 'burst_comparison'))
                # D4-1a: kept as returned, no mode rule, no extra codes
                self.assertEqual(self.field(res, 3, 'burst_rank'), 3)
                self.assertEqual(self.field(res, 3, 'near_duplicate_of'), orig(9))
                self.assertEqual(self.field(res, 3, 'rank_reason'), GENERIC)
                self.assertEqual(self.field(res, 3, 'not_comparable_evidence'), ['x'])
                for n in (1, 2, 3):
                    self.assertEqual(self.codes(res, n), self.BCI)
                self.assertEqual(self.field(res, 1, 'burst_comparison'), 'ranked')
                self.assertEqual(self.field(res, 2, 'burst_rank'), 2)

    def test_category_x_absent_keys_stored_null(self):
        x = {key: DEL for key in BURST_KEYS}
        res, out = self.run_group({1: ranked(1, True), 2: x})
        for key in BURST_KEYS:
            self.assertIsNone(self.field(res, 2, key))
        self.assertEqual(self.codes(res, 2), self.BCI)

    def test_x_frame_is_never_given_a_mode(self):
        # With rank 3 counted, the R subset would fail B4; it passes because X is excluded.
        x = {**ranked(1, True), 'burst_comparison': 'Ranked'}
        res, out = self.run_group({1: ranked(1, True), 2: ranked(2, False), 3: x})
        self.assertEqual(out.failed_rules, frozenset({'C1'}))
        self.assertIs(self.field(res, 3, 'best_in_burst'), True)       # kept, not cleared by B3

    def test_mixed_subsets(self):
        res, out = self.run_group({1: ranked(1, True), 2: ranked(2, False), 3: nc(), 4: nc()})
        self.assertEqual(out.status, 'invalid')
        self.assertEqual(out.failed_rules, frozenset({'C1'}))
        self.assertEqual(out.categories, {orig(1): 'R', orig(2): 'R', orig(3): 'NC', orig(4): 'NC'})
        for n in (1, 2, 3, 4):
            self.assertEqual(self.codes(res, n), self.BCI)
        self.assertEqual(self.field(res, 3, 'burst_comparison'), 'not_comparable')     # K
        self.assertEqual(self.field(res, 1, 'burst_comparison'), 'ranked')

    def test_mixed_nc_subset_of_one_fails_c2(self):
        res, out = self.run_group({1: ranked(1, True), 2: ranked(2, False), 3: nc()})
        self.assertEqual(out.failed_rules, frozenset({'C1', 'C2'}))
        self.assertEqual(self.codes(res, 3), self.BCI)

    def test_mixed_r_link_to_nc_frame_fails_b5(self):
        res, out = self.run_group({1: ranked(1, True), 2: ranked(2, False, nd=orig(3)), 3: nc(), 4: nc()})
        self.assertEqual(out.failed_rules, frozenset({'C1', 'B5'}))
        self.assertEqual(self.codes(res, 2), self.BCI | {'near_duplicate_invalid'})

    def test_all_x(self):
        res, out = self.run_group({1: {**ranked(1, True), 'burst_comparison': None},
                                   2: {**ranked(2, False), 'burst_comparison': 'x'}})
        self.assertEqual(out.failed_rules, frozenset({'C1'}))
        for n in (1, 2):
            self.assertEqual(self.codes(res, n), self.BCI)


class GroupCodeTest(Base):                                                    # 8.3, D4-5
    def test_weak_burst_best_single(self):
        res, out = self.run_group({1: {**ranked(1, True), 'technical_score': 2, 'editorial_score': 2,
                                       'proposed_rating': 2}, 2: ranked(2, False)})
        self.assertEqual(out.status, 'validated')
        self.assertEqual(self.codes(res, 1), {'weak_burst_best'})
        self.assertEqual(self.codes(res, 2), set())

    def test_weak_burst_best_tie_uses_highest_winner(self):
        low = {'technical_score': 2, 'editorial_score': 2, 'proposed_rating': 2}
        res, out = self.run_group({1: {**ranked(1, True, True), **low}, 2: ranked(1, True, True)})
        self.assertEqual(self.codes(res, 1), {'co_best_tie'})
        self.assertEqual(self.codes(res, 2), {'co_best_tie'})
        res, out = self.run_group({1: {**ranked(1, True, True), **low}, 2: {**ranked(1, True, True), **low}})
        self.assertEqual(self.codes(res, 1), {'co_best_tie', 'weak_burst_best'})
        self.assertEqual(self.codes(res, 2), {'co_best_tie', 'weak_burst_best'})

    def test_no_weak_or_tie_codes_outside_validated(self):
        low = {'technical_score': 2, 'editorial_score': 2, 'proposed_rating': 2}
        res, out = self.run_group({1: {**ranked(1, True), **low}, 2: ranked(2, False, reason=GENERIC)})
        self.assertEqual(out.status, 'invalid')
        self.assertNotIn('weak_burst_best', self.codes(res, 1))
        res, out = self.run_group({1: nc(), 2: nc()})
        for n in (1, 2):
            self.assertFalse({'weak_burst_best', 'co_best_tie'} & self.codes(res, n))


class DuplicateFlagIndependenceTest(Base):                                     # R-4: 3.3 vs B5
    """The "duplicate" flag (3.3, model only) and "near_duplicate_of" (B5) are independent:
    the flag is stored as returned and never affects B5 or the group status."""

    def assert_clean(self, res, out, links, flags):
        self.assertEqual(out.status, 'validated')
        self.assertEqual(out.failed_rules, frozenset())
        for n in (1, 2, 3):
            self.assertEqual(self.codes(res, n), set())
            self.assertEqual(self.field(res, n, 'near_duplicate_of'), links.get(n))
            self.assertEqual(self.field(res, n, 'flags'), flags.get(n, []))

    def test_duplicate_flag_with_null_link(self):
        res, out = self.run_group({1: {**ranked(1, True), 'flags': ['duplicate']},
                                   2: {**ranked(2, False), 'flags': ['duplicate']}, 3: ranked(3, False)})
        self.assert_clean(res, out, {}, {1: ['duplicate'], 2: ['duplicate']})
        self.assertEqual(out.winners, (orig(1),))

    def test_valid_link_without_duplicate_flag(self):
        for flags in ([], ['clutter']):
            with self.subTest(flags=flags):
                res, out = self.run_group({1: ranked(1, True), 2: {**ranked(2, False, nd=orig(1)), 'flags': flags},
                                           3: ranked(3, False)})
                self.assert_clean(res, out, {2: orig(1)}, {2: flags})

    def test_both_markers(self):
        res, out = self.run_group({1: ranked(1, True), 2: {**ranked(2, False, nd=orig(1)), 'flags': ['duplicate']},
                                   3: {**ranked(3, False, nd=orig(2)), 'flags': ['duplicate']}})
        self.assert_clean(res, out, {2: orig(1), 3: orig(2)}, {2: ['duplicate'], 3: ['duplicate']})

    def test_neither_marker(self):
        res, out = self.run_group({1: ranked(1, True), 2: ranked(2, False), 3: ranked(3, False)})
        self.assert_clean(res, out, {}, {})

    def test_invalid_link_stays_invalid_regardless_of_duplicate_flag(self):
        links = {'worse rank': orig(3), 'self': orig(2), 'outside group': '/lib/S/DSC09999.ARW', 'winner link': None}
        for label, target in links.items():
            for flags in ([], ['duplicate']):
                with self.subTest(label=label, flags=flags):
                    frame1 = {**ranked(1, True, nd=orig(2)), 'flags': flags} if label == 'winner link' else ranked(1, True)
                    frame2 = {**ranked(2, False, nd=target), 'flags': flags} if target else ranked(2, False)
                    res, out = self.run_group({1: frame1, 2: frame2, 3: ranked(3, False)})
                    bad = 1 if label == 'winner link' else 2
                    self.assertEqual(out.status, 'invalid')
                    self.assertEqual(out.failed_rules, frozenset({'B5'}))
                    self.assertEqual(self.codes(res, bad), {'near_duplicate_invalid', 'burst_ranking_invalid'})
                    self.assertIsNone(self.field(res, bad, 'near_duplicate_of'))
                    self.assertEqual(self.field(res, bad, 'flags'), flags)              # flag kept as returned
                    for n in {1, 2, 3} - {bad}:
                        self.assertEqual(self.codes(res, n), INVALID)


if __name__ == '__main__':
    unittest.main()
