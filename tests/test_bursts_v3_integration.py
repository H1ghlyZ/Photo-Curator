"""S4: end-to-end S3 -> S4 (validate_batch_v3 -> apply_bursts_v3): unevaluable members,
missing and duplicate frames, fatal_flaws, rating independence, non-burst frames, input
immutability and the input contract.

Pure functions only; synthetic values, no files, no tools.
"""
from __future__ import annotations
import copy, dataclasses, unittest

from photo_curator.bursts_v3 import apply_bursts_v3
from photo_curator.schema_v3 import BASE_MATRIX, REVIEW_CODES, make_error_entry
from photo_curator.validate_v3 import FrameContext, validate_batch_v3

SHA = 'f' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Background crowd on the left differs from the speaker frame'
S3_KEYS = ('rating', 'decision', 'moment_override', 'technical_score', 'editorial_score', 'evaluable',
           'distinct_moment', 'distinct_moment_evidence', 'technical', 'editorial', 'flags', 'fatal_flaws')
BURST_KEYS = ('burst_comparison', 'burst_rank', 'best_in_burst', 'co_best',
              'near_duplicate_of', 'rank_reason', 'not_comparable_evidence')


def orig(n):
    return f'/lib/S/DSC{n:05d}.ARW'


def ctx(n, group='g1', batch=0):
    return FrameContext(original_path=orig(n), preview_path=f'/out/previews/p{n:05d}.jpg', session_id='S',
                        burst_group=group, burst_sequence=group, capture_time=None, original_sha256=SHA,
                        batch_index=batch)


def evaluable(n, burst, t=3, e=3, **over):
    raw = {
        'path': orig(n), 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': t, 'editorial_score': e,
        'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]},
        'editorial': {'evidence': [GOOD2]}, 'decisive_evidence': [GOOD], 'proposed_rating': BASE_MATRIX[(t, e)],
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': True, 'distinct_moment_evidence': [GOOD], 'confidence': 0.9,
    }
    raw.update(burst)
    raw.update(over)
    return raw


def unevaluable(n, **over):
    raw = {
        'path': orig(n), 'evaluable': False, 'unevaluable_reason': 'preview is almost fully black',
        'capture_type': None, 'technical_score': None, 'editorial_score': None,
        'technical': {'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []},
        'editorial': {'evidence': []}, 'decisive_evidence': [], 'proposed_rating': 0,
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': False, 'distinct_moment_evidence': [], 'burst_comparison': None, 'burst_rank': None,
        'best_in_burst': None, 'co_best': None, 'near_duplicate_of': None, 'rank_reason': None,
        'not_comparable_evidence': [], 'confidence': 0.9,
    }
    raw.update(over)
    return raw


def ranked(rank, best, nd=None):
    return dict(burst_comparison='ranked', burst_rank=rank, best_in_burst=best, co_best=False,
                near_duplicate_of=nd, rank_reason=GOOD, not_comparable_evidence=[])


def nc():
    return dict(burst_comparison='not_comparable', burst_rank=None, best_in_burst=None, co_best=None,
                near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[GOOD2])


def non_burst():
    return dict(burst_comparison=None, burst_rank=None, best_in_burst=None, co_best=None,
                near_duplicate_of=None, rank_reason=None, not_comparable_evidence=[])


class Base(unittest.TestCase):
    def pipeline(self, batches):
        """batches: list of (contexts dict, photos list). Returns S3 output and S4 output."""
        accepted, errors, contexts = {}, [], {}
        for ctxs, photos in batches:
            acc, errs = validate_batch_v3(sorted(ctxs), photos, ctxs)
            accepted.update(acc); errors.extend(errs); contexts.update(ctxs)
        result, outcomes = apply_bursts_v3(accepted, errors, contexts)
        for a in result.values():
            self.assertTrue(a.review_reasons <= REVIEW_CODES)
        return accepted, errors, contexts, result, outcomes


class UnevaluableMemberTest(Base):
    def test_mixed_group_validated_over_evaluable_frames(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2, 3)}
        accepted, _, _, result, outcomes = self.pipeline([(ctxs, [
            evaluable(1, ranked(1, True)), unevaluable(2), evaluable(3, ranked(2, False))])])
        out = outcomes['g1']
        self.assertEqual(out.status, 'validated')                       # N = 2 (ranks 1, 2)
        self.assertEqual(out.members, (orig(1), orig(2), orig(3)))
        self.assertEqual(out.ranked, (orig(1), orig(3)))
        self.assertEqual(out.unevaluable, (orig(2),))
        u_before, u_after = accepted[orig(2)], result[orig(2)]
        self.assertIsNone(u_after.fields['burst_ranking_status'])
        self.assertEqual(u_after.review_reasons, u_before.review_reasons)
        self.assertEqual({k: v for k, v in u_after.fields.items() if k != 'burst_ranking_status'}, u_before.fields)
        for key in BURST_KEYS:
            self.assertEqual(u_after.fields[key], u_before.fields[key])

    def test_unevaluable_member_is_not_a_near_duplicate_target(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2, 3)}
        *_, result, outcomes = self.pipeline([(ctxs, [
            evaluable(1, ranked(1, True)), unevaluable(2), evaluable(3, ranked(2, False, nd=orig(2)))])])
        self.assertEqual(outcomes['g1'].status, 'invalid')
        self.assertIsNone(result[orig(3)].fields['near_duplicate_of'])
        self.assertEqual(set(result[orig(3)].review_reasons), {'near_duplicate_invalid', 'burst_ranking_invalid'})

    def test_unevaluable_plus_missing_frame(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2, 3, 4)}
        *_, result, outcomes = self.pipeline([(ctxs, [
            evaluable(1, ranked(1, True)), unevaluable(2), evaluable(3, ranked(2, False))])])   # 4: no result
        out = outcomes['g1']
        self.assertEqual(out.status, 'invalid')
        self.assertEqual(out.missing, (orig(4),))
        for n in (1, 3):
            self.assertEqual(set(result[orig(n)].review_reasons), {'burst_frame_missing', 'burst_ranking_invalid'})
        self.assertIsNone(result[orig(2)].fields['burst_ranking_status'])

    def test_all_unevaluable_group_has_no_status(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        *_, result, outcomes = self.pipeline([(ctxs, [unevaluable(1), unevaluable(2)])])
        self.assertIsNone(outcomes['g1'].status)
        self.assertEqual(outcomes['g1'].ranked, ())
        for n in (1, 2):
            self.assertIsNone(result[orig(n)].fields['burst_ranking_status'])
            self.assertEqual(set(result[orig(n)].review_reasons), {'unevaluable'})


class ErrorPathTest(Base):
    def test_duplicate_result_invalidates_group(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        _, errors, _, result, outcomes = self.pipeline([(ctxs, [
            evaluable(1, ranked(1, True)), evaluable(2, ranked(2, False)), evaluable(2, ranked(2, False))])])
        self.assertIsInstance(errors[0]['result'], list)
        self.assertEqual(outcomes['g1'].status, 'invalid')
        self.assertEqual(set(result[orig(1)].review_reasons), {'burst_frame_missing', 'burst_ranking_invalid'})

    def test_outside_batch_result_invalidates_only_that_batch(self):
        b0 = {orig(n): ctx(n, 'gA', 0) for n in (1, 2)}
        b1 = {orig(n): ctx(n, 'gB', 1) for n in (3, 4)}
        *_, outcomes = self.pipeline([
            (b0, [evaluable(1, ranked(1, True)), evaluable(2, ranked(2, False)), {'path': '/elsewhere/X.ARW'}]),
            (b1, [evaluable(3, ranked(1, True)), evaluable(4, ranked(2, False))])])
        self.assertEqual(outcomes['gA'].status, 'invalid')
        self.assertEqual(outcomes['gB'].status, 'validated')

    def test_preview_failure_does_not_invalidate(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2)}
        acc, errs = validate_batch_v3(sorted(ctxs), [evaluable(1, ranked(1, True)),
                                                     evaluable(2, ranked(2, False))], ctxs)
        errs.append(make_error_entry(stage='preview', error='preview failed', original_path=orig(3),
                                     session_id='S', burst_group='g1', sent_to_model=False))
        result, outcomes = apply_bursts_v3(acc, errs, ctxs)
        self.assertEqual(outcomes['g1'].status, 'validated')


class FatalFlawsTest(Base):
    def test_content_has_no_effect(self):
        outcomes_seen = []
        for flaws in ([], ['every face is out of focus']):
            ctxs = {orig(n): ctx(n) for n in (1, 2)}
            *_, result, outcomes = self.pipeline([(ctxs, [
                evaluable(1, ranked(1, True), fatal_flaws=flaws), evaluable(2, ranked(2, False))])])
            outcomes_seen.append((outcomes['g1'].status, {k: (v.rating, v.review_reasons) for k, v in result.items()}))
        self.assertEqual(outcomes_seen[0], outcomes_seen[1])

    def test_unevaluable_with_invalid_fatal_flaws_triggers_b0(self):
        ctxs = {orig(n): ctx(n) for n in (1, 2, 3)}
        _, errors, _, result, outcomes = self.pipeline([(ctxs, [
            evaluable(1, ranked(1, True)), evaluable(2, ranked(2, False)), unevaluable(3, fatal_flaws=['x'])])])
        self.assertEqual(errors[0]['original_path'], orig(3))
        self.assertIs(errors[0]['sent_to_model'], True)
        self.assertEqual(outcomes['g1'].status, 'invalid')
        self.assertEqual(outcomes['g1'].failed_rules, frozenset({'B0'}))


class IndependenceTest(Base):
    def test_s3_values_identical_for_every_status(self):
        scenarios = {
            'validated': [evaluable(1, ranked(1, True)), evaluable(2, ranked(2, False))],
            'not_comparable': [evaluable(1, nc()), evaluable(2, nc())],
            'invalid': [evaluable(1, ranked(1, True)), evaluable(2, {**ranked(2, False), 'rank_reason': 'x'})],
        }
        for status, photos in scenarios.items():
            with self.subTest(status=status):
                ctxs = {orig(n): ctx(n) for n in (1, 2)}
                accepted, *_, result, outcomes = self.pipeline([(ctxs, photos)])
                self.assertEqual(outcomes['g1'].status, status)
                for key, before in accepted.items():
                    after = result[key]
                    self.assertEqual((after.rating, after.decision, after.moment_override),
                                     (before.rating, before.decision, before.moment_override))
                    self.assertIs(after.distinct_moment_claim_valid, before.distinct_moment_claim_valid)
                    self.assertEqual(after.burst_raw, before.burst_raw)
                    self.assertTrue(before.review_reasons <= after.review_reasons)
                    for k in S3_KEYS:
                        self.assertEqual(after.fields[k], before.fields[k], k)


class NonBurstTest(Base):
    def test_non_burst_frame_only_gains_null_status(self):
        ctxs = {orig(1): ctx(1, None), orig(2): ctx(2), orig(3): ctx(3)}
        accepted, *_, result, _ = self.pipeline([(ctxs, [
            evaluable(1, non_burst()), evaluable(2, ranked(1, True)), evaluable(3, ranked(2, False))])])
        before, after = accepted[orig(1)], result[orig(1)]
        self.assertEqual(after.fields, {**before.fields, 'burst_ranking_status': None})
        self.assertEqual(after.review_reasons, before.review_reasons)


class ContractTest(Base):
    def setUp(self):
        self.ctxs = {orig(n): ctx(n) for n in (1, 2)}
        self.accepted, self.errors = validate_batch_v3(sorted(self.ctxs), [
            evaluable(1, ranked(1, True)), evaluable(2, ranked(2, False))], self.ctxs)

    def test_inputs_not_mutated(self):
        snapshot = copy.deepcopy((self.accepted, self.errors, self.ctxs))
        result, _ = apply_bursts_v3(self.accepted, self.errors, self.ctxs)
        self.assertEqual((self.accepted, self.errors, self.ctxs), snapshot)
        self.assertIsNot(result[orig(1)].fields, self.accepted[orig(1)].fields)
        self.assertNotIn('burst_ranking_status', self.accepted[orig(1)].fields)

    def test_contract_violations_raise(self):
        with self.assertRaises(ValueError):
            apply_bursts_v3(self.accepted, [], {orig(1): self.ctxs[orig(1)]})           # no context
        with self.assertRaises(ValueError):
            apply_bursts_v3(self.accepted, [], {**self.ctxs, orig(2): ctx(2, batch=3)})   # group across batches
        with self.assertRaises(ValueError):
            apply_bursts_v3(self.accepted, [], {**self.ctxs, orig(2): ctx(2, 'other')})   # group mismatch
        broken = {**self.accepted, orig(1): dataclasses.replace(self.accepted[orig(1)], burst_raw=None)}
        with self.assertRaises(ValueError):
            apply_bursts_v3(broken, [], self.ctxs)                                         # no burst_raw
        with self.assertRaises(ValueError):
            apply_bursts_v3(self.accepted, ['junk'], self.ctxs)                            # file_refusal


if __name__ == '__main__':
    unittest.main()
