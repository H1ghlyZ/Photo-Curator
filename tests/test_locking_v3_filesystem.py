"""S6: network-filesystem detection (spec 12.6, decision U4; D6-2, D6-4, D6-5).

Synthetic mountinfo files in temporary folders; the real /proc/self/mountinfo is only used in
a smoke test that asserts nothing about its content.
"""
from __future__ import annotations
import os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import locking_v3
from photo_curator.locking_v3 import (NETWORK_FILESYSTEMS, NetworkFilesystemRefusal, check_filesystem,
                                      filesystem_type, parse_mountinfo)

EXPECTED_NETWORK = {'nfs', 'nfs4', 'cifs', 'smb3', 'smbfs', '9p', 'afs', 'ceph', 'glusterfs', 'lustre',
                    'davfs', 'fuse.sshfs', 'fuse.rclone', 'fuse.s3fs', 'fuse.gcsfuse'}


def encode(path: str) -> str:
    return path.replace('\\', '\\134').replace(' ', '\\040').replace('\t', '\\011').replace('\n', '\\012')


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s6-fs-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))

    def mountinfo(self, *mounts, raw_lines=()) -> str:
        lines = [f'{20 + i} 1 0:{20 + i} / {encode(str(mp))} rw,relatime shared:{i} - {fs} src{i} rw'
                 for i, (mp, fs) in enumerate(mounts)]
        path = self.root / f'mountinfo-{len(list(self.root.glob("mountinfo-*")))}'
        path.write_text('\n'.join(list(raw_lines) + lines) + '\n', encoding='utf-8')
        return str(path)

    def dir(self, *parts) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path


class NetworkListTest(Base):
    def test_frozen_list(self):
        self.assertEqual(NETWORK_FILESYSTEMS, EXPECTED_NETWORK)

    def test_every_network_type_refused_with_exact_message(self):
        work = self.dir('net', 'work')
        for fs in sorted(EXPECTED_NETWORK):
            with self.subTest(fs=fs):
                info = self.mountinfo(('/', 'ext4'), (self.root / 'net', fs))
                with self.assertRaises(NetworkFilesystemRefusal) as cm:
                    check_filesystem(work, info)
                self.assertEqual(str(cm.exception), f'output folder is on a network filesystem ({fs}); use a local folder')

    def test_local_and_unknown_types_proceed(self):
        work = self.dir('loc')
        for fs in ('ext4', 'btrfs', 'xfs', 'tmpfs', 'fuse', 'fuse.unknownfs', 'weirdfs', 'NFS'):
            with self.subTest(fs=fs):
                info = self.mountinfo(('/', 'ext4'), (work, fs))
                self.assertIsNone(check_filesystem(work, info))
                self.assertEqual(filesystem_type(work, info), fs)


class BestEffortTest(Base):
    def test_unreadable_mountinfo_proceeds(self):
        work = self.dir('w')
        self.assertIsNone(filesystem_type(work, str(self.root / 'missing-mountinfo')))
        self.assertIsNone(check_filesystem(work, str(self.root / 'missing-mountinfo')))
        with mock.patch('builtins.open', side_effect=PermissionError(13, 'denied')):
            self.assertIsNone(check_filesystem(work, '/proc/self/mountinfo'))

    def test_no_matching_mount(self):
        work = self.dir('w')
        info = self.mountinfo(('/elsewhere', 'nfs'))
        self.assertIsNone(filesystem_type(work, info))

    def test_malformed_lines_are_skipped(self):                                # D6-5
        work = self.dir('net')
        info = self.mountinfo(('/', 'ext4'), (work, 'nfs'),
                              raw_lines=['garbage', '1 2 3', f'9 1 0:9 / {work} rw - ', '', f'9 1 0:9 / rel rw - nfs x'])
        self.assertEqual(filesystem_type(work, info), 'nfs')

    def test_real_mountinfo_smoke(self):
        result = filesystem_type(self.root)
        self.assertTrue(result is None or isinstance(result, str))


class LongestMountTest(Base):
    def test_nested_mounts(self):
        info = self.mountinfo(('/', 'ext4'), (self.root / 'mnt', 'nfs'), (self.root / 'mnt' / 'local', 'xfs'))
        self.assertEqual(filesystem_type(self.dir('mnt', 'local', 'w'), info), 'xfs')
        self.assertEqual(filesystem_type(self.dir('mnt', 'other'), info), 'nfs')
        self.assertEqual(filesystem_type(self.dir('outside'), info), 'ext4')

    def test_component_boundary(self):
        info = self.mountinfo(('/', 'ext4'), (self.root / 'a', 'nfs'))
        self.assertEqual(filesystem_type(self.dir('ab'), info), 'ext4')
        self.assertEqual(filesystem_type(self.dir('a'), info), 'nfs')

    def test_octal_escapes(self):
        spaced = self.dir('with space\ttab')
        info = self.mountinfo(('/', 'ext4'), (spaced, 'cifs'))
        self.assertEqual(filesystem_type(spaced / '..' / spaced.name, info), 'cifs')
        self.assertEqual(parse_mountinfo('1 2 0:1 / /a\\040b rw - nfs s rw'), [('/a b', 'nfs')])

    def test_stacked_mounts_last_wins(self):                                    # D6-4
        work = self.dir('stack')
        self.assertEqual(filesystem_type(work, self.mountinfo(('/', 'ext4'), (work, 'ext4'), (work, 'nfs'))), 'nfs')
        self.assertEqual(filesystem_type(work, self.mountinfo(('/', 'ext4'), (work, 'nfs'), (work, 'ext4'))), 'ext4')


class ResolutionTest(Base):                                                     # D6-2
    def setUp(self):
        super().setUp()
        self.net = self.dir('net')
        self.local = self.dir('local')
        self.info = self.mountinfo(('/', 'ext4'), (self.net, 'nfs'), (self.local, 'ext4'))

    def test_symlinked_work_is_resolved(self):
        link = self.local / 'link-to-net'
        link.symlink_to(self.net)
        with self.assertRaises(NetworkFilesystemRefusal):
            check_filesystem(link, self.info)

    def test_missing_work_uses_nearest_existing_ancestor(self):
        missing = self.net / 'a' / 'b' / 'c'
        with self.assertRaises(NetworkFilesystemRefusal):
            check_filesystem(missing, self.info)
        self.assertIsNone(check_filesystem(self.local / 'a' / 'b' / 'c', self.info))
        self.assertFalse((self.net / 'a').exists())
        self.assertFalse((self.local / 'a').exists())                          # nothing created

    def test_missing_work_below_symlink_to_other_mount(self):
        link = self.local / 'to-net'
        link.symlink_to(self.net)
        with self.assertRaises(NetworkFilesystemRefusal):
            check_filesystem(link / 'new' / 'work', self.info)

    def test_dotdot_in_missing_tail(self):
        path = self.local / 'missing' / '..' / '..' / 'net' / 'missing'
        with self.assertRaises(NetworkFilesystemRefusal):
            check_filesystem(path, self.info)

    def test_does_not_require_existing_target(self):
        self.assertEqual(filesystem_type(self.root / 'nope' / 'deeper', self.info), 'ext4')
        self.assertFalse((self.root / 'nope').exists())


if __name__ == '__main__':
    unittest.main()
