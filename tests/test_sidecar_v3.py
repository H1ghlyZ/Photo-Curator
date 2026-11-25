"""S14: sidecar write procedure (spec 13.2, 13.1 check 11a; spec 20 item 19a). Synthetic files in
temporary folders only; the 'original' is a small dummy file that is never written."""
from __future__ import annotations
import builtins, hashlib, os, shutil, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from photo_curator import atomic_v3, core, sidecar_v3
from photo_curator.sidecar_v3 import (NOT_DURABLE, VERIFY_FAILED, WRITE_FAILED, SidecarError, build_content,
                                      holds_rating, sidecar_not_regular, write_sidecar)

NOW = datetime(2026, 11, 15, 9, 0, 0, 1, tzinfo=timezone.utc)
EXISTING = (b"<?xml version='1.0' encoding='utf-8'?>\n<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" "
            b"xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\" xmlns:xmp=\"http://ns.adobe.com/xap/1.0/\">"
            b"<rdf:RDF><rdf:Description rdf:about=\"\" xmp:Label=\"Red\" xmp:Rating=\"2\" /></rdf:RDF></x:xmpmeta>")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s14-sc-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.original = self.root / 'DSC00001.ARW'
        self.original.write_bytes(b'raw original bytes')
        self.sidecar = Path(str(self.original) + '.xmp')

    def original_state(self):
        return self.original.read_bytes(), os.stat(self.original).st_mtime_ns

    def temps(self):
        return [n for n in os.listdir(self.root) if '.tmp-' in n]

    def backups(self):
        return sorted(n for n in os.listdir(self.root) if n.endswith('.photo-curator.bak'))


class ContentTest(Base):
    def test_same_bytes_as_todays_writer(self):
        twin = self.root / 'twin'
        twin.mkdir()
        for existing in (None, EXISTING):
            with self.subTest(existing=existing is not None):
                image = twin / ('A.ARW' if existing is None else 'B.ARW')
                image.write_bytes(b'x')
                if existing is not None:
                    Path(str(image) + '.xmp').write_bytes(existing)
                core.write_rating_sidecar(image, 4, backup=False)
                self.assertEqual(build_content(existing, 4), Path(str(image) + '.xmp').read_bytes())

    def test_only_rating_changes(self):
        data = build_content(EXISTING, 5)
        self.assertTrue(holds_rating(data, 5))
        self.assertIn(b'xmp:Label="Red"', data)


class SuccessTest(Base):
    def test_new_sidecar(self):
        before = self.original_state()
        result = write_sidecar(self.original, 3, now=NOW)
        self.assertIsNone(result.backup)
        self.assertTrue(holds_rating(self.sidecar.read_bytes(), 3))
        self.assertEqual(self.temps(), [])
        self.assertEqual(self.original_state(), before)

    def test_existing_sidecar_backed_up_and_verified(self):
        self.sidecar.write_bytes(EXISTING)
        result = write_sidecar(self.original, 5, now=NOW)
        self.assertRegex(Path(result.backup).name, r'^DSC00001\.ARW\.xmp\.\d{8}T\d{6}\.\d{6}Z\.photo-curator\.bak$')
        self.assertEqual(Path(result.backup).read_bytes(), EXISTING)
        self.assertTrue(holds_rating(self.sidecar.read_bytes(), 5))

    def test_folder_fsynced(self):
        with mock.patch.object(atomic_v3, '_fsync_dir', wraps=atomic_v3._fsync_dir) as fsync_dir:
            write_sidecar(self.original, 3, now=NOW)
        fsync_dir.assert_called_once_with(str(self.root))


class Check11aTest(Base):
    def test_not_regular(self):
        self.assertFalse(sidecar_not_regular(self.original))                          # absent: fine
        self.sidecar.write_bytes(EXISTING)
        self.assertFalse(sidecar_not_regular(self.original))                          # regular: fine
        self.sidecar.unlink()
        os.symlink(self.root / 'elsewhere.xmp', self.sidecar)
        self.assertTrue(sidecar_not_regular(self.original))                           # symlink (dangling)
        self.sidecar.unlink()
        self.sidecar.mkdir()
        self.assertTrue(sidecar_not_regular(self.original))                           # directory
        # "sidecar equals the original" cannot occur by construction (sidecar = original + '.xmp');
        # the guard exists and is exercised by the path comparison above returning False.
        self.assertNotEqual(sidecar_v3.sidecar_path(self.original), str(self.original))


class BackupFailureTest(Base):
    def assert_stopped(self, contains):
        before = self.sidecar.read_bytes()
        with self.assertRaises(SidecarError) as ctx:
            write_sidecar(self.original, 4, now=NOW)
        self.assertEqual(ctx.exception.kind, 'before_replace')
        self.assertTrue(ctx.exception.message.startswith(WRITE_FAILED))
        self.assertIn(contains, ctx.exception.message)
        self.assertEqual(self.sidecar.read_bytes(), before)                          # byte-identical
        return ctx.exception

    def test_existing_backup_name(self):
        self.sidecar.write_bytes(EXISTING)
        clash = Path(f'{self.sidecar}.20261115T090000.000001Z.photo-curator.bak')
        clash.write_bytes(b'older')
        self.assert_stopped('backup could not be created')
        self.assertEqual(clash.read_bytes(), b'older')

    def test_hash_mismatch_removes_partial(self):
        self.sidecar.write_bytes(EXISTING)
        real = hashlib.sha256
        calls = []

        def fake(data=b''):
            calls.append(1)
            return real(b'different' if len(calls) == 1 else data)
        with mock.patch.object(sidecar_v3.hashlib, 'sha256', side_effect=fake):
            self.assert_stopped('backup failed')
        self.assertEqual(self.backups(), [])

    def test_unwritable_folder(self):
        self.sidecar.write_bytes(EXISTING)
        os.chmod(self.root, 0o555)
        self.addCleanup(os.chmod, self.root, 0o755)
        if os.access(self.root, os.W_OK):
            self.skipTest('permissions ignored')
        self.assert_stopped('backup could not be created')


class BeforeReplacementTest(Base):
    def test_each_failure_leaves_sidecar_unchanged_and_no_temp(self):
        for existing in (None, EXISTING):
            for label, patch in (('temporary write', ('_write_all', OSError(5, 'x'))),
                                 ('temporary fsync', ('_fsync_file', OSError(5, 'x'))),
                                 ('replace raises', ('_replace', OSError(5, 'x')))):
                with self.subTest(label=label, existing=existing is not None):
                    self.setUp()
                    if existing:
                        self.sidecar.write_bytes(existing)
                    with mock.patch.object(atomic_v3, patch[0], side_effect=patch[1]), \
                         self.assertRaises(SidecarError) as ctx:
                        write_sidecar(self.original, 4, now=NOW)
                    self.assertEqual((ctx.exception.kind, ctx.exception.message), ('before_replace', f'{WRITE_FAILED} DSC00001.ARW.xmp'))
                    self.assertEqual(self.temps(), [])
                    if existing:
                        self.assertEqual(self.sidecar.read_bytes(), existing)
                        self.assertEqual(Path(ctx.exception.backup).read_bytes(), existing)   # backup kept
                    else:
                        self.assertFalse(self.sidecar.exists())

    def test_read_back_check_failure(self):
        with mock.patch.object(sidecar_v3, 'holds_rating', return_value=False), self.assertRaises(SidecarError) as ctx:
            write_sidecar(self.original, 4, now=NOW)
        self.assertEqual(ctx.exception.kind, 'before_replace')
        self.assertFalse(self.sidecar.exists())
        self.assertEqual(self.temps(), [])

    def test_invalid_existing_xml_left_untouched(self):
        self.sidecar.write_bytes(b'<not xml')
        with self.assertRaises(SidecarError) as ctx:
            write_sidecar(self.original, 4, now=NOW)
        self.assertIn('invalid XML', ctx.exception.message)
        self.assertEqual(self.sidecar.read_bytes(), b'<not xml')

    def test_temporary_file_left_behind_is_reported(self):
        real_unlink = os.unlink
        with mock.patch.object(atomic_v3, '_replace', side_effect=OSError(5, 'x')), \
             mock.patch.object(atomic_v3.os, 'unlink', side_effect=OSError(13, 'denied')), \
             self.assertRaises(SidecarError) as ctx:
            write_sidecar(self.original, 4, now=NOW)
        self.assertIsNotNone(ctx.exception.leftover)
        self.assertTrue(os.path.exists(ctx.exception.leftover))                      # reported, not deleted
        real_unlink(ctx.exception.leftover)


class AfterReplacementTest(Base):
    def test_durability_failure_no_verification(self):
        self.sidecar.write_bytes(EXISTING)
        checks = mock.Mock(wraps=sidecar_v3.holds_rating)
        replace = mock.Mock(wraps=atomic_v3._replace)
        with mock.patch.object(atomic_v3, '_fsync_dir', side_effect=OSError(5, 'x')), \
             mock.patch.object(atomic_v3, '_replace', replace), \
             mock.patch.object(sidecar_v3, 'holds_rating', checks), self.assertRaises(SidecarError) as ctx:
            write_sidecar(self.original, 4, now=NOW)
        self.assertEqual((ctx.exception.kind, ctx.exception.message), ('durability', f'{NOT_DURABLE} DSC00001.ARW.xmp'))
        self.assertEqual(checks.call_count, 1)                                        # read-back only; step 7 not run
        self.assertEqual(replace.call_count, 1)                                       # no second replace
        self.assertEqual(Path(ctx.exception.backup).read_bytes(), EXISTING)           # backup kept

    def test_verification_failure_no_rollback(self):
        self.sidecar.write_bytes(EXISTING)
        results = iter([True, False])
        with mock.patch.object(sidecar_v3, 'holds_rating', side_effect=lambda *a: next(results)), \
             self.assertRaises(SidecarError) as ctx:
            write_sidecar(self.original, 4, now=NOW)
        self.assertEqual((ctx.exception.kind, ctx.exception.message), ('verification', f'{VERIFY_FAILED} DSC00001.ARW.xmp'))
        self.assertEqual(Path(ctx.exception.backup).read_bytes(), EXISTING)
        self.assertNotEqual(self.sidecar.read_bytes(), EXISTING)                      # no rollback


class OriginalSafetyTest(Base):
    def test_original_never_opened_for_writing(self):
        before = self.original_state()
        real_open, real_os_open, seen = builtins.open, os.open, []

        def watch_open(file, mode='r', *a, **k):
            seen.append((str(file), mode))
            return real_open(file, mode, *a, **k)

        def watch_os_open(path, flags, *a):
            seen.append((str(path), flags))
            return real_os_open(path, flags, *a)
        self.sidecar.write_bytes(EXISTING)
        with mock.patch.object(builtins, 'open', side_effect=watch_open), \
             mock.patch.object(os, 'open', side_effect=watch_os_open):
            write_sidecar(self.original, 4, now=NOW)
        self.assertFalse([s for s in seen if s[0] == str(self.original)])           # never opened at all
        self.assertEqual(self.original_state(), before)


if __name__ == '__main__':
    unittest.main()
