"""v3 analysis pipeline behind ``photo-curator analyze`` (spec 5g3a section 15.4 with 12.6,
12.7, 12.8, 14.3, 14.3a, 14.5, 8.0; implementation checkpoint W-A).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (W-A, CLI-1..3; W-B, CLI-8):
- ``analyze`` runs only this pipeline (M-22(a)); the v2 pipeline functions stay in ``core``
  for tests but are never called from here;
- W-B (CLI-8): the temporary DW-1 text is withdrawn. The 12.7 refusal names the artifacts
  found and the approvals and offers both workflows; ``--archive-existing`` runs the journalled
  archive (``archive_v3.archive_existing``, 12.9) under the exclusive lock already held, then
  the new analysis starts. The refusal triggers are exactly those of 12.7 (unchanged);
- external tools are reached through ``core.run`` and ``core.shutil.which`` so tests can
  replace them with fakes (M-11). No original is ever opened for writing.

Order (15.4 step 1, CLI-2): arguments and ``--output`` are validated before any folder is
created; then the filesystem check, ``mkdir``, ``startup_v3`` (filesystem, exclusive lock,
journal), the 12.7 refusal, batch planning and P1-P4 — all before any preview or Claude call.

Decisions (W-A):
- DWA-1 image discovery (read-only) happens before the output folder is created, so a folder
  without images creates nothing;
- DWA-2 batches are planned over every discovered original (15.4 order); originals rejected
  at ``stage = original`` or ``preview`` are removed from their batch before the Claude call,
  keep their planned ``burst_group`` / ``burst_sequence`` in the error entry, and a batch left
  empty is not sent;
- DWA-3 ``--burst-window`` must be a finite number >= 0 (V31 requires it in the results
  file); it is checked with the other arguments, before anything is written;
- DWA-4 (M-4) previews use the ``make_preview`` name (PV5) but never unlink: an existing file
  with that name is a ``stage = preview`` error.

Fix checkpoint F-1 (OT-1: Claude could not read a prompt in ``/tmp`` under
``permissions.blockReadsOutsideWorkingDirectories``): the prompt lives in a private temporary
folder inside the output folder and Claude gets ``--add-dir`` for that folder and ``previews``
only (K-F1); an output path containing whitespace is refused before anything is written (K-F3);
the working directory of the Claude call is unchanged (K-F4).
"""
from __future__ import annotations

import dataclasses
import hashlib
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from . import core
from .archive_v3 import ArchiveFailure, ArchiveRefusal, archive_existing
from .artifacts_v3 import csv_bytes, gallery_bytes
from .bursts_v3 import BurstPreconditionError, PlannedFrame, check_burst_preconditions_v3
from .capture_time import format_capture_time, wall_clock_seconds
from .document_v3 import assemble_document, document_bytes
from .hashing_v3 import check_original_with_preview, original_error_entry
from .locking_v3 import check_filesystem, startup_v3
from .output_v3 import (ExistingOutputRefusal, OutputFolderUnreadableRefusal, OutputWriteError,
                        refuse_existing_output, write_analysis_outputs)
from .prompt_v3 import PromptFrame, ResponseFormatError, build_prompt, parse_claude_output
from .schema_v3 import MAX_BURST_SIZE_RANGE, make_error_entry
from .validate_v3 import FrameContext, validate_batch_v3

WORKFLOW_NEW_OUTPUT = ('(1) recommended: run analyze again with a different --output folder; the existing '
                       'folder, its results, approvals and previews stay exactly as they are')
WORKFLOW_ARCHIVE = ('(2) run analyze again with --archive-existing: every listed item is moved into '
                    '<output>/archive/<UTC timestamp>/ (nothing is deleted); archived approvals are never '
                    'carried into the new results')


class AnalyzeError(Exception):
    """analyze refuses or fails: message to stderr, exit code 1 (M-23)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


# --- Arguments (CLI-2, DWA-3) --------------------------------------------------------------------

def validate_arguments(args) -> None:
    low, high = MAX_BURST_SIZE_RANGE
    mbs = getattr(args, 'max_burst_size', None)
    if not (isinstance(mbs, int) and not isinstance(mbs, bool) and low <= mbs <= high):
        raise AnalyzeError(f'--max-burst-size must be an integer from {low} to {high}, got {mbs!r}')
    bs = getattr(args, 'batch_size', None)
    if not (isinstance(bs, int) and not isinstance(bs, bool) and 1 <= bs <= 12):
        raise AnalyzeError(f'--batch-size must be 1..12, got {bs!r}')
    window = getattr(args, 'burst_window', None)
    if not (isinstance(window, (int, float)) and not isinstance(window, bool)
            and math.isfinite(window) and window >= 0):
        raise AnalyzeError(f'--burst-window must be a number >= 0, got {window!r}')


# --- 12.7 refusal text (full, W-B) ------------------------------------------------------------------

def refusal_message(refusal: ExistingOutputRefusal) -> str:
    if refusal.approvals is not None:
        approvals = refusal.approvals.describe()
    else:
        approvals = f'unknown ({refusal.approvals_unknown_reason})'
    return (f'output folder already contains analysis output: {", ".join(refusal.found)}; '
            f'approvals in existing results: {approvals}. Two workflows: {WORKFLOW_NEW_OUTPUT}. '
            f'{WORKFLOW_ARCHIVE}.')


# --- Previews (M-4, DWA-4) ------------------------------------------------------------------------

class PreviewError(Exception):
    pass


def preview_name(src: Path) -> str:
    """The ``make_preview`` name (PV5): ``<stem>__<first 10 hex of SHA-1(original_path)>.jpg``."""
    return f'{src.stem}__{hashlib.sha1(str(src).encode("utf-8")).hexdigest()[:10]}.jpg'


def make_preview_v3(src: Path, preview_dir: Path, max_edge: int) -> Path:
    out = preview_dir / preview_name(src)
    if os.path.lexists(out):
        raise PreviewError(f'preview name already exists: {out.name}')
    if src.suffix.lower() in core.RAW:
        exe = core.shutil.which('darktable-cli')
        if not exe:
            raise PreviewError('RAW file found but darktable-cli is missing')
        p = core.run([exe, str(src), str(out), '--width', str(max_edge), '--height', str(max_edge),
                      '--hq', 'true'], timeout=300)
        if p.returncode != 0 or not out.is_file():
            raise PreviewError('darktable-cli failed: ' + (p.stderr or '')[-1000:])
        return out
    try:
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            with open(out, 'xb') as handle:                       # exclusive: never overwrite
                im.save(handle, 'JPEG', quality=90, optimize=True)
    except FileExistsError:
        raise PreviewError(f'preview name already exists: {out.name}') from None
    except Exception as error:
        raise PreviewError(f'cannot create preview: {error}') from None
    return out


# --- Claude call ------------------------------------------------------------------------------------

PROMPT_DIR_PREFIX = '.claude-prompt-'
PROMPT_FILE_NAME = 'prompt.txt'


def _remove_prompt(prompt_file: str, prompt_dir: str) -> None:
    """F-1 cleanup: ``os.unlink`` the prompt, then ``os.rmdir`` its folder; never ``rmtree``.
    A folder that can't be removed is reported and left in place (never deleted recursively)."""
    try:
        os.unlink(prompt_file)
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f'[LEFTOVER] {prompt_file} (prompt file not removed: {error.strerror or error})')
    try:
        os.rmdir(prompt_dir)
    except OSError as error:
        print(f'[LEFTOVER] {prompt_dir} (prompt folder not removed: {error.strerror or error})')


def call_claude(frames: list[PromptFrame], model: str, work: Path) -> list[Any]:
    """One batch through the Claude CLI (via ``core.run``); returns the raw ``photos`` array.

    F-1: the prompt is written to a private temporary folder inside the output folder
    (``<work>/.claude-prompt-*/prompt.txt``, folder 0700, file 0600, separate from ``previews``),
    and Claude is given read access to exactly that folder and ``<work>/previews`` through
    ``--add-dir`` (last on the command line; it takes several values). Claude keeps only the
    Read tool; ``permissions.blockReadsOutsideWorkingDirectories`` stays in force, so ``/tmp``,
    the home folder, the source folder and the rest of the output folder stay closed."""
    claude = core.shutil.which('claude')
    if not claude:
        raise RuntimeError('Claude Code CLI not found. Install/authenticate Claude Code first.')
    prompt_dir = tempfile.mkdtemp(prefix=PROMPT_DIR_PREFIX, dir=os.fspath(work))      # mode 0700
    prompt_file = os.path.join(prompt_dir, PROMPT_FILE_NAME)
    try:
        fd = os.open(prompt_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(build_prompt(frames))
        previews = os.path.join(os.fspath(work), 'previews')
        cmd = [claude, '-p', f'Read the prompt file at {prompt_file} and follow its instructions exactly.',
               '--model', model, '--output-format', 'json', '--max-turns', str(max(24, len(frames) * 2 + 4)),
               '--allowedTools', 'Read', '--disallowedTools', 'Bash,Edit,Write',
               '--add-dir', prompt_dir, previews]
        proc = core.run(cmd, timeout=max(360, len(frames) * 100))
        if proc.returncode:
            raise RuntimeError('Claude Code failed: ' + ((proc.stderr or proc.stdout) or '')[-2000:])
        return parse_claude_output(proc.stdout)
    finally:
        _remove_prompt(prompt_file, prompt_dir)


# --- Pipeline ---------------------------------------------------------------------------------------

def _capture(images: list[Path], root: Path) -> tuple[dict[str, str], dict[str, float]]:
    """14.3: one ExifTool read per session; capture-time strings and wall-clock seconds."""
    strings, seconds = {}, {}
    for members in core.group_by_session(images, root).values():
        for path, record in core.capture_records(members).items():
            strings[path] = format_capture_time(record)
            seconds[path] = wall_clock_seconds(record)
    return strings, seconds


def run_analyze(args) -> dict:
    """``photo-curator analyze`` (v3). Returns the written document."""
    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f'Folder not found: {root}')
    if getattr(args, 'output', None):
        try:
            work = core.validate_output_dir(root, args.output)
        except ValueError as error:
            raise SystemExit(f'Unsafe --output: {error}')
    else:
        work = root / '.photo-curator'
    if any(ch.isspace() for ch in str(work)):                                  # F-1, K-F3
        raise SystemExit(f'Unsafe output folder: {work} contains whitespace. The Claude prompt and the previews '
                         'are referenced by unquoted paths; choose an output folder path without spaces.')
    validate_arguments(args)

    images = core.find_images(root, not args.no_recursive)                     # DWA-1 (read-only)
    if args.limit:
        images = images[:args.limit]
    if not images:
        raise SystemExit('No supported images found.')

    check_filesystem(work)                                                     # before mkdir (D6-2)
    work.mkdir(parents=True, exist_ok=True)
    startup = startup_v3(work, 'exclusive')                                    # fs, lock, journal
    try:
        for name in startup.leftovers:
            print(f'[LEFTOVER] {work / name} (temporary file from an earlier crash; not read, not deleted)')
        try:
            refuse_existing_output(work)                                       # 12.7 (triggers unchanged)
        except OutputFolderUnreadableRefusal as refusal:
            raise AnalyzeError(refusal.message) from None
        except ExistingOutputRefusal as refusal:
            if not getattr(args, 'archive_existing', False):
                raise AnalyzeError(refusal_message(refusal)) from None
            try:                                                               # CLI-8: 12.7 workflow 2, 12.9
                archived = archive_existing(work, startup.lock)
            except ArchiveRefusal as error:
                raise AnalyzeError(f'archive refused: {error.message}') from None
            except ArchiveFailure as error:
                raise AnalyzeError(f'archive failed ({error.stage}): {error.message}; no analysis was started') from None
            approvals = (archived.approvals_summary.describe() if archived.approvals_summary is not None
                         else f'unknown ({archived.approvals_unknown_reason})')
            print(f'Archived {len(archived.moved)} item(s) into {work / archived.archive_folder}; '
                  f'approvals archived: {approvals}. Archived approvals are not carried into the new results.')
        return _analyze_locked(args, root, work, images)
    finally:
        startup.lock.release()


def _analyze_locked(args, root: Path, work: Path, images: list[Path]) -> dict:
    session = {str(p): core.session_for(p, root) for p in images}
    capture_strings, times = _capture(images, root)
    untimed = len(images) - len(times)
    if untimed:
        print(f'{untimed} image(s) lack a capture timestamp; they are analyzed but never burst-grouped.')
    raw_bursts = core.detect_bursts(images, args.burst_window, root, times) if args.burst_window > 0 else {}
    group_map, sequence_map = core.split_bursts(raw_bursts, times, args.max_burst_size)
    batches = [[str(p) for p in b] for b in core.plan_batches(images, args.batch_size, root, times, group_map)]
    planned = [str(p) for p in images]
    try:
        check_burst_preconditions_v3(
            planned, [PlannedFrame(o, session[o], group_map.get(o), sequence_map.get(o) if o in group_map else None)
                      for o in planned], batches, args.max_burst_size)
    except BurstPreconditionError as error:
        raise AnalyzeError(f'burst planning preconditions failed: {error}') from None

    core.mark_output_dir(work)
    previews = work / 'previews'
    core.mark_output_dir(previews)

    errors: list[dict] = []
    contexts: dict[str, FrameContext] = {}
    print(f'Preparing previews for {len(images)} images...')
    for i, path in enumerate(images, 1):
        o = str(path)
        outcome = check_original_with_preview(path, lambda src: make_preview_v3(Path(src), previews, args.max_edge))
        group = group_map.get(o)
        sequence = sequence_map.get(o) if group else None
        if outcome.accepted:
            contexts[o] = FrameContext(original_path=o, preview_path=str(outcome.preview), session_id=session[o],
                                       burst_group=group, burst_sequence=sequence,
                                       capture_time=capture_strings.get(o), original_sha256=outcome.original_sha256,
                                       batch_index=0)
        elif outcome.stage == 'original':
            errors.append(original_error_entry(o, outcome.error, session_id=session[o], burst_group=group,
                                               burst_sequence=sequence, capture_time=capture_strings.get(o)))
            print(f'[SKIP] {path.name}: {outcome.error}')
        else:
            errors.append(make_error_entry(stage='preview', error=outcome.error, original_path=o,
                                           session_id=session[o], burst_group=group, burst_sequence=sequence,
                                           capture_time=capture_strings.get(o), batch_index=None,
                                           sent_to_model=False, result=None, preview_path=None))
            print(f'[SKIP] {path.name}: {outcome.error}')
        if i % 25 == 0:
            print(f'  prepared {i}/{len(images)}')

    accepted_all: dict = {}
    sent_contexts: dict[str, FrameContext] = {}
    done = 0
    for index, batch in enumerate(batches):
        members = [o for o in batch if o in contexts]
        if not members:
            continue                                                            # DWA-2
        ctxs = {o: dataclasses.replace(contexts[o], batch_index=index) for o in members}
        sent_contexts.update(ctxs)
        print(f'Claude reviewing {done + 1}-{done + len(members)} [{session[members[0]]}]...')
        done += len(members)
        frames = [PromptFrame(o, ctxs[o].preview_path, ctxs[o].session_id, ctxs[o].burst_group,
                              ctxs[o].burst_sequence) for o in members]
        try:
            photos = call_claude(frames, args.model, work)
        except (RuntimeError, ResponseFormatError, OSError) as error:
            print(f'[ERROR] batch {index}: {error}')
            for o in members:
                c = ctxs[o]
                errors.append(make_error_entry(stage='batch', error=str(error) or 'Claude call failed',
                                               original_path=o, session_id=c.session_id, burst_group=c.burst_group,
                                               burst_sequence=c.burst_sequence, capture_time=c.capture_time,
                                               batch_index=index, sent_to_model=True, result=None,
                                               preview_path=c.preview_path))
            continue
        accepted, batch_errors = validate_batch_v3(members, photos, ctxs)
        for entry in batch_errors:
            print(f'[WARN] {Path(entry.get("original_path") or "?").name}: {entry["error"]}')
        accepted_all.update(accepted)
        errors.extend(batch_errors)

    doc = assemble_document(accepted_all, errors, sent_contexts, source_folder=str(root),
                            max_burst_size=args.max_burst_size, burst_window_seconds=args.burst_window)
    try:
        write_analysis_outputs(work, results_bytes=document_bytes(doc), csv_bytes=csv_bytes(doc),
                               html_bytes=gallery_bytes(doc))
    except OutputWriteError as error:
        raise AnalyzeError(error.message) from None
    print(f'\nReview files created:\n  {work / "ratings-review.json"}\n  {work / "ratings-review.csv"}\n'
          f'  {work / "ratings-review.html"}\n  {work / "ratings-review.complete.json"}\nNo image metadata was changed.')
    print(f'Rated: {len(doc["results"])} | Errors/skips: {len(doc["errors"])}')
    return doc
