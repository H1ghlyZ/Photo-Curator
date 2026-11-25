"""apply for every schema version (spec 5g3a section 13.1 check order, 13.2, 14.7, 12.1, 12.6;
spec 20 items 19, 19a, 16c apply part; implementation step S14, followed by Gate B).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S14 only): library code, NOT wired into the CLI; ``cli.cmd_apply`` (v2) is unchanged until
W-B. Tests use synthetic folders only; no real library is ever written.

Check order (13.1): 0a/0b start-up (network filesystem, exclusive lock, journal) for every
version; 0c parse and schema version; ``--yes`` safety stop as today; 0d (v3: V31 and
completeness); then per item 1, 2, 3, 4 (v3), 5, 6, 7, 8-10 (v3), 11, 11a, 12 (v3), 13, write.

Approved implementation interpretations reconciling 13.1 with 14.7 for v1/v2 files (the 14.7
guarantee wins: the same items are written with the same ratings and the same items skipped):
- K-14a: a missing original is skipped SILENTLY on v1/v2, as today; v3 prints check 7's message.
- K-14b: v1/v2 keep today's ``Path.is_file()`` (a symlinked original is followed and written);
  v3 uses lstat (check 7).
- K-14c: v1/v2 keep today's rating handling exactly (``isinstance(rating, int)`` and
  ``rating in range(1, 6)``; a non-integer or out-of-range rating is skipped silently;
  ``True`` passes as today and ``str(True)`` is written, as today). No new v1/v2 validation.
- Consequence of the same principle (S14-d): the per-message skip summary that 13.1 asks for is
  printed for v3 only; v1/v2 keep today's summary line unchanged. 14.7 differences 1 (messages
  for checks 5 and 6), 2-5 (start-up), 6 (check 13) and 7 (13.2 writes, check 11a) apply to v1/v2.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import hashing_v3
from .approval_v3 import derive_approval_state
from .core import SUPPORTED, in_generated_output, is_generated, result_original, result_preview
from .locking_v3 import startup_v3
from .reader_v3 import read_results
from .sidecar_v3 import SKIP_NOT_REGULAR, SidecarError, sidecar_not_regular, sidecar_path, write_sidecar

SAFETY_STOP = 'Safety stop: applying writes XMP sidecars. Re-run with --yes only after reviewing the JSON/CSV.'
SKIP_AMBIGUOUS = '[SKIP ambiguous original_path/path]'
SKIP_NOT_ORIGINAL = '[SKIP not an original image]'
SKIP_GENERATED = '[SKIP inside generated output]'
SKIP_INCONSISTENT = '[SKIP inconsistent result]'
SKIP_RATING_0 = '[SKIP rating 0: unevaluable]'
SKIP_BELOW_MIN = '[SKIP below min-rating]'
SKIP_ORIGINAL_MISSING = '[SKIP original missing]'
SKIP_EXISTING = '[SKIP existing sidecar]'
SKIP_UNREADABLE = '[SKIP original unreadable]'
SKIP_APPROVED_CHANGED = '[SKIP approval invalidated: original changed]'
SKIP_CHANGED = '[SKIP original changed since analysis]'
STOP_RESULTS_CHANGED = 'apply stopped: the results file changed during the run (H0); nothing more is applied'


class ApplyRefused(Exception):
    """The whole file is refused before any item is processed (nothing applied)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass
class ApplyReport:
    lines: list[str] = field(default_factory=list)
    applied: int = 0
    skipped: int = 0
    skips_by_message: Counter = field(default_factory=Counter)
    written: list[tuple[str, Any]] = field(default_factory=list)       # (original, rating)
    stopped: str | None = None                                         # stop reason; exit non-zero
    schema_version: Any = None

    @property
    def exit_code(self) -> int:
        return 1 if self.stopped else 0


def _sha_file(path: str) -> str:
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def run_apply(results_path, *, min_rating: int = 1, include_existing: bool = False, yes: bool = False,
              now: datetime | None = None, hasher: Callable[[str], str] | None = None,
              mountinfo_path: str | None = None) -> ApplyReport:
    hasher = hasher or hashing_v3.sha256_file
    path = os.path.abspath(os.fspath(results_path))
    work = Path(path).parent
    kwargs = {} if mountinfo_path is None else {'mountinfo_path': mountinfo_path}
    startup = startup_v3(work, 'exclusive', **kwargs)                              # 0a, 0b
    try:
        try:
            raw = Path(path).read_bytes()
            data = json.loads(raw.decode('utf-8'))                                 # 0c
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise ApplyRefused(f'results file refused: {error}') from None
        version = data.get('schema_version') if isinstance(data, dict) else None
        if not isinstance(data, dict) or version not in (1, 2, 3) or isinstance(version, bool) \
                or not isinstance(data.get('results'), list):
            raise ApplyRefused('Unsupported results schema')
        if not yes:
            raise SystemExit(SAFETY_STOP)
        h0 = hashlib.sha256(raw).hexdigest()
        report = ApplyReport(schema_version=version)
        reader = None
        if version == 3:
            reader = read_results(path)                                            # 0d: V31, completeness
            if reader.refusal:
                raise ApplyRefused(reader.refusal)
        for index, r in enumerate(data['results']):
            if _apply_item(report, r, index, version, reader, data, work, path, h0, min_rating,
                           include_existing, now, hasher):
                break
        if version == 3:
            for message, count in sorted(report.skips_by_message.items()):
                report.lines.append(f'  {message}: {count}')
        report.lines.append(f'Applied {report.applied}; skipped {report.skipped}. Original photo files were not modified.')
        return report
    finally:
        startup.lock.release()


def _skip(report: ApplyReport, message: str | None, text: str | None = None) -> bool:
    report.skipped += 1
    if message is not None:
        report.skips_by_message[message] += 1
        report.lines.append(f'{message} {text}' if text is not None else message)
    return False


def _apply_item(report, r, index, version, reader, data, work, results_path, h0, min_rating,
                include_existing, now, hasher) -> bool:
    """Returns True when apply must stop."""
    if not isinstance(r, dict):
        return _skip(report, SKIP_INCONSISTENT if version == 3 else SKIP_NOT_ORIGINAL, f'item {index}')
    rating = r.get('rating', 0)
    img = Path(result_original(r))
    preview = result_preview(r)
    if r.get('original_path') and r.get('path') and r['original_path'] != r['path']:            # 1
        return _skip(report, SKIP_AMBIGUOUS, img.name)
    if is_generated(img) or (preview and Path(preview) == img) or img.suffix.lower() not in SUPPORTED:  # 2
        return _skip(report, SKIP_NOT_ORIGINAL, str(img))
    if in_generated_output(img) or (preview and Path(preview).resolve() == img.resolve()):        # 3
        return _skip(report, SKIP_GENERATED, str(img))
    if version == 3:
        item = reader.items[index]
        if any(row in item.findings for row in ('V34', 'V35', 'V36', 'V45')):                     # 4
            return _skip(report, SKIP_INCONSISTENT, img.name)
        if rating == 0:                                                                           # 5
            return _skip(report, SKIP_RATING_0, img.name)
        if rating < min_rating:                                                                   # 6
            return _skip(report, SKIP_BELOW_MIN, img.name)
        try:                                                                                      # 7 (lstat)
            regular = stat.S_ISREG(os.lstat(img).st_mode)
        except OSError:
            regular = False
        if not regular:
            return _skip(report, SKIP_ORIGINAL_MISSING, img.name)
        state = derive_approval_state(r, work=work, source_folder=data.get('source_folder'))      # 8-10
        if state.state in ('malformed', 'pending', 'invalidated'):
            return _skip(report, state.message, img.name)
    else:                                                                    # v1/v2: today's rules (K-14a/b/c)
        if not isinstance(rating, int):
            return _skip(report, None)
        if rating == 0:                                                                           # 5 (14.7 #1)
            return _skip(report, SKIP_RATING_0, img.name)
        if rating < min_rating:                                                                   # 6 (14.7 #1)
            return _skip(report, SKIP_BELOW_MIN, img.name)
        if not img.is_file() or rating not in range(1, 6):                                       # silent, as today
            return _skip(report, None)
    if not include_existing and Path(sidecar_path(img)).exists():                                 # 11
        return _skip(report, SKIP_EXISTING, img.name)
    if sidecar_not_regular(img):                                                                  # 11a
        return _skip(report, SKIP_NOT_REGULAR, img.name)
    if version == 3:                                                                              # 12
        try:
            current = hasher(str(img))
        except hashing_v3.HashError:
            return _skip(report, SKIP_UNREADABLE, img.name)
        if current != r['original_sha256']:
            return _skip(report, SKIP_APPROVED_CHANGED if r.get('approved') is True else SKIP_CHANGED, img.name)
    try:                                                                                          # 13
        if _sha_file(results_path) != h0:
            report.stopped = STOP_RESULTS_CHANGED
            report.lines.append(f'{STOP_RESULTS_CHANGED} (stopped at {img.name}; applied so far: {report.applied})')
            return True
    except OSError:
        report.stopped = STOP_RESULTS_CHANGED
        report.lines.append(f'{STOP_RESULTS_CHANGED} (stopped at {img.name}; applied so far: {report.applied})')
        return True
    try:
        written = write_sidecar(img, rating, now=now)
    except SidecarError as error:
        report.stopped = error.message
        report.lines.append(error.message)
        if error.leftover:
            report.lines.append(f'temporary file left behind: {error.leftover}')
        report.lines.append(f'apply stopped; applied so far: {report.applied}')
        return True
    report.applied += 1
    report.written.append((str(img), rating))
    report.lines.append(f'[OK] {img.name}: {rating} stars -> {Path(written.sidecar).name}')
    return False
