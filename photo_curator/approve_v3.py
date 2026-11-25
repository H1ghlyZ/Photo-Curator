"""The approve command as a library: ``--show [--open-preview]``, ``--rating N --preview-sha256``
and ``--revoke`` (spec 5g3a section 12.3, 12.1, 12.2, 12.6, 7.2; spec 20 item 21; S13).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S13 only): library code, NOT wired into the CLI (approve is enabled in W-B, M-5).
Per item only: one call changes at most one item; there is no bulk input.

- ``show_item``: shared lock (12.6); refuses on the 12.3 general refusals; reads and hashes the
  original read-only (``hashing_v3.sha256_file``) and the preview; writes nothing except a
  missing lock file. ``open_preview`` hands the PREVIEW (never the original) to a viewer only
  when PV1-PV6 pass (M-14; the default viewer runs ``xdg-open`` through ``core.run``).
- ``approve_item`` / ``revoke_item``: exclusive lock and steps 1-10, in order. Backups use the
  M-15 name ``<results.json>.<YYYYMMDDTHHMMSS.ffffffZ>.photo-curator.bak`` with exclusive
  creation. The write uses ``atomic_v3.atomic_write`` with the step 6 read-back check and the
  step 7 H0 re-hash as hooks. Failure classes follow the 12.3 table; nothing is rolled back.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import core, hashing_v3
from .approval_v3 import ApprovalState, derive_approval_state, preview_sha256, reapproval_decision, revoked_fields
from .atomic_v3 import BeforeReplaceError, DurabilityError, atomic_write
from .core import OUTPUT_MARKER
from .locking_v3 import startup_v3
from .preview_checks_v3 import check_preview, open_preview_decision
from .reader_v3 import read_results
from .schema_v3 import APPROVAL_FIELDS
from .suggestions_v3 import editorial_suggestion_v3

DURABILITY_MESSAGE = 'approval written but not confirmed durable'
VERIFICATION_MESSAGE = 'approval written but verification failed'
ORIGINAL_CHANGED = "original changed since analysis; can't be approved"
_HEX = set('0123456789abcdef')


class ApproveRefusal(Exception):
    """Refused before the replacement: the results file is byte-identical (exit non-zero)."""

    def __init__(self, message: str, *, backup: str | None = None):
        self.message, self.backup = message, backup
        super().__init__(message)


class ApproveFailure(Exception):
    """After the replacement: ``failure_class`` is ``durability`` or ``verification``."""

    def __init__(self, message: str, *, failure_class: str, backup: str):
        self.message, self.failure_class, self.backup = message, failure_class, backup
        super().__init__(message)


@dataclass(frozen=True)
class ApproveResult:
    action: str
    original_path: str
    backup: str
    state_before: str
    state_after: str


@dataclass
class ShowReport:
    lines: list[str] = field(default_factory=list)
    original_matches: bool | None = None
    preview_ok: bool = False
    opened: bool = False
    open_refusal: str | None = None
    state: ApprovalState | None = None


def backup_name(results_path: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    return f'{results_path}.{stamp}.photo-curator.bak'


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Shared refusal checks (12.3 "Refusals") -------------------------------------------------------

@dataclass
class _Loaded:
    results_path: str
    work: Path
    raw: bytes
    doc: dict
    index: int
    item: dict
    report: Any


def _load(results_path, original_path: str) -> _Loaded:
    path = os.path.abspath(os.fspath(results_path))
    work = Path(path).parent
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise ApproveRefusal('the results file is a symlink')
    except FileNotFoundError:
        raise ApproveRefusal('results file not found') from None
    if not (work.name == core.GENERATED_DIR or (work / OUTPUT_MARKER).is_file()):
        raise ApproveRefusal('the results file is not inside a Photo Curator output folder')
    report = read_results(path)
    if report.refusal:
        raise ApproveRefusal(report.refusal)
    if report.schema_version != 3:
        raise ApproveRefusal('the results file is not schema version 3')
    try:
        with open(path, 'rb') as handle:
            raw = handle.read()
    except OSError as error:
        raise ApproveRefusal(f'results file unreadable: {error.strerror or error}') from None
    doc = json.loads(raw.decode('utf-8'))
    matches = [i for i, r in enumerate(doc['results']) if isinstance(r, dict) and r.get('original_path') == original_path]
    if len(matches) != 1:
        raise ApproveRefusal(f'{original_path!r} matches {len(matches)} items; exactly one required')
    index = matches[0]
    item_report = report.items[index]
    inconsistent = [row for row in item_report.approve_refusals if row in ('V33', 'V34', 'V35', 'V36', 'V45')]
    if inconsistent:
        raise ApproveRefusal(f'the item is inconsistent under 15.3 ({", ".join(inconsistent)})')
    return _Loaded(path, work, raw, doc, index, doc['results'][index], report)


def _original_hash(path: str, hasher: Callable[[str], str]) -> str | None:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    try:
        return hasher(path)
    except hashing_v3.HashError:
        return None


# --- --show ------------------------------------------------------------------------------------------------

def _default_opener(preview_path: str) -> None:
    core.run(['xdg-open', preview_path], timeout=30)


def show_item(results_path, original_path: str, *, open_preview: bool = False,
              opener: Callable[[str], None] | None = None,
              hasher: Callable[[str], str] = None, mountinfo_path: str | None = None) -> ShowReport:
    hasher = hasher or hashing_v3.sha256_file
    work = Path(os.path.abspath(os.fspath(results_path))).parent
    kwargs = {} if mountinfo_path is None else {'mountinfo_path': mountinfo_path}
    startup = startup_v3(work, 'shared', **kwargs)
    try:
        loaded = _load(results_path, original_path)
        r = loaded.item
        source = loaded.doc.get('source_folder')
        current = _original_hash(r['original_path'], hasher)
        out = ShowReport()
        out.original_matches = current is not None and current == r['original_sha256']
        pv = check_preview(r['preview_path'], loaded.work, r['original_path'], source)
        out.preview_ok = pv.ok
        out.state = derive_approval_state(r, work=loaded.work, source_folder=source, original_sha256_now=current)
        lines = out.lines
        lines.append(f'original: {r["original_path"]}')
        lines.append(f'original SHA-256 at analysis: {r["original_sha256"]}')
        lines.append(f'original SHA-256 now: {current if current else "unreadable"}'
                     + ('' if out.original_matches else f'  ({ORIGINAL_CHANGED})'))
        lines.append(f'preview: {r["preview_path"]}')
        for check in ('PV1', 'PV2', 'PV3', 'PV4', 'PV5', 'PV6'):
            failed = dict(pv.failures).get(check)
            lines.append(f'  {check}: {"FAIL " + failed if failed else "ok"}')
        lines.append(f'preview SHA-256 now: {preview_sha256(r["preview_path"]) or "unreadable"}')
        technical = r.get('technical') if isinstance(r.get('technical'), dict) else {}
        lines.append(f'rating: {r["rating"]}  decision: {r["decision"]}  T: {r["technical_score"]}  '
                     f'E: {r["editorial_score"]}  motion: {technical.get("motion")}')
        lines.append(f'flags: {r.get("flags")}  uncertain_checks: {r.get("uncertain_checks")}')
        lines.append(f'review reasons: {r.get("review_reasons")}')
        failing = f' (binding check {out.state.failing_check}: {out.state.message})' if out.state.failing_check else ''
        lines.append(f'approval state: {out.state.label}{failing}')
        for text in r.get('reasons') or []:
            lines.append(f'  evidence: {text}')
        lines.append(f'capture_time: {r.get("capture_time")}  burst_group: {r.get("burst_group")}  '
                     f'burst_comparison: {r.get("burst_comparison")}  burst_ranking_status: '
                     f'{r.get("burst_ranking_status")}  burst_rank: {r.get("burst_rank")}')
        if open_preview:
            allowed, refusal = open_preview_decision(pv)
            if allowed:
                (opener or _default_opener)(r['preview_path'])               # the preview, never the original
                out.opened = True
            else:
                out.open_refusal = refusal
        return out
    finally:
        startup.lock.release()


# --- approve / revoke (12.3 steps 1-10) ----------------------------------------------------------------------

def _changed_paths(old: Any, new: Any, prefix: str = '') -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                out.append(f'{prefix}{key}')
            else:
                out.extend(_changed_paths(old[key], new[key], f'{prefix}{key}.'))
        return out
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        out = []
        for i, (a, b) in enumerate(zip(old, new)):
            out.extend(_changed_paths(a, b, f'{prefix}{i}.'))
        return out
    return [] if (old == new and type(old) is type(new)) else [prefix.rstrip('.')]


def _write(loaded: _Loaded, action: str, updates: dict, expected_state: str, state_before: str,
           now: datetime | None) -> ApproveResult:
    h0 = _sha(loaded.raw)
    # Step 3: backup with exclusive creation, verified against H0.
    backup = backup_name(loaded.results_path, now)
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    except OSError as error:
        raise ApproveRefusal(f'backup could not be created: {error.strerror or error}') from None
    try:
        try:
            view = memoryview(loaded.raw)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        with open(backup, 'rb') as handle:
            if _sha(handle.read()) != h0:
                raise OSError('backup SHA-256 differs from H0')
    except OSError as error:
        try:
            os.unlink(backup)                                                      # partial backup removed
        except OSError:
            pass
        raise ApproveRefusal(f'backup failed: {error}') from None
    # Step 4: new document in memory; only the target's approval fields and editorial_suggestion change.
    new_doc = copy.deepcopy(loaded.doc)
    target = new_doc['results'][loaded.index]
    target.update(updates)
    eligible = loaded.report.items[loaded.index].recomputed_suggestions.get('highlight_eligible')
    if not isinstance(eligible, bool):
        raise ApproveRefusal('editorial suggestion cannot be recomputed for this item', backup=backup)
    target['editorial_suggestion'] = editorial_suggestion_v3(target['rating'], eligible, expected_state)
    allowed = {f'results.{loaded.index}.{f}' for f in APPROVAL_FIELDS + ('editorial_suggestion',)}
    # Step 5: validate the new document and the isolation of the change.
    changed = set(_changed_paths({'results': loaded.doc['results']}, {'results': new_doc['results']}))
    changed |= set(_changed_paths({k: v for k, v in loaded.doc.items() if k != 'results'},
                                  {k: v for k, v in new_doc.items() if k != 'results'}))
    if not changed <= allowed:
        raise ApproveRefusal(f'unexpected changes {sorted(changed - allowed)}', backup=backup)
    data = (json.dumps(new_doc, indent=2, ensure_ascii=False) + '\n').encode('utf-8')

    def verify_temp(temp_path: str) -> None:                                         # step 6
        with open(temp_path, 'rb') as handle:
            if handle.read() != data:
                raise ValueError('temporary file read-back differs')
        report = read_results(temp_path)
        if report.refusal:
            raise ValueError(f'new document fails validation: {report.refusal}')
        item = report.items[loaded.index]
        if [row for row in item.approve_refusals if row != 'V42'] or item.approval_state != expected_state:
            raise ValueError(f'new document item invalid ({item.findings}, state {item.approval_state})')

    def before_replace() -> None:                                                    # step 7
        with open(loaded.results_path, 'rb') as handle:
            if _sha(handle.read()) != h0:
                raise RuntimeError('the results file changed since it was read (H0); refusing')

    try:
        atomic_write(loaded.results_path, data, verify_temp=verify_temp, before_replace=before_replace)
    except BeforeReplaceError as error:                                              # steps 6-8
        raise ApproveRefusal(f'{action} not written: {error.message}', backup=backup) from None
    except DurabilityError:                                                          # step 9; step 10 not run
        raise ApproveFailure(DURABILITY_MESSAGE, failure_class='durability', backup=backup) from None
    # Step 10: verification, only after durability.
    try:
        with open(loaded.results_path, 'rb') as handle:
            if handle.read() != data:
                raise ValueError('results file differs from the document written')
        report = read_results(loaded.results_path)
        if report.refusal or report.items[loaded.index].approval_state != expected_state:
            raise ValueError('results file fails validation after writing')
    except (OSError, ValueError, IndexError):
        raise ApproveFailure(VERIFICATION_MESSAGE, failure_class='verification', backup=backup) from None
    return ApproveResult(action, loaded.item['original_path'], backup, state_before, expected_state)


def approve_item(results_path, original_path: str, *, rating: Any, preview_sha256_arg: Any,
                 now: datetime | None = None, hasher: Callable[[str], str] = None,
                 mountinfo_path: str | None = None) -> ApproveResult:
    """``approve <results.json> <original_path> --rating N --preview-sha256 <hex>``."""
    hasher = hasher or hashing_v3.sha256_file
    work = Path(os.path.abspath(os.fspath(results_path))).parent
    kwargs = {} if mountinfo_path is None else {'mountinfo_path': mountinfo_path}
    startup = startup_v3(work, 'exclusive', **kwargs)                                # step 1
    try:
        loaded = _load(results_path, original_path)                                  # step 2
        r = loaded.item
        source = loaded.doc.get('source_folder')
        if r['rating'] == 0:
            raise ApproveRefusal('rating 0 can never be approved')
        if not (isinstance(rating, int) and not isinstance(rating, bool)) or rating != r['rating']:
            raise ApproveRefusal(f'--rating is missing or differs from the item rating {r["rating"]}')
        if not (isinstance(preview_sha256_arg, str) and len(preview_sha256_arg) == 64 and set(preview_sha256_arg) <= _HEX):
            raise ApproveRefusal('--preview-sha256 is missing or not 64 lowercase hex characters')
        pv = check_preview(r['preview_path'], loaded.work, r['original_path'], source)
        if not pv.ok:
            raise ApproveRefusal(f'preview checks failed: {", ".join(pv.failed_ids)}')
        if preview_sha256(r['preview_path']) != preview_sha256_arg:
            raise ApproveRefusal('--preview-sha256 differs from the SHA-256 of the current preview file')
        current = _original_hash(r['original_path'], hasher)                         # step 4 (hashed once)
        if current is None:
            raise ApproveRefusal('the original is missing, not a regular file, or unreadable')
        if current != r['original_sha256']:
            raise ApproveRefusal(f'{ORIGINAL_CHANGED}: analyse again into a new output folder')
        state = derive_approval_state(r, work=loaded.work, source_folder=source, original_sha256_now=current)
        ok, text = reapproval_decision(state.state, 'approve')
        if not ok:
            raise ApproveRefusal(text)
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        updates = {'approved': True, 'approved_rating': rating, 'approved_at': stamp,
                   'approved_original_path': r['original_path'], 'approved_original_sha256': current,
                   'approved_preview_path': r['preview_path'], 'approved_preview_sha256': preview_sha256_arg}
        return _write(loaded, 'approval', updates, 'approved', state.state, now)
    finally:
        startup.lock.release()


def revoke_item(results_path, original_path: str, *, now: datetime | None = None,
                mountinfo_path: str | None = None) -> ApproveResult:
    """``approve <results.json> <original_path> --revoke``. Never hashes the original."""
    work = Path(os.path.abspath(os.fspath(results_path))).parent
    kwargs = {} if mountinfo_path is None else {'mountinfo_path': mountinfo_path}
    startup = startup_v3(work, 'exclusive', **kwargs)
    try:
        loaded = _load(results_path, original_path)
        state = derive_approval_state(loaded.item, work=loaded.work, source_folder=loaded.doc.get('source_folder'))
        ok, text = reapproval_decision(state.state, 'revoke')
        if not ok:
            raise ApproveRefusal(text)
        return _write(loaded, 'revocation', revoked_fields(), 'pending', state.state, now)
    finally:
        startup.lock.release()
