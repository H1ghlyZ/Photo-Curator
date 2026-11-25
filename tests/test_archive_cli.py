"""W-B: ``analyze --archive-existing`` through the CLI (spec 12.7 workflow 2, 12.9, CLI-8; spec 20
item 15 archive parts and 16b CLI). Fakes for ExifTool, darktable and Claude; temporary folders only."""
from __future__ import annotations
import hashlib, json, os, unittest
from pathlib import Path

from photo_curator.analyze_v3 import WORKFLOW_ARCHIVE, WORKFLOW_NEW_OUTPUT
from photo_curator.approve_v3 import approve_item
from photo_curator.locking_v3 import JOURNAL_NAME
from photo_curator.preview_checks_v3 import check_preview
from photo_curator.reader_v3 import read_results
from tests.fake_claude_v3 import FakeClaudeV3, evaluable_result
from tests.test_analyze_v3_e2e import S1, Base, tree


class ArchiveCliTest(Base):
    def setUp(self):
        super().setUp()
        self.review_item = str(self.root / S1 / 'DSC00004.JPG')
        self.claude = FakeClaudeV3({self.review_item: evaluable_result(self.review_item, confidence=0.4)})

    def approve_review_item(self):
        doc = json.loads((self.work / 'ratings-review.json').read_text(encoding='utf-8'))
        r = next(x for x in doc['results'] if x['original_path'] == self.review_item)
        approve_item(self.work / 'ratings-review.json', self.review_item, rating=r['rating'],
                     preview_sha256_arg=hashlib.sha256(Path(r['preview_path']).read_bytes()).hexdigest())

    def archive_folders(self):
        root = self.work / 'archive'
        return sorted(p for p in root.iterdir()) if root.is_dir() else []

    def test_refusal_offers_both_workflows(self):
        self.analyze()
        before = tree(self.work)
        code, _, err = self.main('analyze', str(self.root), '--output', str(self.work))
        self.assertEqual(code, 1)
        self.assertIn(WORKFLOW_NEW_OUTPUT, err)
        self.assertIn(WORKFLOW_ARCHIVE, err)
        self.assertEqual(tree(self.work), before)

    def test_archive_existing_then_fresh_analysis(self):
        self.analyze()
        self.approve_review_item()
        old_results = (self.work / 'ratings-review.json').read_bytes()
        code, out, err = self.main('analyze', str(self.root), '--output', str(self.work), '--archive-existing')
        self.assertEqual(code, 0, err)
        self.assertIn('approvals archived: 1 passing', out)
        self.assertIn('Archived approvals are not carried into the new results', out)
        folders = self.archive_folders()
        self.assertEqual(len(folders), 1)
        archived = folders[0]
        self.assertEqual((archived / 'ratings-review.json').read_bytes(), old_results)       # moved, unchanged
        self.assertTrue((archived / 'archive-journal.json').is_file())
        self.assertTrue((archived / '.photo-curator-output').is_file())                      # marked as output
        self.assertFalse((self.work / JOURNAL_NAME).exists())
        new = json.loads((self.work / 'ratings-review.json').read_text(encoding='utf-8'))
        self.assertTrue(all(r['approved'] is False for r in new['results']))                 # U2: start unapproved
        self.assertTrue(read_results(self.work / 'ratings-review.json').refusal is None)

    def test_archived_approvals_fail_pv1(self):
        self.analyze()
        self.approve_review_item()
        self.main('analyze', str(self.root), '--output', str(self.work), '--archive-existing')
        archived = self.archive_folders()[0]
        doc = json.loads((archived / 'ratings-review.json').read_text(encoding='utf-8'))
        r = next(x for x in doc['results'] if x['approved'] is True)
        result = check_preview(r['preview_path'], archived, r['original_path'], doc['source_folder'])
        self.assertIn('PV1', result.failed_ids)

    def test_nothing_deleted(self):
        self.analyze()
        before = {name: digest for name, digest in tree(self.work).items() if name != 'ratings-review.json.lock'}
        self.main('analyze', str(self.root), '--output', str(self.work), '--archive-existing')
        archived = self.archive_folders()[0].name
        after = tree(self.work)
        for name, digest in before.items():
            if name.startswith(('ratings-review.', 'previews/')):
                self.assertEqual(after.get(f'archive/{archived}/{name}'), digest, name)

    def test_journal_present_refuses_and_points_to_recover_archive(self):
        self.analyze()
        (self.work / JOURNAL_NAME).write_bytes(b'{}')
        code, _, err = self.main('analyze', str(self.root), '--output', str(self.work), '--archive-existing')
        self.assertEqual(code, 1)
        self.assertIn('recover-archive', err)


if __name__ == '__main__':
    unittest.main()
