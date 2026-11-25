"""Preview checks PV1-PV6 (spec 5g3a section 12.5, V42; spec 20 item 20; implementation step S11).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S11 only): library code, NOT wired into any command. ``approve --show``,
``--open-preview``, approve and apply binding (S12-S14) call ``check_preview``; a failure is
never repaired. The preview is only inspected with lstat/realpath: it is never opened, read or
hashed here, and the original is never opened.

- PV1 ``preview_path`` is absolute and its parent, resolved, is exactly ``<work>/previews``
  resolved;
- PV2 ``<work>/previews`` holds a valid marker: ``.photo-curator-output`` is a regular file
  (not a symlink), as ``core.mark_output_dir`` writes it;
- PV3 the preview is not a symlink and is a regular file (lstat);
- PV4 the file name ends in ``.jpg`` (lowercase);
- PV5 the file name is exactly ``<original stem>__<first 10 hex of SHA-1(original_path)>.jpg``;
- PV6 the preview's resolved path is not the original and is not inside the source folder.

PV6 interpretation (APPROVED IMPLEMENTATION DEVIATION B-11c, not a spec revision): the default
output ``<source>/.photo-curator`` lies inside the source folder, which 12.3 accepts as an
output folder. A preview inside the source folder therefore passes PV6 only when it lies in
valid generated output, all of which must hold:
  (a) ``<work>`` is a real directory (not a symlink) holding a regular-file marker
      ``.photo-curator-output``;
  (b) ``<work>/previews`` is a real directory (not a symlink) holding a regular-file marker;
  (c) the preview's resolved path has ``<work>/previews`` (resolved) as its direct parent;
  (d) the preview's resolved path differs from the original's resolved path.
Nothing is exempted by folder name alone; a preview inside the source folder that fails any
of (a)-(d) fails PV6. PV1-PV3 are checked independently and are never relaxed by this rule.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath

from .core import OUTPUT_MARKER

APPLY_SKIP_MESSAGE = '[SKIP approval unverifiable: preview invalid]'
OPEN_PREVIEW_REFUSAL = 'refusing to open the preview: preview checks failed'
CHECK_IDS = ('PV1', 'PV2', 'PV3', 'PV4', 'PV5', 'PV6')


@dataclass(frozen=True)
class PreviewCheckResult:
    """``failures`` is a tuple of ``(check id, message)`` in PV order; empty means valid."""
    failures: tuple[tuple[str, str], ...]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def failed_ids(self) -> tuple[str, ...]:
        return tuple(check for check, _ in self.failures)


def expected_preview_name(original_path: str) -> str:
    stem = PurePosixPath(original_path).stem
    return f'{stem}__{hashlib.sha1(original_path.encode("utf-8")).hexdigest()[:10]}.jpg'


def _inside(path: str, folder: str) -> bool:
    folder = folder.rstrip('/') or '/'
    return path == folder or path.startswith(folder + '/')


def _real_dir_with_marker(directory: str) -> bool:
    try:
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            return False
        return stat.S_ISREG(os.lstat(os.path.join(directory, OUTPUT_MARKER)).st_mode)
    except OSError:
        return False


def in_valid_generated_previews(preview_path: str, work) -> bool:
    """B-11c conditions (a)-(c): the resolved preview sits directly in a valid, marked,
    non-symlink ``<work>/previews`` of a valid, marked, non-symlink ``<work>``."""
    work_path = os.path.abspath(os.fspath(work))
    previews_path = os.path.join(work_path, 'previews')
    if not (_real_dir_with_marker(work_path) and _real_dir_with_marker(previews_path)):
        return False
    previews_resolved = os.path.realpath(previews_path)
    if os.path.dirname(previews_resolved) != os.path.realpath(work_path):
        return False
    return os.path.dirname(os.path.realpath(preview_path)) == previews_resolved


# --- One predicate per check. Each returns None when it passes, otherwise its failure message.
# They are independent: none calls another, and none opens a file (lstat / realpath only).

def pv1(preview_path, work) -> str | None:
    if not isinstance(preview_path, str) or not os.path.isabs(preview_path):
        return 'preview_path is not an absolute path'
    previews = os.path.realpath(os.path.join(os.fspath(work), 'previews'))
    if os.path.realpath(os.path.dirname(preview_path)) != previews:
        return 'preview is not directly inside <work>/previews'
    return None


def pv2(work) -> str | None:
    marker = os.path.join(os.path.realpath(os.path.join(os.fspath(work), 'previews')), OUTPUT_MARKER)
    try:
        if stat.S_ISREG(os.lstat(marker).st_mode):
            return None
    except OSError:
        pass
    return '<work>/previews is not a marked output folder'


def pv3(preview_path) -> str | None:
    try:
        if stat.S_ISREG(os.lstat(preview_path).st_mode):
            return None
    except (OSError, TypeError, ValueError):
        pass
    return 'preview is missing, a symlink, or not a regular file'


def pv4(preview_path) -> str | None:
    if isinstance(preview_path, str) and os.path.basename(preview_path).endswith('.jpg'):
        return None
    return 'preview file name does not end in .jpg'


def pv5(preview_path, original_path) -> str | None:
    if (isinstance(preview_path, str) and isinstance(original_path, str)
            and os.path.basename(preview_path) == expected_preview_name(original_path)):
        return None
    return 'preview file name is not the name derived from original_path'


def pv6(preview_path, work, original_path, source_folder) -> str | None:
    resolved = os.path.realpath(preview_path)
    original_resolved = os.path.realpath(original_path) if isinstance(original_path, str) else None
    source = os.path.realpath(source_folder) if isinstance(source_folder, str) and source_folder else None
    if resolved == original_resolved:
        return 'preview is the original'
    if source is not None and _inside(resolved, source) and not in_valid_generated_previews(preview_path, work):
        return 'preview lies inside the source folder outside valid generated output'
    return None


def check_preview(preview_path, work, original_path, source_folder) -> PreviewCheckResult:
    """Run PV1-PV6 for one item. ``work`` is the folder of the results file being read.
    A non-absolute ``preview_path`` reports PV1 only (the other checks need a path)."""
    if not isinstance(preview_path, str) or not os.path.isabs(preview_path):
        return PreviewCheckResult((('PV1', pv1(preview_path, work)),))
    outcomes = (('PV1', pv1(preview_path, work)), ('PV2', pv2(work)), ('PV3', pv3(preview_path)),
                ('PV4', pv4(preview_path)), ('PV5', pv5(preview_path, original_path)),
                ('PV6', pv6(preview_path, work, original_path, source_folder)))
    return PreviewCheckResult(tuple((check, message) for check, message in outcomes if message is not None))


def open_preview_decision(result: PreviewCheckResult) -> tuple[bool, str | None]:
    """``--open-preview`` contract (12.3): open only when PV1-PV6 all pass. Pure decision: it
    never opens a file and never starts a viewer. Wiring the command is W-B."""
    if not isinstance(result, PreviewCheckResult):
        raise TypeError('PreviewCheckResult required')
    if result.ok:
        return True, None
    return False, f'{OPEN_PREVIEW_REFUSAL} ({", ".join(result.failed_ids)})'
