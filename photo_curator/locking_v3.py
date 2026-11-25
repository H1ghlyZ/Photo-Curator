"""Locks, start-up order and network-filesystem refusal for v3-era commands (spec 5g3a
section 12.6, 13.1 checks 0a/0b, 14.7 differences 2-5, V47; implementation step S6).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S6 only):
- library code; NOT wired into any CLI command or the v2 pipeline (D6-1). Wiring, CLI
  messages and exit codes, the ``gallery`` command and the S1 listing tests are left to the
  wiring stage;
- the archive journal itself and ``recover-archive`` are S7; this module only refuses when
  ``archive-in-progress.json`` exists (the journal-check hook).

Rules:
- Lock file ``<work>/ratings-review.json.lock``: opened with ``O_CREAT | O_NOFOLLOW |
  O_NONBLOCK | O_CLOEXEC`` plus ``O_RDONLY`` for a shared lock or ``O_RDWR`` for an exclusive
  lock (D6-7). It must be a regular file (checked with ``fstat`` on the opened descriptor).
  It is never deleted, chmod-ed or repaired, on any path.
- ``flock`` is always non-blocking; there is no waiting and no lock upgrade. Contention gives
  the exact message of spec 12.6. The kernel releases the lock when the process exits.
- On a read-only folder or file an existing lock file can still give a shared lock; an
  exclusive lock then fails safely (``LockFileRefusal``). No command can proceed without a lock.
- Network filesystems (decision U4): ``/proc/self/mountinfo`` is read and the mount with the
  longest mount point containing the resolved ``<work>`` decides. When ``<work>`` does not
  exist yet, its nearest existing ancestor (after resolution) is used (D6-2). Detection is
  best effort: an unreadable mountinfo, an unknown type or a generic ``fuse`` mount proceeds
  without any claim that the folder is local. Among identical mount points the last entry
  wins (D6-4); malformed lines are skipped (D6-5).
- Start-up order (``startup_v3``): (1) filesystem check, (2) lock, (3) journal check unless
  exempt (``recover-archive`` only), then the temporary-file listing. It never opens the
  results file or a preview. A journal refusal releases the lock first.
- ``*.tmp-*`` leftovers are listed from the top level of ``<work>`` only (D6-6); they are
  never read or deleted.
- The journal counts as present for any directory entry, including a broken symlink (D6-8).
"""
from __future__ import annotations

import errno
import fcntl
import fnmatch
import os
import re
import stat
from dataclasses import dataclass

LOCK_NAME = 'ratings-review.json.lock'
JOURNAL_NAME = 'archive-in-progress.json'
MOUNTINFO = '/proc/self/mountinfo'
TMP_PATTERN = '*.tmp-*'
LOCK_MODES = ('shared', 'exclusive')

# Spec 12.6: known network filesystem types. Extend only together with the tests.
NETWORK_FILESYSTEMS = frozenset({
    'nfs', 'nfs4',
    'cifs', 'smb3', 'smbfs',
    '9p', 'afs', 'ceph', 'glusterfs', 'lustre', 'davfs',
    'fuse.sshfs', 'fuse.rclone', 'fuse.s3fs', 'fuse.gcsfuse',
})

BUSY_MESSAGE = 'output folder busy: another Photo Curator command is using it; retry after it finishes'
JOURNAL_MESSAGE = 'archive in progress; run `recover-archive`'

_COMMON_FLAGS = os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_OPEN_FLAGS = {'shared': os.O_RDONLY | _COMMON_FLAGS, 'exclusive': os.O_RDWR | _COMMON_FLAGS}
_FLOCK_OPS = {'shared': fcntl.LOCK_SH | fcntl.LOCK_NB, 'exclusive': fcntl.LOCK_EX | fcntl.LOCK_NB}
_NOT_REGULAR_ERRNOS = frozenset({errno.ELOOP, errno.EISDIR, errno.ENXIO})
_OCTAL = re.compile(r'\\([0-7]{3})')


class StartupRefusal(Exception):
    """A start-up refusal: the command must change nothing and exit non-zero."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NetworkFilesystemRefusal(StartupRefusal):
    pass


class OutputBusyRefusal(StartupRefusal):
    pass


class LockFileRefusal(StartupRefusal):
    pass


class ArchiveInProgressRefusal(StartupRefusal):
    pass


# --- Filesystem detection (12.6, U4) -----------------------------------------------------

def _decode(field: str) -> str:
    return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text: str) -> list[tuple[str, str]]:
    """``(mount_point, fstype)`` per well-formed line, in file order; malformed lines skipped."""
    entries = []
    for line in text.splitlines():
        parts = line.split(' ')
        try:
            separator = parts.index('-', 6)
        except ValueError:
            continue
        if separator + 1 >= len(parts) or not parts[separator + 1]:
            continue
        mount_point = _decode(parts[4])
        if not mount_point.startswith('/'):
            continue
        entries.append((mount_point, parts[separator + 1]))
    return entries


def _nearest_existing(path) -> str:
    candidate = os.path.realpath(os.fspath(path))
    while not os.path.exists(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return candidate


def _contains(mount_point: str, path: str) -> bool:
    mount = os.path.normpath(mount_point)
    return mount == '/' or path == mount or path.startswith(mount + '/')


def filesystem_type(work, mountinfo_path: str = MOUNTINFO) -> str | None:
    """Type of the mount holding ``work`` (or its nearest existing ancestor), or None."""
    try:
        with open(mountinfo_path, encoding='utf-8', errors='surrogateescape') as handle:
            text = handle.read()
    except OSError:
        return None
    target = _nearest_existing(work)
    best, best_length = None, -1
    for mount_point, fstype in parse_mountinfo(text):
        if _contains(mount_point, target):
            length = len(os.path.normpath(mount_point))
            if length >= best_length:                  # ties: the later (visible) mount wins
                best, best_length = fstype, length
    return best


def check_filesystem(work, mountinfo_path: str = MOUNTINFO) -> None:
    """Refuse a known network filesystem; otherwise return without any claim."""
    fstype = filesystem_type(work, mountinfo_path)
    if fstype in NETWORK_FILESYSTEMS:
        raise NetworkFilesystemRefusal(f'output folder is on a network filesystem ({fstype}); use a local folder')


# --- Lock (12.6) --------------------------------------------------------------------------

def _work_dir(work) -> str:
    resolved = os.path.realpath(os.fspath(work))
    if not os.path.isdir(resolved):
        raise ValueError(f'output folder does not exist or is not a directory: {work}')
    return resolved


class LockHandle:
    """A held lock; ``release`` closes the descriptor. The lock file is never deleted."""

    def __init__(self, fd: int, path: str, mode: str):
        self.fd, self.path, self.mode = fd, path, mode
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            os.close(self.fd)

    def __enter__(self) -> 'LockHandle':
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _not_regular(path: str) -> LockFileRefusal:
    return LockFileRefusal(f'lock file {path} is a symlink or not a regular file; refusing')


def _cannot_open(path: str, error: OSError) -> LockFileRefusal:
    return LockFileRefusal(f'cannot create or open lock file {path}: {os.strerror(error.errno or 0)}; refusing')


def acquire_lock(work, mode: str) -> LockHandle:
    """Take the shared or exclusive lock without waiting."""
    if mode not in LOCK_MODES:
        raise ValueError(f'lock mode must be "shared" or "exclusive", got {mode!r}')
    path = os.path.join(_work_dir(work), LOCK_NAME)
    try:
        fd = os.open(path, _OPEN_FLAGS[mode], 0o644)
    except OSError as error:
        if error.errno in _NOT_REGULAR_ERRNOS:
            raise _not_regular(path) from None
        raise _cannot_open(path, error) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _not_regular(path)
        try:
            fcntl.flock(fd, _FLOCK_OPS[mode])
        except BlockingIOError:
            raise OutputBusyRefusal(BUSY_MESSAGE) from None
        except OSError as error:
            raise _cannot_open(path, error) from None
    except BaseException:
        os.close(fd)
        raise
    return LockHandle(fd, path, mode)


# --- Journal hook and leftovers ------------------------------------------------------------

def check_no_archive_journal(work) -> None:
    if os.path.lexists(os.path.join(os.fspath(work), JOURNAL_NAME)):
        raise ArchiveInProgressRefusal(JOURNAL_MESSAGE)


def list_tmp_leftovers(work) -> list[str]:
    """Names of top-level ``*.tmp-*`` entries in ``work``; never opened, never deleted."""
    return sorted(name for name in os.listdir(os.fspath(work)) if fnmatch.fnmatchcase(name, TMP_PATTERN))


# --- Start-up order --------------------------------------------------------------------------

@dataclass(frozen=True)
class StartupResult:
    lock: LockHandle
    leftovers: tuple[str, ...]


def startup_v3(work, mode: str, *, exempt_journal: bool = False,
               mountinfo_path: str = MOUNTINFO) -> StartupResult:
    """Spec 12.6 start-up order. The caller holds ``result.lock`` until it finishes."""
    if mode not in LOCK_MODES:
        raise ValueError(f'lock mode must be "shared" or "exclusive", got {mode!r}')
    check_filesystem(work, mountinfo_path)            # (1) before anything is created
    lock = acquire_lock(work, mode)                   # (2)
    try:
        if not exempt_journal:
            check_no_archive_journal(work)            # (3)
        leftovers = tuple(list_tmp_leftovers(work))
    except BaseException:
        lock.release()
        raise
    return StartupResult(lock, leftovers)
