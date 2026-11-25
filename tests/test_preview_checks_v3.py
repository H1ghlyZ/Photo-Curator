"""S11: preview checks PV1-PV6 (spec 12.5, V42; spec 20 item 20; PV6 per approved deviation B-11c).

Each check fails on its own in a synthetic folder. The consequences named in item 20 are
asserted through the reader (S11): apply skip message, approve refusal (V42) and the
``--open-preview`` refusal constant (the command is wired in W-B).
"""
from __future__ import annotations
import os, tempfile, unittest
from pathlib import Path

from photo_curator.core import OUTPUT_MARKER
from photo_curator.preview_checks_v3 import (APPLY_SKIP_MESSAGE, CHECK_IDS, OPEN_PREVIEW_REFUSAL, check_preview,
                                             expected_preview_name, in_valid_generated_previews,
                                             open_preview_decision, pv1, pv2, pv3, pv4, pv5, pv6)


class Base(unittest.TestCase):
    """A library with originals and two output layouts: default ``<source>/.photo-curator`` and
    an external ``--output`` folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s11-pv-')
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.source = self.tmp / 'library'
        (self.source / 'DCIM').mkdir(parents=True)
        self.original = str(self.source / 'DCIM' / 'DSC00001.ARW')
        Path(self.original).write_bytes(b'raw')
        self.default_work = self.make_work(self.source / '.photo-curator')
        self.external_work = self.make_work(self.tmp / 'review-out')

    def make_work(self, work: Path) -> Path:
        (work / 'previews').mkdir(parents=True)
        (work / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        (work / 'previews' / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        (work / 'previews' / expected_preview_name(self.original)).write_bytes(b'\xff\xd8 preview')
        return work

    def preview(self, work: Path) -> str:
        return str(work / 'previews' / expected_preview_name(self.original))

    def check(self, work: Path, preview: str | None = None, original: str | None = None):
        return check_preview(preview or self.preview(work), work, original or self.original, str(self.source))


class ValidTest(Base):
    def test_external_output_passes(self):
        self.assertTrue(self.check(self.external_work).ok)

    def test_default_output_inside_source_passes_b11c(self):
        result = self.check(self.default_work)
        self.assertTrue(result.ok, result.failures)
        self.assertTrue(in_valid_generated_previews(self.preview(self.default_work), self.default_work))

    def test_ids_in_order(self):
        self.assertEqual(CHECK_IDS, ('PV1', 'PV2', 'PV3', 'PV4', 'PV5', 'PV6'))


class EachCheckFailsAloneTest(Base):
    """Item 20: PV1-PV6 each failing on its own, on the external layout (and PV6 on both)."""

    def assert_only(self, result, check_id):
        self.assertEqual(result.failed_ids, (check_id,), result.failures)

    def test_pv1_wrong_folder(self):
        other = self.external_work / 'elsewhere'
        other.mkdir()
        moved = other / expected_preview_name(self.original)
        moved.write_bytes(b'\xff\xd8')
        self.assert_only(self.check(self.external_work, str(moved)), 'PV1')

    def test_pv1_relative_path(self):
        result = check_preview('previews/x.jpg', self.external_work, self.original, str(self.source))
        self.assertEqual(result.failed_ids, ('PV1',))

    def test_pv2_unmarked_previews(self):
        os.unlink(self.external_work / 'previews' / OUTPUT_MARKER)
        self.assert_only(self.check(self.external_work), 'PV2')

    def test_pv2_marker_is_a_symlink(self):
        marker = self.external_work / 'previews' / OUTPUT_MARKER
        os.unlink(marker)
        os.symlink(self.external_work / OUTPUT_MARKER, marker)
        self.assert_only(self.check(self.external_work), 'PV2')

    def test_pv3_symlink(self):
        preview = Path(self.preview(self.external_work))
        target = self.external_work / 'real.bin'
        os.replace(preview, target)
        os.symlink(target, preview)
        self.assert_only(self.check(self.external_work), 'PV3')

    def test_pv3_missing(self):
        os.unlink(self.preview(self.external_work))
        self.assert_only(self.check(self.external_work), 'PV3')

    def test_pv4_wrong_extension(self):
        # A .JPG name also breaks PV5 (the derived name ends in .jpg); use a derived stem with .jpeg
        # and check that PV4 is reported, then the exact-only case via original whose name matches.
        name = expected_preview_name(self.original)[:-4] + '.JPG'
        path = self.external_work / 'previews' / name
        path.write_bytes(b'\xff\xd8')
        result = self.check(self.external_work, str(path))
        self.assertIn('PV4', result.failed_ids)
        self.assertNotIn('PV1', result.failed_ids)
        self.assertNotIn('PV3', result.failed_ids)

    def test_pv5_wrong_derived_name(self):
        path = self.external_work / 'previews' / 'DSC00001__0000000000.jpg'
        path.write_bytes(b'\xff\xd8')
        self.assert_only(self.check(self.external_work, str(path)), 'PV5')

    def test_pv6_preview_equal_to_original(self):
        # The original itself is given as preview_path: PV6 is reported (PV1 and PV5 too, since
        # the original is not in previews; the point is that PV6 is never skipped).
        result = self.check(self.external_work, self.original)
        self.assertIn('PV6', result.failed_ids)

    def test_pv6_inside_source_outside_generated_output(self):
        """B-11c: a 'work' inside the source without valid markers is not exempt."""
        work = self.source / 'not-output'
        (work / 'previews').mkdir(parents=True)
        (work / 'previews' / OUTPUT_MARKER).write_text('marker', encoding='utf-8')   # previews marked
        preview = work / 'previews' / expected_preview_name(self.original)
        preview.write_bytes(b'\xff\xd8')
        self.assert_only(check_preview(str(preview), work, self.original, str(self.source)), 'PV6')

    def test_pv6_default_output_with_symlinked_previews_escaping(self):
        """B-11c (4): a symlinked ``previews`` is never exempt, even under .photo-curator."""
        work = self.source / '.photo-curator-2'
        work.mkdir()
        (work / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        real = self.source / 'DCIM' / 'hidden'
        real.mkdir()
        (real / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        os.symlink(real, work / 'previews')
        preview = work / 'previews' / expected_preview_name(self.original)
        preview.write_bytes(b'\xff\xd8')
        result = check_preview(str(preview), work, self.original, str(self.source))
        self.assertIn('PV6', result.failed_ids)
        self.assertFalse(in_valid_generated_previews(str(preview), work))

    def test_pv6_not_exempt_by_folder_name(self):
        """A folder named .photo-curator without the work marker is not valid generated output."""
        os.unlink(self.default_work / OUTPUT_MARKER)
        self.assert_only(self.check(self.default_work), 'PV6')

    def test_pv6_traversal_resolves_outside(self):
        escaped = str(self.default_work / 'previews' / '..' / '..' / 'DCIM' / expected_preview_name(self.original))
        Path(escaped).resolve().write_bytes(b'\xff\xd8')
        result = self.check(self.default_work, escaped)
        self.assertIn('PV6', result.failed_ids)
        self.assertIn('PV1', result.failed_ids)


class PredicateUnitTest(Base):
    """Each predicate on its own (pass and fail), independent of the others. Integration tests
    above stay unchanged; there PV4 always comes with PV5 and PV6-equality with PV1/PV5, because
    the derived name ends in .jpg and an original can't carry its own derived name."""

    def test_pv1(self):
        self.assertIsNone(pv1(self.preview(self.external_work), self.external_work))
        self.assertEqual(pv1('rel/x.jpg', self.external_work), 'preview_path is not an absolute path')
        self.assertIsNotNone(pv1(str(self.external_work / 'x.jpg'), self.external_work))

    def test_pv2(self):
        self.assertIsNone(pv2(self.external_work))
        os.unlink(self.external_work / 'previews' / OUTPUT_MARKER)
        self.assertIsNotNone(pv2(self.external_work))

    def test_pv3(self):
        self.assertIsNone(pv3(self.preview(self.external_work)))
        self.assertIsNotNone(pv3(str(self.external_work / 'previews' / 'missing.jpg')))
        self.assertIsNotNone(pv3(str(self.external_work / 'previews')))           # a directory

    def test_pv4_alone(self):
        self.assertIsNone(pv4('/w/previews/a.jpg'))
        for name in ('/w/previews/a.JPG', '/w/previews/a.jpeg', '/w/previews/a.png', '/w/previews/a'):
            with self.subTest(name=name):
                self.assertEqual(pv4(name), 'preview file name does not end in .jpg')

    def test_pv5(self):
        good = '/w/previews/' + expected_preview_name(self.original)
        self.assertIsNone(pv5(good, self.original))
        self.assertIsNotNone(pv5('/w/previews/DSC00001__0000000000.jpg', self.original))
        self.assertIsNotNone(pv5(good, None))

    def test_pv6_alone(self):
        self.assertIsNone(pv6(self.preview(self.external_work), self.external_work, self.original, str(self.source)))
        self.assertIsNone(pv6(self.preview(self.default_work), self.default_work, self.original, str(self.source)))
        self.assertEqual(pv6(self.original, self.external_work, self.original, str(self.source)),
                         'preview is the original')                               # equality, on its own
        os.unlink(self.default_work / OUTPUT_MARKER)
        self.assertEqual(pv6(self.preview(self.default_work), self.default_work, self.original, str(self.source)),
                         'preview lies inside the source folder outside valid generated output')

    def test_check_preview_is_the_union_of_the_predicates(self):
        name = expected_preview_name(self.original)[:-4] + '.JPG'
        path = str(self.external_work / 'previews' / name)
        Path(path).write_bytes(b'\xff\xd8')
        result = self.check(self.external_work, path)
        expected = [c for c, fail in (('PV1', pv1(path, self.external_work)), ('PV2', pv2(self.external_work)),
                                      ('PV3', pv3(path)), ('PV4', pv4(path)), ('PV5', pv5(path, self.original)),
                                      ('PV6', pv6(path, self.external_work, self.original, str(self.source))))
                    if fail is not None]
        self.assertEqual(list(result.failed_ids), expected)
        self.assertEqual(expected, ['PV4', 'PV5'])                               # documented dependency


class OpenPreviewDecisionTest(Base):
    """Helper-level contract for --open-preview (S11). The command itself is wired in W-B."""

    def test_valid_may_open(self):
        self.assertEqual(open_preview_decision(self.check(self.external_work)), (True, None))
        self.assertEqual(open_preview_decision(self.check(self.default_work)), (True, None))

    def test_each_pv_failure_refused(self):
        def make(check):
            work = self.external_work
            if check == 'PV1':
                other = work / 'other'
                other.mkdir()
                p = other / expected_preview_name(self.original)
                p.write_bytes(b'\xff\xd8')
                return self.check(work, str(p))
            if check == 'PV2':
                os.unlink(work / 'previews' / OUTPUT_MARKER)
                return self.check(work)
            if check == 'PV3':
                os.unlink(self.preview(work))
                return self.check(work)
            if check == 'PV4':
                p = work / 'previews' / 'x.png'
                p.write_bytes(b'x')
                return self.check(work, str(p))
            if check == 'PV5':
                p = work / 'previews' / 'DSC00001__0000000000.jpg'
                p.write_bytes(b'x')
                return self.check(work, str(p))
            os.unlink(self.default_work / OUTPUT_MARKER)                          # PV6
            return self.check(self.default_work)
        for check in CHECK_IDS:
            with self.subTest(check=check):
                self.setUp()
                result = make(check)
                self.assertIn(check, result.failed_ids)
                allowed, message = open_preview_decision(result)
                self.assertFalse(allowed)
                self.assertTrue(message.startswith(OPEN_PREVIEW_REFUSAL))
                self.assertIn(check, message)

    def test_never_opens_files_or_runs_programs(self):
        import builtins, subprocess
        from unittest import mock
        result = self.check(self.external_work)
        with mock.patch.object(builtins, 'open', side_effect=AssertionError('opened')), \
             mock.patch.object(os, 'open', side_effect=AssertionError('opened')), \
             mock.patch.object(subprocess, 'run', side_effect=AssertionError('ran')), \
             mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('ran')):
            open_preview_decision(result)
        with self.assertRaises(TypeError):
            open_preview_decision('ok')


class ConsequenceConstantsTest(unittest.TestCase):
    def test_messages(self):
        self.assertEqual(APPLY_SKIP_MESSAGE, '[SKIP approval unverifiable: preview invalid]')
        self.assertIn('refusing to open the preview', OPEN_PREVIEW_REFUSAL)


if __name__ == '__main__':
    unittest.main()
