"""Schema compatibility and apply-target safety tests.

Uses a throwaway temp directory with empty placeholder files. write_rating_sidecar is
mocked, so no XMP is ever written; the real photo folder is never touched.
"""
from __future__ import annotations
import argparse, json, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import cli
from photo_curator.core import load_results


class TempLibrary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='photo-curator-test-')
        self.root = Path(self._tmp.name).resolve()
        (self.root / 'DCIM' / '100MSDCF').mkdir(parents=True)
        (self.root / 'DCIM 2').mkdir()
        self.work = self.root / '.photo-curator'; (self.work / 'previews').mkdir(parents=True)
        self.raw1 = self.root / 'DCIM' / '100MSDCF' / 'DSC00001.ARW'
        self.raw2 = self.root / 'DCIM 2' / 'GAT00001.ARW'
        self.jpg = self.root / 'DCIM 2' / 'GAT00002.JPG'
        self.prev1 = self.work / 'previews' / 'DSC00001__aaaa.jpg'
        self.prev2 = self.work / 'previews' / 'GAT00001__bbbb.jpg'
        self.prev_guess = self.work / 'previews' / 'GAT00001__zzzz.jpg'   # decoy for filename guessing
        for f in (self.raw1, self.raw2, self.jpg, self.prev1, self.prev2, self.prev_guess): f.write_bytes(b'')

    def tearDown(self): self._tmp.cleanup()

    def write(self, payload):
        path = self.work / 'ratings-review.json'
        path.write_text(json.dumps(payload), encoding='utf-8'); return path

    def v1(self):
        return {'schema_version': 1, 'results': [
            {'path': str(self.raw1), 'rating': 4, 'decision': 'strong'},
            {'path': str(self.prev1), 'rating': 5, 'decision': 'exceptional'},   # legacy bug: preview as source
        ], 'errors': []}

    def v2(self):
        return {'schema_version': 2, 'results': [
            {'path': str(self.raw1), 'original_path': str(self.raw1), 'preview_path': str(self.prev1),
             'session_id': 'DCIM/100MSDCF', 'rating': 3, 'burst_group': 'burst-A-p1', 'burst_sequence': 'burst-A',
             'best_in_burst': True},
            {'path': str(self.raw2), 'original_path': str(self.raw2), 'preview_path': str(self.prev2),
             'session_id': 'DCIM 2', 'rating': 2},
            {'path': str(self.jpg), 'original_path': str(self.jpg), 'preview_path': '', 'session_id': 'DCIM 2', 'rating': 4},
            {'path': str(self.raw2), 'original_path': str(self.raw1), 'preview_path': str(self.prev1), 'rating': 5},  # ambiguous
            {'path': str(self.prev2), 'original_path': str(self.prev2), 'preview_path': str(self.prev2), 'rating': 5},  # preview as original
        ], 'errors': []}


class CompatTests(TempLibrary):
    def test_schema_v1_readable(self):
        data = load_results(self.write(self.v1()))
        self.assertEqual(len(data['results']), 2)

    def test_schema_v2_readable(self):
        data = load_results(self.write(self.v2()))
        self.assertEqual(data['results'][0]['burst_sequence'], 'burst-A')

    def test_unknown_schema_rejected(self):
        with self.assertRaises(ValueError): load_results(self.write({'schema_version': 99, 'results': []}))
        with self.assertRaises(ValueError): load_results(self.write({'schema_version': 2}))

    def test_gallery_uses_exact_saved_preview_path(self):
        path = self.write(self.v2())
        cli.cmd_gallery(argparse.Namespace(results=str(path)))
        page = (self.work / 'review.html').read_text(encoding='utf-8')
        self.assertIn('previews/GAT00001__bbbb.jpg', page)
        self.assertNotIn('GAT00001__zzzz.jpg', page)            # no filename guessing
        self.assertIn('Sequence: burst-A', page)
        self.assertIn('id="session-filter"', page); self.assertIn('id="best-only"', page)

    def test_gallery_reads_v1(self):
        cli.cmd_gallery(argparse.Namespace(results=str(self.write(self.v1()))))
        self.assertTrue((self.work / 'review.html').is_file())


class ApplyTargetTests(TempLibrary):
    def apply(self, payload, yes=True):
        # W-B (X-4): the `apply` command runs apply_command (apply_v3); the mock moves to its writer.
        from photo_curator import apply_v3
        from photo_curator.sidecar_v3 import SidecarResult
        args = argparse.Namespace(results=str(self.write(payload)), min_rating=1, include_existing=False, yes=yes)
        with mock.patch.object(apply_v3, 'write_sidecar', return_value=SidecarResult('/dev/null/x.xmp', None)) as w:
            cli.apply_command(args)
        return [Path(c.args[0]) for c in w.call_args_list]

    def test_apply_targets_originals_only_v2(self):
        targets = self.apply(self.v2())
        self.assertEqual(sorted(targets), sorted([self.raw1, self.raw2, self.jpg]))
        for t in targets: self.assertNotIn('.photo-curator', t.parts)

    def test_apply_skips_legacy_preview_paths_v1(self):
        self.assertEqual(self.apply(self.v1()), [self.raw1])

    def test_apply_requires_yes(self):
        from photo_curator import apply_v3                              # W-B (X-4): wired apply command
        with mock.patch.object(apply_v3, 'write_sidecar') as w:
            args = argparse.Namespace(results=str(self.write(self.v2())), min_rating=1, include_existing=False, yes=False)
            with self.assertRaises(SystemExit): cli.apply_command(args)
            w.assert_not_called()

    def test_no_sidecars_created(self):
        self.apply(self.v2())
        self.assertEqual(list(self.root.rglob('*.xmp')), [])


if __name__ == '__main__':
    unittest.main()
