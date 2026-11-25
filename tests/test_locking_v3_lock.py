"""S6: lock file rules and flock semantics (spec 12.6; D6-3, D6-7, D6-9).

Real flock on temporary folders. Concurrency uses separate file descriptors in this process
and real child processes (sys.executable) with timeouts; one child is killed with SIGKILL.
Permission tests are skipped when running as root; mocked EROFS tests always run.
"""
from __future__ import annotations
import errno, os, signal, socket, stat, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

from photo_curator import locking_v3
from photo_curator.locking_v3 import (BUSY_MESSAGE, LOCK_NAME, LockFileRefusal, OutputBusyRefusal, acquire_lock)

ROOT = str(Path(__file__).resolve().parent.parent)
IS_ROOT = hasattr(os, 'geteuid') and os.geteuid() == 0
CHILD = ('import sys, time\n'
         'sys.path.insert(0, sys.argv[1])\n'
         'from photo_curator.locking_v3 import acquire_lock\n'
         'h = acquire_lock(sys.argv[2], sys.argv[3])\n'
         'print("locked", flush=True)\n'
         'time.sleep(60)\n')


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s6-lock-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        self.lock = self.work / LOCK_NAME

    def take(self, mode):
        handle = acquire_lock(self.work, mode)
        self.addCleanup(handle.release)
        return handle

    def assert_busy(self, mode):
        start = time.monotonic()
        with self.assertRaises(OutputBusyRefusal) as cm:
            acquire_lock(self.work, mode)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual(str(cm.exception), BUSY_MESSAGE)

    def listing(self):
        return sorted(p.name for p in self.work.iterdir())


class LockFileTest(Base):
    def test_created_regular_and_never_deleted(self):
        for mode in ('shared', 'exclusive'):
            with self.subTest(mode=mode):
                with acquire_lock(self.work, mode) as handle:
                    self.assertTrue(stat.S_ISREG(os.lstat(self.lock).st_mode))
                    self.assertEqual(handle.mode, mode)
                self.assertTrue(self.lock.is_file())
                handle.release()                                             # idempotent
                self.assertTrue(self.lock.is_file())

    def test_open_flags_per_mode(self):                                     # D6-7
        real_open = os.open
        for mode, access in (('shared', os.O_RDONLY), ('exclusive', os.O_RDWR)):
            with self.subTest(mode=mode):
                with mock.patch.object(locking_v3.os, 'open', side_effect=real_open) as spy:
                    acquire_lock(self.work, mode).release()
                flags = spy.call_args.args[1]
                self.assertEqual(flags & os.O_ACCMODE, access)
                for flag in (os.O_CREAT, os.O_NOFOLLOW, os.O_NONBLOCK, os.O_CLOEXEC):
                    self.assertTrue(flags & flag, flag)
                self.assertEqual(spy.call_args.args[2], 0o644)

    def test_descriptor_not_inheritable(self):
        for mode in ('shared', 'exclusive'):
            with acquire_lock(self.work, mode) as handle:
                self.assertFalse(os.get_inheritable(handle.fd))

    def test_contract(self):
        with self.assertRaises(ValueError):
            acquire_lock(self.work, 'read')
        with self.assertRaises(ValueError):
            acquire_lock(self.work / 'missing', 'shared')
        (self.work / 'file').write_text('x')
        with self.assertRaises(ValueError):
            acquire_lock(self.work / 'file', 'exclusive')
        self.assertEqual(self.listing(), ['file'])                            # no lock file created


class NotRegularTest(Base):
    def assert_not_regular(self):
        before = self.listing()
        for mode in ('shared', 'exclusive'):
            with self.subTest(mode=mode):
                with self.assertRaises(LockFileRefusal) as cm:
                    acquire_lock(self.work, mode)
                self.assertEqual(str(cm.exception), f'lock file {self.lock} is a symlink or not a regular file; refusing')
        self.assertEqual(self.listing(), before)

    def test_symlink_to_valid_file(self):
        (self.work / 'real').write_text('')
        self.lock.symlink_to(self.work / 'real')
        self.assert_not_regular()

    def test_broken_symlink(self):
        self.lock.symlink_to(self.work / 'nowhere')
        self.assert_not_regular()
        self.assertFalse((self.work / 'nowhere').exists())

    def test_fifo(self):
        os.mkfifo(self.lock)
        self.assert_not_regular()

    def test_directory(self):
        self.lock.mkdir()
        self.assert_not_regular()

    def test_socket(self):
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        try:
            server.bind(str(self.lock))
        except OSError as error:
            self.skipTest(f'cannot bind a UNIX socket here: {error}')
        self.assert_not_regular()


class ContentionTest(Base):
    def test_matrix(self):
        expected = {('shared', 'shared'): True, ('shared', 'exclusive'): False,
                    ('exclusive', 'shared'): False, ('exclusive', 'exclusive'): False}
        for (held, wanted), ok in expected.items():
            with self.subTest(held=held, wanted=wanted):
                holder = acquire_lock(self.work, held)
                try:
                    if ok:
                        acquire_lock(self.work, wanted).release()
                    else:
                        self.assert_busy(wanted)
                finally:
                    holder.release()
                acquire_lock(self.work, wanted).release()                     # succeeds once released

    def test_no_holder(self):
        for mode in ('shared', 'exclusive'):
            acquire_lock(self.work, mode).release()

    def test_exclusive_waits_for_every_shared_holder(self):
        holders = [acquire_lock(self.work, 'shared') for _ in range(3)]
        for index, holder in enumerate(holders):
            self.assert_busy('exclusive')
            holder.release()
        acquire_lock(self.work, 'exclusive').release()


@unittest.skipIf(IS_ROOT, 'root bypasses file permissions')
class ReadOnlyPermissionTest(Base):
    def chmod(self, path, mode):
        original = stat.S_IMODE(os.stat(path).st_mode)
        os.chmod(path, mode)
        self.addCleanup(os.chmod, path, original)

    def outcome(self, mode):
        try:
            acquire_lock(self.work, mode).release()
            return 'ok'
        except LockFileRefusal as refusal:
            self.assertIn('cannot create or open lock file', str(refusal))
            return 'refused'

    def test_r1_readonly_folder_existing_writable_lock(self):
        self.lock.write_text(''); os.chmod(self.lock, 0o644)
        self.chmod(self.work, 0o555)
        self.assertEqual((self.outcome('shared'), self.outcome('exclusive')), ('ok', 'ok'))

    def test_r2_readonly_folder_readonly_lock(self):
        self.lock.write_text(''); os.chmod(self.lock, 0o444)
        self.addCleanup(os.chmod, self.lock, 0o644)
        self.chmod(self.work, 0o555)
        self.assertEqual((self.outcome('shared'), self.outcome('exclusive')), ('ok', 'refused'))

    def test_r3_readonly_folder_without_lock(self):
        self.chmod(self.work, 0o555)
        self.assertEqual((self.outcome('shared'), self.outcome('exclusive')), ('refused', 'refused'))
        self.assertFalse(self.lock.exists())

    def test_r4_writable_folder_readonly_lock(self):
        self.lock.write_text(''); os.chmod(self.lock, 0o444)
        self.addCleanup(os.chmod, self.lock, 0o644)
        self.assertEqual((self.outcome('shared'), self.outcome('exclusive')), ('ok', 'refused'))
        self.assertEqual(stat.S_IMODE(os.stat(self.lock).st_mode), 0o444)       # never chmod-ed


class MockedErofsTest(Base):                                                    # runs as root too
    def erofs_open(self, real_open, when):
        def fake(path, flags, *args):
            if when(flags):
                raise OSError(errno.EROFS, os.strerror(errno.EROFS))
            return real_open(path, flags, *args)
        return fake

    def test_r5_exclusive_without_write_access(self):
        self.lock.write_text('')
        real = os.open
        with mock.patch.object(locking_v3.os, 'open',
                               side_effect=self.erofs_open(real, lambda f: f & os.O_ACCMODE == os.O_RDWR)):
            with self.assertRaises(LockFileRefusal) as cm:
                acquire_lock(self.work, 'exclusive')
            acquire_lock(self.work, 'shared').release()
        self.assertEqual(str(cm.exception),
                         f'cannot create or open lock file {self.lock}: {os.strerror(errno.EROFS)}; refusing')
        self.assertTrue(self.lock.exists())

    def test_r6_shared_cannot_create(self):
        real = os.open
        with mock.patch.object(locking_v3.os, 'open', side_effect=self.erofs_open(real, lambda f: True)):
            with self.assertRaises(LockFileRefusal):
                acquire_lock(self.work, 'shared')
        self.assertFalse(self.lock.exists())

    def test_flock_os_error_closes_and_refuses(self):
        with mock.patch.object(locking_v3.fcntl, 'flock', side_effect=OSError(errno.ENOLCK, 'no locks')):
            with self.assertRaises(LockFileRefusal):
                acquire_lock(self.work, 'exclusive')
        acquire_lock(self.work, 'exclusive').release()


class ProcessTest(Base):                                                        # D6-9
    def spawn(self, mode):
        proc = subprocess.Popen([sys.executable, '-B', '-c', CHILD, ROOT, str(self.work), mode],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.reap, proc)
        line = proc.stdout.readline().strip()
        self.assertEqual(line, 'locked', proc.stderr.read() if proc.poll() is not None else '')
        return proc

    def reap(self, proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        proc.stdout.close(); proc.stderr.close()

    def test_killed_exclusive_holder_does_not_block(self):
        proc = self.spawn('exclusive')
        self.assert_busy('shared')
        self.assert_busy('exclusive')
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        acquire_lock(self.work, 'exclusive').release()
        acquire_lock(self.work, 'shared').release()
        self.assertTrue(self.lock.is_file())

    def test_shared_holder_in_another_process(self):
        proc = self.spawn('shared')
        acquire_lock(self.work, 'shared').release()
        self.assert_busy('exclusive')
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        acquire_lock(self.work, 'exclusive').release()

    def test_child_does_not_inherit_the_lock(self):
        holder = acquire_lock(self.work, 'exclusive')
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], close_fds=False)
        self.addCleanup(lambda: (child.kill(), child.wait(timeout=10)))
        holder.release()
        acquire_lock(self.work, 'exclusive').release()                      # child is still alive
        self.assertIsNone(child.poll())


if __name__ == '__main__':
    unittest.main()
