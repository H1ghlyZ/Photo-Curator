"""S4: B0 and spec 8.2.1 identity of error entries (D3-11, D4-7), including an access-order
test that fails if ``burst_group`` is read before identity classification, or read at all on
an entry whose identity is unknown.

Pure functions only; synthetic values, no files, no tools.
"""
from __future__ import annotations
import unittest
from unittest import mock

from photo_curator import bursts_v3
from photo_curator.bursts_v3 import apply_bursts_v3, b0_affected_groups
from photo_curator.schema_v3 import BASE_MATRIX, make_error_entry
from photo_curator.validate_v3 import Accepted, FrameContext, validate_batch_v3, validate_frame_v3

SHA = 'e' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Background crowd on the left differs from the speaker frame'


def orig(n):
    return f'/lib/S/DSC{n:05d}.ARW'


def ctx(n, group, batch, session='S'):
    return FrameContext(original_path=orig(n), preview_path=f'/out/previews/p{n:05d}.jpg', session_id=session,
                        burst_group=group, burst_sequence=group, capture_time=None, original_sha256=SHA,
                        batch_index=batch)


def model(n, rank, best):
    return {
        'path': orig(n), 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': 3, 'editorial_score': 3,
        'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]},
        'editorial': {'evidence': [GOOD2]}, 'decisive_evidence': [], 'proposed_rating': BASE_MATRIX[(3, 3)],
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': False, 'distinct_moment_evidence': [], 'confidence': 0.9,
        'burst_comparison': 'ranked', 'burst_rank': rank, 'best_in_burst': best, 'co_best': False,
        'near_duplicate_of': None, 'rank_reason': GOOD, 'not_comparable_evidence': [],
    }


# Layout: gA = frames 1,2 (batch 0, session S); gB = frames 3,4 (batch 1, session S);
#         gC = frames 5,6 (batch 2, session T); frame 7 not in a burst (batch 0, session S).
LAYOUT = {1: ('gA', 0, 'S'), 2: ('gA', 0, 'S'), 3: ('gB', 1, 'S'), 4: ('gB', 1, 'S'),
          5: ('gC', 2, 'T'), 6: ('gC', 2, 'T'), 7: (None, 0, 'S')}


def contexts():
    return {orig(n): ctx(n, g, b, s) for n, (g, b, s) in LAYOUT.items()}


def accepted_all():
    out = {}
    for n, (g, _, _) in LAYOUT.items():
        raw = model(n, 1 if n % 2 else 2, bool(n % 2))
        if g is None:
            raw.update(burst_comparison=None, burst_rank=None, best_in_burst=None, co_best=None, rank_reason=None)
        acc = validate_frame_v3(raw, contexts()[orig(n)])
        assert isinstance(acc, Accepted), getattr(acc, 'entry', None)
        out[orig(n)] = acc
    return out


def known(n, sent=True, stage='result'):
    g, b, s = LAYOUT[n]
    if not sent:
        return make_error_entry(stage='preview', error='preview failed', original_path=orig(n), session_id=s,
                                burst_group=g, sent_to_model=False)
    return make_error_entry(stage=stage, error='synthetic', original_path=orig(n), session_id=s, burst_group=g,
                            burst_sequence=g, batch_index=b, sent_to_model=True,
                            preview_path=f'/out/previews/p{n:05d}.jpg')


def unknown(batch=0, session='S'):
    return make_error_entry(stage='result', error='path outside batch', original_path=None, session_id=session,
                            burst_group=None, batch_index=batch, sent_to_model=True, result={'path': '/x'})


def without(entry, *keys):
    return {k: v for k, v in entry.items() if k not in keys}


class Base(unittest.TestCase):
    def affected(self, errors):
        return b0_affected_groups(errors, contexts())


class KnownIdentityTest(Base):
    def test_sent_true_invalidates_named_group_only(self):
        self.assertEqual(self.affected([known(3)]), {'gB'})

    def test_sent_true_null_group_affects_nothing(self):
        self.assertEqual(self.affected([known(7)]), frozenset())

    def test_sent_false_never_triggers(self):
        self.assertEqual(self.affected([known(1, sent=False), known(3, sent=False)]), frozenset())

    def test_batch_stage_entry(self):
        self.assertEqual(self.affected([known(5, stage='batch')]), {'gC'})


class UnknownIdentityTest(Base):
    def test_from_validate_batch_v3_uses_batch_scope(self):
        ctxs = {orig(n): contexts()[orig(n)] for n in (1, 2, 7)}
        accepted, errors = validate_batch_v3([orig(1), orig(2), orig(7)],
                                             [model(1, 1, True), model(2, 2, False), 'junk',
                                              {**model(7, 1, True), 'path': '/elsewhere/X.ARW'}], ctxs)
        unknown_entries = [e for e in errors if e['original_path'] is None]
        self.assertEqual(len(unknown_entries), 2)
        self.assertTrue(all(e['burst_group'] is None for e in unknown_entries))
        self.assertEqual(self.affected(unknown_entries), {'gA'})   # batch 0 only, despite burst_group null

    def test_scope_order(self):
        base = known(3)                                             # batch 1, session S, group gB
        self.assertEqual(self.affected([without(base, 'burst_group')]), {'gB'})                     # batch
        self.assertEqual(self.affected([{**without(base, 'burst_group'), 'batch_index': None}]),
                         {'gA', 'gB'})                                                              # session S
        self.assertEqual(self.affected([{**without(base, 'burst_group', 'session_id'), 'batch_index': None}]),
                         {'gA', 'gB', 'gC'})                                                        # file

    def test_burst_group_of_unknown_entry_is_ignored(self):
        # Identity unknown (sent_to_model missing / not bool): scope comes from batch 1, never
        # from the entry's burst_group 'gA'.
        for sent in ('missing', 'yes', 1, None):
            with self.subTest(sent=sent):
                entry = {**known(3), 'burst_group': 'gA'}
                entry = without(entry, 'sent_to_model') if sent == 'missing' else {**entry, 'sent_to_model': sent}
                self.assertEqual(self.affected([entry]), {'gB'})

    def test_wrong_type_identity_fields(self):
        self.assertEqual(self.affected([{**known(1), 'burst_group': 7}]), {'gA'})
        self.assertEqual(self.affected([{**known(5), 'original_path': 42}]), {'gC'})
        self.assertEqual(self.affected([unknown(batch=2, session='T')]), {'gC'})


class RefusalTest(Base):                                                    # D4-7
    def test_file_refusal_raises(self):
        for entry in ('not an object', without(known(1), 'stage'), {**known(1), 'error': ''},
                      {**known(1), 'stage': 'hash'}):
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    self.affected([entry])

    def test_errors_must_be_a_list(self):
        with self.assertRaises(ValueError):
            b0_affected_groups({'x': 1}, contexts())


class GroupEffectTest(Base):
    def test_codes_and_status(self):
        result, outcomes = apply_bursts_v3(accepted_all(), [known(3), known(5, sent=False)], contexts())
        self.assertEqual(outcomes['gA'].status, 'validated')
        self.assertEqual(outcomes['gB'].status, 'invalid')
        self.assertEqual(outcomes['gC'].status, 'validated')                 # sent_to_model = false
        self.assertEqual(outcomes['gB'].failed_rules, frozenset({'B0'}))
        for n in (3, 4):
            self.assertEqual(set(result[orig(n)].review_reasons), {'burst_frame_missing', 'burst_ranking_invalid'})
        for n in (1, 2, 5, 6, 7):
            self.assertEqual(result[orig(n)].review_reasons, frozenset())


class GuardedEntry(dict):
    """Raises if 'burst_group' is read, or the entry iterated, while ``allow`` is False."""
    allow = False

    def _guard(self, key=None, iterate=False):
        if not self.allow and (iterate or key == 'burst_group'):
            raise AssertionError('burst_group read before (or without) identity classification')

    def __getitem__(self, key):
        self._guard(key); return super().__getitem__(key)

    def get(self, key, default=None):
        self._guard(key); return super().get(key, default)

    def __contains__(self, key):
        self._guard(key); return super().__contains__(key)

    def __iter__(self):
        self._guard(iterate=True); return super().__iter__()

    def keys(self):
        self._guard(iterate=True); return super().keys()

    def items(self):
        self._guard(iterate=True); return super().items()

    def values(self):
        self._guard(iterate=True); return super().values()


class AccessOrderTest(Base):
    def guarded_classify(self):
        real = bursts_v3.classify_error_entry

        def wrapper(entry):
            entry.allow = True                     # classification itself may read burst_group
            findings = real(entry)
            entry.allow = not findings['identity_unknown']
            return findings
        return mock.patch.object(bursts_v3, 'classify_error_entry', side_effect=wrapper)

    def entries(self):
        return [GuardedEntry(e) for e in (
            known(3), known(7), known(1, sent=False), unknown(batch=2, session='T'),
            without(known(1), 'sent_to_model'), {**known(5), 'burst_group': 7},
            {**without(known(3), 'burst_group', 'session_id'), 'batch_index': None})]

    def test_b0_reads_burst_group_only_after_known_identity(self):
        with self.guarded_classify() as patched:
            affected = b0_affected_groups(self.entries(), contexts())
            self.assertEqual(patched.call_count, 7)
        self.assertEqual(affected, {'gA', 'gB', 'gC'})

    def test_apply_respects_access_order(self):
        with self.guarded_classify():
            result, outcomes = apply_bursts_v3(accepted_all(), self.entries(), contexts())
        self.assertEqual({g: o.status for g, o in outcomes.items()},
                         {'gA': 'invalid', 'gB': 'invalid', 'gC': 'invalid'})

    def test_guard_catches_a_wrong_order(self):
        entry = GuardedEntry(unknown())
        with self.assertRaises(AssertionError):
            entry['burst_group']                     # read before classification
        with self.guarded_classify():
            bursts_v3.classify_error_entry(entry)
        with self.assertRaises(AssertionError):
            entry.get('burst_group')                 # unknown identity: still never readable
        known_entry = GuardedEntry(known(3))
        with self.guarded_classify():
            bursts_v3.classify_error_entry(known_entry)
        self.assertEqual(known_entry['burst_group'], 'gB')


if __name__ == '__main__':
    unittest.main()
