"""Analysis output artifacts, completion manifest and completeness checks (spec 5g3a
sections 12.8, 12.7 refusal list, V30/V31 file level, V46; implementation step S7a).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S7a only):
- library code; NOT wired into any CLI command or the v2 pipeline. Locks are the caller's
  responsibility (``locking_v3.startup_v3``); nothing here takes or releases a lock;
- the bytes of the results JSON, CSV and gallery are produced by later steps (S10); this
  module only writes them, in order, and binds them with the manifest;
- per-item read-time checks (V32-V45) belong to the reader (S11). Only the file-level checks
  V30 and V31 that 12.8 makes part of completeness are implemented here.

Rules:
- Write order 12.8: results, CSV, gallery, then the manifest, each through
  ``atomic_v3.atomic_write``. A failure before a replacement stops the run (no later
  artifact). A folder-fsync failure after the results, CSV or gallery replacement stops the
  run with a durability failure and no manifest. A folder-fsync failure after the manifest
  replacement is reported as a durability failure; nothing is rolled back.
- Preview accounting lists ``<work>/previews`` once with lstat, ignores the marker, and
  classifies every other entry as referenced or orphan. Nothing is ever deleted.
- Completeness is read-only: it never writes, repairs or deletes anything. Any failure to read
  the results file, the manifest, the CSV or the gallery (missing, not a regular file,
  permission denied, I/O error) makes the output incomplete; no raw ``OSError`` escapes.
- The 12.7 refusal list uses the spec's glob patterns literally (``fnmatchcase``).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .atomic_v3 import BeforeReplaceError, DurabilityError, atomic_write
from .core import OUTPUT_MARKER
from .schema_v3 import (BURST_SCOPE, CONFIDENCE_THRESHOLD, MAX_BURST_SIZE_RANGE, RUBRIC_VERSION,
                        SCHEMA_VERSION_V3, classify_error_entry)

RESULTS_NAME = 'ratings-review.json'
CSV_NAME = 'ratings-review.csv'
HTML_NAME = 'ratings-review.html'
MANIFEST_NAME = 'ratings-review.complete.json'
PREVIEWS_DIR = 'previews'
JOURNAL_NAME = 'archive-in-progress.json'
ARTIFACT_ORDER = (RESULTS_NAME, CSV_NAME, HTML_NAME, MANIFEST_NAME)
MANIFEST_FIELDS = ('run_id', 'created_at', 'results_sha256_at_analysis', 'csv_sha256',
                   'html_sha256', 'referenced_preview_count', 'orphan_preview_count')
SCHEMA_VERSIONS_READABLE = frozenset({1, 2, 3})
INCOMPLETE_MESSAGE = 'analysis output incomplete'

BACKUP_PATTERN = RESULTS_NAME + '.*.photo-curator.bak'     # spec 12.7, as a glob
TMP_PATTERN = '*.tmp-*'                                     # spec 12.6 / 12.7, as a glob

_HEX64 = re.compile(r'[0-9a-f]{64}')


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


# --- File-level checks that completeness depends on (V30, V31) --------------------------

def is_uuid_v4(value: Any) -> bool:
    """A string that ``uuid.UUID`` parses as an RFC 4122 version 4 UUID (spec 14.4). No
    particular string representation is required."""
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return parsed.variant == uuid.RFC_4122 and parsed.version == 4


def is_utc_z(value: Any) -> bool:
    """An ISO 8601 timestamp string ending in ``Z`` (UTC)."""
    if not isinstance(value, str) or not value.endswith('Z'):
        return False
    try:
        datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        return False
    return True


def v30_problems(raw: bytes) -> tuple[list[str], Any]:
    """V30: valid JSON object with ``schema_version`` 1, 2 or 3. Returns (problems, doc)."""
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return (['results file is not valid JSON'], None)
    if not isinstance(doc, dict):
        return (['results file is not a JSON object'], None)
    version = doc.get('schema_version')
    if not _is_int(version) or version not in SCHEMA_VERSIONS_READABLE:
        return ([f'unsupported schema_version {version!r}'], doc)
    return ([], doc)


def v31_problems(doc: dict) -> list[str]:
    """V31: v3 file-level fields (14.4) and every ``errors`` entry (14.5)."""
    problems = []
    if doc.get('rubric_version') != RUBRIC_VERSION:
        problems.append('rubric_version is not "v3"')
    erv = doc.get('evidence_rules_version')
    if not isinstance(erv, str) or erv.strip() == '':
        problems.append('evidence_rules_version missing or empty')
    if not is_uuid_v4(doc.get('run_id')):
        problems.append('run_id missing or not a version 4 UUID')
    ct = doc.get('confidence_threshold')
    if not _is_number(ct) or ct != CONFIDENCE_THRESHOLD:
        problems.append('confidence_threshold is not 0.6')
    low, high = MAX_BURST_SIZE_RANGE
    mbs = doc.get('max_burst_size')
    if not _is_int(mbs) or not low <= mbs <= high:
        problems.append('max_burst_size is not 2-12')
    if doc.get('burst_scope') != BURST_SCOPE:
        problems.append('burst_scope is not "per_session"')
    bws = doc.get('burst_window_seconds')
    if not _is_number(bws) or bws != bws or bws < 0 or bws == float('inf'):
        problems.append('burst_window_seconds invalid')
    if not isinstance(doc.get('results'), list):
        problems.append('results is not a list')
    errors = doc.get('errors')
    if not isinstance(errors, list):
        problems.append('errors is not a list')
    else:
        for index, entry in enumerate(errors):
            for finding in classify_error_entry(entry)['file_refusal']:
                problems.append(f'errors[{index}]: {finding}')
    return problems


# --- Preview accounting (12.8) -----------------------------------------------------------

@dataclass(frozen=True)
class PreviewCounts:
    referenced: int
    orphan: int
    orphan_names: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def listed(self) -> int:
        return self.referenced + self.orphan


def _referenced_paths(doc: dict) -> tuple[set[str], list[str]]:
    paths, warnings = set(), []
    for key in ('results', 'errors'):
        entries = doc.get(key)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or entry.get('preview_path') is None:
                continue
            value = entry['preview_path']
            if isinstance(value, str) and os.path.isabs(value):
                paths.add(os.path.normpath(value))
            else:
                warnings.append(f'{key}[{index}]: preview_path invalid; treated as no reference')
    return paths, warnings


def count_previews(work, doc: dict) -> PreviewCounts:
    """List ``<work>/previews`` once (lstat, no symlinks followed) and classify each entry.

    A missing ``previews`` folder gives zero entries. A ``previews`` entry that is a symlink
    or not a directory is refused with ``ValueError`` (nothing is listed through it).
    """
    previews = os.path.join(os.path.realpath(os.fspath(work)), PREVIEWS_DIR)
    try:
        st = os.lstat(previews)
    except FileNotFoundError:
        return PreviewCounts(0, 0, ())
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError(f'{previews} is a symlink or not a directory')
    referenced_paths, warnings = _referenced_paths(doc)
    referenced, orphans = 0, []
    for name in sorted(os.listdir(previews)):
        if name == OUTPUT_MARKER:
            continue
        full = os.path.join(previews, name)
        mode = os.lstat(full).st_mode
        if stat.S_ISREG(mode) and name.endswith('.jpg') and full in referenced_paths:
            referenced += 1
        else:
            orphans.append(name)
    return PreviewCounts(referenced, len(orphans), tuple(orphans), tuple(warnings))


# --- Manifest -------------------------------------------------------------------------------

def build_manifest(*, run_id: str, results_bytes: bytes, csv_bytes: bytes, html_bytes: bytes,
                   counts: PreviewCounts, created_at: str | None = None) -> dict[str, Any]:
    return {
        'run_id': run_id,
        'created_at': created_at or utc_now_z(),
        'results_sha256_at_analysis': sha256_bytes(results_bytes),
        'csv_sha256': sha256_bytes(csv_bytes),
        'html_sha256': sha256_bytes(html_bytes),
        'referenced_preview_count': counts.referenced,
        'orphan_preview_count': counts.orphan,
    }


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def manifest_problems(manifest: Any) -> list[str]:
    """Shape of a parsed manifest (12.8 table, V46 malformed)."""
    if not isinstance(manifest, dict):
        return ['manifest is not a JSON object']
    problems = [f'manifest field {name} missing' for name in MANIFEST_FIELDS if name not in manifest]
    if problems:
        return problems
    if not is_uuid_v4(manifest['run_id']):
        problems.append('manifest run_id is not a version 4 UUID')
    if not is_utc_z(manifest['created_at']):
        problems.append('manifest created_at is not a UTC timestamp with Z')
    for name in ('results_sha256_at_analysis', 'csv_sha256', 'html_sha256'):
        value = manifest[name]
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            problems.append(f'manifest {name} is not a SHA-256 hex digest')
    for name in ('referenced_preview_count', 'orphan_preview_count'):
        value = manifest[name]
        if not _is_int(value) or value < 0:
            problems.append(f'manifest {name} is not a non-negative integer')
    return problems


# --- Writing the four artifacts (12.8) -------------------------------------------------------

class OutputWriteError(Exception):
    """Analyze must stop and exit non-zero. ``failure_class`` is ``before_replace`` or
    ``durability``; ``artifact`` is the file name; ``written`` lists the artifacts that were
    replaced (in order) before the stop."""

    def __init__(self, message: str, *, failure_class: str, artifact: str,
                 written: tuple[str, ...], cause: BaseException):
        self.message, self.failure_class, self.artifact = message, failure_class, artifact
        self.written, self.cause = written, cause
        super().__init__(message)


@dataclass(frozen=True)
class OutputWriteResult:
    manifest: dict[str, Any]
    counts: PreviewCounts


def write_analysis_outputs(work, *, results_bytes: bytes, csv_bytes: bytes, html_bytes: bytes,
                           created_at: str | None = None,
                           _test_after_artifact: Callable[[str], None] | None = None) -> OutputWriteResult:
    """Write the four artifacts in 12.8 order. The caller holds the exclusive lock and has
    created every preview.

    ``_test_after_artifact(name)`` is a TEST SEAM ONLY (simulated crashes between writes). It
    runs after each successful write. Production callers must never pass it.

    The manifest's ``run_id`` is taken from ``results_bytes``, which must pass V30/V31 as a
    v3 file; otherwise ``ValueError`` is raised before anything is written.
    """
    work_dir = os.path.realpath(os.fspath(work))
    problems, doc = v30_problems(results_bytes)
    if not problems and doc.get('schema_version') != SCHEMA_VERSION_V3:
        problems = ['results file is not schema_version 3']
    if not problems:
        problems = v31_problems(doc)
    if problems:
        raise ValueError('refusing to write analysis output: ' + '; '.join(problems))
    written: list[str] = []

    def _write(name: str, data: bytes) -> None:
        try:
            atomic_write(os.path.join(work_dir, name), data)
        except BeforeReplaceError as error:
            raise OutputWriteError(f'{name}: not written; analysis stopped: {error.message}',
                                   failure_class='before_replace', artifact=name,
                                   written=tuple(written), cause=error) from error
        except DurabilityError as error:
            written.append(name)
            raise OutputWriteError(f'{name}: written but not confirmed durable; analysis stopped; '
                                   'output not claimed durable', failure_class='durability',
                                   artifact=name, written=tuple(written), cause=error) from error
        written.append(name)
        if _test_after_artifact is not None:
            _test_after_artifact(name)

    _write(RESULTS_NAME, results_bytes)
    _write(CSV_NAME, csv_bytes)
    _write(HTML_NAME, html_bytes)
    counts = count_previews(work_dir, doc)          # just before the manifest is written
    manifest = build_manifest(run_id=doc['run_id'], results_bytes=results_bytes,
                              csv_bytes=csv_bytes, html_bytes=html_bytes, counts=counts,
                              created_at=created_at)
    _write(MANIFEST_NAME, manifest_bytes(manifest))
    return OutputWriteResult(manifest, counts)


# --- Completeness (12.8, V46) ----------------------------------------------------------------

@dataclass(frozen=True)
class Completeness:
    """``applicable`` is False for v1/v2 files (no manifest; checked as today)."""
    applicable: bool
    complete: bool
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: Any = None


MISSING = 'missing'


def _reason(error: OSError) -> str:
    return f'unreadable ({os.strerror(error.errno) if error.errno else error})'


def _read_regular(path: str) -> tuple[bytes | None, str | None]:
    """``(bytes, None)`` for a readable regular, non-symlink file; otherwise ``(None, reason)``
    with reason ``'missing'``, ``'not a regular file'`` or ``'unreadable (...)'``. Never raises
    ``OSError``. Opened read-only, without following a symlink and without blocking."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, MISSING
    except OSError as error:
        return None, _reason(error)
    if not stat.S_ISREG(st.st_mode):
        return None, 'not a regular file'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, MISSING
    except OSError as error:
        return None, _reason(error)
    data, problem = None, None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):      # replaced between lstat and open
            problem = 'not a regular file'
        else:
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b''.join(chunks)
    except OSError as error:
        problem = _reason(error)
    finally:
        try:
            os.close(fd)                                 # attempted exactly once
        except OSError as error:                         # fail closed: never trust the bytes
            if problem is None:
                problem = _reason(error)
    return (data, None) if problem is None else (None, problem)


def check_completeness(work) -> Completeness:
    """Decide whether ``<work>`` holds complete v3 output. Read-only; never raises ``OSError``
    for the four artifacts. An unreadable results file is treated as incomplete v3 output
    (fail closed), because its schema version can't be known."""
    work_dir = os.path.realpath(os.fspath(work))
    results_raw, reason = _read_regular(os.path.join(work_dir, RESULTS_NAME))
    if results_raw is None:
        return Completeness(True, False, (f'{RESULTS_NAME} {reason}',))
    problems, doc = v30_problems(results_raw)
    if problems:
        return Completeness(True, False, tuple(problems))
    version = doc['schema_version']
    if version != SCHEMA_VERSION_V3:
        return Completeness(False, True, (), (), version)
    problems = [f'{RESULTS_NAME}: {p}' for p in v31_problems(doc)]
    manifest_raw, reason = _read_regular(os.path.join(work_dir, MANIFEST_NAME))
    manifest = None
    if manifest_raw is None:
        problems.append(f'{MANIFEST_NAME} {reason}')
    else:
        try:
            manifest = json.loads(manifest_raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            problems.append(f'{MANIFEST_NAME} does not parse')
        else:
            shape = manifest_problems(manifest)
            problems.extend(shape)
            if shape:
                manifest = None
    if manifest is not None and manifest['run_id'] != doc.get('run_id'):
        problems.append(f'{MANIFEST_NAME} run_id does not match {RESULTS_NAME}')
    for name, key in ((CSV_NAME, 'csv_sha256'), (HTML_NAME, 'html_sha256')):
        raw, reason = _read_regular(os.path.join(work_dir, name))
        if raw is None:
            problems.append(f'{name} {reason}')
        elif manifest is not None and sha256_bytes(raw) != manifest[key]:
            problems.append(f'{name} SHA-256 does not match {MANIFEST_NAME}')
    warnings: list[str] = []
    if not problems:
        warnings = recount_warnings(work_dir, manifest, doc)
    return Completeness(True, not problems, tuple(problems), tuple(warnings), version)


def recount_warnings(work, manifest: dict, doc: dict) -> list[str]:
    """V46 recount: a difference is a warning only; nothing changes."""
    try:
        counts = count_previews(work, doc)
    except (OSError, ValueError) as error:
        return [f'preview recount not possible: {error}']
    warnings = list(counts.warnings)
    if counts.referenced != manifest['referenced_preview_count']:
        warnings.append(f'referenced preview count differs from manifest '
                        f'({counts.referenced} now, {manifest["referenced_preview_count"]} at analysis)')
    if counts.orphan != manifest['orphan_preview_count']:
        warnings.append(f'orphan preview count differs from manifest '
                        f'({counts.orphan} now, {manifest["orphan_preview_count"]} at analysis)')
    return warnings


class IncompleteOutputRefusal(Exception):
    """apply / approve (all forms) / review / coverage report: refuse, change nothing."""

    def __init__(self, completeness: Completeness):
        self.completeness = completeness
        self.message = INCOMPLETE_MESSAGE + ': ' + '; '.join(completeness.problems)
        super().__init__(self.message)


def require_complete(work) -> Completeness:
    """Raise ``IncompleteOutputRefusal`` unless the v3 output is complete (v1/v2: pass)."""
    result = check_completeness(work)
    if result.applicable and not result.complete:
        raise IncompleteOutputRefusal(result)
    return result


# --- Existing output in <work> (12.7 refusal list) --------------------------------------------

APPROVAL_FIELDS = ('approved', 'approved_rating', 'approved_at', 'approved_original_path',
                   'approved_original_sha256', 'approved_preview_path', 'approved_preview_sha256')


@dataclass(frozen=True)
class ApprovalSummary:
    """Approvals in an existing results file, for the 12.7 refusal message only.

    - ``passing``: 12.2 structurally valid and passing binding checks 1, 2 and 4 (12.1). Binding
      checks 5 and 6 (preview validity PV1-PV6 and preview SHA-256) and check 3 (original
      content) are NOT run here, so these approvals are not verified;
    - ``invalidated``: valid per 12.2 but failing binding check 1, 2 or 4;
    - ``malformed``: breaking a 12.2 rule;
    - ``undetermined``: items whose approval state can't be derived (not an object, or
      ``needs_review`` / ``rating`` not usable for the 12.2 rules).
    """
    passing: int = 0
    invalidated: int = 0
    malformed: int = 0
    undetermined: int = 0

    def describe(self) -> str:
        return (f'{self.passing} passing structural and binding checks 1, 2, 4 (preview checks and '
                f'original content not verified), {self.invalidated} invalidated, '
                f'{self.malformed} malformed, {self.undetermined} undetermined')


def _is_abs_path(value: Any) -> bool:
    return isinstance(value, str) and value != '' and os.path.isabs(value)


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def approval_state_without_files(item: Any) -> str:
    """12.1 state from the stored fields only: ``'malformed'``, ``'not_approved'`` (pending or
    not_required), ``'invalidated'``, ``'passing'`` (checks 1, 2, 4 pass; 3, 5, 6 not run) or
    ``'undetermined'``. Never opens a file and never hashes anything."""
    if not isinstance(item, dict):
        return 'undetermined'
    if any(name not in item for name in APPROVAL_FIELDS):
        return 'malformed'
    approved = item['approved']
    others = [item[name] for name in APPROVAL_FIELDS[1:]]
    if not isinstance(approved, bool):
        return 'malformed'
    if approved is False:
        return 'not_approved' if all(value is None for value in others) else 'malformed'
    needs_review, rating = item.get('needs_review'), item.get('rating')
    if not isinstance(needs_review, bool) or not _is_int(rating):
        return 'undetermined'
    if needs_review is False or rating == 0:
        return 'malformed'
    ar = item['approved_rating']
    if not (_is_int(ar) and 1 <= ar <= 5 and is_utc_z(item['approved_at'])
            and _is_abs_path(item['approved_original_path']) and _is_abs_path(item['approved_preview_path'])
            and _is_hex64(item['approved_original_sha256']) and _is_hex64(item['approved_preview_sha256'])):
        return 'malformed'
    if item['approved_original_sha256'] != item.get('original_sha256'):
        return 'malformed'
    if ar != rating:                                                      # binding check 1
        return 'invalidated'
    if item['approved_original_path'] != item.get('original_path'):      # binding check 2
        return 'invalidated'
    if item['approved_preview_path'] != item.get('preview_path'):        # binding check 4
        return 'invalidated'
    return 'passing'


def summarize_approvals(doc: dict) -> ApprovalSummary | None:
    """Summary for a parsed results file; None when ``results`` isn't a list. v1/v2 items have
    no approval fields and are ``not_required`` (12.1), so they count in no category."""
    results = doc.get('results')
    if not isinstance(results, list):
        return None
    if doc.get('schema_version') != SCHEMA_VERSION_V3:
        return ApprovalSummary()
    counts = {'passing': 0, 'invalidated': 0, 'malformed': 0, 'undetermined': 0}
    for item in results:
        state = approval_state_without_files(item)
        if state in counts:
            counts[state] += 1
    return ApprovalSummary(**counts)


class ExistingOutputRefusal(Exception):
    """analyze refuses (12.7). ``found`` names the entries. ``approvals`` is an
    ``ApprovalSummary``, or None with ``approvals_unknown_reason`` when it can't be derived.
    The refusal itself never depends on reading any file."""

    def __init__(self, found: tuple[str, ...], approvals: ApprovalSummary | None,
                 approvals_unknown_reason: str | None = None):
        self.found, self.approvals = found, approvals
        self.approvals_unknown_reason = approvals_unknown_reason
        if approvals is not None:
            text = approvals.describe()
        else:
            text = f'unknown ({approvals_unknown_reason})'
        self.message = (f'output folder already contains analysis output: {", ".join(found)}; '
                        f'approvals in existing results: {text}. Use a new --output '
                        'folder (recommended) or --archive-existing')
        if JOURNAL_NAME in found:
            self.message = (f'archive in progress in output folder ({JOURNAL_NAME}); '
                            'run `recover-archive`; found: ' + ', '.join(found))
        super().__init__(self.message)


class OutputFolderUnreadableRefusal(ExistingOutputRefusal):
    """analyze refuses because ``<work>`` itself can't be listed: existing output can't be
    ruled out, so the folder is never treated as empty."""

    def __init__(self, reason: str):
        super().__init__((), None, reason)
        self.reason = reason
        self.message = (f'output folder cannot be listed ({reason}); refusing, because existing '
                        'analysis output cannot be ruled out')
        self.args = (self.message,)


def existing_output_entries(work) -> tuple[str, ...]:
    """Top-level names in ``<work>`` that are on the 12.7 refusal list. Read-only. A
    ``previews`` entry that is not a real directory, or can't be listed, counts (fail closed).
    If ``<work>`` itself can't be listed, ``OutputFolderUnreadableRefusal`` is raised."""
    work_dir = os.fspath(work)
    try:
        names = os.listdir(work_dir)
    except OSError as error:
        raise OutputFolderUnreadableRefusal(os.strerror(error.errno) if error.errno else str(error)) from None
    found = []
    for name in sorted(names):
        if (name in ARTIFACT_ORDER or name == JOURNAL_NAME
                or fnmatch.fnmatchcase(name, BACKUP_PATTERN) or fnmatch.fnmatchcase(name, TMP_PATTERN)):
            found.append(name)
        elif name == PREVIEWS_DIR:
            full = os.path.join(work_dir, name)
            try:
                if os.path.islink(full) or not os.path.isdir(full) or os.listdir(full):
                    found.append(name)
            except OSError:
                found.append(name)
    return tuple(found)


def _approvals(work_dir: str) -> tuple[ApprovalSummary | None, str | None]:
    raw, reason = _read_regular(os.path.join(work_dir, RESULTS_NAME))
    if raw is None:
        if reason == MISSING:
            return ApprovalSummary(), None
        return None, f'{RESULTS_NAME} {reason}'
    problems, doc = v30_problems(raw)
    if problems:
        return None, f'{RESULTS_NAME}: ' + '; '.join(problems)
    summary = summarize_approvals(doc)
    if summary is None:
        return None, f'{RESULTS_NAME}: results is not a list'
    return summary, None


def refuse_existing_output(work) -> None:
    """Raise ``ExistingOutputRefusal`` when ``<work>`` holds any 12.7 refusal-list entry."""
    found = existing_output_entries(work)
    if found:
        summary, reason = _approvals(os.fspath(work))
        raise ExistingOutputRefusal(found, summary, reason)
