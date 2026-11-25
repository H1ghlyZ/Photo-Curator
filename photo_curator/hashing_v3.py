"""Read-only original hashing and the preview-generation integrity procedure (spec 5g3a
sections 14.3a, 14.1 ``original_sha256``, 14.5 ``stage = original``; implementation step S8).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S8 only):
- library code; NOT wired into the v2 pipeline or any CLI command (analysis wiring is W-A);
- ``sha256_file`` is the single hash function for originals. Later steps (approve 12.3,
  ``approve --show``, apply check 12) reuse it; ``review``, the gallery and the coverage
  report must never call it.

Rules:
- Originals are only ever opened ``O_RDONLY | O_NOFOLLOW`` and streamed; nothing here can
  write, rename or replace an original.
- 14.3a: lstat (S1), hash (H1), generate the preview, lstat (S2), hash (H2). Accept only when
  both hashes succeeded, H1 = H2 and S1 = S2 (size, ``mtime_ns``, inode, device). There is no
  size/mtime-only mode and no way to skip either hash (decision A37).
- A preview made for a rejected original is left where the generator put it (never
  deleted); the ``stage = original`` error entry has ``preview_path = null``.
- A failure of the preview generator itself is a ``stage = preview`` outcome (14.5, 15.4
  step 2); the second lstat and hash are then not run.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from typing import Any, Callable

from .schema_v3 import make_error_entry

ERROR_MISSING = 'original missing or not a regular file'
ERROR_UNREADABLE = 'original could not be read for hashing'
ERROR_CONTENT = 'original content changed during preview generation'
ERROR_METADATA = 'original file metadata changed during preview generation'

_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_CHUNK = 1 << 20


class HashError(Exception):
    """The file could not be hashed (missing, not a regular file, or a read error)."""


def sha256_file(path) -> str:
    """SHA-256 of a regular, non-symlink file, opened read-only and streamed."""
    target = os.fspath(path)
    try:
        fd = os.open(target, _READ_FLAGS)
    except OSError as error:
        raise HashError(f'{target}: cannot open for reading ({error.strerror or error})') from None
    digest, failure = hashlib.sha256(), None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            failure = 'not a regular file'
        else:
            while True:
                chunk = os.read(fd, _CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as error:
        failure = f'read error ({error.strerror or error})'
    finally:
        try:
            os.close(fd)
        except OSError as error:
            failure = failure or f'close error ({error.strerror or error})'
    if failure:
        raise HashError(f'{target}: {failure}')
    return digest.hexdigest()


@dataclass(frozen=True)
class FileIdentity:
    size: int
    mtime_ns: int
    inode: int
    device: int


def file_identity(path) -> FileIdentity | None:
    """lstat identity of a regular file; None when missing, not regular, or not stat-able."""
    try:
        st = os.lstat(os.fspath(path))
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return FileIdentity(st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev)


@dataclass(frozen=True)
class OriginalOutcome:
    """``stage`` is ``'accepted'``, ``'original'`` (rejected, 14.3a) or ``'preview'``
    (generator failed). ``preview`` is whatever the generator returned (None if it never
    ran or failed); it is informational only and never referenced by a rejection."""
    stage: str
    original_sha256: str | None = None
    error: str | None = None
    preview: Any = None

    @property
    def accepted(self) -> bool:
        return self.stage == 'accepted'


def check_original_with_preview(original, generate_preview: Callable[[Any], Any]) -> OriginalOutcome:
    """Run the 14.3a procedure for one original around ``generate_preview(original)``."""
    s1 = file_identity(original)                                   # step 1
    if s1 is None:
        return OriginalOutcome('original', error=ERROR_MISSING)
    try:
        h1 = sha256_file(original)                                 # step 2
    except HashError:
        return OriginalOutcome('original', error=ERROR_UNREADABLE)
    try:
        preview = generate_preview(original)                       # step 3
    except Exception as error:                                     # preview failure (14.5)
        return OriginalOutcome('preview', error=f'preview generation failed: {error}')
    s2 = file_identity(original)                                   # step 4
    try:
        h2 = sha256_file(original)                                 # step 5
    except HashError:
        return OriginalOutcome('original', error=ERROR_UNREADABLE, preview=preview)
    if h1 != h2:
        return OriginalOutcome('original', error=ERROR_CONTENT, preview=preview)
    if s2 != s1:
        return OriginalOutcome('original', error=ERROR_METADATA, preview=preview)
    return OriginalOutcome('accepted', original_sha256=h1, preview=preview)


def original_error_entry(original_path: str, error: str, *, session_id: str | None = None,
                         burst_group: str | None = None, burst_sequence: str | None = None,
                         capture_time: str | None = None) -> dict:
    """14.5 entry for a 14.3a rejection: ``stage = original``, ``sent_to_model = false``,
    ``batch_index = null``, ``preview_path = null``, ``result = null``."""
    return make_error_entry(stage='original', error=error, original_path=original_path,
                            session_id=session_id, burst_group=burst_group,
                            burst_sequence=burst_sequence, capture_time=capture_time,
                            batch_index=None, sent_to_model=False, result=None, preview_path=None)
