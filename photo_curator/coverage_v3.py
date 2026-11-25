"""Coverage report, read-only (spec 5g3a section 7.3, 12.8 preview accounting, V37, V40, V46, 14.7;
spec 20 item 22; implementation step S15).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S15 only): library code, NOT wired into the CLI (``photo-curator coverage <results.json>``
is W-C, M-24, shared lock). ``build_report`` returns a data structure and text lines (P-15d);
printing and exit codes are W-C's.

Rules:
- Built only from the results JSON (via ``reader_v3.read_results``) and the manifest/preview
  listing for preview accounting. It never reads image metadata, never hashes an original
  (``hashing_v3`` is not used), never writes, deletes or moves anything.
- v3 only (14.7); incomplete output and every whole-file refusal of the reader refuse (12.8).
- Suggestion counts use the reader's recomputed suggestions (V37, P-15b); a stored value that
  differs is shown as a warning. ``capture_type = null`` is counted as ``unknown`` (P-15c).
- Timeline gaps (P-15a): per session, usable photos are evaluable results with rating >= 3 and a
  valid ``capture_time``; they are sorted by the stored wall-clock time and a gap is reported
  between two consecutive usable photos when it lasts >= 10 minutes (inclusive). The first and
  last timed image of the session only bound the observation window; no gap is reported before
  the first or after the last usable photo. A session without any usable photo is reported as
  such, with no artificial gap. A session whose timed images carry different offsets (or a mix
  of offset and none) gets no gaps at all, only the 7.3 warning listing the offsets.
- Wording is neutral; nothing is said to be missing.

Interpretations (S15, reported for review):
- S15-e ``total`` per session = results + ``errors`` entries of that session (every original once);
  ``errors`` entries without a valid ``session_id`` are counted under ``unknown / no session``.
- S15-f counts use the stored values of every result; items that fail 15.3 item checks are still
  counted and a warning names how many there are.
- S15-g "no hash function" (item 22) means the ORIGINAL hash function; the completeness check
  (CSV/gallery hashes, 12.8) and the reader's preview hash (binding check 6) still run.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .output_v3 import MANIFEST_NAME, count_previews
from .preview_checks_v3 import expected_preview_name
from .reader_v3 import read_results

GAP_MINUTES = 10
UNKNOWN_SESSION = 'unknown / no session'
UNKNOWN_CAPTURE_TYPE = 'unknown'
NOT_VERIFIED = 'analysis-time SHA-256, not verified against the current file'
ORPHAN_LABEL = 'orphan previews: generated output not referenced by any result or error; not deleted'
_CAPTURE_RE = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,3})?([+-]\d\d:\d\d)?')


class CoverageRefusal(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass
class SessionCounts:
    total: int = 0
    evaluable: int = 0
    rating_0: int = 0
    ratings: dict = field(default_factory=lambda: {r: 0 for r in range(1, 6)})
    errors: int = 0


@dataclass
class Gap:
    session: str
    start: str
    end: str
    minutes: float


@dataclass
class CoverageReport:
    sessions: dict[str, SessionCounts] = field(default_factory=dict)
    capture_types: Counter = field(default_factory=Counter)
    capture_type_rating: Counter = field(default_factory=Counter)        # (type, rating) -> n
    documentation_suggestions: Counter = field(default_factory=Counter)
    editorial_suggestions: Counter = field(default_factory=Counter)
    gaps: list[Gap] = field(default_factory=list)
    sessions_without_usable: list[str] = field(default_factory=list)
    observation: dict[str, tuple[str, str]] = field(default_factory=dict)
    mixed_offsets: dict[str, list[str]] = field(default_factory=dict)
    untimed: list[dict] = field(default_factory=list)
    manifest_counts: tuple[int, int] | None = None
    recount: tuple[int, int] | None = None
    orphans: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def _parse_time(value: Any):
    """(wall-clock datetime, offset or '') for a valid 14.3 capture_time, else None."""
    if not isinstance(value, str):
        return None
    m = _CAPTURE_RE.fullmatch(value)
    if not m:
        return None
    try:
        wall = datetime.fromisoformat(m.group(1) + (m.group(2) or ''))
    except ValueError:
        return None
    return wall, m.group(3) or ''


def _session(value: Any) -> str:
    return value if isinstance(value, str) and value.strip() else UNKNOWN_SESSION


def build_report(results_path) -> CoverageReport:
    path = os.path.abspath(os.fspath(results_path))
    reader = read_results(path)
    if reader.refusal:
        raise CoverageRefusal(reader.refusal)
    if reader.schema_version != 3:
        raise CoverageRefusal('the coverage report accepts schema version 3 only')
    doc = reader.doc
    out = CoverageReport()
    out.warnings.extend(w for w in reader.warnings if w.startswith(('V32', 'V43', 'V46')))
    inconsistent = sum(1 for i in reader.items if any(r in i.findings for r in ('V34', 'V35', 'V36', 'V45')))
    if inconsistent:
        out.warnings.append(f'{inconsistent} item(s) are inconsistent under 15.3; counts use their stored values')

    timed: dict[str, list] = {}
    usable: dict[str, list] = {}
    for item, r in zip(reader.items, doc['results']):
        if not isinstance(r, dict):
            continue
        session = _session(r.get('session_id'))
        counts = out.sessions.setdefault(session, SessionCounts())
        counts.total += 1
        rating = r.get('rating')
        if r.get('evaluable') is True:
            counts.evaluable += 1
        if rating == 0 and not isinstance(rating, bool):
            counts.rating_0 += 1
        elif isinstance(rating, int) and not isinstance(rating, bool) and 1 <= rating <= 5:
            counts.ratings[rating] += 1
        ctype = r.get('capture_type') if isinstance(r.get('capture_type'), str) else UNKNOWN_CAPTURE_TYPE
        out.capture_types[ctype] += 1
        out.capture_type_rating[(ctype, rating)] += 1
        recomputed = item.recomputed_suggestions
        out.documentation_suggestions[recomputed.get('documentation_suggestion', 'not recomputed')] += 1
        out.editorial_suggestions[recomputed.get('editorial_suggestion', 'not recomputed')] += 1
        out.warnings.extend(f'{Path(str(r.get("original_path"))).name}: {w}' for w in item.warnings if w.startswith('V37'))
        parsed = _parse_time(r.get('capture_time'))
        if parsed is None:
            out.untimed.append({'original_path': r.get('original_path'), 'capture_time': r.get('capture_time'),
                                'original_sha256': r.get('original_sha256')})
            continue
        timed.setdefault(session, []).append((parsed, r.get('capture_time')))
        if r.get('evaluable') is True and isinstance(rating, int) and not isinstance(rating, bool) and rating >= 3:
            usable.setdefault(session, []).append((parsed[0], r.get('capture_time')))
    for entry in doc['errors']:
        session = _session(entry.get('session_id'))
        counts = out.sessions.setdefault(session, SessionCounts())
        counts.errors += 1
        counts.total += 1
        parsed = _parse_time(entry.get('capture_time'))
        if parsed is not None and session != UNKNOWN_SESSION:
            timed.setdefault(session, []).append((parsed, entry.get('capture_time')))

    for session, items in sorted(timed.items()):
        ordered = sorted(items, key=lambda x: x[0][0])
        out.observation[session] = (ordered[0][1], ordered[-1][1])
        offsets = sorted({p[1] for p, _ in items})
        if len(offsets) > 1:
            out.mixed_offsets[session] = [o or 'no offset' for o in offsets]
            out.warnings.append(f'mixed time offsets in session {session}; timeline gaps not computed '
                                f'(offsets: {", ".join(out.mixed_offsets[session])})')
            continue
        points = sorted(usable.get(session, []), key=lambda x: x[0])
        if not points:
            out.sessions_without_usable.append(session)
            continue
        for (t1, s1), (t2, s2) in zip(points, points[1:]):
            minutes = (t2 - t1).total_seconds() / 60
            if minutes >= GAP_MINUTES:
                out.gaps.append(Gap(session, s1, s2, minutes))

    manifest = json.loads(Path(path).with_name(MANIFEST_NAME).read_text(encoding='utf-8'))
    out.manifest_counts = (manifest['referenced_preview_count'], manifest['orphan_preview_count'])
    counts = count_previews(Path(path).parent, doc)
    out.recount = (counts.referenced, counts.orphan)
    out.orphans = list(counts.orphan_names)
    if out.recount != out.manifest_counts:
        out.warnings.append(f'preview recount {out.recount} differs from the manifest {out.manifest_counts}')
    _lines(out, doc)
    return out


def _orphan_note(name: str, doc: dict) -> str:
    for entry in doc['errors']:
        if (entry.get('stage') == 'original' and isinstance(entry.get('original_path'), str)
                and expected_preview_name(entry['original_path']) == name):
            return f'  (matches the stage = original error for {PurePosixPath(entry["original_path"]).name}: {entry.get("error")})'
    return ''


def _lines(out: CoverageReport, doc: dict) -> None:
    lines = out.lines
    lines.append('Coverage report (facts only; read-only; built from the results JSON)')
    lines.append('Per session: total / evaluable / rating 0 / ratings 1-5 / errors')
    for session in sorted(out.sessions):
        c = out.sessions[session]
        ratings = ' '.join(f'{r}:{c.ratings[r]}' for r in range(1, 6))
        lines.append(f'  {session}: total {c.total} / evaluable {c.evaluable} / rating 0: {c.rating_0} / {ratings} / errors {c.errors}')
    lines.append('Per capture type:')
    for ctype in sorted(out.capture_types):
        by_rating = ', '.join(f'rating {r}: {n}' for (t, r), n in sorted(out.capture_type_rating.items(), key=lambda kv: str(kv[0]))
                              if t == ctype)
        lines.append(f'  {ctype}: {out.capture_types[ctype]} ({by_rating})')
    lines.append('Documentation suggestions: ' + ', '.join(f'{k}: {v}' for k, v in sorted(out.documentation_suggestions.items())))
    lines.append('Editorial suggestions: ' + ', '.join(f'{k}: {v}' for k, v in sorted(out.editorial_suggestions.items())))
    lines.append(f'Timeline gaps (>= {GAP_MINUTES} minutes between consecutive rating >= 3 images, per session):')
    for gap in out.gaps:
        lines.append(f'  no rating >= 3 image between {gap.start} and {gap.end} in {gap.session} ({gap.minutes:.1f} min)')
    if not out.gaps:
        lines.append('  none')
    for session in out.sessions_without_usable:
        lines.append(f'  {session}: no rating >= 3 image with a capture time')
    for session, (first, last) in sorted(out.observation.items()):
        lines.append(f'  observed in {session}: {first} to {last}')
    lines.append(f'Untimed images ({len(out.untimed)}), not placed on the timeline:')
    for u in out.untimed:
        lines.append(f'  {u["original_path"]}  capture_time: {u["capture_time"]!r}  SHA-256: {u["original_sha256"]} ({NOT_VERIFIED})')
    lines.append(f'Previews: manifest referenced {out.manifest_counts[0]}, orphan {out.manifest_counts[1]}; '
                 f'recount referenced {out.recount[0]}, orphan {out.recount[1]}')
    if out.orphans:
        lines.append(ORPHAN_LABEL + ':')
        for name in out.orphans:
            lines.append(f'  {name}{_orphan_note(name, doc)}')
    for warning in out.warnings:
        lines.append(f'Warning: {warning}')
