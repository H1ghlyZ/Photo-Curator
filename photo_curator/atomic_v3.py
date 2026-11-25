"""Atomic single-file write core for v3 writers (spec 5g3a sections 12.8, 12.3 steps 6-9,
13.2 steps 4-6; implementation step S7a).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S7a only):
- library code; NOT wired into any CLI command or the v2 pipeline;
- one primitive, ``atomic_write``, shared by every v3 writer (artifacts 12.8, journal 12.9,
  approve 12.3, sidecar 13.2). Writer-specific steps (backups, H0 re-hash, read-back
  validation, post-durability verification) are supplied by the caller through hooks.

Procedure (one file):
1. create ``<name>.tmp-<pid>-<random>`` in the target's folder with
   ``O_CREAT | O_EXCL | O_NOFOLLOW`` (never overwrites, never follows a symlink);
2. write all bytes, flush, fsync the temporary file;
3. ``verify_temp(temp_path)`` hook (read-back checks, 12.3 step 6, 13.2 step 4);
4. ``before_replace()`` hook (for example the 12.3 step 7 H0 re-hash);
5. ``os.replace`` the temporary file over the target;
6. fsync the folder.

Failure classes (12.3, 12.8, 13.2):
- any failure in steps 1-5 (including an ``os.replace`` that raises) is a
  ``BeforeReplaceError``: the target is untouched. The core removes the temporary file it
  created itself; if that removal fails, the path is reported as a leftover in the error and
  is never retried. Pre-existing ``*.tmp-*`` files from earlier crashes are never touched;
- a failure in step 6 is a ``DurabilityError``: the target holds the new bytes as far as the
  operating system reported, but durability is not confirmed. No rollback, no second replace.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Callable

TMP_INFIX = '.tmp-'
_TMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


class AtomicWriteError(Exception):
    """Base class; ``path`` is the target file."""

    def __init__(self, message: str, path: str):
        self.message, self.path = message, path
        super().__init__(message)


class BeforeReplaceError(AtomicWriteError):
    """The target was not replaced and is unchanged. ``leftover`` names a temporary file the
    core created but could not remove (otherwise None); ``cause`` is the original error."""

    def __init__(self, message: str, path: str, *, step: str, cause: BaseException | None,
                 leftover: str | None):
        super().__init__(message, path)
        self.step, self.cause, self.leftover = step, cause, leftover


class DurabilityError(AtomicWriteError):
    """``os.replace`` succeeded but the folder fsync failed: present, not confirmed durable."""

    def __init__(self, message: str, path: str, *, cause: BaseException):
        super().__init__(message, path)
        self.cause = cause


@dataclass(frozen=True)
class AtomicWriteResult:
    path: str
    temp_name: str          # the temporary name used (it no longer exists after success)


def temp_name_for(path: str, *, pid: int | None = None, token: str | None = None) -> str:
    """``<path>.tmp-<pid>-<random>`` in the same folder as ``path``."""
    pid = os.getpid() if pid is None else pid
    token = secrets.token_hex(8) if token is None else token
    return f'{path}{TMP_INFIX}{pid}-{token}'


# Small indirections so tests can simulate one failing operation at a time.
def _fsync_file(fd: int) -> None:
    os.fsync(fd)


def _fsync_dir(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(src: str, dst: str) -> None:
    os.replace(src, dst)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _remove_own_temp(temp: str) -> str | None:
    """Remove the temporary file this call created; return its path if that fails."""
    try:
        os.unlink(temp)
    except FileNotFoundError:
        return None
    except OSError:
        return temp
    return None


def atomic_write(path, data: bytes, *,
                 verify_temp: Callable[[str], None] | None = None,
                 before_replace: Callable[[], None] | None = None) -> AtomicWriteResult:
    """Write ``data`` to ``path`` atomically (see module docstring)."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError('atomic_write needs bytes')
    target = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(target)
    temp = temp_name_for(target)
    step = 'create temporary file'
    created = False
    try:
        fd = os.open(temp, _TMP_FLAGS, 0o644)
        created = True
        try:
            step = 'write temporary file'
            _write_all(fd, bytes(data))
            step = 'fsync temporary file'
            _fsync_file(fd)
        finally:
            os.close(fd)
        if verify_temp is not None:
            step = 'verify temporary file'
            verify_temp(temp)
        if before_replace is not None:
            step = 'before replace'
            before_replace()
        step = 'replace'
        _replace(temp, target)
    except Exception as error:
        leftover = _remove_own_temp(temp) if created else None
        raise BeforeReplaceError(f'{os.path.basename(target)}: write failed before replacement '
                                 f'({step}): {error}', target, step=step, cause=error,
                                 leftover=leftover) from error
    try:
        _fsync_dir(directory)
    except Exception as error:
        raise DurabilityError(f'{os.path.basename(target)}: written but not confirmed durable '
                              f'(folder fsync failed): {error}', target, cause=error) from error
    return AtomicWriteResult(target, os.path.basename(temp))
