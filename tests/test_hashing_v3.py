"""S8: read-only SHA-256 of originals and lstat identity (spec 14.3a, 14.1).

Synthetic files in temporary folders only.
"""
from __future__ import annotations
import hashlib, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import hashing_v3
from photo_curator.hashing_v3 import FileIdentity, HashError, file_identity, sha256_file


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s8-hash-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.file = self.root / 'DSC00001.ARW'
        self.file.write_bytes(b'raw bytes ' * 1000)


class Sha256Test(Base):
    def test_matches_hashlib(self):
        self.assertEqual(sha256_file(self.file), hashlib.sha256(self.file.read_bytes()).hexdigest())

    def test_streams_large_file_in_chunks(self):
        big = self.root / 'big.ARW'
        big.write_bytes(os.urandom(3 * (1 << 20) + 17))
        real_read, sizes = os.read, []
        with mock.patch.object(hashing_v3.os, 'read', side_effect=lambda fd, n: (sizes.append(n), real_read(fd, n))[1]):
            digest = sha256_file(big)
        self.assertEqual(digest, hashlib.sha256(big.read_bytes()).hexdigest())
        self.assertGreaterEqual(len(sizes), 4)
        self.assertTrue(all(n == 1 << 20 for n in sizes))

    def test_empty_file(self):
        empty = self.root / 'empty.JPG'
        empty.write_bytes(b'')
        self.assertEqual(sha256_file(empty), hashlib.sha256(b'').hexdigest())

    def test_opened_read_only_never_for_writing(self):
        real_open, flags = os.open, []
        with mock.patch.object(hashing_v3.os, 'open', side_effect=lambda p, f, *a: (flags.append(f), real_open(p, f, *a))[1]):
            sha256_file(self.file)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), 0)
        self.assertTrue(flags[0] & os.O_NOFOLLOW)

    def test_file_unchanged_by_hashing(self):
        before = (self.file.read_bytes(), os.stat(self.file).st_mtime_ns)
        sha256_file(self.file)
        self.assertEqual((self.file.read_bytes(), os.stat(self.file).st_mtime_ns), before)

    def test_failures_raise_hash_error(self):
        os.symlink(self.file, self.root / 'link.ARW')
        (self.root / 'dir.ARW').mkdir()
        for path in (self.root / 'missing.ARW', self.root / 'link.ARW', self.root / 'dir.ARW'):
            with self.subTest(path=path.name), self.assertRaises(HashError):
                sha256_file(path)

    def test_read_and_close_errors_raise_hash_error(self):
        with mock.patch.object(hashing_v3.os, 'read', side_effect=OSError(5, 'Input/output error')), \
             self.assertRaises(HashError):
            sha256_file(self.file)
        real_close, closed = os.close, []

        def close(fd):
            real_close(fd)
            closed.append(fd)
            raise OSError(5, 'Input/output error')
        with mock.patch.object(hashing_v3.os, 'close', side_effect=close), self.assertRaises(HashError):
            sha256_file(self.file)
        self.assertEqual(len(closed), 1)                       # descriptor closed exactly once


class IdentityTest(Base):
    def test_regular_file(self):
        st = os.lstat(self.file)
        self.assertEqual(file_identity(self.file), FileIdentity(st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev))

    def test_not_regular_or_missing(self):
        os.symlink(self.file, self.root / 'link.ARW')
        (self.root / 'dir.ARW').mkdir()
        for path in (self.root / 'missing.ARW', self.root / 'link.ARW', self.root / 'dir.ARW'):
            with self.subTest(path=path.name):
                self.assertIsNone(file_identity(path))

    def test_detects_mtime_and_size_and_inode_change(self):
        first = file_identity(self.file)
        os.utime(self.file, ns=(first.mtime_ns + 1000, first.mtime_ns + 1000))
        self.assertNotEqual(file_identity(self.file), first)
        replacement = self.root / 'new'
        replacement.write_bytes(self.file.read_bytes())
        os.utime(replacement, ns=(first.mtime_ns, first.mtime_ns))
        os.replace(replacement, self.file)
        changed = file_identity(self.file)
        self.assertEqual((changed.size, changed.mtime_ns), (first.size, first.mtime_ns))
        self.assertNotEqual(changed.inode, first.inode)


if __name__ == '__main__':
    unittest.main()
