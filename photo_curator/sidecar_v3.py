"""Sidecar write procedure for apply, every schema version (spec 5g3a section 13.2, 13.1 check 11a;
spec 20 item 19a; implementation step S14).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S14 only): library code, NOT wired into the CLI (apply is rewired in W-B).

- ``<sidecar>`` is ``<original path>.xmp``. The original is never opened, renamed or replaced.
- Content: built exactly as ``core.write_rating_sidecar`` builds it today (same ElementTree
  steps, same serialisation), setting only ``xmp:Rating`` to ``str(rating)``.
- Backup (only when the sidecar exists and overwriting is allowed): ``<sidecar>.<UTC
  YYYYMMDDTHHMMSS.ffffffZ>.photo-curator.bak`` (M-15), exclusive creation, fsync, re-read and
  SHA-256 check; any failure removes the partial backup and stops apply.
- Write: ``atomic_v3.atomic_write`` (temporary ``<sidecar>.tmp-<pid>-<random>``, fsync, read-back
  check of the intended rating, ``os.replace``, folder fsync), then verification (step 7) only
  after durability succeeded.
- Every failure raises ``SidecarError`` with the exact 13.2 message; apply stops on it.
"""
from __future__ import annotations

import hashlib
import io
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .atomic_v3 import BeforeReplaceError, DurabilityError, atomic_write
from .core import NS

WRITE_FAILED = '[ERROR sidecar write failed]'
NOT_DURABLE = '[ERROR sidecar written but not confirmed durable]'
VERIFY_FAILED = '[ERROR sidecar written but verification failed]'
SKIP_NOT_REGULAR = '[SKIP sidecar not a regular file]'
_RDF = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}'
_RATING = '{http://ns.adobe.com/xap/1.0/}Rating'


class SidecarError(Exception):
    """``kind``: ``before_replace``, ``durability`` or ``verification``. ``message`` starts with
    the 13.2 error text. ``leftover`` names a temporary file that could not be removed."""

    def __init__(self, kind: str, message: str, *, sidecar: str, backup: str | None = None,
                 leftover: str | None = None):
        self.kind, self.message, self.sidecar, self.backup, self.leftover = kind, message, sidecar, backup, leftover
        super().__init__(message)


@dataclass(frozen=True)
class SidecarResult:
    sidecar: str
    backup: str | None


def sidecar_path(original) -> str:
    return os.fspath(original) + '.xmp'


def sidecar_not_regular(original) -> bool:
    """13.1 check 11a / 13.2 step 1: the sidecar path equals the original, or exists but is a
    symlink or not a regular file (lstat)."""
    original_path = os.fspath(original)
    sidecar = sidecar_path(original_path)
    if os.path.abspath(sidecar) == os.path.abspath(original_path):
        return True
    try:
        mode = os.lstat(sidecar).st_mode
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return not stat.S_ISREG(mode)


def build_content(existing: bytes | None, rating) -> bytes:
    """The new sidecar bytes, exactly as ``core.write_rating_sidecar`` builds them today."""
    if existing is not None:
        try:
            root = ET.fromstring(existing)
        except ET.ParseError:
            raise ValueError('existing sidecar is invalid XML; left untouched') from None
        tree = ET.ElementTree(root)
    else:
        root = ET.Element('{adobe:ns:meta/}xmpmeta')
        tree = ET.ElementTree(root)
        rdf = ET.SubElement(root, _RDF + 'RDF')
        ET.SubElement(rdf, _RDF + 'Description', {_RDF + 'about': ''})
    descriptions = root.findall('.//rdf:Description', NS)
    if not descriptions:
        rdf = root.find('.//rdf:RDF', NS)
        if rdf is None:
            rdf = ET.SubElement(root, _RDF + 'RDF')
        desc = ET.SubElement(rdf, _RDF + 'Description', {_RDF + 'about': ''})
    else:
        desc = descriptions[0]
    desc.set(_RATING, str(rating))
    buffer = io.BytesIO()
    tree.write(buffer, encoding='utf-8', xml_declaration=True)
    return buffer.getvalue()


def holds_rating(data: bytes, rating) -> bool:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return False
    return any(d.get(_RATING) == str(rating) for d in root.findall('.//rdf:Description', NS))


def _backup(sidecar: str, existing: bytes, now: datetime | None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = f'{sidecar}.{stamp}.photo-curator.bak'
    name = os.path.basename(sidecar)
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    except OSError as error:
        raise SidecarError('before_replace', f'{WRITE_FAILED} {name}: backup could not be created '
                                             f'({error.strerror or error})', sidecar=sidecar) from None
    try:
        try:
            view = memoryview(existing)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        with open(backup, 'rb') as handle:
            if hashlib.sha256(handle.read()).hexdigest() != hashlib.sha256(existing).hexdigest():
                raise OSError('backup SHA-256 differs from the existing sidecar')
    except OSError as error:
        try:
            os.unlink(backup)                                                  # partial backup removed
        except OSError:
            pass
        raise SidecarError('before_replace', f'{WRITE_FAILED} {name}: backup failed ({error})',
                           sidecar=sidecar) from None
    return backup


def write_sidecar(original, rating, *, now: datetime | None = None) -> SidecarResult:
    """13.2 steps 2-7 for one item. The caller has passed check 11a and every earlier check."""
    sidecar = sidecar_path(original)
    name = os.path.basename(sidecar)
    existing = None
    if os.path.lexists(sidecar):
        try:
            with open(sidecar, 'rb') as handle:
                existing = handle.read()
        except OSError as error:
            raise SidecarError('before_replace', f'{WRITE_FAILED} {name}: existing sidecar unreadable '
                                                 f'({error.strerror or error})', sidecar=sidecar) from None
    backup = _backup(sidecar, existing, now) if existing is not None else None           # step 2
    try:
        data = build_content(existing, rating)                                           # step 3
    except ValueError as error:
        raise SidecarError('before_replace', f'{WRITE_FAILED} {name}: {error}', sidecar=sidecar,
                           backup=backup) from None

    def verify_temp(temp: str) -> None:                                                  # step 4
        with open(temp, 'rb') as handle:
            if not holds_rating(handle.read(), rating):
                raise ValueError('temporary sidecar does not hold the intended rating')

    try:
        atomic_write(sidecar, data, verify_temp=verify_temp)                             # steps 4-6
    except BeforeReplaceError as error:
        raise SidecarError('before_replace', f'{WRITE_FAILED} {name}', sidecar=sidecar, backup=backup,
                           leftover=error.leftover) from None
    except DurabilityError:
        raise SidecarError('durability', f'{NOT_DURABLE} {name}', sidecar=sidecar, backup=backup) from None
    try:                                                                                 # step 7
        with open(sidecar, 'rb') as handle:
            ok = holds_rating(handle.read(), rating)
    except OSError:
        ok = False
    if not ok:
        raise SidecarError('verification', f'{VERIFY_FAILED} {name}', sidecar=sidecar, backup=backup)
    return SidecarResult(sidecar, backup)
