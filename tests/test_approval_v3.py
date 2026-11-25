"""S12: approval validation, 12.1 state derivation and the 12.3 re-approval table (spec 20 item 21,
validation part). Synthetic output folder; originals are never opened or hashed here."""
from __future__ import annotations
import hashlib, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import hashing_v3
from photo_curator.approval_v3 import (BINDING_MESSAGES, MALFORMED_MESSAGE, PENDING_MESSAGE, REAPPROVAL_TABLE,
                                       approval_field_problems, derive_approval_state, reapproval_decision,
                                       revoked_fields)
from photo_curator.core import OUTPUT_MARKER
from photo_curator.preview_checks_v3 import expected_preview_name
from photo_curator.schema_v3 import APPROVAL_FIELDS

ORIG_SHA = 'a' * 64


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s12-')
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name))
        self.source = root / 'library'
        self.source.mkdir()
        self.work = root / 'out'
        (self.work / 'previews').mkdir(parents=True)
        (self.work / OUTPUT_MARKER).write_text('m', encoding='utf-8')
        (self.work / 'previews' / OUTPUT_MARKER).write_text('m', encoding='utf-8')
        self.original = str(self.source / 'DSC00001.ARW')
        self.preview = self.work / 'previews' / expected_preview_name(self.original)
        self.preview.write_bytes(b'\xff\xd8 preview bytes')

    def item(self, **over) -> dict:
        item = {'original_path': self.original, 'path': self.original, 'preview_path': str(self.preview),
                'rating': 4, 'needs_review': True, 'original_sha256': ORIG_SHA,
                'approved': True, 'approved_rating': 4, 'approved_at': '2026-11-15T07:12:03Z',
                'approved_original_path': self.original, 'approved_original_sha256': ORIG_SHA,
                'approved_preview_path': str(self.preview),
                'approved_preview_sha256': hashlib.sha256(self.preview.read_bytes()).hexdigest()}
        item.update(over)
        return item

    def derive(self, item, **kw):
        return derive_approval_state(item, work=self.work, source_folder=str(self.source), **kw)


class MalformedTest(Base):
    """Item 21 'malformed fields': each 12.2 rule broken on its own."""

    CASES = {
        'non-bool approved': {'approved': 1}, 'string "true"': {'approved': 'true'},
        'missing approved_rating': None, 'bool approved_rating': {'approved_rating': True},
        'approved_rating 0': {'approved_rating': 0}, 'approved_rating 6': {'approved_rating': 6},
        'approved_at without Z': {'approved_at': '2026-11-15T07:12:03'},
        'approved_at unparseable': {'approved_at': 'yesterdayZ'},
        'relative original path': {'approved_original_path': 'DSC00001.ARW'},
        'relative preview path': {'approved_preview_path': 'previews/x.jpg'},
        'short hash': {'approved_preview_sha256': 'b' * 63}, 'uppercase hash': {'approved_preview_sha256': 'B' * 64},
        'approved_original_sha256 != original_sha256': {'approved_original_sha256': 'c' * 64},
        'approved = false with a non-null field': {'approved': False, 'approved_rating': None, 'approved_at': None,
                                                   'approved_original_path': None, 'approved_original_sha256': None,
                                                   'approved_preview_path': None},
        'approval on needs_review = false': {'needs_review': False},
        'approval on a rating-0 item': {'rating': 0, 'approved_rating': 1},
    }

    def test_each_rule(self):
        for label, change in self.CASES.items():
            with self.subTest(rule=label):
                item = self.item()
                if change is None:
                    del item['approved_rating']
                else:
                    item.update(change)
                problems = approval_field_problems(item)
                self.assertTrue(problems, label)
                state = self.derive(item)
                self.assertEqual((state.state, state.message), ('malformed', MALFORMED_MESSAGE))
                self.assertEqual(state.problems, tuple(problems))

    def test_never_repaired(self):
        item = self.item(approved='true')
        before = dict(item)
        self.derive(item)
        self.assertEqual(item, before)

    def test_well_formed_has_no_problems(self):
        self.assertEqual(approval_field_problems(self.item()), [])
        self.assertEqual(approval_field_problems(self.item(**revoked_fields())), [])


class StateTest(Base):
    def test_not_required_and_pending(self):
        self.assertEqual(self.derive(self.item(needs_review=False, **revoked_fields())).state, 'not_required')
        pending = self.derive(self.item(**revoked_fields()))
        self.assertEqual((pending.state, pending.message), ('pending', PENDING_MESSAGE))

    def test_approved_without_original_hash_is_unverified(self):
        state = self.derive(self.item())
        self.assertEqual((state.state, state.original_verified, state.label),
                         ('approved', False, 'approved (original not verified)'))

    def test_approved_with_matching_original_hash_is_verified(self):
        state = self.derive(self.item(), original_sha256_now=ORIG_SHA)
        self.assertEqual((state.state, state.label), ('approved', 'approved'))

    def test_never_hashes_the_original(self):
        with mock.patch.object(hashing_v3, 'sha256_file', side_effect=AssertionError('original hashed')):
            self.derive(self.item())
            self.derive(self.item(), original_sha256_now=ORIG_SHA)


class BindingChecksTest(Base):
    """Each binding check failing on its own, with its 12.1 message (item 21 'stale approvals')."""

    def assert_invalid(self, state, number):
        self.assertEqual((state.state, state.failing_check, state.message),
                         ('invalidated', number, BINDING_MESSAGES[number]))

    def test_1_rating_changed(self):
        self.assert_invalid(self.derive(self.item(rating=3)), 1)

    def test_2_original_path_changed(self):
        self.assert_invalid(self.derive(self.item(original_path='/library/DSC00002.ARW')), 2)

    def test_3_original_content_changed_or_unreadable(self):
        self.assert_invalid(self.derive(self.item(), original_sha256_now='f' * 64), 3)
        self.assert_invalid(self.derive(self.item(), original_sha256_now=None), 3)
        self.assertEqual(BINDING_MESSAGES[3], '[SKIP approval invalidated: original changed]')

    def test_4_preview_path_changed(self):
        self.assert_invalid(self.derive(self.item(approved_preview_path=str(self.work / 'previews' / 'other.jpg'))), 4)

    def test_5_preview_missing_or_symlink(self):
        item = self.item()                                  # approval made while the preview was valid
        os.replace(self.preview, self.work / 'real.bin')
        self.assert_invalid(self.derive(item), 5)                                                  # missing
        os.symlink(self.work / 'real.bin', self.preview)
        self.assert_invalid(self.derive(item), 5)                                                  # symlink

    def test_6_preview_content_changed(self):
        item = self.item()
        self.preview.write_bytes(b'\xff\xd8 edited preview')
        self.assert_invalid(self.derive(item), 6)

    def test_diagnostic_order(self):
        """An item failing checks 3 and 4: --show (hash given) reports 3; readers report 4."""
        item = self.item(approved_preview_path=str(self.work / 'previews' / 'other.jpg'))
        self.assert_invalid(self.derive(item, original_sha256_now='f' * 64), 3)
        self.assert_invalid(self.derive(item), 4)
        both = self.item(rating=3, approved_preview_path='/x/y.jpg')
        self.assert_invalid(self.derive(both), 1)

    def test_preview_checks_can_be_skipped_only_explicitly(self):
        item = self.item()
        os.unlink(self.preview)
        self.assertEqual(self.derive(item, run_preview_checks=False).state, 'approved')
        self.assert_invalid(self.derive(item), 5)


class ReapprovalTableTest(unittest.TestCase):
    """Item 21 're-approval table': every cell."""

    EXPECTED = {
        ('not_required', 'approve'): False, ('not_required', 'revoke'): False,
        ('pending', 'approve'): True, ('pending', 'revoke'): False,
        ('approved', 'approve'): False, ('approved', 'revoke'): True,
        ('invalidated', 'approve'): True, ('invalidated', 'revoke'): True,
        ('malformed', 'approve'): False, ('malformed', 'revoke'): False,
    }

    def test_every_cell(self):
        self.assertEqual(set(REAPPROVAL_TABLE), set(self.EXPECTED))
        for (state, action), allowed in self.EXPECTED.items():
            with self.subTest(state=state, action=action):
                ok, text = reapproval_decision(state, action)
                self.assertIs(ok, allowed)
                self.assertEqual(text.startswith('refused'), not allowed)

    def test_texts(self):
        self.assertEqual(reapproval_decision('approved', 'approve')[1],
                         'refused: already approved; revoke first to change it')
        self.assertEqual(reapproval_decision('malformed', 'revoke')[1], 'refused: revoking would be a repair')
        self.assertIn('revokes -> pending', reapproval_decision('invalidated', 'revoke')[1])

    def test_unknown(self):
        with self.assertRaises(ValueError):
            reapproval_decision('approved', 'delete')

    def test_revoked_fields(self):
        fields = revoked_fields()
        self.assertEqual(tuple(fields), APPROVAL_FIELDS)
        self.assertIs(fields['approved'], False)
        self.assertTrue(all(fields[f] is None for f in APPROVAL_FIELDS[1:]))


if __name__ == '__main__':
    unittest.main()
