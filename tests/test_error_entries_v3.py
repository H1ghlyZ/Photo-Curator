"""S1: spec 14.5 error entries and spec 8.2.1 identity rules, with synthetic values only.

Not wired into the v2 analysis pipeline (decision A-4). No files, no external tools.
"""
from __future__ import annotations
import unittest

from photo_curator.schema_v3 import (ERROR_ENTRY_FIELDS, ERROR_STAGES, ErrorEntryError,
                                     classify_error_entry, identity_scope, make_error_entry)

ORIG = '/library/DCIM/100MSDCF/DSC00001.ARW'
PREV = '/out/previews/DSC00001__abcdef0123.jpg'


def valid(stage: str) -> dict:
    if stage in ('original', 'preview'):
        return make_error_entry(stage=stage, error='synthetic failure', original_path=ORIG,
                                session_id='DCIM/100MSDCF', burst_group=None,
                                capture_time='2026-11-15T10:14:03.250', sent_to_model=False)
    return make_error_entry(stage=stage, error='synthetic failure', original_path=ORIG,
                            session_id='DCIM/100MSDCF', burst_group='burst-A', burst_sequence='burst-A',
                            capture_time='2026-11-15T10:14:03.250', batch_index=3, sent_to_model=True,
                            result={'path': ORIG} if stage == 'result' else None, preview_path=PREV)


class ShapeTest(unittest.TestCase):
    def test_stages(self):
        self.assertEqual(ERROR_STAGES, ('original', 'preview', 'batch', 'result'))

    def test_every_stage_has_exactly_the_eleven_fields(self):
        self.assertEqual(len(ERROR_ENTRY_FIELDS), 11)
        for stage in ERROR_STAGES:
            with self.subTest(stage=stage):
                entry = valid(stage)
                self.assertEqual(tuple(entry), ERROR_ENTRY_FIELDS)
                self.assertEqual(entry['stage'], stage)

    def test_unsent_stages(self):
        for stage in ('original', 'preview'):
            e = valid(stage)
            self.assertIs(e['sent_to_model'], False)
            self.assertIsNone(e['batch_index']); self.assertIsNone(e['preview_path']); self.assertIsNone(e['result'])

    def test_sent_stages(self):
        for stage in ('batch', 'result'):
            e = valid(stage)
            self.assertIs(e['sent_to_model'], True)
            self.assertEqual(e['batch_index'], 3); self.assertEqual(e['preview_path'], PREV)
        self.assertIsNone(valid('batch')['result'])

    def test_result_entry_naming_no_frame(self):
        e = make_error_entry(stage='result', error='path outside batch', original_path=None,
                             session_id='DCIM 2', burst_group=None, batch_index=0, sent_to_model=True,
                             result={'path': '/elsewhere/X.ARW'})
        self.assertIsNone(e['original_path'])


class ConstructorRejectsTest(unittest.TestCase):
    def bad(self, **overrides):
        base = dict(stage='batch', error='x', original_path=ORIG, session_id='S', burst_group=None,
                    batch_index=0, sent_to_model=True, preview_path=PREV)
        base.update(overrides)
        with self.assertRaises(ErrorEntryError):
            make_error_entry(**base)

    def test_unknown_stage_and_empty_error(self):
        self.bad(stage='hash'); self.bad(error=''); self.bad(error='   ')

    def test_unsent_stage_rules(self):
        for stage in ('original', 'preview'):
            unsent = dict(stage=stage, sent_to_model=False, batch_index=None, preview_path=None)
            self.bad(**{**unsent, 'sent_to_model': True})
            self.bad(**{**unsent, 'batch_index': 0})
            self.bad(**{**unsent, 'preview_path': PREV})
            self.bad(**{**unsent, 'original_path': None})
            self.bad(**{**unsent, 'result': {'x': 1}})

    def test_sent_stage_rules(self):
        self.bad(sent_to_model=False)
        self.bad(batch_index=None); self.bad(batch_index=-1); self.bad(batch_index=True); self.bad(batch_index='1')
        self.bad(session_id=None); self.bad(session_id='')
        self.bad(burst_group='')
        self.bad(original_path=None)                         # only 'result' may name no frame
        self.bad(result={'x': 1})                            # 'batch' carries no result

    def test_sent_to_model_must_be_bool(self):
        self.bad(sent_to_model=1); self.bad(sent_to_model=None)


class ClassifyTest(unittest.TestCase):
    def test_valid_entries_have_no_findings(self):
        for stage in ERROR_STAGES:
            with self.subTest(stage=stage):
                self.assertEqual(classify_error_entry(valid(stage)),
                                 {'file_refusal': [], 'identity_unknown': [], 'warnings': []})

    def test_file_refusal_v31(self):
        self.assertTrue(classify_error_entry('not an object')['file_refusal'])
        self.assertTrue(classify_error_entry({**valid('batch'), 'stage': 'nope'})['file_refusal'])
        self.assertTrue(classify_error_entry({**valid('batch'), 'error': ''})['file_refusal'])
        missing = {k: v for k, v in valid('batch').items() if k != 'stage'}
        self.assertTrue(classify_error_entry(missing)['file_refusal'])

    def test_identity_unknown_v44(self):
        cases = {
            'sent_to_model missing': {k: v for k, v in valid('batch').items() if k != 'sent_to_model'},
            'sent_to_model not bool': {**valid('batch'), 'sent_to_model': 'yes'},
            'names no frame': {**valid('result'), 'original_path': None},
            'original_path missing': {k: v for k, v in valid('batch').items() if k != 'original_path'},
            'original_path wrong type': {**valid('batch'), 'original_path': 42},
            'session_id missing': {k: v for k, v in valid('batch').items() if k != 'session_id'},
            'burst_group missing': {k: v for k, v in valid('batch').items() if k != 'burst_group'},
            'burst_group wrong type': {**valid('batch'), 'burst_group': 7},
        }
        for label, entry in cases.items():
            with self.subTest(label=label):
                f = classify_error_entry(entry)
                self.assertTrue(f['identity_unknown'], label)
                self.assertEqual(f['file_refusal'], [], label)   # identity never refuses the file

    def test_unsent_entry_without_session_is_not_identity_unknown(self):
        e = {**valid('preview'), 'session_id': None}
        self.assertEqual(classify_error_entry(e)['identity_unknown'], [])

    def test_invalid_preview_path_is_warning_only(self):
        f = classify_error_entry({**valid('batch'), 'preview_path': 5})
        self.assertTrue(f['warnings'])
        self.assertEqual(f['file_refusal'], []); self.assertEqual(f['identity_unknown'], [])

    def test_classification_does_not_modify_entry(self):
        e = {**valid('batch'), 'burst_group': 7}
        before = dict(e)
        classify_error_entry(e)
        self.assertEqual(e, before)


class IdentityScopeTest(unittest.TestCase):
    def test_batch_then_session_then_file(self):
        self.assertEqual(identity_scope({'batch_index': 4, 'session_id': 'S'}), ('batch', 4))
        self.assertEqual(identity_scope({'batch_index': None, 'session_id': 'S'}), ('session', 'S'))
        self.assertEqual(identity_scope({'batch_index': 'x', 'session_id': 'S'}), ('session', 'S'))
        self.assertEqual(identity_scope({'batch_index': True, 'session_id': ''}), ('file', None))
        self.assertEqual(identity_scope({}), ('file', None))


if __name__ == '__main__':
    unittest.main()
