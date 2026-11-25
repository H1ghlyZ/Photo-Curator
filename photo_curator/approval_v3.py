"""Approval validation, approval state and the re-approval table (spec 5g3a sections 12.1, 12.2,
12.3 "Re-approval and revocation", V38; spec 20 item 21, validation part; implementation step S12).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S12 only): pure validation and derivation, NOT wired into any command. Nothing here
writes a file. The approve command (writing, backups, H0, 12.3 steps 1-10) is S13; apply's
check order (13.1) is S14.

Rules:
- ``approval_field_problems`` lists every 12.2 rule an item breaks; any problem makes the state
  ``malformed``, which is reported and never repaired.
- ``derive_approval_state`` follows 12.1 rows in order (malformed, not_required, pending,
  invalidated, approved) and the binding checks in DIAGNOSTIC order 1-6. Check 3 (original
  content) runs only when the caller supplies the original's current SHA-256
  (``approve --show``, approve, apply check 12). Without it the state is "approved (original
  not verified)" (12.1 reader rule); this module never hashes an original itself.
- Check 5 is PV1-PV6 (``preview_checks_v3``, with the approved PV6 deviation B-11c); check 6
  hashes the preview file only.
- ``reapproval_decision`` is the 12.3 table, cell by cell.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from typing import Any

from .output_v3 import is_utc_z
from .preview_checks_v3 import APPLY_SKIP_MESSAGE as PREVIEW_INVALID_MESSAGE, check_preview
from .schema_v3 import APPROVAL_FIELDS

_HEX64 = re.compile(r'[0-9a-f]{64}')

BINDING_MESSAGES = {
    1: '[SKIP approval invalidated: rating changed]',
    2: '[SKIP approval invalidated: original changed]',
    3: '[SKIP approval invalidated: original changed]',
    4: '[SKIP approval invalidated: preview changed]',
    5: PREVIEW_INVALID_MESSAGE,
    6: '[SKIP approval invalidated: preview changed]',
}
MALFORMED_MESSAGE = '[SKIP approval malformed]'
PENDING_MESSAGE = '[SKIP needs review: not approved]'

# 12.3 re-approval table: (state, action) -> (allowed, outcome or refusal text).
REAPPROVAL_TABLE = {
    ('not_required', 'approve'): (False, 'refused: nothing to approve'),
    ('not_required', 'revoke'): (False, 'refused: no-op'),
    ('pending', 'approve'): (True, 'approves'),
    ('pending', 'revoke'): (False, 'refused: no-op'),
    ('approved', 'approve'): (False, 'refused: already approved; revoke first to change it'),
    ('approved', 'revoke'): (True, 'revokes -> pending'),
    ('invalidated', 'approve'): (True, 'approves again, replacing all seven fields, after a new --show'),
    ('invalidated', 'revoke'): (True, 'revokes -> pending'),
    ('malformed', 'approve'): (False, 'refused: fix by hand first'),
    ('malformed', 'revoke'): (False, 'refused: revoking would be a repair'),
}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _abs_path(v: Any) -> bool:
    return isinstance(v, str) and v != '' and os.path.isabs(v)


def _hex64(v: Any) -> bool:
    return isinstance(v, str) and _HEX64.fullmatch(v) is not None


def approval_field_problems(item: Any) -> list[str]:
    """Every 12.2 rule the item's approval fields break (empty list: well-formed)."""
    if not isinstance(item, dict):
        return ['item is not an object']
    missing = [f for f in APPROVAL_FIELDS if f not in item]
    if missing:
        return [f'missing approval field(s) {missing}']
    problems: list[str] = []
    approved = item['approved']
    if not isinstance(approved, bool):
        return ['approved is not a JSON boolean']
    others = {f: item[f] for f in APPROVAL_FIELDS[1:]}
    if approved is False:
        not_null = [f for f, v in others.items() if v is not None]
        if not_null:
            problems.append(f'approved = false with non-null field(s) {not_null}')
        return problems
    rating = item.get('approved_rating')
    if not (_is_int(rating) and 1 <= rating <= 5):
        problems.append('approved_rating is not an integer 1-5')
    if not is_utc_z(item.get('approved_at')):
        problems.append('approved_at is not an ISO 8601 UTC timestamp with Z')
    for f in ('approved_original_path', 'approved_preview_path'):
        if not _abs_path(item.get(f)):
            problems.append(f'{f} is not an absolute path')
    for f in ('approved_original_sha256', 'approved_preview_sha256'):
        if not _hex64(item.get(f)):
            problems.append(f'{f} is not 64 lowercase hex characters')
    if item.get('approved_original_sha256') != item.get('original_sha256'):
        problems.append('approved_original_sha256 differs from the analysis-time original_sha256')
    if item.get('needs_review') is not True:
        problems.append('approval on an item with needs_review = false (or needs_review not a bool)')
    if item.get('rating') == 0 or not _is_int(item.get('rating')):
        problems.append('approval on a rating-0 item (or rating not an integer)')
    return problems


@dataclass(frozen=True)
class ApprovalState:
    state: str                          # malformed / not_required / pending / invalidated / approved
    failing_check: int | None = None    # binding check (diagnostic order) for 'invalidated'
    message: str | None = None          # skip / refusal message
    original_verified: bool = False     # check 3 ran and passed
    problems: tuple[str, ...] = ()      # 12.2 problems for 'malformed'

    @property
    def label(self) -> str:
        if self.state == 'approved' and not self.original_verified:
            return 'approved (original not verified)'
        return self.state


def preview_sha256(path: Any) -> str | None:
    """SHA-256 of the preview file (regular, not a symlink), streamed; None if unreadable."""
    if not isinstance(path, str):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return None
    digest, ok = hashlib.sha256(), True
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            ok = False
        else:
            while chunk := os.read(fd, 1 << 20):
                digest.update(chunk)
    except OSError:
        ok = False
    finally:
        try:
            os.close(fd)
        except OSError:
            ok = False
    return digest.hexdigest() if ok else None


_NOT_GIVEN = object()


def derive_approval_state(item: dict, *, work, source_folder, original_sha256_now: Any = _NOT_GIVEN,
                          run_preview_checks: bool = True) -> ApprovalState:
    """12.1 for one item. ``original_sha256_now``: the original's current SHA-256 as computed by
    the caller (``None`` = could not be read); omit it to leave check 3 unverified."""
    problems = approval_field_problems(item)
    if problems:
        return ApprovalState('malformed', message=MALFORMED_MESSAGE, problems=tuple(problems))
    if item['approved'] is False:
        if item.get('needs_review') is False:
            return ApprovalState('not_required')
        if item.get('needs_review') is True:
            return ApprovalState('pending', message=PENDING_MESSAGE)
        return ApprovalState('malformed', message=MALFORMED_MESSAGE, problems=('needs_review is not a bool',))
    checks = [(1, lambda: item['approved_rating'] == item['rating']),
              (2, lambda: item['approved_original_path'] == item['original_path'])]
    verified = False
    if original_sha256_now is not _NOT_GIVEN:
        checks.append((3, lambda: original_sha256_now is not None
                       and original_sha256_now == item['approved_original_sha256']))
        verified = True
    checks.append((4, lambda: item['approved_preview_path'] == item['preview_path']))
    if run_preview_checks:
        checks.append((5, lambda: check_preview(item['preview_path'], work, item['original_path'], source_folder).ok))
        checks.append((6, lambda: preview_sha256(item['preview_path']) == item['approved_preview_sha256']))
    for number, passes in checks:
        if not passes():
            return ApprovalState('invalidated', failing_check=number, message=BINDING_MESSAGES[number])
    return ApprovalState('approved', original_verified=verified)


def reapproval_decision(state: str, action: str) -> tuple[bool, str]:
    """The 12.3 re-approval table. ``action`` is ``'approve'`` or ``'revoke'``."""
    if (state, action) not in REAPPROVAL_TABLE:
        raise ValueError(f'unknown state/action {state!r}/{action!r}')
    return REAPPROVAL_TABLE[(state, action)]


def revoked_fields() -> dict:
    """12.3 step 4 revoke: ``approved = false`` and the other six fields ``null``."""
    return {'approved': False, **{f: None for f in APPROVAL_FIELDS[1:]}}
