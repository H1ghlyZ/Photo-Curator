"""S1: freeze current v1/v2 behaviour, refuse schema v3 everywhere, pin the approved spec.

Throwaway temp directory with empty placeholder files. write_rating_sidecar is mocked
(decision A-5), so no XMP is ever written. GTR and the pilot are never touched. The spec
file is only read.
"""
from __future__ import annotations
import argparse, contextlib, hashlib, io, json, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import cli
from photo_curator.core import load_results

SPEC = Path(__file__).resolve().parent.parent / 'docs' / 'spec-v3-revision-5g3a-draft.md'
SPEC_SHA256 = '364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25'


class Library(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='photo-curator-s1-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.work = self.root / 'out'; (self.work / 'previews').mkdir(parents=True)
        (self.work / '.photo-curator-output').write_text('marker', encoding='utf-8')
        (self.work / 'previews' / '.photo-curator-output').write_text('marker', encoding='utf-8')
        self.a = self.root / 'lib' / 'DCIM' / '100MSDCF' / 'DSC00001.ARW'
        self.b = self.root / 'lib' / 'DCIM 2' / 'GAT00001.JPG'
        self.txt = self.root / 'lib' / 'notes.txt'
        self.prev = self.work / 'previews' / 'DSC00001__aaaaaaaaaa.jpg'
        for f in (self.a, self.b, self.txt, self.prev):
            f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'')

    def tearDown(self):
        self.assertEqual(list(self.root.rglob('*.xmp*')), [], 'a test created an XMP sidecar')

    def write(self, payload) -> Path:
        path = self.work / 'ratings-review.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        return path

    def apply(self, path: Path) -> tuple[list[Path], str, mock.MagicMock]:
        # W-B (X-4): the `apply` command runs apply_command (apply_v3); the mock moves to its writer.
        from photo_curator import apply_v3
        from photo_curator.sidecar_v3 import SidecarResult
        args = argparse.Namespace(results=str(path), min_rating=1, include_existing=False, yes=True)
        out = io.StringIO()
        with mock.patch.object(apply_v3, 'write_sidecar', return_value=SidecarResult('/dev/null/x.xmp', None)) as w, \
             contextlib.redirect_stdout(out):
            cli.apply_command(args)
        return [Path(c.args[0]) for c in w.call_args_list], out.getvalue(), w


class V1RegressionTest(Library):
    def payload(self):
        return {'schema_version': 1, 'errors': [], 'results': [
            {'path': str(self.a), 'rating': 4, 'decision': 'strong'},
            {'path': str(self.b), 'rating': 3, 'decision': 'usable'},
            {'path': str(self.prev), 'rating': 5, 'decision': 'exceptional'},   # inside generated output
            {'path': str(self.txt), 'rating': 5, 'decision': 'exceptional'},    # unsupported extension
        ]}

    def test_v1_without_v2_v3_fields_is_readable(self):
        data = load_results(self.write(self.payload()))
        for r in data['results']:
            for absent in ('original_path', 'preview_path', 'session_id', 'burst_group', 'burst_sequence'):
                self.assertNotIn(absent, r)

    def test_v1_apply_targets_and_messages_unchanged(self):
        targets, log, _ = self.apply(self.write(self.payload()))
        self.assertEqual(targets, [self.a, self.b])
        self.assertNotIn('[SKIP ambiguous original_path/path]', log)   # check 1: target is `path`
        self.assertNotIn('[SKIP inconsistent result]', log)
        self.assertIn(f'[SKIP inside generated output] {self.prev}', log)
        self.assertIn(f'[SKIP not an original image] {self.txt}', log)
        self.assertIn('Applied 2; skipped 2.', log)

    def test_v1_gallery_keeps_review_html(self):                        # decision D-2
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_gallery(argparse.Namespace(results=str(self.write(self.payload()))))
        self.assertTrue((self.work / 'review.html').is_file())
        self.assertFalse((self.work / 'ratings-review.html').exists())


class V2RegressionTest(Library):
    def payload(self):
        return {'schema_version': 2, 'errors': [], 'results': [
            {'path': str(self.a), 'original_path': str(self.a), 'preview_path': str(self.prev),
             'session_id': 'DCIM/100MSDCF', 'rating': 4},                     # no burst_sequence
            {'path': str(self.b), 'original_path': str(self.b), 'preview_path': '',
             'session_id': 'DCIM 2', 'rating': 2},
            {'path': str(self.b), 'original_path': str(self.a), 'preview_path': str(self.prev),
             'rating': 5},                                                    # ambiguous
        ]}

    def test_v2_apply_targets_and_messages_unchanged(self):
        targets, log, _ = self.apply(self.write(self.payload()))
        self.assertEqual(targets, [self.a, self.b])
        self.assertEqual(log.count('[SKIP ambiguous original_path/path]'), 1)
        self.assertIn('Applied 2; skipped 1.', log)

    def test_v2_gallery_keeps_review_html(self):                        # decision D-2
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_gallery(argparse.Namespace(results=str(self.write(self.payload()))))
        self.assertTrue((self.work / 'review.html').is_file())
        self.assertFalse((self.work / 'ratings-review.html').exists())


class V3RefusedTest(Library):
    """Decision A-1 / D-1 / Gate B: no existing command accepts a schema v3 file."""

    def v3_path(self) -> Path:
        return self.write({'schema_version': 3, 'rubric_version': 'v3', 'results': [
            {'path': str(self.a), 'original_path': str(self.a), 'rating': 4}], 'errors': []})

    def listing(self):
        return sorted(p.relative_to(self.root) for p in self.root.rglob('*'))

    def without_lock(self, listing):
        # W-B (X-4): every lock-taking command may create the lock file (14.7 difference 3).
        return [p for p in listing if p.name != 'ratings-review.json.lock']

    def test_gallery_refuses_v3_and_writes_nothing(self):
        path = self.v3_path(); before = self.listing()
        with self.assertRaisesRegex(ValueError, 'Unsupported results schema'):           # D-1 kept
            cli.cmd_gallery(argparse.Namespace(results=str(path)))
        self.assertFalse((self.work / 'review.html').exists())
        self.assertFalse((self.work / 'ratings-review.html').exists())
        self.assertEqual(self.without_lock(self.listing()), before)

    def test_review_refuses_v3(self):
        # W-B (CLI-5): review takes the v3 path; this incomplete v3 file is refused (V31/12.8).
        path = self.v3_path(); before = self.listing()
        with self.assertRaises(cli.CommandRefusal), contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_review(argparse.Namespace(results=str(path)))
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(self.without_lock(self.listing()), before)

    def test_apply_refuses_v3_and_never_writes(self):
        # W-B (CLI-4): apply takes the v3 path; this incomplete v3 file is refused before any item.
        from photo_curator import apply_v3
        path = self.v3_path(); before = self.listing()
        args = argparse.Namespace(results=str(path), min_rating=1, include_existing=False, yes=True)
        with mock.patch.object(apply_v3, 'write_sidecar') as w:
            with self.assertRaises(apply_v3.ApplyRefused):
                cli.apply_command(args)
            w.assert_not_called()
        self.assertEqual(self.without_lock(self.listing()), before)


class SpecPinTest(unittest.TestCase):
    def test_approved_spec_unchanged(self):                             # decision A-6 (read-only)
        self.assertTrue(SPEC.is_file(), SPEC)
        self.assertEqual(hashlib.sha256(SPEC.read_bytes()).hexdigest(), SPEC_SHA256)


if __name__ == '__main__':
    unittest.main()
