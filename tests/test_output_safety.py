"""Output-folder safety: marker files, --output validation, symlinks and apply targets.

Everything happens inside a fresh temporary directory with empty placeholder "images"
(find_images and apply only look at paths/suffixes, never pixel data). The real photo
library, GTR and the pilot folder are never touched. write_rating_sidecar is mocked in
the apply tests, and every test also asserts that no .xmp file was created.
"""
from __future__ import annotations
import argparse, contextlib, io, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import cli
from photo_curator.core import (GENERATED_DIR, OUTPUT_MARKER, OUTPUT_MARKER_TEXT, find_images,
                                in_generated_output, mark_output_dir, validate_output_dir)


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'')
    return path


class TempTree(unittest.TestCase):
    """tmp/
         source/             <- the photo library being scanned
         out/                <- a custom --output folder (outside source)
         elsewhere/          <- unrelated user folder outside source
    """
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='photo-curator-safety-')
        self.addCleanup(self._tmp.cleanup)   # runs after tearDown, even if its assertion fails
        self.tmp = Path(self._tmp.name).resolve()
        self.source = self.tmp / 'source'; self.source.mkdir()
        self.out = self.tmp / 'out'
        self.elsewhere = self.tmp / 'elsewhere'; self.elsewhere.mkdir()
        self.original = touch(self.source / 'DCIM' / '100MSDCF' / 'DSC00001.ARW')

    def tearDown(self):
        self.assertEqual(list(self.tmp.rglob('*.xmp*')), [], 'a test created an XMP sidecar')

    def symlink(self, link: Path, target: Path) -> Path:
        link.parent.mkdir(parents=True, exist_ok=True)
        try: link.symlink_to(target, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError) as e: self.skipTest(f'symlinks unavailable: {e}')
        return link


# 1-3 ---------------------------------------------------------------------------------
class FindImagesExclusionTest(TempTree):
    def test_marked_custom_output_dir_excluded(self):                                  # 1
        review = self.source / 'my-review'          # arbitrary name, not .photo-curator
        touch(review / 'previews' / 'DSC00001__abc.jpg'); touch(review / 'loose.JPG')
        mark_output_dir(review)
        self.assertEqual(find_images(self.source), [self.original])
        # Scanning a parent that contains a marked output folder next to the source.
        touch(self.out / 'previews' / 'DSC00001__abc.jpg'); mark_output_dir(self.out)
        self.assertEqual(find_images(self.tmp), [self.original])
        # Scanning the marked folder itself, or anything below it, yields nothing.
        self.assertEqual(find_images(self.out), [])
        self.assertEqual(find_images(self.out / 'previews'), [])

    def test_nested_marked_dirs_excluded(self):                                         # 2
        deep = self.source / 'a' / 'b' / 'generated'
        mark_output_dir(deep)
        touch(deep / 'x.jpg')
        touch(deep / 'unmarked-child' / 'grandchild' / 'y.tif')   # marker covers all descendants
        inner = self.source / 'a' / 'b' / 'generated' / 'previews'
        mark_output_dir(inner); touch(inner / 'z.jpg')            # marked inside marked
        keep = touch(self.source / 'a' / 'b' / 'keep.jpg')       # sibling of the marked dir
        self.assertEqual(find_images(self.source), sorted([self.original, keep]))
        self.assertEqual(find_images(deep / 'unmarked-child'), [])   # root below a marked ancestor

    def test_unmarked_user_dirs_remain_discoverable(self):                              # 3
        expected = [self.original]
        for name in ('previews', 'review', 'photo-curator-output', 'out', 'Exports'):
            expected.append(touch(self.source / name / f'{name}.jpg'))
        # A directory (not a file) called like the marker does not count as a marker.
        odd = self.source / 'odd'; (odd / OUTPUT_MARKER).mkdir(parents=True)
        expected.append(touch(odd / 'odd.jpg'))
        # A marker-like file with a different name does not count either.
        near = self.source / 'near'; touch(near / (OUTPUT_MARKER + '.txt'))
        expected.append(touch(near / 'near.jpg'))
        self.assertEqual(find_images(self.source), sorted(expected))
        for p in expected: self.assertFalse(in_generated_output(p), p)


# 4-5 ---------------------------------------------------------------------------------
class MarkOutputDirTest(TempTree):
    def test_idempotent(self):                                                          # 4
        m1 = mark_output_dir(self.out / 'previews')   # creates missing parents
        st1 = m1.stat()
        m2 = mark_output_dir(self.out / 'previews')
        self.assertEqual(m1, m2)
        self.assertEqual(m1.read_text(encoding='utf-8'), OUTPUT_MARKER_TEXT)
        self.assertEqual((st1.st_ino, st1.st_mtime_ns), (m2.stat().st_ino, m2.stat().st_mtime_ns))
        self.assertEqual([p.name for p in (self.out / 'previews').iterdir()], [OUTPUT_MARKER])  # no temp leftovers
        self.assertFalse((self.out / OUTPUT_MARKER).exists())   # only the given dir is marked

    def test_existing_marker_never_overwritten(self):                                   # 5
        self.out.mkdir()
        marker = self.out / OUTPUT_MARKER
        marker.write_text('user edited this', encoding='utf-8')
        os.utime(marker, ns=(1_000_000_000, 1_000_000_000))
        before = marker.stat()
        self.assertEqual(mark_output_dir(self.out), marker)
        after = marker.stat()
        self.assertEqual(marker.read_text(encoding='utf-8'), 'user edited this')
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_size),
                         (after.st_ino, after.st_mtime_ns, after.st_size))

    def test_non_regular_marker_rejected_and_left_alone(self):                          # 5 (edge)
        as_dir = self.tmp / 'as-dir'; (as_dir / OUTPUT_MARKER).mkdir(parents=True)
        touch(as_dir / OUTPUT_MARKER / 'keep')
        with self.assertRaises(RuntimeError): mark_output_dir(as_dir)
        self.assertTrue((as_dir / OUTPUT_MARKER / 'keep').is_file())

        dangling = self.tmp / 'dangling'; dangling.mkdir()
        target = self.tmp / 'nowhere' / 'target'
        self.symlink(dangling / OUTPUT_MARKER, target)
        with self.assertRaises(RuntimeError): mark_output_dir(dangling)
        self.assertTrue((dangling / OUTPUT_MARKER).is_symlink())
        self.assertFalse(target.exists())   # did not write through the symlink
        for d in (as_dir, dangling):
            self.assertEqual([p for p in d.iterdir() if p.name.endswith('.tmp')], [])


# 6-9 ---------------------------------------------------------------------------------
class ValidateOutputDirTest(TempTree):
    def assertRejected(self, output, fragment):
        with self.assertRaises(ValueError) as cm: validate_output_dir(self.source, output)
        self.assertIn(fragment, str(cm.exception))

    def test_output_inside_source_rejected(self):                                       # 6
        for out in (self.source, self.source / 'review', self.source / 'DCIM' / 'new' / 'deep',
                    self.source / 'DCIM' / '..' / 'review'):
            with self.subTest(out=out): self.assertRejected(out, 'inside the source folder')
        # Symlink outside the source that points back into it.
        link = self.symlink(self.tmp / 'sneaky', self.source / 'DCIM')
        self.assertRejected(link, 'inside the source folder')
        self.assertRejected(link / 'child', 'inside the source folder')
        # Relative path resolved against the current directory.
        cwd = Path.cwd()
        try:
            os.chdir(self.source)
            self.assertRejected('review', 'inside the source folder')
        finally:
            os.chdir(cwd)
        self.assertFalse((self.source / 'review').exists())   # validation never creates anything

    def test_output_containing_source_rejected(self):                                   # 7
        for out in (self.tmp, self.tmp.parent, Path(self.tmp.anchor)):
            with self.subTest(out=out): self.assertRejected(out, 'contains the source folder')
        link = self.symlink(self.elsewhere / 'up', self.tmp)
        self.assertRejected(link, 'contains the source folder')
        self.assertFalse((self.tmp / OUTPUT_MARKER).exists())

    def test_output_outside_source_accepted(self):                                      # 8
        self.assertEqual(validate_output_dir(self.source, self.out), self.out)          # missing
        self.assertFalse(self.out.exists())
        empty = self.tmp / 'empty'; empty.mkdir()
        self.assertEqual(validate_output_dir(self.source, empty), empty)                # empty
        sibling = self.tmp / 'source-review'      # shares a name prefix, not a path prefix
        self.assertEqual(validate_output_dir(self.source, sibling), sibling)
        touch(self.out / 'previews' / 'old.jpg'); mark_output_dir(self.out)
        self.assertEqual(validate_output_dir(self.source, self.out), self.out)          # marked rerun
        link = self.symlink(self.tmp / 'out-link', self.out)
        self.assertEqual(validate_output_dir(self.source, link), self.out)              # resolved

    def test_non_empty_unmarked_output_rejected(self):                                  # 9
        user = self.elsewhere / 'Holiday'; touch(user / 'IMG_0001.JPG')
        self.assertRejected(user, 'not a Photo Curator output folder')
        hidden_only = self.tmp / 'hidden'; touch(hidden_only / '.DS_Store')
        self.assertRejected(hidden_only, 'not a Photo Curator output folder')
        a_file = touch(self.tmp / 'file.txt')
        self.assertRejected(a_file, 'not a directory')
        self.assertFalse((user / OUTPUT_MARKER).exists())   # never marked by validation

    def test_cli_rejects_unsafe_output_before_writing(self):                            # 6/7/9 via CLI
        touch(self.source / 'a.jpg')
        user = self.elsewhere / 'Holiday'; touch(user / 'IMG_0001.JPG')
        before = sorted(self.tmp.rglob('*'))
        for out in (self.source / 'review', self.tmp, user):
            args = argparse.Namespace(folder=str(self.source), output=str(out), no_recursive=False, limit=None)
            with self.subTest(out=out), mock.patch.object(cli, 'make_preview') as mp, \
                 mock.patch.object(cli, 'analyze_batch') as ab:
                with self.assertRaises(SystemExit) as cm: cli.cmd_analyze(args)
                self.assertIn('Unsafe --output', str(cm.exception))
                mp.assert_not_called(); ab.assert_not_called()
        self.assertEqual(sorted(self.tmp.rglob('*')), before)


# 10-11 -------------------------------------------------------------------------------
class SymlinkTest(TempTree):
    def test_symlinks_into_generated_output_excluded(self):                             # 10
        preview = touch(self.out / 'previews' / 'DSC00001__abc.jpg'); mark_output_dir(self.out)
        legacy = touch(self.source / GENERATED_DIR / 'previews' / 'DSC00002__def.jpg')   # no marker
        self.symlink(self.source / 'link-to-preview.jpg', preview)
        self.symlink(self.source / 'link-to-legacy.jpg', legacy)
        dir_link = self.symlink(self.source / 'linked-previews', self.out / 'previews')
        self.assertEqual(find_images(self.source), [self.original])
        for p in (self.source / 'link-to-preview.jpg', self.source / 'link-to-legacy.jpg',
                  dir_link / 'DSC00001__abc.jpg'):
            with self.subTest(p=p): self.assertTrue(in_generated_output(p))

    def test_symlinks_resolving_outside_generated_output(self):                         # 11
        real = touch(self.elsewhere / 'IMG_0100.JPG')
        # (a) Link in the source to an ordinary photo elsewhere: discovered as its real path.
        self.symlink(self.source / 'shortcut.jpg', real)
        self.assertEqual(find_images(self.source), sorted([self.original, real]))
        self.assertFalse(in_generated_output(self.source / 'shortcut.jpg'))
        # (b) Link placed inside generated output that points at a real original: the
        #     original stays discoverable once via the source; the link itself is never a
        #     source, and as an apply target it is refused because of where it sits.
        mark_output_dir(self.out)
        via_out = self.symlink(self.out / 'previews' / 'DSC00001.ARW', self.original)
        self.assertEqual(find_images(self.out), [])
        self.assertEqual(find_images(self.tmp).count(self.original), 1)
        self.assertTrue(in_generated_output(via_out))
        self.assertFalse(in_generated_output(self.original))
        # (c) A linked directory pointing at an unmarked folder is not treated as output.
        dlink = self.symlink(self.tmp / 'plain-link', self.elsewhere)
        self.assertFalse(in_generated_output(dlink / 'IMG_0100.JPG'))


# 12-13 -------------------------------------------------------------------------------
class ApplyTargetTest(TempTree):
    def apply(self, results: list[dict]) -> tuple[list[Path], str]:
        results_dir = self.tmp / 'results'; results_dir.mkdir(exist_ok=True)
        path = results_dir / 'ratings-review.json'
        path.write_text(json.dumps({'schema_version': 2, 'results': results, 'errors': []}), encoding='utf-8')
        args = argparse.Namespace(results=str(path), min_rating=1, include_existing=False, yes=True)
        # W-B (X-4): the `apply` command runs apply_command (apply_v3); the mock moves to its writer.
        from photo_curator import apply_v3
        from photo_curator.sidecar_v3 import SidecarResult
        written: list[Path] = []
        def fake_write(img, rating, now=None):
            written.append(Path(img)); return SidecarResult(str(img) + '.xmp', None)
        out = io.StringIO()
        with mock.patch.object(apply_v3, 'write_sidecar', side_effect=fake_write), contextlib.redirect_stdout(out):
            cli.apply_command(args)
        return written, out.getvalue()

    @staticmethod
    def row(p: Path, rating: int = 4) -> dict:
        return {'path': str(p), 'original_path': str(p), 'rating': rating, 'decision': 'strong'}

    def test_apply_rejects_targets_in_marked_dirs(self):                                # 12
        mark_output_dir(self.out); mark_output_dir(self.out / 'previews')
        in_out = touch(self.out / 'previews' / 'DSC00001__abc.jpg')
        in_out_root = touch(self.out / 'stray.JPG')
        nested = self.source / 'exports' / 'generated'; mark_output_dir(nested)
        in_nested = touch(nested / 'sub' / 'IMG_1.jpg')
        via_link = self.symlink(self.source / 'link.jpg', in_out)
        via_dir_link = self.symlink(self.elsewhere / 'outlink', self.out / 'previews') / 'DSC00001__abc.jpg'
        bad = [in_out, in_out_root, in_nested, via_link, via_dir_link]
        written, log = self.apply([self.row(self.original)] + [self.row(p) for p in bad])
        self.assertEqual(written, [self.original])   # positive control: real original still applied
        for p in bad:
            with self.subTest(p=p): self.assertIn(f'[SKIP inside generated output] {p}', log)
        self.assertIn('Applied 1; skipped 5.', log)

    def test_apply_rejects_preview_path_resolving_to_target(self):                     # 12 (edge)
        # A row whose recorded preview is the same file as its target is never applied.
        link = self.symlink(self.elsewhere / 'alias.ARW', self.original)
        row = {**self.row(link), 'preview_path': str(self.original)}
        written, log = self.apply([row])
        self.assertEqual(written, [])
        self.assertIn('[SKIP inside generated output]', log)

    def test_legacy_photo_curator_paths_protected(self):                               # 13
        legacy = self.source / GENERATED_DIR
        prev = touch(legacy / 'previews' / 'DSC00001__abc.jpg')    # legacy layout: no marker
        stray = touch(legacy / 'IMG_9.JPG')
        other = touch(self.elsewhere / GENERATED_DIR / 'x.tif')     # any .photo-curator, anywhere
        self.assertFalse((legacy / OUTPUT_MARKER).exists())
        self.assertEqual(find_images(self.source), [self.original])
        self.assertEqual(find_images(legacy), [])
        self.assertEqual(find_images(self.elsewhere), [])
        for p in (prev, stray, other): self.assertTrue(in_generated_output(p), p)
        written, log = self.apply([self.row(self.original), self.row(prev), self.row(stray), self.row(other),
                                   {'path': str(prev), 'rating': 5}])          # v1-style row, path only
        self.assertEqual(written, [self.original])
        self.assertEqual(log.count('[SKIP not an original image]'), 4)
        self.assertIn('Applied 1; skipped 4.', log)


if __name__ == '__main__':
    unittest.main()
