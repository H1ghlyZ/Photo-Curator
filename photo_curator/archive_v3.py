"""Journalled archive of existing output and explicit recovery (spec 5g3a sections 12.7
``--archive-existing``, 12.9, 12.6 lock rows, V47; implementation step S7b).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S7b only):
- library code; NOT wired into any CLI command (``--archive-existing`` and
  ``recover-archive`` are enabled together in W-B, decision M-6);
- ``archive_existing`` runs inside analyze, which already holds the exclusive lock; the
  caller passes that lock. The three recovery functions take their own lock through
  ``locking_v3.startup_v3(..., exempt_journal=True)`` (shared for the report, exclusive for
  ``--complete`` / ``--rollback``) and release it before returning.

Journal (``<work>/archive-in-progress.json``, written with ``atomic_v3.atomic_write``):
    {"archive_folder": "archive/<YYYYMMDDTHHMMSSZ>",
     "entries": [{"source": "<name>", "target": "archive/<ts>/<name>", "kind": "file",
                  "sha256": "<64 hex>"},
                 {"source": "previews", "target": "archive/<ts>/previews", "kind": "folder",
                  "names": [<sorted entry names>], "count": <n>}]}
Sources are single top-level names in ``<work>``; targets are exactly
``<archive_folder>/<source>``. A journal that breaks any of these rules is malformed, so a
hand-edited journal can never move anything outside ``<work>`` or outside its archive folder.

Schema boundary (12.9): recovery reads only the journal (parsed), lstat of each source and
target, the bytes of each journalled file (hashed only) and the entry names of ``previews``.
It never parses the results file, the CSV, the gallery or the manifest.

Nothing in this module deletes anything. Failures never roll back and never retry.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .atomic_v3 import BeforeReplaceError, DurabilityError, atomic_write
from .core import mark_output_dir
from .locking_v3 import JOURNAL_NAME, LOCK_NAME, MOUNTINFO, LockHandle, startup_v3
from .output_v3 import ExistingOutputRefusal, OutputFolderUnreadableRefusal, refuse_existing_output

ARCHIVE_DIR = 'archive'
ARCHIVED_JOURNAL = 'archive-journal.json'
ROLLED_BACK_JOURNAL = 'archive-journal.rolled-back.json'
PREVIEWS_DIR = 'previews'
NO_RECOVERY_MESSAGE = 'no archive recovery pending in {work}'
NO_ARCHIVE_MESSAGE = 'no archive in progress: nothing to complete or roll back'
STATES = ('pending', 'done', 'conflict', 'lost')

_TS_RE = re.compile(r'\d{8}T\d{6}Z')
_HEX64 = re.compile(r'[0-9a-f]{64}')


def archive_timestamp(now: datetime | None = None) -> str:
    """M-16: ``YYYYMMDDTHHMMSSZ`` in UTC."""
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


# --- Errors ---------------------------------------------------------------------------------

class ArchiveRefusal(Exception):
    """Archive refused before the journal was written: nothing changed."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ArchiveFailure(Exception):
    """Archive stopped after the journal step began. The journal may exist and then blocks the
    folder until ``recover-archive`` resolves it. ``done``/``pending`` list sources."""

    def __init__(self, message: str, *, stage: str, done: tuple[str, ...] = (),
                 pending: tuple[str, ...] = (), journal_written: bool = True):
        self.message, self.stage, self.done, self.pending = message, stage, done, pending
        self.journal_written = journal_written
        super().__init__(message)


class RecoveryRefusal(Exception):
    """``--complete`` / ``--rollback`` refused before any rename: nothing changed."""

    def __init__(self, message: str, problems: tuple[str, ...] = ()):
        self.message, self.problems = message, problems
        super().__init__(message)


class RecoveryFailure(Exception):
    """A rename (or the final verification) failed partway; the journal is left in place."""

    def __init__(self, message: str, moved: tuple[str, ...]):
        self.message, self.moved = message, moved
        super().__init__(message)


# --- Small filesystem helpers (never raise OSError) ------------------------------------------

def _strerror(error: OSError) -> str:
    return os.strerror(error.errno) if error.errno else str(error)


def _sha256_regular(path: str) -> str | None:
    """SHA-256 of a regular, non-symlink file, streamed; None if that can't be established."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return None
    digest, ok = hashlib.sha256(), True
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            ok = False
        else:
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        ok = False
    finally:
        try:
            os.close(fd)
        except OSError:
            ok = False
    return digest.hexdigest() if ok else None


def _is_real_dir(path: str) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _names(path: str) -> list[str] | None:
    """Sorted entry names of a real (non-symlink) directory, or None."""
    if not _is_real_dir(path):
        return None
    try:
        return sorted(os.listdir(path))
    except OSError:
        return None


def _read_journal_bytes(path: str) -> tuple[bytes | None, str | None]:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, 'missing'
    except OSError as error:
        return None, f'unreadable ({_strerror(error)})'
    if not stat.S_ISREG(st.st_mode):
        return None, 'not a regular file'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as error:
        return None, f'unreadable ({_strerror(error)})'
    data, problem = b'', None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
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
        problem = f'unreadable ({_strerror(error)})'
    finally:
        try:
            os.close(fd)
        except OSError as error:
            problem = problem or f'unreadable ({_strerror(error)})'
    return (data, None) if problem is None else (None, problem)


# --- Journal model and well-formedness --------------------------------------------------------

def _is_single_name(value) -> bool:
    return (isinstance(value, str) and value not in ('', '.', '..') and '/' not in value
            and '\0' not in value)


def journal_problems(journal) -> list[str]:
    """Well-formedness of a parsed journal (12.9 "it must be well-formed")."""
    if not isinstance(journal, dict):
        return ['journal is not a JSON object']
    problems = []
    folder = journal.get('archive_folder')
    parts = folder.split('/') if isinstance(folder, str) else []
    if len(parts) != 2 or parts[0] != ARCHIVE_DIR or not _TS_RE.fullmatch(parts[1]):
        problems.append('archive_folder is not "archive/<YYYYMMDDTHHMMSSZ>"')
        folder = None
    entries = journal.get('entries')
    if not isinstance(entries, list) or not entries:
        return problems + ['entries is not a non-empty list']
    seen = set()
    for index, entry in enumerate(entries):
        where = f'entries[{index}]'
        if not isinstance(entry, dict):
            problems.append(f'{where} is not an object')
            continue
        source = entry.get('source')
        if not _is_single_name(source) or source in (JOURNAL_NAME, ARCHIVE_DIR, LOCK_NAME):
            problems.append(f'{where}: source is not an allowed top-level name')
            continue
        if source in seen:
            problems.append(f'{where}: duplicate source {source!r}')
        seen.add(source)
        if folder is not None and entry.get('target') != f'{folder}/{source}':
            problems.append(f'{where}: target is not {folder}/{source}')
        kind = entry.get('kind')
        if kind == 'file':
            if not (isinstance(entry.get('sha256'), str) and _HEX64.fullmatch(entry['sha256'])):
                problems.append(f'{where}: sha256 is not 64 lowercase hex characters')
        elif kind == 'folder':
            names, count = entry.get('names'), entry.get('count')
            if source != PREVIEWS_DIR:
                problems.append(f'{where}: only "previews" may be a folder entry')
            if (not isinstance(names, list) or not all(_is_single_name(n) for n in names)
                    or names != sorted(set(names))):
                problems.append(f'{where}: names is not a sorted list of unique entry names')
            elif not (isinstance(count, int) and not isinstance(count, bool) and count == len(names)):
                problems.append(f'{where}: count does not equal the number of names')
        else:
            problems.append(f'{where}: kind is not "file" or "folder"')
    return problems


@dataclass(frozen=True)
class EntryStatus:
    source: str
    target: str
    kind: str
    state: str                  # pending / done / conflict / lost
    verified: bool | None       # content check at the existing location; None if not checkable
    detail: str = ''


def _entry_status(work_dir: str, entry: dict) -> EntryStatus:
    src = os.path.join(work_dir, entry['source'])
    tgt = os.path.join(work_dir, entry['target'])
    s, t = os.path.lexists(src), os.path.lexists(tgt)
    state = {(True, False): 'pending', (False, True): 'done', (True, True): 'conflict',
             (False, False): 'lost'}[(s, t)]
    verified, detail = None, ''
    if state in ('pending', 'done'):
        where = src if state == 'pending' else tgt
        if entry['kind'] == 'file':
            digest = _sha256_regular(where)
            verified = digest == entry['sha256']
            if not verified:
                detail = 'not a readable regular file' if digest is None else 'SHA-256 differs from journal'
        else:
            names = _names(where)
            verified = names == entry['names']
            if not verified:
                detail = ('not a readable real directory' if names is None
                          else 'previews listing differs from journal')
    return EntryStatus(entry['source'], entry['target'], entry['kind'], state, verified, detail)


@dataclass(frozen=True)
class RecoveryReport:
    journal_present: bool
    message: str
    well_formed: bool = False
    problems: tuple[str, ...] = ()
    entries: tuple[EntryStatus, ...] = ()
    archive_folder: str | None = None
    archive_folder_ok: bool = False


def _inspect(work_dir: str) -> tuple[RecoveryReport, dict | None]:
    path = os.path.join(work_dir, JOURNAL_NAME)
    if not os.path.lexists(path):
        return RecoveryReport(False, NO_RECOVERY_MESSAGE.format(work=work_dir)), None
    raw, reason = _read_journal_bytes(path)
    if raw is None:
        return RecoveryReport(True, f'journal {reason}', False, (f'journal {reason}',)), None
    try:
        journal = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return RecoveryReport(True, 'journal does not parse', False, ('journal does not parse',)), None
    problems = journal_problems(journal)
    if problems:
        return RecoveryReport(True, 'journal malformed', False, tuple(problems)), None
    entries = tuple(_entry_status(work_dir, e) for e in journal['entries'])
    folder = journal['archive_folder']
    folder_ok = (_is_real_dir(os.path.join(work_dir, ARCHIVE_DIR))
                 and _is_real_dir(os.path.join(work_dir, folder)))
    return RecoveryReport(True, 'archive recovery pending', True, (), entries, folder, folder_ok), journal


# --- Archive (12.7 --archive-existing, 12.9 steps 1-6) ----------------------------------------

@dataclass(frozen=True)
class ArchiveResult:
    archive_folder: str                 # relative to <work>
    moved: tuple[str, ...]
    approvals_summary: object           # output_v3.ApprovalSummary or None
    approvals_unknown_reason: str | None


def _check_exclusive(work_dir: str, lock: LockHandle) -> None:
    if (not isinstance(lock, LockHandle) or lock.released or lock.mode != 'exclusive'
            or os.path.realpath(lock.path) != os.path.join(work_dir, LOCK_NAME)):
        raise ArchiveRefusal('archive requires the exclusive lock on this output folder')


def _verify_all_done(work_dir: str, journal: dict) -> list[str]:
    """Step 6: every target exists and matches; previews holds the names; no source remains."""
    problems = []
    for entry in journal['entries']:
        status = _entry_status(work_dir, entry)
        if status.state != 'done':
            problems.append(f'{entry["source"]}: state {status.state}, expected done')
        elif not status.verified:
            problems.append(f'{entry["source"]}: {status.detail}')
    return problems


def archive_existing(work, lock: LockHandle, *, now: datetime | None = None) -> ArchiveResult:
    """Move every 12.7 refusal-list item into ``<work>/archive/<ts>/`` (12.9 steps 1-6).
    The caller holds ``lock`` (exclusive, on this folder) for the whole call."""
    work_dir = os.path.realpath(os.fspath(work))
    _check_exclusive(work_dir, lock)
    if os.path.lexists(os.path.join(work_dir, JOURNAL_NAME)):
        raise ArchiveRefusal(f'archive in progress ({JOURNAL_NAME}); run `recover-archive`')
    try:
        refuse_existing_output(work_dir)
        raise ArchiveRefusal('nothing to archive: no existing analysis output')
    except OutputFolderUnreadableRefusal as refusal:
        raise ArchiveRefusal(refusal.message) from None
    except ExistingOutputRefusal as refusal:
        found, approvals, approvals_reason = refusal.found, refusal.approvals, refusal.approvals_unknown_reason

    # Step 1: move list, hashes, previews names; refuse if anything is unsafe or a target exists.
    folder = f'{ARCHIVE_DIR}/{archive_timestamp(now)}'
    archive_root = os.path.join(work_dir, ARCHIVE_DIR)
    if os.path.lexists(archive_root) and not _is_real_dir(archive_root):
        raise ArchiveRefusal(f'{ARCHIVE_DIR} exists and is not a real directory')
    if os.path.lexists(os.path.join(work_dir, folder)):
        raise ArchiveRefusal(f'archive target {folder} already exists')
    entries = []
    for name in found:
        src = os.path.join(work_dir, name)
        try:
            mode = os.lstat(src).st_mode
        except OSError as error:
            raise ArchiveRefusal(f'{name}: cannot inspect ({_strerror(error)})') from None
        if name == PREVIEWS_DIR:
            names = _names(src)
            if names is None:
                raise ArchiveRefusal(f'{name}: not a readable real directory')
            entries.append({'source': name, 'target': f'{folder}/{name}', 'kind': 'folder',
                            'names': names, 'count': len(names)})
        elif stat.S_ISREG(mode):
            digest = _sha256_regular(src)
            if digest is None:
                raise ArchiveRefusal(f'{name}: cannot be read to hash')
            entries.append({'source': name, 'target': f'{folder}/{name}', 'kind': 'file', 'sha256': digest})
        else:
            raise ArchiveRefusal(f'{name}: not a regular file; refusing to archive it')
    journal = {'archive_folder': folder, 'entries': entries}
    sources = tuple(e['source'] for e in entries)

    # Step 2: journal, atomically. From here the folder is blocked.
    try:
        atomic_write(os.path.join(work_dir, JOURNAL_NAME),
                     (json.dumps(journal, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
    except BeforeReplaceError as error:
        raise ArchiveFailure(f'journal not written; nothing moved: {error.message}', stage='journal',
                             pending=sources, journal_written=False) from error
    except DurabilityError as error:
        raise ArchiveFailure(f'journal written but not confirmed durable; archive stopped; folder '
                             f'blocked until `recover-archive`: {error.message}', stage='journal',
                             pending=sources) from error

    # Step 3: archive folder, marked as output.
    try:
        if not os.path.lexists(archive_root):
            os.mkdir(archive_root)
        os.mkdir(os.path.join(work_dir, folder))
        mark_output_dir(Path(work_dir, folder))
    except (OSError, RuntimeError) as error:
        raise ArchiveFailure(f'cannot create archive folder {folder}: {error}; folder blocked until '
                             '`recover-archive`', stage='archive folder', pending=sources) from None

    # Steps 4-5: renames in journal order; stop at the first failure.
    done: list[str] = []
    for entry in entries:
        try:
            os.rename(os.path.join(work_dir, entry['source']), os.path.join(work_dir, entry['target']))
        except OSError as error:
            raise ArchiveFailure(f'moving {entry["source"]} failed ({_strerror(error)}); archive stopped; '
                                 'folder blocked until `recover-archive`', stage='rename',
                                 done=tuple(done), pending=sources[len(done):]) from None
        done.append(entry['source'])

    # Step 6: verify, then move the journal into the archive folder.
    problems = _verify_all_done(work_dir, journal)
    if problems:
        raise ArchiveFailure('archive verification failed: ' + '; '.join(problems), stage='verify',
                             done=tuple(done))
    try:
        os.rename(os.path.join(work_dir, JOURNAL_NAME), os.path.join(work_dir, folder, ARCHIVED_JOURNAL))
    except OSError as error:
        raise ArchiveFailure(f'moving the journal failed ({_strerror(error)}); folder blocked until '
                             '`recover-archive`', stage='journal move', done=tuple(done)) from None
    return ArchiveResult(folder, tuple(done), approvals, approvals_reason)


# --- recover-archive (12.9) -------------------------------------------------------------------

def recover_report(work, *, mountinfo_path: str = MOUNTINFO) -> RecoveryReport:
    """Report mode: shared lock, changes nothing (the lock file may be created, 12.6)."""
    startup = startup_v3(work, 'shared', exempt_journal=True, mountinfo_path=mountinfo_path)
    try:
        return _inspect(os.path.realpath(os.fspath(work)))[0]
    finally:
        startup.lock.release()


@dataclass(frozen=True)
class RecoveryResult:
    mode: str
    moved: tuple[str, ...]
    journal_moved_to: str


def _preflight(work_dir: str, mode: str) -> tuple[dict, RecoveryReport]:
    report, journal = _inspect(work_dir)
    if not report.journal_present:
        raise RecoveryRefusal(NO_ARCHIVE_MESSAGE)
    if journal is None:
        raise RecoveryRefusal(f'refusing: {report.message}', report.problems)
    problems = []
    if not report.archive_folder_ok:
        problems.append(f'archive folder {report.archive_folder} is missing or not a real directory')
    acting = 'pending' if mode == 'complete' else 'done'
    for status in report.entries:
        if status.state in ('conflict', 'lost'):
            problems.append(f'{status.source}: state {status.state}')
        elif status.state == acting and not status.verified:
            problems.append(f'{status.source}: {status.detail}')
    if problems:
        raise RecoveryRefusal('refusing before any move: ' + '; '.join(problems), tuple(problems))
    return journal, report


def _recover(work, mode: str, mountinfo_path: str) -> RecoveryResult:
    startup = startup_v3(work, 'exclusive', exempt_journal=True, mountinfo_path=mountinfo_path)
    try:
        work_dir = os.path.realpath(os.fspath(work))
        journal, report = _preflight(work_dir, mode)
        by_source = {e['source']: e for e in journal['entries']}
        order = [s for s in report.entries if s.state == ('pending' if mode == 'complete' else 'done')]
        if mode == 'rollback':
            order.reverse()
        moved: list[str] = []
        for status in order:
            entry = by_source[status.source]
            src, tgt = os.path.join(work_dir, entry['source']), os.path.join(work_dir, entry['target'])
            a, b = (src, tgt) if mode == 'complete' else (tgt, src)
            try:
                os.rename(a, b)
            except OSError as error:
                raise RecoveryFailure(f'moving {entry["source"]} failed ({_strerror(error)}); stopped; '
                                      'journal left in place', tuple(moved)) from None
            moved.append(entry['source'])
        folder = journal['archive_folder']
        if mode == 'complete':
            problems = _verify_all_done(work_dir, journal)
            if problems:
                raise RecoveryFailure('verification after completing failed: ' + '; '.join(problems),
                                      tuple(moved))
            final = f'{folder}/{ARCHIVED_JOURNAL}'
        else:
            final = f'{folder}/{ROLLED_BACK_JOURNAL}'
        if os.path.lexists(os.path.join(work_dir, final)):
            raise RecoveryFailure(f'{final} already exists; journal left in place', tuple(moved))
        try:
            os.rename(os.path.join(work_dir, JOURNAL_NAME), os.path.join(work_dir, final))
        except OSError as error:
            raise RecoveryFailure(f'moving the journal failed ({_strerror(error)})', tuple(moved)) from None
        return RecoveryResult(mode, tuple(moved), final)
    finally:
        startup.lock.release()


def recover_complete(work, *, mountinfo_path: str = MOUNTINFO) -> RecoveryResult:
    """``--complete``: move only ``pending`` entries, then run step 6 and move the journal."""
    return _recover(work, 'complete', mountinfo_path)


def recover_rollback(work, *, mountinfo_path: str = MOUNTINFO) -> RecoveryResult:
    """``--rollback``: move only ``done`` entries back (reverse journal order); keep the archive
    folder; rename the journal to ``archive-journal.rolled-back.json`` inside it."""
    return _recover(work, 'rollback', mountinfo_path)
