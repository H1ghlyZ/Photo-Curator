"""S3: V1 batch outcomes and spec 14.5 error entries (D3-7, D3-10..D3-13), field checks
V2-V20/V25/V27, model-supplied C/H keys (D3-5) and the FrameContext contract (D3-8).

Pure validator only; synthetic values, no files, no tools.
"""
from __future__ import annotations
import dataclasses, unittest

from photo_curator.schema_v3 import (ERROR_ENTRY_FIELDS, REVIEW_CODES, classify_error_entry,
                                     identity_scope)
from photo_curator.validate_v3 import (ABSENT, Accepted, FrameContext, Rejected,
                                       validate_batch_v3, validate_frame_v3)

LIB = '/library/DCIM/100MSDCF'
SHA = 'b' * 64
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Eyes of the speaker meet the audience on the left'
BURST_KEYS = ('burst_comparison', 'burst_rank', 'best_in_burst', 'co_best',
              'near_duplicate_of', 'rank_reason', 'not_comparable_evidence')


def orig(n: int) -> str:
    return f'{LIB}/DSC{n:05d}.ARW'


def prev(n: int) -> str:
    return f'/out/previews/DSC{n:05d}__{n:010d}.jpg'


def ctx(n=1, burst_group=None, batch_index=2, session='DCIM/100MSDCF', **over):
    base = dict(original_path=orig(n), preview_path=prev(n), session_id=session,
                burst_group=burst_group, burst_sequence=burst_group,
                capture_time=f'2026-11-15T10:14:{n:02d}', original_sha256=SHA, batch_index=batch_index)
    base.update(over)
    return FrameContext(**base)


def evaluable(n=1, **over):
    raw = {
        'path': orig(n), 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': 3, 'editorial_score': 3,
        'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]},
        'editorial': {'evidence': [GOOD2]}, 'decisive_evidence': [], 'proposed_rating': 3,
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': False, 'distinct_moment_evidence': [], 'burst_comparison': None,
        'burst_rank': None, 'best_in_burst': None, 'co_best': None, 'near_duplicate_of': None,
        'rank_reason': None, 'not_comparable_evidence': [], 'confidence': 0.9,
    }
    raw.update(over)
    return raw


def batch(ns=(1, 2, 3), **ctx_over):
    originals = [orig(n) for n in ns]
    return originals, {orig(n): ctx(n, **ctx_over) for n in ns}


class Base(unittest.TestCase):
    def ok(self, raw, context=None) -> Accepted:
        out = validate_frame_v3(raw, context or ctx())
        self.assertIsInstance(out, Accepted, getattr(out, 'entry', None))
        self.assertTrue(out.review_reasons <= REVIEW_CODES)
        return out

    def assert_entry_shape(self, entry):
        self.assertEqual(tuple(entry), ERROR_ENTRY_FIELDS)
        self.assertEqual(len(entry), 11)
        self.assertEqual(entry['stage'], 'result')
        self.assertIs(entry['sent_to_model'], True)
        self.assertEqual(classify_error_entry(entry)['file_refusal'], [])

    def assert_known(self, entry, n, batch_index=2):
        self.assert_entry_shape(entry)
        c = ctx(n, batch_index=batch_index)
        self.assertEqual(entry['original_path'], c.original_path)
        self.assertEqual(entry['session_id'], c.session_id)
        self.assertEqual(entry['capture_time'], c.capture_time)
        self.assertEqual(entry['batch_index'], batch_index)
        self.assertEqual(entry['preview_path'], c.preview_path)        # D3-13: from FrameContext
        self.assertEqual(classify_error_entry(entry)['identity_unknown'], [])

    def assert_unknown(self, entry, batch_index=2):
        self.assert_entry_shape(entry)
        self.assertIsNone(entry['original_path'])
        self.assertEqual(entry['session_id'], 'DCIM/100MSDCF')
        self.assertIsNone(entry['burst_group']); self.assertIsNone(entry['burst_sequence'])
        self.assertIsNone(entry['capture_time'])
        self.assertIsNone(entry['preview_path'])                       # D3-13
        self.assertEqual(entry['batch_index'], batch_index)
        self.assertTrue(classify_error_entry(entry)['identity_unknown'])
        self.assertEqual(identity_scope(entry), ('batch', batch_index))


class V1Test(Base):
    def test_all_accepted(self):
        originals, ctxs = batch()
        accepted, errors = validate_batch_v3(originals, [evaluable(1), evaluable(2), evaluable(3)], ctxs)
        self.assertEqual(sorted(accepted), sorted(originals))
        self.assertEqual(errors, [])

    def test_non_object_items(self):
        originals, ctxs = batch((1,))
        items = ['text', 5, None, [orig(1)], evaluable(1)]
        accepted, errors = validate_batch_v3(originals, items, ctxs)
        self.assertEqual(list(accepted), [orig(1)])
        self.assertEqual(len(errors), 4)
        for entry, item in zip(errors, items):
            self.assert_unknown(entry)
            self.assertEqual(entry['result'], item)
            self.assertIn('non-object', entry['error'])

    def test_outside_batch_and_preview_path(self):                      # C-6
        originals, ctxs = batch((1, 2))
        items = [evaluable(1, path=prev(1)), evaluable(2, path='/elsewhere/X.ARW'),
                 {k: v for k, v in evaluable(2).items() if k != 'path'}, evaluable(2, path=7)]
        accepted, errors = validate_batch_v3(originals, items, ctxs)
        self.assertEqual(accepted, {})
        unknown, named = errors[:4], errors[4:]
        for entry, item in zip(unknown, items):
            self.assert_unknown(entry)
            self.assertEqual(entry['result'], item)
            self.assertIn('outside', entry['error'])
        self.assertEqual([e['original_path'] for e in named], [orig(1), orig(2)])
        for entry, n in zip(named, (1, 2)):
            self.assert_known(entry, n)
            self.assertIsNone(entry['result'])
            self.assertIn('no result', entry['error'])

    def test_contradictory_original_path(self):                         # D3-12
        originals, ctxs = batch((1, 2))
        items = [evaluable(1, original_path=orig(2)), evaluable(2, original_path=None)]
        accepted, errors = validate_batch_v3(originals, items, ctxs)
        self.assertEqual(accepted, {})
        for entry in errors[:2]:
            self.assert_unknown(entry)
            self.assertIn('contradicts', entry['error'])
        self.assertEqual([e['original_path'] for e in errors[2:]], [orig(1), orig(2)])

    def test_identical_original_path_ignored(self):
        originals, ctxs = batch((1,))
        accepted, errors = validate_batch_v3(originals, [evaluable(1, original_path=orig(1))], ctxs)
        self.assertEqual(errors, [])
        self.assertEqual(accepted[orig(1)].review_reasons, frozenset())

    def test_duplicates_one_entry_with_ordered_array(self):              # C-5, D3-7
        originals, ctxs = batch((1, 2))
        first, second, third = evaluable(1), evaluable(1, technical_score=5), evaluable(1, foo=1)
        accepted, errors = validate_batch_v3(originals, [first, evaluable(2), second, third], ctxs)
        self.assertEqual(list(accepted), [orig(2)])
        self.assertEqual(len(errors), 1)
        entry = errors[0]
        self.assert_known(entry, 1)
        self.assertIsInstance(entry['result'], list)
        self.assertEqual(entry['result'], [first, second, third])
        self.assertIn('duplicate', entry['error'])

    def test_rejected_single_result(self):
        originals, ctxs = batch((1,))
        bad = evaluable(1, technical_score=9)
        accepted, errors = validate_batch_v3(originals, [bad], ctxs)
        self.assertEqual(accepted, {})
        self.assert_known(errors[0], 1)
        self.assertEqual(errors[0]['result'], bad)
        self.assertIsInstance(errors[0]['result'], dict)

    def test_exactly_one_outcome_per_original_and_order(self):
        originals, ctxs = batch((1, 2, 3, 4))
        items = ['junk', evaluable(3), evaluable(1, path='/x/Y.ARW'), evaluable(4), evaluable(4),
                 evaluable(2, evaluable='no')]
        accepted, errors = validate_batch_v3(originals, items, ctxs)
        named = [e for e in errors if e['original_path'] is not None]
        unknown = [e for e in errors if e['original_path'] is None]
        self.assertEqual(errors, unknown + named)
        self.assertEqual([e['result'] for e in unknown], ['junk', items[2]])
        self.assertEqual([e['original_path'] for e in named], sorted(e['original_path'] for e in named))
        outcomes = sorted(list(accepted) + [e['original_path'] for e in named])
        self.assertEqual(outcomes, sorted(originals))

    def test_burst_identity_copied_on_known_entries(self):
        originals = [orig(1), orig(2)]
        ctxs = {orig(n): ctx(n, burst_group='burst-A') for n in (1, 2)}
        _, errors = validate_batch_v3(originals, [evaluable(1, technical=None)], ctxs)
        for entry in errors:
            self.assertEqual(entry['burst_group'], 'burst-A')
            self.assertEqual(entry['burst_sequence'], 'burst-A')
            self.assertEqual(classify_error_entry(entry)['identity_unknown'], [])

    def test_unknown_entries_never_carry_burst_identity(self):           # D3-11
        originals = [orig(1)]
        ctxs = {orig(1): ctx(1, burst_group='burst-A')}
        _, errors = validate_batch_v3(originals, [42], ctxs)
        self.assert_unknown(errors[0])


class PreviewAccountingTest(Base):                                       # D3-13
    def referenced(self, accepted, errors):
        refs = {a.fields['preview_path'] for a in accepted.values()}
        refs |= {e['preview_path'] for e in errors if e['preview_path'] is not None}
        return refs

    def test_every_sent_preview_referenced_once_by_its_original(self):
        originals, ctxs = batch((1, 2, 3, 4, 5))
        items = [evaluable(1), 'junk', evaluable(2, path=prev(2)), evaluable(3), evaluable(3),
                 evaluable(4, technical=None), evaluable(5, original_path=orig(1))]
        accepted, errors = validate_batch_v3(originals, items, ctxs)
        self.assertEqual(self.referenced(accepted, errors), {prev(n) for n in range(1, 6)})
        for entry in errors:
            if entry['original_path'] is None:
                self.assertIsNone(entry['preview_path'])
            else:
                self.assertEqual(entry['preview_path'], ctxs[entry['original_path']].preview_path)

    def test_unknown_entries_add_no_reference(self):
        originals, ctxs = batch((1,))
        accepted, errors = validate_batch_v3(originals, ['a', {'path': prev(1)}, evaluable(1)], ctxs)
        unknown = [e for e in errors if e['original_path'] is None]
        self.assertEqual(len(unknown), 2)
        self.assertTrue(all(e['preview_path'] is None for e in unknown))
        self.assertEqual(self.referenced(accepted, errors), {prev(1)})

    def test_original_with_only_no_result_entry_still_referenced(self):
        originals, ctxs = batch((1,))
        accepted, errors = validate_batch_v3(originals, [evaluable(1, path=prev(1))], ctxs)
        self.assertEqual(accepted, {})
        self.assertEqual(self.referenced(accepted, errors), {prev(1)})


class BatchContractTest(Base):                                           # D3-8, D3-10
    def test_preconditions(self):
        originals, ctxs = batch((1, 2))
        with self.assertRaises(ValueError):
            validate_batch_v3(originals, {'photos': []}, ctxs)
        with self.assertRaises(ValueError):
            validate_batch_v3([], [], {})
        with self.assertRaises(ValueError):
            validate_batch_v3([orig(1), orig(1)], [], {orig(1): ctx(1)})
        with self.assertRaises(ValueError):
            validate_batch_v3(originals, [], {orig(1): ctx(1)})
        with self.assertRaises(ValueError):
            validate_batch_v3([orig(1)], [], {orig(1): ctx(2)})

    def test_mixed_sessions_or_batch_indexes(self):
        with self.assertRaises(ValueError):
            validate_batch_v3([orig(1), orig(2)], [], {orig(1): ctx(1), orig(2): ctx(2, session='DCIM 2')})
        with self.assertRaises(ValueError):
            validate_batch_v3([orig(1), orig(2)], [], {orig(1): ctx(1), orig(2): ctx(2, batch_index=3)})


class FrameContextTest(Base):                                            # D3-8
    def test_violations_raise(self):
        bad = {
            'original_path': ['relative/DSC.ARW', '', None],
            'preview_path': ['previews/x.jpg', None],
            'session_id': ['', None, 3],
            'burst_group': ['', 5],
            'burst_sequence': ['', 5],
            'capture_time': [5],
            'original_sha256': ['A' * 64, 'a' * 63, None],
            'batch_index': [-1, True, '0', None],
        }
        for field, values in bad.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        validate_frame_v3(evaluable(), dataclasses.replace(ctx(), **{field: value}))

    def test_missing_context_and_non_object(self):
        with self.assertRaises(ValueError):
            validate_frame_v3(evaluable(), None)
        with self.assertRaises(ValueError):
            validate_frame_v3('not an object', ctx())

    def test_code_fields_copied_verbatim(self):
        c = ctx(burst_group=None)
        out = self.ok(evaluable(path='/model/says/elsewhere.ARW', session_id='X', preview_path='/p.jpg',
                                capture_time='1999', original_sha256='0' * 64), c)
        f = out.fields
        self.assertEqual(f['path'], c.original_path); self.assertEqual(f['original_path'], c.original_path)
        self.assertEqual(f['preview_path'], c.preview_path); self.assertEqual(f['session_id'], c.session_id)
        self.assertEqual(f['capture_time'], c.capture_time); self.assertEqual(f['original_sha256'], SHA)
        self.assertIsNone(f['burst_group']); self.assertIsNone(f['burst_sequence'])
        self.assertEqual(out.review_reasons, frozenset())                # D3-5: C keys, no code


class ModelKeysTest(Base):                                               # D3-5, V26, V27
    def test_unknown_keys_ignored_at_every_level(self):
        raw = evaluable(foo=1, technical={**evaluable()['technical'], 'lens': '24mm'},
                        editorial={'evidence': [GOOD2], 'mood': 'calm'})
        out = self.ok(raw)
        self.assertNotIn('foo', out.fields)
        self.assertEqual(set(out.fields['technical']), {'motion', 'motion_evidence', 'recoverable_in_raw', 'evidence'})
        self.assertEqual(set(out.fields['editorial']), {'evidence'})
        self.assertEqual(out.review_reasons, frozenset())

    def test_code_fields_ignored_without_code(self):
        raw = evaluable(decision='exceptional', burst_ranking_status='validated', needs_review=False,
                        review_reasons=[], reasons=['x'], documentation_suggestion='retain',
                        editorial_suggestion='highlight', moment_override=True)
        out = self.ok(raw)
        self.assertEqual(out.review_reasons, frozenset())
        for key in ('burst_ranking_status', 'needs_review', 'review_reasons', 'reasons',
                    'documentation_suggestion', 'editorial_suggestion'):
            self.assertNotIn(key, out.fields)
        self.assertEqual(out.fields['decision'], 'usable')

    def test_human_fields_ignored_with_field_invalid(self):
        for key, value in (('approved', True), ('approved_rating', 3), ('approved_at', '2026-01-01T00:00:00Z'),
                           ('approved_original_path', orig(1)), ('approved_original_sha256', SHA),
                           ('approved_preview_path', prev(1)), ('approved_preview_sha256', SHA)):
            with self.subTest(key=key):
                out = self.ok(evaluable(**{key: value}))
                self.assertNotIn(key, out.fields)
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))

    def test_human_fields_on_unevaluable(self):
        raw = {'path': orig(1), 'evaluable': False, 'unevaluable_reason': 'unreadable preview data',
               'capture_type': None, 'technical_score': None, 'editorial_score': None,
               'technical': {'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []},
               'editorial': {'evidence': []}, 'decisive_evidence': [], 'proposed_rating': 0,
               'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
               'distinct_moment': False, 'distinct_moment_evidence': [], 'not_comparable_evidence': [],
               'confidence': 1, 'approved': False,
               **{k: None for k in BURST_KEYS[:6]}}
        out = self.ok(raw)
        self.assertEqual(out.review_reasons, frozenset({'unevaluable', 'field_invalid'}))


class FieldTest(Base):
    def test_v5_unevaluable_reason_on_evaluable(self):
        out = self.ok(evaluable(unevaluable_reason=None))
        self.assertIsNone(out.fields['unevaluable_reason'])
        self.assertEqual(out.review_reasons, frozenset())
        for value in ('looks fine', '', '   '):                             # string: K
            with self.subTest(value=value):
                out = self.ok(evaluable(unevaluable_reason=value))
                self.assertEqual(out.fields['unevaluable_reason'], value)
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))

    def test_v5_missing_or_not_string_is_null(self):                        # D3-14
        for value in (5, True, ['dark'], {'why': 'dark'}, 0.5):
            with self.subTest(value=value):
                out = self.ok(evaluable(unevaluable_reason=value))
                self.assertIsNone(out.fields['unevaluable_reason'])
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
        raw = evaluable(); del raw['unevaluable_reason']
        out = self.ok(raw)
        self.assertIsNone(out.fields['unevaluable_reason'])
        self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))

    def test_v7_capture_type(self):
        for value in (None, 'selfie', 'Candid', 3):
            with self.subTest(value=value):
                out = self.ok(evaluable(capture_type=value))
                self.assertIsNone(out.fields['capture_type'])
                self.assertIn('field_invalid', out.review_reasons)
        for value in ('candid', 'posed_portrait', 'group', 'detail', 'venue'):
            self.assertEqual(self.ok(evaluable(capture_type=value)).fields['capture_type'], value)

    def test_v10_recoverable_in_raw(self):
        tech = evaluable()['technical']
        for value in (None, 'highlights', [1]):
            with self.subTest(value=value):
                out = self.ok(evaluable(technical={**tech, 'recoverable_in_raw': value}))
                self.assertIsNone(out.fields['technical']['recoverable_in_raw'])
                self.assertIn('field_invalid', out.review_reasons)
        out = self.ok(evaluable(technical={**tech, 'recoverable_in_raw': ['shadow detail on the left']}))
        self.assertEqual(out.fields['technical']['recoverable_in_raw'], ['shadow detail on the left'])

    def test_v15_flags_stored_exactly(self):
        out = self.ok(evaluable(flags=['subject_cut_off', 'clutter']))       # strong tilt case
        self.assertEqual(out.fields['flags'], ['subject_cut_off', 'clutter'])
        self.assertEqual(out.review_reasons, frozenset())
        for value in (None, 'clutter', ['clutter', 'none'], ['poor_exposure'], [['clutter']], ['Clutter']):
            with self.subTest(value=value):
                out = self.ok(evaluable(flags=value))
                self.assertIsNone(out.fields['flags'])
                self.assertIn('field_invalid', out.review_reasons)

    def test_v15_fatal_flaws_evaluable(self):
        out = self.ok(evaluable(fatal_flaws=['every face is out of focus']))
        self.assertEqual(out.fields['fatal_flaws'], ['every face is out of focus'])
        self.assertEqual(out.review_reasons, frozenset())
        for value in (None, 'x', [1]):
            with self.subTest(value=value):
                out = self.ok(evaluable(fatal_flaws=value))
                self.assertIsNone(out.fields['fatal_flaws'])
                self.assertIn('field_invalid', out.review_reasons)

    def test_v16_uncertain_checks(self):
        out = self.ok(evaluable(uncertain_checks=['focus', 'eyes']))
        self.assertEqual(out.fields['uncertain_checks'], ['focus', 'eyes'])
        self.assertEqual(out.review_reasons, frozenset({'critical_defect_uncertain'}))
        for value in (None, 'focus', ['blur'], [None]):
            with self.subTest(value=value):
                out = self.ok(evaluable(uncertain_checks=value))
                self.assertIsNone(out.fields['uncertain_checks'])
                self.assertTrue({'field_invalid', 'motion_conflict'} <= out.review_reasons)

    def test_v18_distinct_moment_type(self):
        for value in (None, 'true', 1):
            with self.subTest(value=value):
                out = self.ok(evaluable(distinct_moment=value))
                self.assertIsNone(out.fields['distinct_moment'])
                self.assertIn('field_invalid', out.review_reasons)
                self.assertIs(out.distinct_moment_claim_valid, False)

    def test_v20_burst_fields_outside_burst(self):
        for key, value in (('burst_comparison', 'ranked'), ('burst_rank', 1), ('best_in_burst', True),
                           ('co_best', False), ('near_duplicate_of', orig(2)), ('rank_reason', GOOD)):
            with self.subTest(key=key):
                out = self.ok(evaluable(**{key: value}))
                self.assertIsNone(out.fields[key])
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
                self.assertIsNone(out.burst_raw)
        for value in ([GOOD], ['x'], [1]):                                  # non-empty list: K
            with self.subTest(value=value):
                out = self.ok(evaluable(not_comparable_evidence=value))
                self.assertEqual(out.fields['not_comparable_evidence'], value)
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
        out = self.ok(evaluable(not_comparable_evidence=[]))
        self.assertEqual(out.fields['not_comparable_evidence'], [])
        self.assertEqual(out.review_reasons, frozenset())

    def test_v20_not_comparable_evidence_not_list_is_null(self):             # D3-14
        for value in (None, 'text', 0, {'why': 'x'}, True):
            with self.subTest(value=value):
                out = self.ok(evaluable(not_comparable_evidence=value))
                self.assertIsNone(out.fields['not_comparable_evidence'])
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))

    def test_v20_missing_keys_outside_burst(self):                        # O-S3-1, D3-14
        for key in BURST_KEYS:
            with self.subTest(key=key):
                raw = evaluable(); del raw[key]
                out = self.ok(raw)
                self.assertIsNone(out.fields[key])
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
                self.assertIsNone(out.burst_raw)

    def test_burst_raw_inside_burst(self):
        raw = evaluable(burst_comparison='ranked', burst_rank=1, best_in_burst=True, co_best='x')
        del raw['rank_reason']
        out = self.ok(raw, ctx(burst_group='burst-A'))
        self.assertEqual(set(out.burst_raw), set(BURST_KEYS))
        self.assertEqual(out.burst_raw['burst_comparison'], 'ranked')
        self.assertEqual(out.burst_raw['co_best'], 'x')                  # raw, unconverted
        self.assertIs(out.burst_raw['rank_reason'], ABSENT)
        self.assertIsNone(out.burst_raw['near_duplicate_of'])
        for key in BURST_KEYS:
            self.assertNotIn(key, out.fields)
        self.assertEqual(out.review_reasons, frozenset())

    def test_v25_confidence(self):
        for value in (0, 1, 0.0, 0.6, 1.0):
            with self.subTest(value=value):
                out = self.ok(evaluable(confidence=value))
                self.assertEqual(out.fields['confidence'], value)
                self.assertNotIn('field_invalid', out.review_reasons)
        for value in (None, True, False, '0.9', 1.5, -0.1, 2, float('nan'), float('inf')):
            with self.subTest(value=value):
                out = self.ok(evaluable(confidence=value))
                self.assertIsNone(out.fields['confidence'])
                self.assertIn('field_invalid', out.review_reasons)
                self.assertNotIn('low_confidence', out.review_reasons)
        raw = evaluable(); del raw['confidence']
        self.assertIn('field_invalid', self.ok(raw).review_reasons)

    def test_low_confidence_threshold(self):
        self.assertIn('low_confidence', self.ok(evaluable(confidence=0.59)).review_reasons)
        self.assertIn('low_confidence', self.ok(evaluable(confidence=0)).review_reasons)
        self.assertNotIn('low_confidence', self.ok(evaluable(confidence=0.6)).review_reasons)


if __name__ == '__main__':
    unittest.main()
