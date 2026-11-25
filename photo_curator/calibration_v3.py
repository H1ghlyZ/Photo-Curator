"""Calibration tooling, read-only (spec 5g3a section 16 metrics, section 18 measurements; spec 20
items 24-25, tooling only; implementation step S17).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S17, decisions K-17a..K-17i): library only, not wired into the CLI. Running it on real
photos is the separate, unauthorised step M-26. Nothing here claims that the evidence rules are
calibrated, and nothing here suggests keyword or default changes.

Rules:
- Read-only. Results files are read through ``locking_v3.startup_v3`` with the SHARED lock
  (filesystem check -> lock -> journal), one folder at a time and released on every path
  (K-17i); the lock file is the only possible side effect (12.6). Human ratings need no lock.
- Never hashes an original, never reads image metadata, never opens an original, never writes,
  deletes or moves anything. Dataset separation uses stored ``original_path`` and the stored
  ``original_sha256`` only (K-17c).
- Keeper = human rating >= 3 (K-17e). Human T/E agreement is unavailable: the approved ground-
  truth format has no T/E (K-17d). Batch size, turns, duration and output length are not stored
  and are reported as unavailable, never as zero (K-17f).
- Rating 0, error entries, inconsistent items, unmatched and unlabeled originals are kept apart
  from the metric population; results of different rubric/evidence versions are never pooled
  (K-17h). Every refusal is fail-closed (CalibrationRefusal).
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .locking_v3 import StartupRefusal, startup_v3
from .reader_v3 import read_results
from .schema_v3 import CAPTURE_TYPES, SCHEMA_VERSION_V3

UNAVAILABLE = 'unavailable'
TE_UNAVAILABLE_REASON = 'human T/E not in the approved ground-truth format'
BATCH_SIZE_UNAVAILABLE_REASON = 'batch size not stored in results'
RUN_METRIC_UNAVAILABLE_REASON = 'not recorded by analyze'
KEEPER_MIN = 3
INCONSISTENT_ROWS = ('V34', 'V35', 'V36', 'V45')
HUMAN_FIELDS = ('human_rating', 'reason', 'capture_type')
MISSING_RESULT_PREFIX = 'V1: no result'
_HEX64 = re.compile(r'[0-9a-f]{64}')


class CalibrationRefusal(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class HumanRating:
    human_rating: int
    reason: str
    capture_type: str


@dataclass(frozen=True)
class Ratio:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return None if self.denominator == 0 else self.numerator / self.denominator

    def text(self) -> str:
        if self.denominator == 0:
            return f'{self.numerator}/{self.denominator} (n/a (no data))'
        return f'{self.numerator}/{self.denominator} ({100 * self.numerator / self.denominator:.1f}%)'


@dataclass
class CalibrationMetrics:
    results_path: str
    human_path: str
    rubric_version: Any
    evidence_rules_version: Any
    evidence_rules_changed: bool
    warnings: list[str]
    population: list[tuple[str, int, int, str]]               # (original, model, human, human capture type)
    exact: Ratio
    within_one: Ratio
    keeper_recall: Ratio
    false_reject: Ratio
    confusion: dict[tuple[int, int], int]                     # (human, model) -> count, all 25 cells
    by_capture_type: dict[str, tuple[Ratio, Ratio]]           # human capture type -> (exact, within one)
    te_agreement: str = UNAVAILABLE
    te_reason: str = TE_UNAVAILABLE_REASON
    group_status: Counter = field(default_factory=Counter)
    group_reasons: Counter = field(default_factory=Counter)
    rating_0: list[str] = field(default_factory=list)
    inconsistent: list[tuple[str, list[str]]] = field(default_factory=list)
    errors_by_stage: Counter = field(default_factory=Counter)
    errors_matched: list[tuple[str, str]] = field(default_factory=list)   # (original, stage)
    unmatched_human: list[str] = field(default_factory=list)
    unlabeled: int = 0


@dataclass
class SeparationReport:
    cal_path: str
    eval_path: str
    verdict: str                                              # 'separate' | 'overlap' | 'unverifiable'
    path_overlap: list[str]
    hash_overlap: list[tuple[str, list[str], list[str]]]      # (sha, cal originals, eval originals)
    unverifiable: list[tuple[str, str, str]]                  # (set, original or '?', why)
    duplicate_sha_within: list[tuple[str, str, list[str]]]    # (set, sha, originals); info only


@dataclass
class FileBatch:
    results_path: str
    rubric_version: Any
    evidence_rules_version: Any
    max_burst_size: Any
    results_count: int
    batch_errors: int
    batch_indexes: list[int]
    batch_messages: Counter
    missing_results: int
    other_result_errors: Counter
    other_stage_errors: Counter
    largest_burst_group: int | None
    batch_size: str = UNAVAILABLE
    turns: str = UNAVAILABLE
    duration: str = UNAVAILABLE
    output_length: str = UNAVAILABLE


@dataclass
class BatchReport:
    groups: dict[tuple[Any, Any], list[FileBatch]]            # (rubric, evidence) -> files; never pooled


# --- Human ratings (spec 16) ------------------------------------------------------------------

def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise CalibrationRefusal(f'human ratings malformed: duplicate key(s) {dupes}')
    return dict(pairs)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_human_ratings(path) -> dict[str, HumanRating]:
    """Validate ``{original_path: {human_rating, reason, capture_type}}``; any problem refuses the file."""
    try:
        raw = Path(os.fspath(path)).read_bytes()
    except OSError as error:
        raise CalibrationRefusal(f'human ratings unreadable: {error.strerror or error}') from None
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as error:
        raise CalibrationRefusal(f'human ratings malformed: {error}') from None
    if not isinstance(data, dict) or not data:
        raise CalibrationRefusal('human ratings malformed: top level must be a non-empty object')
    problems, out = [], {}
    for key, value in data.items():
        if not (os.path.isabs(key) and os.path.normpath(key) == key):
            problems.append(f'{key!r}: not an absolute, normalised original path')
            continue
        if not isinstance(value, dict) or set(value) != set(HUMAN_FIELDS):
            problems.append(f'{key}: needs exactly the fields {list(HUMAN_FIELDS)}')
            continue
        rating, reason, ctype = value['human_rating'], value['reason'], value['capture_type']
        if not (_is_int(rating) and 1 <= rating <= 5):
            problems.append(f'{key}: human_rating is not an integer 1-5')
        if not (isinstance(reason, str) and reason.strip()):
            problems.append(f'{key}: reason is not a non-empty string')
        if ctype not in CAPTURE_TYPES:
            problems.append(f'{key}: capture_type is not one of {sorted(CAPTURE_TYPES)}')
        if not problems or not any(p.startswith(f'{key}:') for p in problems):
            out[key] = HumanRating(rating, reason, ctype)
    if problems:
        raise CalibrationRefusal('human ratings malformed: ' + '; '.join(problems))
    return out


# --- Results, read under the shared lock (K-17i) ---------------------------------------------

def _locked_read(results_path):
    path = Path(os.path.abspath(os.fspath(results_path)))
    try:
        startup = startup_v3(path.parent, 'shared')
    except StartupRefusal as error:
        raise CalibrationRefusal(f'{path}: {error.message}') from None
    except OSError as error:
        raise CalibrationRefusal(f'{path}: output folder unusable: {error.strerror or error}') from None
    try:
        report = read_results(path)
    finally:
        startup.lock.release()
    if report.refusal:
        raise CalibrationRefusal(f'{path}: {report.refusal}')
    if report.schema_version != SCHEMA_VERSION_V3:
        raise CalibrationRefusal(f'{path}: calibration accepts schema version 3 only')
    doc = report.doc
    seen = Counter(e.get('original_path') for e in list(doc['results']) + list(doc['errors'])
                   if isinstance(e, dict) and isinstance(e.get('original_path'), str))
    dupes = sorted(p for p, n in seen.items() if n > 1)
    if dupes:
        raise CalibrationRefusal(f'{path}: duplicate original_path {dupes}')
    return path, report


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


# --- Metrics (spec 16) -----------------------------------------------------------------------

def compute_metrics(results_path, human_path) -> CalibrationMetrics:
    path, report = _locked_read(results_path)
    doc = report.doc
    source = doc.get('source_folder')
    human_abs = os.path.abspath(os.fspath(human_path))
    if not isinstance(source, str) or not os.path.isabs(source):
        raise CalibrationRefusal(f'{path}: source_folder is not an absolute path')
    for human_form, source_form in ((os.path.realpath(human_abs), os.path.realpath(source)),
                                    (os.path.normpath(human_abs), os.path.normpath(source))):
        if _inside(human_form, source_form):
            raise CalibrationRefusal('human ratings file is inside the source folder (spec 16)')
    human = load_human_ratings(human_abs)

    population, rating_0, inconsistent, labelled = [], [], [], set()
    for item, r in zip(report.items, doc['results']):
        original = r.get('original_path') if isinstance(r, dict) else None
        if original not in human:
            continue
        labelled.add(original)
        rows = [row for row in INCONSISTENT_ROWS if row in item.findings]
        rating = r.get('rating')
        if rows:
            inconsistent.append((original, rows))
        elif rating == 0 and _is_int(rating):
            rating_0.append(original)
        elif r.get('evaluable') is True and _is_int(rating) and 1 <= rating <= 5:
            h = human[original]
            population.append((original, rating, h.human_rating, h.capture_type))
        else:
            inconsistent.append((original, ['not an evaluable rating 1-5']))
    errors_by_stage, errors_matched = Counter(), []
    for entry in doc['errors']:
        original = entry.get('original_path') if isinstance(entry, dict) else None
        if original in human and original not in labelled:
            labelled.add(original)
            errors_by_stage[str(entry.get('stage'))] += 1
            errors_matched.append((original, str(entry.get('stage'))))
    results_originals = {r.get('original_path') for r in doc['results'] if isinstance(r, dict)}

    n = len(population)
    keepers = [p for p in population if p[2] >= KEEPER_MIN]
    confusion = {(h, m): 0 for h in range(1, 6) for m in range(1, 6)}
    for _, m, h, _ in population:
        confusion[(h, m)] += 1
    by_type = {}
    for ctype in sorted({p[3] for p in population}):
        rows = [p for p in population if p[3] == ctype]
        by_type[ctype] = (Ratio(sum(1 for p in rows if p[1] == p[2]), len(rows)),
                          Ratio(sum(1 for p in rows if abs(p[1] - p[2]) <= 1), len(rows)))
    status, reasons = Counter(), Counter()
    for group in report.groups.values():
        status[group.effective] += 1
        reasons.update(group.reasons)
    return CalibrationMetrics(
        results_path=str(path), human_path=human_abs, rubric_version=doc.get('rubric_version'),
        evidence_rules_version=doc.get('evidence_rules_version'),
        evidence_rules_changed=report.evidence_rules_changed,
        warnings=[w for w in report.warnings if w.startswith('V43')],
        population=population,
        exact=Ratio(sum(1 for p in population if p[1] == p[2]), n),
        within_one=Ratio(sum(1 for p in population if abs(p[1] - p[2]) <= 1), n),
        keeper_recall=Ratio(sum(1 for p in keepers if p[1] >= KEEPER_MIN), len(keepers)),
        false_reject=Ratio(sum(1 for p in keepers if p[1] <= 2), len(keepers)),
        confusion=confusion, by_capture_type=by_type, group_status=status, group_reasons=reasons,
        rating_0=rating_0, inconsistent=inconsistent, errors_by_stage=errors_by_stage,
        errors_matched=errors_matched, unmatched_human=sorted(set(human) - labelled),
        unlabeled=sum(1 for o in results_originals if isinstance(o, str) and o not in human))


# --- Dataset separation (spec 16, Q10; stored values only) ----------------------------------

def _originals(label: str, report) -> tuple[dict[str, str | None], list[tuple[str, str, str]]]:
    found, unverifiable = {}, []
    for item, r in zip(report.items, report.doc['results']):
        original = r.get('original_path') if isinstance(r, dict) else None
        if not (isinstance(original, str) and os.path.isabs(original) and os.path.normpath(original) == original):
            unverifiable.append((label, str(original) if original is not None else '?', 'original_path not usable'))
            continue
        sha = r.get('original_sha256')
        if 'V45' in item.findings or not (isinstance(sha, str) and _HEX64.fullmatch(sha)):
            unverifiable.append((label, original, 'stored original_sha256 missing or invalid'))
            found[original] = None
        else:
            found[original] = sha
    for entry in report.doc['errors']:
        original = entry.get('original_path') if isinstance(entry, dict) else None
        if not (isinstance(original, str) and os.path.isabs(original) and os.path.normpath(original) == original):
            unverifiable.append((label, str(original) if original is not None else '?',
                                 'error entry without a usable original_path'))
            continue
        unverifiable.append((label, original, f'error entry (stage {entry.get("stage")}): no stored SHA-256'))
        found[original] = None
    return found, unverifiable


def check_separation(cal_results, eval_results) -> SeparationReport:
    cal_path, cal = _locked_read(cal_results)            # sequential: one lock at a time
    eval_path, ev = _locked_read(eval_results)
    a, ua = _originals('calibration', cal)
    b, ub = _originals('evaluation', ev)
    path_overlap = sorted(set(a) & set(b))
    by_sha_a, by_sha_b = {}, {}
    for found, by_sha in ((a, by_sha_a), (b, by_sha_b)):
        for original, sha in found.items():
            if sha is not None:
                by_sha.setdefault(sha, []).append(original)
    hash_overlap = [(sha, sorted(by_sha_a[sha]), sorted(by_sha_b[sha])) for sha in sorted(set(by_sha_a) & set(by_sha_b))]
    duplicates = [(label, sha, sorted(paths)) for label, by_sha in (('calibration', by_sha_a), ('evaluation', by_sha_b))
                  for sha, paths in sorted(by_sha.items()) if len(paths) > 1]
    unverifiable = ua + ub
    verdict = 'overlap' if (path_overlap or hash_overlap) else ('unverifiable' if unverifiable else 'separate')
    return SeparationReport(str(cal_path), str(eval_path), verdict, path_overlap, hash_overlap, unverifiable, duplicates)


# --- Batch measurements (spec 18; only what results store) ---------------------------------

def batch_measurements(results_paths) -> BatchReport:
    paths = [os.path.abspath(os.fspath(p)) for p in results_paths]
    if not paths:
        raise CalibrationRefusal('batch measurements need at least one results file')
    dupes = sorted(p for p, n in Counter(paths).items() if n > 1)
    if dupes:
        raise CalibrationRefusal(f'the same results file is listed more than once: {dupes}')
    groups: dict[tuple[Any, Any], list[FileBatch]] = {}
    for given in paths:
        path, report = _locked_read(given)                # sequential: one lock at a time
        doc = report.doc
        batch_indexes, batch_messages, other_result, other_stage = set(), Counter(), Counter(), Counter()
        batch_errors = missing = 0
        sizes = Counter()
        for r in doc['results']:
            if isinstance(r, dict) and isinstance(r.get('burst_group'), str):
                sizes[r['burst_group']] += 1
        for entry in doc['errors']:
            if not isinstance(entry, dict):
                continue
            stage, message = entry.get('stage'), str(entry.get('error'))
            if isinstance(entry.get('burst_group'), str):
                sizes[entry['burst_group']] += 1
            if stage == 'batch':
                batch_errors += 1
                batch_messages[message] += 1
                if _is_int(entry.get('batch_index')):
                    batch_indexes.add(entry['batch_index'])
            elif stage == 'result':
                if message.startswith(MISSING_RESULT_PREFIX):
                    missing += 1
                else:
                    other_result[message.split(':', 1)[0]] += 1
            else:
                other_stage[str(stage)] += 1
        key = (doc.get('rubric_version'), doc.get('evidence_rules_version'))
        groups.setdefault(key, []).append(FileBatch(
            results_path=str(path), rubric_version=key[0], evidence_rules_version=key[1],
            max_burst_size=doc.get('max_burst_size'), results_count=len(doc['results']),
            batch_errors=batch_errors, batch_indexes=sorted(batch_indexes), batch_messages=batch_messages,
            missing_results=missing, other_result_errors=other_result, other_stage_errors=other_stage,
            largest_burst_group=max(sizes.values()) if sizes else None))
    return BatchReport(groups)


# --- Report lines (data + text; printing is the caller's) -----------------------------------

def report_lines(*, metrics: CalibrationMetrics | None = None, separation: SeparationReport | None = None,
                 batches: BatchReport | None = None) -> list[str]:
    lines = ['Calibration report (read-only; built from results JSON and human ratings; nothing written)']
    if metrics is not None:
        m = metrics
        lines.append(f'Results: {m.results_path}  rubric_version {m.rubric_version!r}  '
                     f'evidence_rules_version {m.evidence_rules_version!r}')
        lines.append(f'Human ratings: {m.human_path}')
        lines.extend(f'Warning: {w}' for w in m.warnings)
        lines.append(f'Population (evaluable, rating 1-5, consistent, human-rated): {len(m.population)}')
        lines.append(f'Kept apart: rating 0 {len(m.rating_0)}; inconsistent {len(m.inconsistent)}; '
                     f'error entries {sum(m.errors_by_stage.values())} '
                     f'({", ".join(f"{k}: {v}" for k, v in sorted(m.errors_by_stage.items())) or "none"}); '
                     f'human-rated but not in the results {len(m.unmatched_human)}; results without a human rating {m.unlabeled}')
        for original, rows in m.inconsistent:
            lines.append(f'  inconsistent: {original} ({", ".join(rows)})')
        for original in m.unmatched_human:
            lines.append(f'  not in the results: {original}')
        lines.append(f'Exact agreement: {m.exact.text()}')
        lines.append(f'Within one star: {m.within_one.text()}')
        lines.append(f'Keeper recall (keeper = human rating >= {KEEPER_MIN}): {m.keeper_recall.text()}')
        lines.append(f'False-reject rate (human keepers rated <= 2): {m.false_reject.text()}')
        lines.append('Confusion matrix (rows human 1-5, columns model 1-5):')
        for h in range(1, 6):
            lines.append(f'  human {h}: ' + ' '.join(str(m.confusion[(h, mm)]) for mm in range(1, 6)))
        lines.append('By human capture type (exact; within one):')
        for ctype, (exact, within) in m.by_capture_type.items():
            lines.append(f'  {ctype}: {exact.text()}; {within.text()}')
        lines.append(f'T and E agreement: {m.te_agreement} ({m.te_reason})')
        lines.append('Burst group status: ' + (', '.join(f'{k}: {v}' for k, v in sorted(m.group_status.items())) or 'none'))
        for reason, count in sorted(m.group_reasons.items()):
            lines.append(f'  reason: {reason} ({count})')
    if separation is not None:
        s = separation
        lines.append(f'Dataset separation (stored paths and stored SHA-256 only): {s.verdict}')
        lines.append(f'  calibration: {s.cal_path}')
        lines.append(f'  evaluation: {s.eval_path}')
        for original in s.path_overlap:
            lines.append(f'  path overlap: {original}')
        for sha, a, b in s.hash_overlap:
            lines.append(f'  SHA-256 overlap {sha}: calibration {a}; evaluation {b}')
        for label, original, why in s.unverifiable:
            lines.append(f'  not verifiable ({label}): {original}: {why}')
        for label, sha, paths in s.duplicate_sha_within:
            lines.append(f'  same SHA-256 within the {label} set (information): {sha}: {paths}')
    if batches is not None:
        lines.append('Batch measurements (per rubric/evidence version; never pooled):')
        for (rubric, evidence), files in sorted(batches.groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
            lines.append(f'  rubric_version {rubric!r}, evidence_rules_version {evidence!r}:')
            for f in files:
                lines.append(f'    {f.results_path}: results {f.results_count}; batch errors {f.batch_errors} '
                             f'(batches {f.batch_indexes or "none"}); missing results {f.missing_results}; '
                             f'other result errors {dict(sorted(f.other_result_errors.items())) or "none"}; '
                             f'other stages {dict(sorted(f.other_stage_errors.items())) or "none"}; '
                             f'largest burst group {f.largest_burst_group if f.largest_burst_group is not None else "none"} '
                             f'(max_burst_size {f.max_burst_size})')
                lines.append(f'      batch size: {f.batch_size} ({BATCH_SIZE_UNAVAILABLE_REASON}); turns: {f.turns}, '
                             f'duration: {f.duration}, output length: {f.output_length} ({RUN_METRIC_UNAVAILABLE_REASON})')
    return lines
