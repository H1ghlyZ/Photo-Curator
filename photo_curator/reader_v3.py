"""Read-time checks for stored results, V30-V47 (spec 5g3a sections 15.3, 12.1, 12.2, 12.5, 12.8,
14.1, 14.2, 8.2.1; spec 20 item 13; implementation step S11).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S11 only): library code, NOT wired into any command. Locks and start-up stay with the
caller (``locking_v3.startup_v3``). The reader never writes, never repairs, never hashes an
original; it hashes a preview only for binding check 6 of an item that claims an approval.

Responses (15.3): ``F`` refuses the whole file (``ReadReport.refusal``); ``S`` gives an item
``skip_message`` (apply) and ``approve_refusals``; display-only rows give warnings; eligibility
rows make a group ``invalid`` for highlights. Apply ordering (13.1) is the apply step's (S14).

Approved deviations implemented here (documented behaviour, not a spec revision):
- B-11a (V44 at read time): stored results carry no ``batch_index``, so an error entry whose
  identity can't be established is scoped to its session when ``session_id`` is a non-empty
  string, otherwise to the whole file. Batch scope is never used. Read time can therefore be
  STRICTER than analysis and is not guaranteed to reproduce it exactly.
- V43 separation without changing S4: the burst re-run calls ``bursts_v3.apply_bursts_v3``
  unchanged on the stored fields (with ``errors=[]``; B0 is computed here under B-11a). When
  ``evidence_rules_version`` equals the reader's, the re-run status is S4's status. When it
  differs, the non-evidence status is derived from ``GroupOutcome.failed_rules``: ``C1`` ->
  invalid; ranked mode -> invalid iff B3, B4 or B5 failed (B6 is evidence and ignored);
  not-comparable mode -> ``not_comparable`` iff at least two ranked frames and every ranking
  field is null (the structural part of C2), else invalid. No stored value, evidence or review
  reason is replaced, generated or modified; S4's returned objects are discarded.
- D4-4b: the effective status is monotone (``suggestions_v3.effective_burst_status_v3``): a
  stored ``invalid`` is never upgraded; any difference from the re-run is ``invalid`` with a
  neutral label (cause not determinable from stored data).
- D3-14, D4-1, D4-4, D5-3 are accepted as stored shapes / eligibility rules (see S3-S5).
- B-11c (PV6) is implemented in ``preview_checks_v3``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .bursts_v3 import apply_bursts_v3
from .capture_time import is_valid_offset
from .core import SUPPORTED, in_generated_output, is_generated
from .locking_v3 import JOURNAL_NAME, JOURNAL_MESSAGE
from .output_v3 import INCOMPLETE_MESSAGE, approval_state_without_files, check_completeness, v30_problems, v31_problems
from .preview_checks_v3 import APPLY_SKIP_MESSAGE as PREVIEW_INVALID_MESSAGE, check_preview
from .schema_v3 import (APPROVAL_FIELDS, BASE_MATRIX, BURST_COMPARISON, BURST_RANKING_STATUS, CAPTURE_TYPES,
                        CONFIDENCE_THRESHOLD, DECISIONS, EVIDENCE_RULES_VERSION, FLAGS, MOTION_CLASSES,
                        OVERRIDE_CELLS, RESULT_FIELDS, REVIEW_CODES, SCHEMA_VERSION_V3, UNCERTAIN_CHECKS,
                        classify_error_entry)
from .suggestions_v3 import (DRIFT_LIMIT_PERCENT, documentation_suggestion_v3, editorial_suggestion_v3,
                             effective_burst_status_v3, highlight_eligible_v3)
from .validate_v3 import BURST_MODEL_FIELDS, Accepted, FrameContext, is_specific

SKIP_AMBIGUOUS = '[SKIP ambiguous original_path/path]'
SKIP_NOT_ORIGINAL = '[SKIP not an original image]'
SKIP_GENERATED = '[SKIP inside generated output]'
SKIP_INCONSISTENT = '[SKIP inconsistent result]'
SKIP_MALFORMED = '[SKIP approval malformed]'
BINDING_MESSAGES = {
    1: '[SKIP approval invalidated: rating changed]',
    2: '[SKIP approval invalidated: original changed]',
    4: '[SKIP approval invalidated: preview changed]',
    5: PREVIEW_INVALID_MESSAGE,
    6: '[SKIP approval invalidated: preview changed]',
}
EVIDENCE_CHANGED_LABEL = 'evidence rules changed since analysis'
NEUTRAL_MISMATCH = 'stored burst status differs from a re-run; cause not determinable from stored data'
_CAPTURE_RE = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,3})?([+-]\d\d:\d\d)?')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_INTENTIONAL = frozenset({'intentional_pan', 'intentional_drag'})


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(e, str) for e in v)


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ''


def _abs_str(v: Any) -> bool:
    return isinstance(v, str) and v != '' and os.path.isabs(v)


# --- Reports ---------------------------------------------------------------------------------------

@dataclass
class ItemReport:
    index: int
    target: str | None
    findings: dict[str, list[str]] = field(default_factory=dict)
    skip_message: str | None = None                  # first S message among V33, V34-V36, V45
    approval_state: str | None = None                # v3 only (12.1 reader rule)
    approval_message: str | None = None              # malformed / binding-check message
    eligibility_invalid: bool = False                # V39 / V41 / V44
    gallery_flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recomputed_suggestions: dict[str, Any] = field(default_factory=dict)

    def add(self, row: str, problem: str) -> None:
        self.findings.setdefault(row, []).append(problem)

    @property
    def approve_refusals(self) -> list[str]:
        """Rows that make approve refuse (15.3 'approve F')."""
        rows = [r for r in ('V33', 'V34', 'V35', 'V36', 'V45') if r in self.findings]
        if self.approval_state == 'malformed':
            rows.append('V38')
        if 'V42' in self.findings:
            rows.append('V42')
        return rows


@dataclass
class GroupReport:
    burst_group: str
    stored: Any
    rerun: str | None
    effective: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class ReadReport:
    refusal: str | None = None
    refusal_row: str | None = None
    schema_version: Any = None
    doc: dict | None = None
    items: list[ItemReport] = field(default_factory=list)
    groups: dict[str, GroupReport] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    evidence_rules_changed: bool = False
    warnings_distribution: dict | None = None          # V32 recomputed values for display


# --- File level: V47, V30, V31, V46 ------------------------------------------------------------------

def read_results(path, *, check_previews: bool = True) -> ReadReport:
    """Read and check one results file. ``<work>`` is the file's folder."""
    results_path = Path(os.fspath(path))
    work = results_path.parent
    if os.path.lexists(work / JOURNAL_NAME):                                    # V47, before parsing
        return ReadReport(refusal=JOURNAL_MESSAGE, refusal_row='V47')
    try:
        raw = results_path.read_bytes()
    except OSError as error:
        return ReadReport(refusal=f'results file unreadable: {error.strerror or error}', refusal_row='V30')
    problems, doc = v30_problems(raw)                                           # V30
    if problems:
        return ReadReport(refusal='; '.join(problems), refusal_row='V30')
    version = doc['schema_version']
    report = ReadReport(schema_version=version, doc=doc)
    if not isinstance(doc.get('results'), list):
        report.refusal, report.refusal_row = 'results is not a list', 'V30'
        return report
    if version != SCHEMA_VERSION_V3:                                            # v1/v2: V33 only
        for index, item in enumerate(doc['results']):
            report.items.append(_v33(ItemReport(index, None), item, version))
        return report
    problems = v31_problems(doc)                                                # V31
    if problems:
        report.refusal, report.refusal_row = '; '.join(problems), 'V31'
        return report
    completeness = check_completeness(work)                                     # V46
    if not completeness.complete:
        report.refusal = INCOMPLETE_MESSAGE + ': ' + '; '.join(completeness.problems)
        report.refusal_row = 'V46'
        return report
    report.warnings.extend(f'V46: {w}' for w in completeness.warnings)
    report.evidence_rules_changed = doc['evidence_rules_version'] != EVIDENCE_RULES_VERSION   # V43
    if report.evidence_rules_changed:
        report.warnings.append(f'V43: {EVIDENCE_CHANGED_LABEL} ({doc["evidence_rules_version"]!r} != '
                               f'{EVIDENCE_RULES_VERSION!r}); evidence checks are not re-run')
    _check_v3_items(report, work, check_previews)
    return report


# --- V33 (every version) -------------------------------------------------------------------------------

def _v33(item: ItemReport, result: Any, version: Any) -> ItemReport:
    if not isinstance(result, dict):
        item.add('V33' if version != SCHEMA_VERSION_V3 else 'V36', 'result is not an object')
        item.skip_message = SKIP_NOT_ORIGINAL if version != SCHEMA_VERSION_V3 else SKIP_INCONSISTENT
        return item
    original, path, preview = result.get('original_path'), result.get('path'), result.get('preview_path')
    target = original if 'original_path' in result else path
    item.target = target if isinstance(target, str) else None
    if 'original_path' in result and 'path' in result and original != path:
        item.add('V33', 'original_path and path differ')
        item.skip_message = item.skip_message or SKIP_AMBIGUOUS
        return item
    if not isinstance(target, str) or not target:
        if version == SCHEMA_VERSION_V3:
            return item                                                         # V36 reports it
        item.add('V33', 'no target path')
        item.skip_message = item.skip_message or SKIP_NOT_ORIGINAL
        return item
    target_path = Path(target)
    if (is_generated(target_path) or target_path.suffix.lower() not in SUPPORTED
            or ('preview_path' in result and preview == target)):
        item.add('V33', 'target is generated, unsupported, or equals preview_path')
        item.skip_message = item.skip_message or SKIP_NOT_ORIGINAL
    elif in_generated_output(target_path):
        item.add('V33', 'target is inside generated output')
        item.skip_message = item.skip_message or SKIP_GENERATED
    return item


# --- v3 items --------------------------------------------------------------------------------------------

def group_modes(results: list) -> dict[str, str | None]:
    """The comparison mode of each group from its evaluable members' stored ``burst_comparison``:
    ``'ranked'``, ``'not_comparable'``, or None when it can't be determined (mixed, null or
    invalid values, i.e. C1). Used only to pick the 14.2 W/K shape rules (V36)."""
    values: dict[str, set] = {}
    for r in results:
        if isinstance(r, dict) and _nonempty_str(r.get('burst_group')) and r.get('evaluable') is True:
            bc = r.get('burst_comparison')
            values.setdefault(r['burst_group'], set()).add(bc if isinstance(bc, str) else repr(bc))
    return {g: (v.pop() if len(v) == 1 and next(iter(v)) in BURST_COMPARISON else None)
            for g, v in values.items()}


def _check_v3_items(report: ReadReport, work: Path, check_previews: bool) -> None:
    doc = report.doc
    modes = group_modes(doc['results'])
    for index, result in enumerate(doc['results']):
        item = _v33(ItemReport(index, None), result, SCHEMA_VERSION_V3)
        report.items.append(item)
        if not isinstance(result, dict):
            continue
        _v36(item, result, report.evidence_rules_changed, modes.get(result.get('burst_group')))
        _v34(item, result)                                  # always: a hand-edited rating is V34
        if all(k in result for k in ('needs_review', 'review_reasons', 'confidence')):
            _v35(item, result)
        _v45(item, result)
        _v40(item, result)
        if any(row in item.findings for row in ('V34', 'V35', 'V36', 'V45')):
            item.skip_message = item.skip_message or SKIP_INCONSISTENT
        if 'V34' in item.findings or 'V35' in item.findings:
            item.gallery_flags.append('inconsistent stored result')
    _bursts(report)
    for item, result in zip(report.items, doc['results']):
        if isinstance(result, dict):
            _approval(item, result, work, doc.get('source_folder'), check_previews)
            _v37(item, result, report)
    _v32(report)


def _v34(item: ItemReport, r: dict) -> None:
    if any(k not in r for k in ('rating', 'decision', 'moment_override', 'evaluable')):
        return                                                                  # V36 reports it
    rating, decision, mo = r['rating'], r['decision'], r['moment_override']
    if not _is_int(rating) or not 0 <= rating <= 5:
        item.add('V34', 'rating is not an integer 0-5')
        return
    if decision != DECISIONS[rating]:
        item.add('V34', 'decision differs from the 6.5 mapping')
    if not isinstance(mo, bool):
        item.add('V34', 'moment_override is not a bool')
        return
    if r['evaluable'] is False:
        if rating != 0:
            item.add('V34', 'unevaluable item with rating != 0')
        return
    if r['evaluable'] is not True:
        return                                                                  # V36 reports it
    t, e = r.get('technical_score'), r.get('editorial_score')
    if not (_is_int(t) and 1 <= t <= 5 and _is_int(e) and 1 <= e <= 5):
        return                                                                  # V36 reports it
    if mo and (t, e) not in OVERRIDE_CELLS:
        item.add('V34', 'moment_override outside the three valid cells')
        return
    expected = OVERRIDE_CELLS[(t, e)] if mo else BASE_MATRIX[(t, e)]
    if rating != expected:
        item.add('V34', f'rating {rating} differs from the matrix result {expected}')


def _v35(item: ItemReport, r: dict) -> None:
    needs, reasons, confidence = r['needs_review'], r['review_reasons'], r['confidence']
    if not isinstance(needs, bool):
        item.add('V35', 'needs_review is not a bool')
    if (not isinstance(reasons, list) or not all(isinstance(c, str) and c in REVIEW_CODES for c in reasons)
            or len(set(reasons)) != len(reasons)):
        item.add('V35', 'review_reasons is not a list of unique known codes')
        return
    if isinstance(needs, bool) and needs != bool(reasons):
        item.add('V35', 'needs_review differs from (review_reasons non-empty)')
    low = confidence is not None and _is_number(confidence) and confidence < CONFIDENCE_THRESHOLD
    if ('low_confidence' in reasons) != low:
        item.add('V35', 'low_confidence present differs from (confidence < 0.6)')
    if confidence is None and 'field_invalid' not in reasons:
        item.add('V35', 'confidence is null without field_invalid')


def _v45(item: ItemReport, r: dict) -> None:
    sha = r.get('original_sha256')
    if not (isinstance(sha, str) and _HEX64.fullmatch(sha)):
        item.add('V45', 'original_sha256 missing, null, or not 64 lowercase hex characters')


def _v40(item: ItemReport, r: dict) -> None:
    value = r.get('capture_time')
    if value is None:
        return
    m = _CAPTURE_RE.fullmatch(value) if isinstance(value, str) else None
    ok = m is not None and (m.group(3) is None or is_valid_offset(m.group(3)))
    if ok:
        try:
            datetime.fromisoformat(m.group(1))
        except ValueError:
            ok = False
    if not ok:
        item.add('V40', 'capture_time does not match 14.3')
        item.warnings.append('V40: listed as untimed (invalid capture_time)')


# --- V36: stored shapes (14.1, 14.2) --------------------------------------------------------------

def _evidence_shape(item, name, value, reasons, *, required, rules_changed, w_reason='evidence_missing',
                    optional_w='field_invalid', extra=()):
    """One evidence list: valid, W (null) or K (as returned) with the reason 14.2 requires."""
    def need(*codes):
        missing = [c for c in codes + tuple(extra) if c not in reasons]
        if missing:
            item.add('V36', f'{name}: stored shape needs review reason(s) {missing}')
    if value is None:
        need(w_reason if required else optional_w)
        return
    if not _is_str_list(value):
        item.add('V36', f'{name}: not a list of strings')
        return
    if not required:
        return
    if not value:
        need('evidence_missing')
    elif not rules_changed and not all(is_specific(v) for v in value):
        need('evidence_generic')


def _v36(item: ItemReport, r: dict, rules_changed: bool, mode: str | None = None) -> None:
    missing = [k for k in RESULT_FIELDS if k not in r]
    if missing:
        item.add('V36', f'missing fields {missing}')
        return
    reasons = set(r['review_reasons']) if isinstance(r['review_reasons'], list) else set()
    for key in ('path', 'original_path', 'preview_path'):
        if not _abs_str(r[key]):
            item.add('V36', f'{key} is not an absolute path')
    if not _nonempty_str(r['session_id']):
        item.add('V36', 'session_id is not a non-empty string')
    group, sequence = r['burst_group'], r['burst_sequence']
    for key in ('burst_group', 'burst_sequence'):
        if r[key] is not None and not _nonempty_str(r[key]):
            item.add('V36', f'{key} is not a non-empty string or null')
    if (group is None) != (sequence is None):
        item.add('V36', 'burst_group and burst_sequence disagree')
    confidence = r['confidence']
    if confidence is not None and not (_is_number(confidence) and 0 <= confidence <= 1):
        item.add('V36', 'confidence is not a number 0-1 or null')
    evaluable = r['evaluable']
    if not isinstance(evaluable, bool):
        item.add('V36', 'evaluable is not a bool')
        return
    for key, allowed in (('flags', FLAGS), ('uncertain_checks', UNCERTAIN_CHECKS)):
        value = r[key]
        if value is None:
            required = ['field_invalid']
            if key == 'uncertain_checks' and evaluable:              # 14.2: + motion_conflict if evaluable
                required.append('motion_conflict')
            missing = [c for c in required if c not in reasons]
            if missing:
                item.add('V36', f'{key}: null needs {missing}')
        elif not (isinstance(value, list) and all(isinstance(v, str) and v in allowed for v in value)):
            item.add('V36', f'{key}: not a list of allowed values')
    if evaluable:
        _v36_evaluable(item, r, reasons, rules_changed, mode)
    else:
        _v36_unevaluable(item, r, reasons)


def _v36_evaluable(item, r, reasons, rules_changed, mode=None):
    def need(name, *codes):
        missing = [c for c in codes if c not in reasons]
        if missing:
            item.add('V36', f'{name}: stored shape needs review reason(s) {missing}')
    if r['unevaluable_reason'] is not None:
        if isinstance(r['unevaluable_reason'], str):
            need('unevaluable_reason', 'field_invalid')
        else:
            item.add('V36', 'unevaluable_reason is not null or a string')
    if r['capture_type'] is None:
        need('capture_type', 'field_invalid')
    elif r['capture_type'] not in CAPTURE_TYPES:
        item.add('V36', 'capture_type not allowed')
    t, e = r['technical_score'], r['editorial_score']
    if not (_is_int(t) and 1 <= t <= 5 and _is_int(e) and 1 <= e <= 5):
        item.add('V36', 'technical_score / editorial_score not integers 1-5')
    technical = r['technical']
    if not (isinstance(technical, dict) and set(technical) == {'motion', 'motion_evidence', 'recoverable_in_raw', 'evidence'}):
        item.add('V36', 'technical is not an object with the four keys')
    else:
        motion = technical['motion']
        if motion not in MOTION_CLASSES:
            item.add('V36', 'technical.motion not allowed')
        _evidence_shape(item, 'technical.motion_evidence', technical['motion_evidence'], reasons,
                        required=motion in _INTENTIONAL, rules_changed=rules_changed)
        if technical['recoverable_in_raw'] is None:
            need('technical.recoverable_in_raw', 'field_invalid')
        elif not _is_str_list(technical['recoverable_in_raw']):
            item.add('V36', 'technical.recoverable_in_raw not a list of strings')
        _evidence_shape(item, 'technical.evidence', technical['evidence'], reasons, required=True,
                        rules_changed=rules_changed)
    editorial = r['editorial']
    if editorial is None:
        need('editorial', 'evidence_missing')
    elif not (isinstance(editorial, dict) and set(editorial) == {'evidence'}):
        item.add('V36', 'editorial is not null or an object with key evidence')
    else:
        _evidence_shape(item, 'editorial.evidence', editorial['evidence'], reasons, required=True,
                        rules_changed=rules_changed)
    rating = r['rating']
    required = r['proposed_moment_override'] is True or rating in (1, 4, 5)
    _evidence_shape(item, 'decisive_evidence', r['decisive_evidence'], reasons, required=required,
                    rules_changed=rules_changed)
    proposed = r['proposed_rating']
    if proposed is None:
        need('proposed_rating', 'rating_rule_mismatch')
    elif not (_is_int(proposed) and 0 <= proposed <= 5):
        item.add('V36', 'proposed_rating not an integer 0-5 or null')
    for key in ('proposed_moment_override', 'distinct_moment'):
        if r[key] is None:
            need(key, 'field_invalid')
        elif not isinstance(r[key], bool):
            item.add('V36', f'{key} not a bool or null')
    if r['fatal_flaws'] is None:
        need('fatal_flaws', 'field_invalid')
    elif not _is_str_list(r['fatal_flaws']):
        item.add('V36', 'fatal_flaws not a list of strings')
    claim = r['distinct_moment'] is True
    dme = r['distinct_moment_evidence']
    if claim:
        _evidence_shape(item, 'distinct_moment_evidence', dme, reasons, required=True,
                        rules_changed=rules_changed, extra=() if dme is not None and _is_str_list(dme) and dme
                        and (rules_changed or all(is_specific(v) for v in dme)) else ('distinct_moment_invalid',))
    elif dme is None or (isinstance(dme, list) and dme):
        if dme is not None and not _is_str_list(dme):
            item.add('V36', 'distinct_moment_evidence not a list of strings')
        need('distinct_moment_evidence', 'field_invalid')
    elif dme != []:
        item.add('V36', 'distinct_moment_evidence not a list')
    _v36_burst(item, r, reasons, need, mode, rules_changed)


def _v36_burst(item, r, reasons, need, mode=None, rules_changed=False):
    nce = r['not_comparable_evidence']
    if r['burst_group'] is None:
        for key in BURST_MODEL_FIELDS[:6]:
            if r[key] is not None:
                item.add('V36', f'{key} must be null outside a burst')
        if nce is None or (isinstance(nce, list) and nce):
            if nce is not None and not _is_str_list(nce):
                item.add('V36', 'not_comparable_evidence not a list of strings')
            need('not_comparable_evidence', 'field_invalid')
        elif nce != []:
            item.add('V36', 'not_comparable_evidence not a list')
        if r['burst_ranking_status'] is not None:
            item.add('V36', 'burst_ranking_status must be null outside a burst')
        return
    bc = r['burst_comparison']
    if bc is None:
        need('burst_comparison', 'burst_comparison_invalid')
    elif bc not in BURST_COMPARISON:
        item.add('V36', 'burst_comparison not allowed')
    if r['burst_rank'] is not None and not (_is_int(r['burst_rank']) and r['burst_rank'] >= 1):
        item.add('V36', 'burst_rank not an integer >= 1 or null')
    for key in ('best_in_burst', 'co_best'):
        if r[key] is not None and not isinstance(r[key], bool):
            item.add('V36', f'{key} not a bool or null')
    if r['near_duplicate_of'] is not None and not _abs_str(r['near_duplicate_of']):
        item.add('V36', 'near_duplicate_of not an absolute path or null')
    if r['rank_reason'] is not None and not isinstance(r['rank_reason'], str):
        item.add('V36', 'rank_reason not a string or null')
    if nce is not None and not _is_str_list(nce):
        item.add('V36', 'not_comparable_evidence not a list of strings')
        return
    ranking = ('burst_rank', 'best_in_burst', 'co_best', 'near_duplicate_of', 'rank_reason')
    if mode == 'ranked':                                             # 14.2 shapes in a ranked group
        if r['burst_rank'] is None:
            need('burst_rank', 'burst_rank_invalid')                 # W after B4
        for key in ('best_in_burst', 'co_best'):
            if r[key] is None:
                item.add('V36', f'{key} must be a bool in a ranked group')
        reason = r['rank_reason']
        if reason is None:
            need('rank_reason', 'evidence_missing')                  # W (B6)
        elif isinstance(reason, str) and not reason.strip():
            item.add('V36', 'rank_reason is an empty string')
        elif isinstance(reason, str) and not rules_changed and not is_specific(reason):
            need('rank_reason', 'evidence_generic')                  # K (B6)
        if nce is None or nce:
            need('not_comparable_evidence', 'field_invalid')         # D4-4 / K in a ranked group
    elif mode == 'not_comparable':                                   # 14.2 shapes in a not_comparable group
        if any(r[key] is not None for key in ranking):
            need('ranking fields', 'burst_comparison_invalid')       # K in a failed C2 claim
        if nce is None or not nce:
            need('not_comparable_evidence', 'evidence_missing', 'burst_comparison_invalid')
        elif not rules_changed and not all(is_specific(v) for v in nce):
            need('not_comparable_evidence', 'evidence_generic', 'burst_comparison_invalid')
    # mode None (C1, D4-1a): no mode-specific evidence rule; type checks above only.


def _v36_unevaluable(item, r, reasons):
    expected = {
        'technical_score': None, 'editorial_score': None,
        'technical': {'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []},
        'editorial': {'evidence': []}, 'decisive_evidence': [], 'distinct_moment_evidence': [],
        'not_comparable_evidence': [], 'proposed_moment_override': False, 'distinct_moment': False,
        'fatal_flaws': [], 'burst_ranking_status': None,          # rating/decision/override: V34
        'documentation_suggestion': 'unrated', 'editorial_suggestion': 'not_suggested',
        'needs_review': True,
    }
    expected.update({key: None for key in BURST_MODEL_FIELDS[:6]})
    for key, value in expected.items():
        if r[key] != value or type(r[key]) is not type(value):
            item.add('V36', f'unevaluable: {key} must be {value!r}')
    if not _nonempty_str(r['unevaluable_reason']):
        item.add('V36', 'unevaluable: unevaluable_reason must be a non-empty string')
    if r['capture_type'] is not None and r['capture_type'] not in CAPTURE_TYPES:
        item.add('V36', 'capture_type not allowed')
    proposed = r['proposed_rating']
    if proposed != 0 and 'rating_rule_mismatch' not in reasons:
        item.add('V36', 'unevaluable: proposed_rating != 0 needs rating_rule_mismatch')
    if 'unevaluable' not in reasons:
        item.add('V36', 'unevaluable: review_reasons must include unevaluable')


# --- Bursts: V39, V41, V44 (B-11a, V43 separation, D4-4b) ----------------------------------------------

def _b0_groups(doc: dict, results: list) -> tuple[set[str], list[str]]:
    """B0 at read time under B-11a: known identity names its group; unknown identity -> the
    groups of its session, or of the whole file. Batch scope is never used."""
    by_session: dict[str, set[str]] = {}
    every: set[str] = set()
    for r in results:
        if isinstance(r, dict) and _nonempty_str(r.get('burst_group')):
            every.add(r['burst_group'])
            if _nonempty_str(r.get('session_id')):
                by_session.setdefault(r['session_id'], set()).add(r['burst_group'])
    affected, notes = set(), []
    for index, entry in enumerate(doc['errors']):
        findings = classify_error_entry(entry)
        if findings['identity_unknown']:
            session = entry.get('session_id')
            scope = by_session.get(session, set()) if _nonempty_str(session) else every
            affected |= scope
            notes.append(f'V44: errors[{index}] identity unknown ({findings["identity_unknown"]}); '
                         f'{"session " + repr(session) if _nonempty_str(session) else "whole file"} invalid')
            continue
        if entry['sent_to_model'] is True and entry['burst_group'] is not None:
            affected.add(entry['burst_group'])
    return affected, notes


def _bursts(report: ReadReport) -> None:
    doc = report.doc
    results = doc['results']
    members: dict[str, list[int]] = {}
    for item, r in zip(report.items, results):
        if isinstance(r, dict) and _nonempty_str(r.get('burst_group')):
            members.setdefault(r['burst_group'], []).append(item.index)
    b0, notes = _b0_groups(doc, results)
    report.warnings.extend(notes)
    sent_errors: dict[str, int] = {}
    for entry in doc['errors']:
        if (isinstance(entry, dict) and not classify_error_entry(entry)['identity_unknown']
                and entry['sent_to_model'] is True and entry['burst_group'] is not None):
            sent_errors[entry['burst_group']] = sent_errors.get(entry['burst_group'], 0) + 1
    for group, indexes in sorted(members.items()):
        rs = [results[i] for i in indexes]
        evaluable = [r for r in rs if r.get('evaluable') is True]
        stored_values = {json.dumps(r.get('burst_ranking_status')) for r in evaluable}
        stored = evaluable[0].get('burst_ranking_status') if len(stored_values) == 1 and evaluable else None
        reasons: list[str] = []
        if len(stored_values) > 1:
            reasons.append('V39: burst_ranking_status differs within the group')
        if any(r.get('burst_ranking_status') is not None and r.get('burst_ranking_status') not in BURST_RANKING_STATUS
               for r in evaluable):
            reasons.append('V39: burst_ranking_status not an allowed value')
        if len({r.get('session_id') for r in rs}) > 1:                          # V41 P1
            reasons.append('V41: group spans several sessions')
        if len(rs) + sent_errors.get(group, 0) > doc['max_burst_size']:         # V41 P3
            reasons.append('V41: group larger than max_burst_size')
        if group in b0:
            reasons.append('V44/B0: a frame of this group was sent to the model and is in errors '
                           'or its identity is unknown')
        rerun = None
        if evaluable and not any(r.startswith('V41') for r in reasons):
            rerun, why = _rerun(group, indexes, results, report)
            if why:
                reasons.append(why)
        if not evaluable:
            effective = 'invalid' if reasons else None
        else:
            given = rerun if rerun is not None else 'invalid'
            effective = effective_burst_status_v3(stored if len(stored_values) == 1 else 'invalid', given)
            if any(r.startswith(('V39', 'V41', 'V44')) for r in reasons):
                effective = 'invalid'
            if stored in BURST_RANKING_STATUS and rerun is not None and stored != rerun:
                reasons.append(NEUTRAL_MISMATCH)
        report.groups[group] = GroupReport(group, stored, rerun, effective, reasons)
        if effective == 'invalid':
            for i in indexes:
                item = report.items[i]
                item.eligibility_invalid = True
                for reason in reasons:
                    row = reason.split(':')[0].split('/')[0]
                    item.add(row if row in ('V39', 'V41', 'V44') else 'V39', reason)
                if group in b0:
                    item.gallery_flags.append('burst_frame_missing')
                item.gallery_flags.append('burst group invalid')


def _rerun(group: str, indexes: list[int], results: list, report: ReadReport) -> tuple[str | None, str | None]:
    """Re-run S4 unchanged on stored fields; never modifies stored data (V39, V43)."""
    accepted, contexts = {}, {}
    try:
        for i in indexes:
            r = results[i]
            if any(k not in r for k in RESULT_FIELDS):
                return None, 'V39: a group member lacks stored fields; re-run not possible'
            ctx = FrameContext(original_path=r['original_path'], preview_path=r['preview_path'],
                               session_id=r['session_id'], burst_group=r['burst_group'],
                               burst_sequence=r['burst_sequence'], capture_time=None,
                               original_sha256=r['original_sha256'], batch_index=0)
            rating = r['rating'] if _is_int(r['rating']) else 0
            fields = {k: r[k] for k in RESULT_FIELDS if k not in APPROVAL_FIELDS and k not in (
                'burst_ranking_status', 'documentation_suggestion', 'editorial_suggestion', 'needs_review',
                'review_reasons', 'reasons')}
            burst_raw = {k: r[k] for k in BURST_MODEL_FIELDS} if r['evaluable'] is True else None
            accepted[r['original_path']] = Accepted(fields=fields, rating=rating, decision=str(r['decision']),
                                                    moment_override=bool(r['moment_override']),
                                                    review_reasons=frozenset(), distinct_moment_claim_valid=False,
                                                    burst_raw=burst_raw)
            contexts[r['original_path']] = ctx
        _out, outcomes = apply_bursts_v3(accepted, [], contexts)
    except (ValueError, TypeError, KeyError) as error:
        return None, f'V39: re-run not possible from stored data ({error})'
    outcome = outcomes[group]
    if not report.evidence_rules_changed:
        return outcome.status, None
    failed = outcome.failed_rules
    if 'C1' in failed:
        return 'invalid', None
    modes = set(outcome.categories.values())
    if modes == {'R'}:
        return ('invalid' if failed & {'B3', 'B4', 'B5'} else 'validated'), None
    if modes == {'NC'}:
        frames = [results[i] for i in indexes if results[i]['evaluable'] is True]
        structural = len(frames) >= 2 and all(f[k] is None for f in frames
                                              for k in ('burst_rank', 'best_in_burst', 'co_best',
                                                        'near_duplicate_of', 'rank_reason'))
        return ('not_comparable' if structural else 'invalid'), None
    return 'invalid', None


# --- V38 / 12.1 approval state (reader rule: checks 1, 2, 4-6; check 3 never) ----------------------------

def _preview_sha(path: str) -> str | None:
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


def _approval(item: ItemReport, r: dict, work: Path, source_folder, check_previews: bool) -> None:
    state = approval_state_without_files(r)
    if state in ('malformed', 'undetermined'):
        item.approval_state, item.approval_message = 'malformed', SKIP_MALFORMED
        item.add('V38', 'approval fields break a 12.2 rule' if state == 'malformed'
                 else 'approval state cannot be derived (needs_review or rating unusable)')
        return
    if state == 'not_approved':
        item.approval_state = 'pending' if r.get('needs_review') is True else 'not_required'
        return
    if state == 'invalidated':
        check = 1 if r['approved_rating'] != r['rating'] else 2 if r['approved_original_path'] != r['original_path'] else 4
        item.approval_state, item.approval_message = 'invalidated', BINDING_MESSAGES[check]
        return
    if check_previews:                                                          # binding checks 5 and 6
        result = check_preview(r['preview_path'], work, r['original_path'], source_folder)
        if not result.ok:
            item.add('V42', ', '.join(result.failed_ids))
            item.approval_state, item.approval_message = 'invalidated', BINDING_MESSAGES[5]
            return
        if _preview_sha(r['preview_path']) != r['approved_preview_sha256']:
            item.approval_state, item.approval_message = 'invalidated', BINDING_MESSAGES[6]
            return
    item.approval_state = 'approved'                                            # original not verified


# --- V37 suggestions, V32 distribution (display only) ---------------------------------------------------

def _v37(item: ItemReport, r: dict, report: ReadReport) -> None:
    if any(row in item.findings for row in ('V34', 'V36')) or item.approval_state is None:
        return
    try:
        reasons = r['review_reasons'] if isinstance(r['review_reasons'], list) else []
        dme = r['distinct_moment_evidence']
        if report.evidence_rules_changed:
            claim = r['distinct_moment'] is True and 'distinct_moment_invalid' not in reasons
        else:
            claim = (r['distinct_moment'] is True and _is_str_list(dme) and bool(dme)
                     and all(is_specific(v) for v in dme))
        claim = claim and _is_int(r['editorial_score']) and r['editorial_score'] >= 2
        group = r['burst_group']
        revalidated = report.groups[group].effective if group in report.groups else None
        kwargs = {} if group is None else {'revalidated': revalidated}
        documentation, _ = documentation_suggestion_v3(r['rating'], claim)
        eligible = highlight_eligible_v3(r, reasons, claim, **kwargs)
        editorial = editorial_suggestion_v3(r['rating'], eligible, item.approval_state)
    except (ValueError, KeyError, TypeError) as error:
        item.warnings.append(f'V37: suggestions could not be recomputed ({error})')
        return
    item.recomputed_suggestions = {'documentation_suggestion': documentation, 'editorial_suggestion': editorial,
                                   'highlight_eligible': eligible}
    for key, value in (('documentation_suggestion', documentation), ('editorial_suggestion', editorial)):
        if r.get(key) != value:
            item.warnings.append(f'V37: stored {key} {r.get(key)!r} differs from recomputed {value!r}')
    if report.evidence_rules_changed:
        item.warnings.append(f'V43: suggestions {EVIDENCE_CHANGED_LABEL}')


def _v32(report: ReadReport) -> None:
    doc = report.doc
    counts = {str(k): 0 for k in range(1, 6)}
    rating_0 = 0
    for r in doc['results']:
        if not isinstance(r, dict) or not _is_int(r.get('rating')):
            continue
        if r['rating'] == 0:
            rating_0 += 1
        elif r.get('evaluable') is True and 1 <= r['rating'] <= 5:
            counts[str(r['rating'])] += 1
    denominator = sum(counts.values())
    percentages = {k: (100 * v / denominator if denominator else None) for k, v in counts.items()}
    drift = ((counts['4'] + counts['5']) * 100 > DRIFT_LIMIT_PERCENT * denominator) if denominator else None
    recomputed = {'denominator': denominator, 'counts': counts, 'percentages': percentages,
                  'rating_0': rating_0, 'errors': len(doc['errors'])}
    if doc.get('distribution') != recomputed:
        report.warnings.append('V32: stored distribution differs from a recomputation; showing recomputed values')
    if doc.get('calibration_drift_warning') != drift or not (doc.get('calibration_drift_warning') is None
                                                             or isinstance(doc.get('calibration_drift_warning'), bool)):
        report.warnings.append('V32: stored calibration_drift_warning differs from a recomputation')
    report.warnings_distribution = recomputed
