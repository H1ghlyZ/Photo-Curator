"""S8: original integrity during preview generation (spec 14.3a; spec 20 item 16c, analysis part).

A test double replaces the preview generator; it writes a synthetic preview into a marked
``previews`` folder and can change the original between the two hashes. No darktable, no
Claude, no real photos.
"""
from __future__ import annotations
import os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import hashing_v3
from photo_curator.core import OUTPUT_MARKER
from photo_curator.hashing_v3 import (ERROR_CONTENT, ERROR_METADATA, ERROR_MISSING, ERROR_UNREADABLE,
                                      check_original_with_preview, original_error_entry, sha256_file)
from photo_curator.schema_v3 import classify_error_entry


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s8-int-')
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name))
        self.library = root / 'library'
        self.library.mkdir()
        self.previews = root / 'out' / 'previews'
        self.previews.mkdir(parents=True)
        (self.previews / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        self.original = self.library / 'DSC00001.ARW'
        self.original.write_bytes(b'original raw content ' * 500)

    def generator(self, mutate=None):
        """Preview double: writes previews/<stem>__x.jpg, then optionally mutates the original."""
        calls = []

        def generate(original):
            calls.append(original)
            preview = self.previews / f'{Path(original).stem}__0123456789.jpg'
            preview.write_bytes(b'\xff\xd8 preview')
            if mutate:
                mutate(Path(original))
            return preview
        return generate, calls


class AcceptTest(Base):
    def test_unchanged_original_accepted_with_h1(self):
        expected = sha256_file(self.original)
        generate, calls = self.generator()
        outcome = check_original_with_preview(self.original, generate)
        self.assertTrue(outcome.accepted)
        self.assertEqual(outcome.original_sha256, expected)
        self.assertIsNone(outcome.error)
        self.assertEqual(calls, [self.original])

    def test_hashes_exactly_twice_no_skip_option(self):
        counted = mock.Mock(wraps=hashing_v3.sha256_file)
        generate, _ = self.generator()
        with mock.patch.object(hashing_v3, 'sha256_file', counted):
            outcome = check_original_with_preview(self.original, generate)
        self.assertTrue(outcome.accepted)
        self.assertEqual(counted.call_count, 2)
        import inspect
        self.assertEqual(list(inspect.signature(check_original_with_preview).parameters),
                         ['original', 'generate_preview'])                  # no skip/fast option

    def test_original_never_opened_for_writing_and_unchanged(self):
        before = (self.original.read_bytes(), os.stat(self.original).st_mtime_ns)
        real_open, flags = os.open, []
        generate, _ = self.generator()
        with mock.patch.object(hashing_v3.os, 'open',
                               side_effect=lambda p, f, *a: (flags.append((p, f)), real_open(p, f, *a))[1]):
            check_original_with_preview(self.original, generate)
        writes = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        self.assertEqual(len(flags), 2)
        self.assertTrue(all(f & writes == 0 for _, f in flags))
        self.assertEqual((self.original.read_bytes(), os.stat(self.original).st_mtime_ns), before)


class RejectTest(Base):
    def assert_rejected(self, outcome, error, *, preview_expected: bool):
        self.assertEqual(outcome.stage, 'original')
        self.assertEqual(outcome.error, error)
        self.assertIsNone(outcome.original_sha256)
        entry = original_error_entry(str(self.original), outcome.error, capture_time=None)
        self.assertEqual(entry['stage'], 'original')
        self.assertIs(entry['sent_to_model'], False)
        self.assertIsNone(entry['batch_index'])
        self.assertIsNone(entry['preview_path'])
        self.assertIsNone(entry['result'])
        self.assertEqual(classify_error_entry(entry), {'file_refusal': [], 'identity_unknown': [], 'warnings': []})
        previews = [p.name for p in self.previews.iterdir() if p.name != OUTPUT_MARKER]
        self.assertEqual(bool(previews), preview_expected)               # orphan preview left, never deleted

    def test_content_changed(self):
        generate, _ = self.generator(mutate=lambda p: p.write_bytes(b'edited by a sync tool'))
        self.assert_rejected(check_original_with_preview(self.original, generate), ERROR_CONTENT,
                             preview_expected=True)

    def test_metadata_changed_same_content(self):
        def touch(p):
            st = os.stat(p)
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
        generate, _ = self.generator(mutate=touch)
        self.assert_rejected(check_original_with_preview(self.original, generate), ERROR_METADATA,
                             preview_expected=True)

    def test_inode_changed_same_content_and_mtime(self):
        def swap(p):
            st = os.stat(p)
            copy = p.with_name('copy.tmp')
            copy.write_bytes(p.read_bytes())
            os.utime(copy, ns=(st.st_atime_ns, st.st_mtime_ns))
            os.replace(copy, p)
        generate, _ = self.generator(mutate=swap)
        self.assert_rejected(check_original_with_preview(self.original, generate), ERROR_METADATA,
                             preview_expected=True)

    def test_read_failure_at_step_2(self):
        generate, calls = self.generator()
        with mock.patch.object(hashing_v3, 'sha256_file', side_effect=hashing_v3.HashError('x')):
            outcome = check_original_with_preview(self.original, generate)
        self.assert_rejected(outcome, ERROR_UNREADABLE, preview_expected=False)
        self.assertEqual(calls, [])                                      # no preview generated

    def test_read_failure_at_step_5(self):
        real, count = hashing_v3.sha256_file, []

        def second_fails(path):
            count.append(path)
            if len(count) == 2:
                raise hashing_v3.HashError('x')
            return real(path)
        generate, _ = self.generator()
        with mock.patch.object(hashing_v3, 'sha256_file', side_effect=second_fails):
            outcome = check_original_with_preview(self.original, generate)
        self.assert_rejected(outcome, ERROR_UNREADABLE, preview_expected=True)

    def test_original_deleted_during_preview(self):
        generate, _ = self.generator(mutate=lambda p: p.unlink())
        self.assert_rejected(check_original_with_preview(self.original, generate), ERROR_UNREADABLE,
                             preview_expected=True)

    def test_not_a_regular_file_at_step_1(self):
        link = self.library / 'link.ARW'
        os.symlink(self.original, link)
        folder = self.library / 'folder.ARW'
        folder.mkdir()
        for path in (link, folder, self.library / 'missing.ARW'):
            with self.subTest(path=path.name):
                generate, calls = self.generator()
                outcome = check_original_with_preview(path, generate)
                self.assertEqual((outcome.stage, outcome.error), ('original', ERROR_MISSING))
                self.assertEqual(calls, [])

    def test_unhashed_rejection_never_sends_or_references_preview(self):
        generate, _ = self.generator(mutate=lambda p: p.write_bytes(b'changed'))
        outcome = check_original_with_preview(self.original, generate)
        entry = original_error_entry(str(self.original), outcome.error)
        self.assertIsNone(entry['preview_path'])
        self.assertTrue(Path(outcome.preview).exists())


class PreviewFailureTest(Base):
    def test_generator_failure_is_preview_stage(self):
        def broken(original):
            raise RuntimeError('darktable-cli failed')
        counted = mock.Mock(wraps=hashing_v3.sha256_file)
        with mock.patch.object(hashing_v3, 'sha256_file', counted):
            outcome = check_original_with_preview(self.original, broken)
        self.assertEqual(outcome.stage, 'preview')
        self.assertIn('darktable-cli failed', outcome.error)
        self.assertEqual(counted.call_count, 1)                           # second hash not run


if __name__ == '__main__':
    unittest.main()
