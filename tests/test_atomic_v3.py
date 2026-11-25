"""S7a: atomic write core (spec 12.8; failure classes as in 12.3 and 13.2).

Synthetic temporary folders only; failures are simulated by patching one operation at a time.
"""
from __future__ import annotations
import contextlib, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import atomic_v3
from photo_curator.atomic_v3 import (AtomicWriteResult, BeforeReplaceError, DurabilityError,
                                     atomic_write, temp_name_for)


class Crash(BaseException):
    """Simulated process crash (not an Exception, so nothing cleans up)."""


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s7a-atomic-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.target = self.root / 'ratings-review.json'

    def listing(self) -> list[str]:
        return sorted(os.listdir(self.root))

    def temps(self) -> list[str]:
        return [n for n in self.listing() if '.tmp-' in n]


class SuccessTest(Base):
    def test_new_file_written(self):
        result = atomic_write(self.target, b'{"a": 1}\n')
        self.assertIsInstance(result, AtomicWriteResult)
        self.assertEqual(self.target.read_bytes(), b'{"a": 1}\n')
        self.assertEqual(self.listing(), ['ratings-review.json'])

    def test_existing_file_replaced(self):
        self.target.write_bytes(b'old')
        atomic_write(self.target, b'new')
        self.assertEqual(self.target.read_bytes(), b'new')
        self.assertEqual(self.temps(), [])

    def test_temp_name_pattern_same_folder(self):
        result = atomic_write(self.target, b'x')
        self.assertRegex(result.temp_name, r'^ratings-review\.json\.tmp-%d-[0-9a-f]{16}$' % os.getpid())
        name = temp_name_for(str(self.target), pid=7, token='abc')
        self.assertEqual(name, str(self.target) + '.tmp-7-abc')

    def test_steps_in_order(self):
        calls = []
        real_fsync_file, real_replace, real_fsync_dir = (atomic_v3._fsync_file, atomic_v3._replace,
                                                          atomic_v3._fsync_dir)
        with mock.patch.object(atomic_v3, '_fsync_file', side_effect=lambda fd: (calls.append('fsync_file'), real_fsync_file(fd))), \
             mock.patch.object(atomic_v3, '_replace', side_effect=lambda s, d: (calls.append('replace'), real_replace(s, d))), \
             mock.patch.object(atomic_v3, '_fsync_dir', side_effect=lambda d: (calls.append('fsync_dir'), real_fsync_dir(d))):
            atomic_write(self.target, b'x', verify_temp=lambda p: calls.append('verify'),
                         before_replace=lambda: calls.append('before_replace'))
        self.assertEqual(calls, ['fsync_file', 'verify', 'before_replace', 'replace', 'fsync_dir'])

    def test_folder_fsynced_is_target_folder(self):
        with mock.patch.object(atomic_v3, '_fsync_dir') as fsync_dir:
            atomic_write(self.target, b'x')
        fsync_dir.assert_called_once_with(str(self.root))

    def test_verify_temp_sees_written_bytes(self):
        seen = {}
        atomic_write(self.target, b'payload', verify_temp=lambda p: seen.update(data=Path(p).read_bytes(), path=p))
        self.assertEqual(seen['data'], b'payload')
        self.assertTrue(seen['path'].startswith(str(self.target) + '.tmp-'))

    def test_rejects_non_bytes(self):
        with self.assertRaises(TypeError):
            atomic_write(self.target, 'text')
        self.assertEqual(self.listing(), [])


class BeforeReplaceTest(Base):
    """Every failure before the replacement: target byte-identical (or absent), temp removed."""

    def assert_unchanged(self, before: bytes | None, error: BeforeReplaceError, step: str):
        self.assertEqual(error.step, step)
        self.assertIsNone(error.leftover)
        self.assertEqual(self.temps(), [])
        if before is None:
            self.assertFalse(self.target.exists())
        else:
            self.assertEqual(self.target.read_bytes(), before)

    def run_failure(self, step: str, existing: bool, **kwargs):
        before = None
        if existing:
            self.target.write_bytes(b'OLD CONTENT')
            before = b'OLD CONTENT'
        with self.assertRaises(BeforeReplaceError) as ctx:
            atomic_write(self.target, b'NEW', **kwargs)
        self.assert_unchanged(before, ctx.exception, step)
        return ctx.exception

    def test_each_step(self):
        boom = OSError(5, 'simulated')
        cases = {
            'write temporary file': dict(patch=('_write_all', boom)),
            'fsync temporary file': dict(patch=('_fsync_file', boom)),
            'verify temporary file': dict(kw={'verify_temp': mock.Mock(side_effect=ValueError('bad read-back'))}),
            'before replace': dict(kw={'before_replace': mock.Mock(side_effect=RuntimeError('H0 changed'))}),
            'replace': dict(patch=('_replace', boom)),
        }
        for existing in (False, True):
            for step, case in cases.items():
                with self.subTest(step=step, existing=existing):
                    if self.target.exists():
                        self.target.unlink()
                    patcher = (mock.patch.object(atomic_v3, case['patch'][0], side_effect=case['patch'][1])
                               if 'patch' in case else contextlib.nullcontext())
                    with patcher, mock.patch.object(atomic_v3, '_fsync_dir') as fsync_dir:
                        error = self.run_failure(step, existing, **case.get('kw', {}))
                    fsync_dir.assert_not_called()
                    self.assertIsNotNone(error.cause)

    def test_create_failure_when_folder_missing(self):
        target = self.root / 'missing' / 'x.json'
        with self.assertRaises(BeforeReplaceError) as ctx:
            atomic_write(target, b'x')
        self.assertEqual(ctx.exception.step, 'create temporary file')
        self.assertIsNone(ctx.exception.leftover)

    def test_exclusive_create_never_overwrites_or_follows_symlink(self):
        outside = self.root / 'outside.txt'
        outside.write_bytes(b'precious')
        with mock.patch.object(atomic_v3, 'temp_name_for', return_value=str(self.root / 'clash.tmp-1-a')):
            os.symlink(outside, self.root / 'clash.tmp-1-a')
            with self.assertRaises(BeforeReplaceError) as ctx:
                atomic_write(self.target, b'x')
        self.assertEqual(ctx.exception.step, 'create temporary file')
        self.assertEqual(outside.read_bytes(), b'precious')
        self.assertTrue(os.path.islink(self.root / 'clash.tmp-1-a'))     # not removed: not ours
        self.assertFalse(self.target.exists())

    def test_unremovable_temp_reported_as_leftover(self):
        real_unlink = os.unlink
        with mock.patch.object(atomic_v3, '_fsync_file', side_effect=OSError(5, 'x')), \
             mock.patch.object(atomic_v3.os, 'unlink', side_effect=OSError(13, 'denied')):
            with self.assertRaises(BeforeReplaceError) as ctx:
                atomic_write(self.target, b'x')
        leftover = ctx.exception.leftover
        self.assertIsNotNone(leftover)
        self.assertTrue(os.path.exists(leftover))
        self.assertRegex(os.path.basename(leftover), r'^ratings-review\.json\.tmp-\d+-[0-9a-f]+$')
        real_unlink(leftover)

    def test_existing_leftovers_never_touched(self):
        old = self.root / 'ratings-review.json.tmp-1-dead'
        old.write_bytes(b'crash leftover')
        atomic_write(self.target, b'x')
        with mock.patch.object(atomic_v3, '_replace', side_effect=OSError(5, 'x')):
            with self.assertRaises(BeforeReplaceError):
                atomic_write(self.target, b'y')
        self.assertEqual(old.read_bytes(), b'crash leftover')
        self.assertEqual(self.target.read_bytes(), b'x')

    def test_crash_leaves_temp_as_leftover(self):
        self.target.write_bytes(b'OLD')
        with mock.patch.object(atomic_v3, '_replace', side_effect=Crash()):
            with self.assertRaises(Crash):
                atomic_write(self.target, b'NEW')
        self.assertEqual(self.target.read_bytes(), b'OLD')
        self.assertEqual(len(self.temps()), 1)
        self.assertEqual((self.root / self.temps()[0]).read_bytes(), b'NEW')


class DurabilityTest(Base):
    def test_folder_fsync_failure_after_replace(self):
        self.target.write_bytes(b'OLD')
        replace = mock.Mock(wraps=atomic_v3._replace)
        with mock.patch.object(atomic_v3, '_replace', replace), \
             mock.patch.object(atomic_v3, '_fsync_dir', side_effect=OSError(5, 'simulated')):
            with self.assertRaises(DurabilityError) as ctx:
                atomic_write(self.target, b'NEW')
        self.assertNotIsInstance(ctx.exception, BeforeReplaceError)
        self.assertIn('not confirmed durable', ctx.exception.message)
        self.assertEqual(self.target.read_bytes(), b'NEW')     # new document, no rollback
        self.assertEqual(replace.call_count, 1)                # no second replace
        self.assertEqual(self.temps(), [])


if __name__ == '__main__':
    unittest.main()
