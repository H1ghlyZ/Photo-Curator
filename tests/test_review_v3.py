"""S10: ``review`` text for v3 documents (spec 14.6 review command, 12.1 reader rule; spec 20
item 18 "the v3 review hint pointing to approve --show")."""
from __future__ import annotations
import os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import artifacts_v3, hashing_v3
from photo_curator.artifacts_v3 import NOT_VERIFIED_LABEL, REVIEW_HINT, review_lines
from tests.test_document_v3 import STANDARD_GROUPS, Run, original, standard_overrides


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s10-rev-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        self.run_ = Run(self.work, STANDARD_GROUPS, standard_overrides())

    def block(self, lines: list[str], n: int) -> list[str]:
        start = next(i for i, line in enumerate(lines) if f'DSC{n:05d}.ARW' in line)
        end = next((i for i in range(start + 1, len(lines)) if '★' in lines[i] or '☆' in lines[i]), len(lines))
        return lines[start:end]


class HintTest(Base):
    def test_hint_points_to_approve_show(self):
        text = '\n'.join(review_lines(self.run_.doc))
        self.assertTrue(text.endswith(REVIEW_HINT))
        self.assertIn('photo-curator approve <results.json> <original_path> --show', text)
        self.assertNotIn('To approve, edit ratings-review.json', text)


class ContentTest(Base):
    def test_review_reasons_state_and_unverified_hash(self):
        lines = review_lines(self.run_.doc)
        block = self.block(lines, 9)
        self.assertIn('    approval: pending', block)
        self.assertIn('    - unevaluable', block)
        self.assertIn(f"    original SHA-256: {'d' * 64} ({NOT_VERIFIED_LABEL})", block)

    def test_not_required_state(self):
        plain = next(r for r in self.run_.doc['results'] if r['needs_review'] is False)
        block = self.block(review_lines(self.run_.doc), int(Path(plain['original_path']).name[3:8]))
        self.assertIn('    approval: not required', block)

    def test_stored_approval_labelled_original_not_verified(self):
        doc = dict(self.run_.doc)
        r9 = next(r for r in doc['results'] if r['original_path'] == original(1))
        approved = dict(r9, needs_review=True, review_reasons=['low_confidence'], approved=True,
                        approved_rating=r9['rating'], approved_at='2026-11-15T07:12:03Z',
                        approved_original_path=r9['original_path'], approved_original_sha256=r9['original_sha256'],
                        approved_preview_path=r9['preview_path'], approved_preview_sha256='e' * 64)
        doc['results'] = [approved]
        block = self.block(review_lines(doc), 1)
        self.assertIn('    approval: approved (original not verified; preview checks not run)', block)
        doc['results'] = [dict(approved, approved_rating=5 if r9['rating'] != 5 else 4)]
        self.assertIn('    approval: invalidated', self.block(review_lines(doc), 1))
        doc['results'] = [dict(approved, approved='true')]
        self.assertIn('    approval: malformed', self.block(review_lines(doc), 1))

    def test_one_block_per_result_sorted_by_rating(self):
        lines = review_lines(self.run_.doc)
        headers = [line for line in lines if '★' in line or '☆' in line]
        self.assertEqual(len(headers), len(self.run_.doc['results']))
        stars = [h.split()[0].count('★') for h in headers]
        self.assertEqual(stars, sorted(stars, reverse=True))

    def test_review_never_hashes_or_opens_files(self):
        with mock.patch.object(hashing_v3, 'sha256_file', side_effect=AssertionError('hashed')), \
             mock.patch.object(artifacts_v3.os, 'open', side_effect=AssertionError('opened')), \
             mock.patch('builtins.open', side_effect=AssertionError('opened')):
            review_lines(self.run_.doc)


if __name__ == '__main__':
    unittest.main()
