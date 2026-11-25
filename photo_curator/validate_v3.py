"""Pure v3 frame validation (spec 5g3a sections 6, 9, 10, 14.1, 14.2, 14.5, 15.1, 15.2, 15.4
steps 4-9; implementation step S3).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S3 only):
- pure functions, no file I/O, NOT wired into the CLI or the v2 analysis pipeline (C-1);
- burst-group rules (B0-B6, C1, C2, V21-V24, V28, V29) belong to S4 (C-2): for an evaluable
  frame inside a burst the seven raw burst fields are handed over unvalidated in
  ``Accepted.burst_raw``;
- suggestions, ``needs_review``, ``reasons``, ``burst_ranking_status`` and the seven approval
  fields are produced by later steps; ``Accepted`` is an in-memory value, never serialized.

Documented implementation decisions (approved for S3; not spec changes):
- D3-1..D3-4: specificity check in ``is_specific``; present but insufficient evidence
  (including an empty or whitespace-only entry) gives ``evidence_generic``.
- D3-5: model-supplied code (C) fields are ignored and not stored, with no review code;
  model-supplied human (H) approval fields are ignored and not stored, and add
  ``field_invalid``. C fields come only from ``FrameContext`` or from computation here; H
  fields are written only by the approve command, never from model output.
- D3-6: ``score_gap`` is never raised on unevaluable results.
- D3-7 (interpretation of spec 14.5 ``result``): duplicate results for one original give ONE
  error entry for that original whose ``result`` is an ordered array of every raw duplicate
  item, in input order. On an entry naming an original, a single rejected result is always
  an object, so ``result`` is an array exactly when it holds duplicates.
- D3-8: a FrameContext or batch contract violation raises ``ValueError``.
- D3-10: every context of one batch shares ``batch_index`` and ``session_id``.
- D3-11: an entry naming no frame stores ``original_path``, ``burst_group``,
  ``burst_sequence`` and ``capture_time`` as null; its identity is unknown because
  ``original_path`` is null, so B0 (S4) applies at batch scope. S4 must classify an entry
  (``classify_error_entry``) before reading its ``burst_group``.
- D3-12: matching uses the model's ``path`` only; a model ``original_path`` that is present
  and not identical to ``path`` rejects the item as identity unknown.
- D3-13 (APPROVED DEVIATION from the spec 14.5 wording; to be clarified in the next spec
  revision): ``preview_path`` is null only on ``stage = "result"`` entries whose
  ``original_path`` is null. Every entry naming an original takes ``preview_path`` from its
  FrameContext. The 11-field entry shape is unchanged.
- O-S3-1 (accepted principle): a missing key is never treated as a valid ``null``. For the six
  non-list burst fields outside a burst (V20) a missing key takes the spec 14.2 W shape
  ``null`` (``field_invalid``).
- D3-14 (APPROVED DEVIATION; to be clarified in the next spec revision): spec 14.2 has no
  stored shape for a missing or wrong-typed value of these three fields, so:
  * ``unevaluable_reason`` on an evaluable result (V5): missing or not a string -> ``null`` +
    ``field_invalid``; a string -> kept as returned (K) + ``field_invalid``;
  * ``distinct_moment_evidence`` without a claim (V19): missing or not a list -> ``null`` +
    ``field_invalid``; a non-empty list -> kept as returned (K) + ``field_invalid``;
  * ``not_comparable_evidence`` outside a burst (V20): missing or not a list -> ``null`` +
    ``field_invalid``; a non-empty list -> kept as returned (K) + ``field_invalid``.
  Exactly ``[]`` (V19, V20) and ``null`` (V5) remain the only values with no review code.
"""
from __future__ import annotations

import copy
import math
import os
import re
from dataclasses import dataclass
from typing import Any

from .schema_v3 import (APPROVAL_FIELDS, BASE_MATRIX, CAPTURE_TYPES, CONCRETE_KEYWORDS,
                        CONFIDENCE_THRESHOLD, DECISIONS, EVIDENCE_MIN_LENGTH, FLAGS,
                        GENERIC_PHRASES, MOTION_CLASSES, OVERRIDE_CELLS, REVIEW_CODES,
                        UNCERTAIN_CHECKS, make_error_entry)

BURST_MODEL_FIELDS = ('burst_comparison', 'burst_rank', 'best_in_burst', 'co_best',
                      'near_duplicate_of', 'rank_reason', 'not_comparable_evidence')
BURST_NULL_FIELDS = BURST_MODEL_FIELDS[:6]          # null outside a burst / when unevaluable
INTENTIONAL_MOTION = frozenset({'intentional_pan', 'intentional_drag'})
SUBJECT_BLUR_CONFLICTS = frozenset({'intentional_pan', 'intentional_drag', 'uncertain', 'frozen'})
DECISIVE_RATINGS = frozenset({1, 4, 5})             # V12b
SCORE_GAP = 3

_TOKEN_RE = re.compile(r'[^\W_]+')
_SHA256_RE = re.compile(r'[0-9a-f]{64}')
_MISSING = object()                                  # internal: key not present


class _Absent:
    """Sentinel for a burst key the model did not return (keeps C1 (a) apart from null)."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return 'ABSENT'

    def __reduce__(self):
        return (_Absent, ())


ABSENT = _Absent()


# --- Specificity check (spec 9.3; D3-1, D3-2) -----------------------------------------

def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


_GENERIC_TOKENS = tuple(sorted((tuple(_tokens(p)) for p in GENERIC_PHRASES), key=len, reverse=True))
_KEYWORD_TOKENS = tuple(tuple(_tokens(k)) for k in CONCRETE_KEYWORDS)


def _contains(tokens: list[str], seq: tuple[str, ...]) -> bool:
    n = len(seq)
    return n > 0 and any(tuple(tokens[i:i + n]) == seq for i in range(len(tokens) - n + 1))


def is_specific(entry: Any) -> bool:
    """True only if ``entry`` is a string of at least 20 characters after trimming, is not
    made only of generic phrases, and contains a concrete keyword as whole tokens."""
    if not isinstance(entry, str) or len(entry.strip()) < EVIDENCE_MIN_LENGTH:
        return False
    tokens = _tokens(entry)
    remaining, i = [], 0
    while i < len(tokens):
        for phrase in _GENERIC_TOKENS:
            if phrase and tuple(tokens[i:i + len(phrase)]) == phrase:
                i += len(phrase)
                break
        else:
            remaining.append(tokens[i])
            i += 1
    if not remaining:
        return False
    return any(_contains(tokens, kw) for kw in _KEYWORD_TOKENS)


# --- Small type helpers -----------------------------------------------------------------

def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _is_empty_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 0


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ''


def _valid_confidence(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value in (0, 1)
    if isinstance(value, float):
        return math.isfinite(value) and 0.0 <= value <= 1.0
    return False


@dataclass(frozen=True)
class _Evidence:
    stored: Any
    codes: frozenset
    passes: bool


def _evidence(value: Any, required: bool) -> _Evidence:
    """Spec 9.1, 14.2 and the evidence-state table (plan 4.2)."""
    if value is _MISSING or not _is_str_list(value):
        return _Evidence(None, frozenset({'evidence_missing' if required else 'field_invalid'}), False)
    if not required:
        return _Evidence(value, frozenset(), bool(value) and all(is_specific(e) for e in value))
    if not value:
        return _Evidence(value, frozenset({'evidence_missing'}), False)
    if not all(is_specific(e) for e in value):
        return _Evidence(value, frozenset({'evidence_generic'}), False)
    return _Evidence(value, frozenset(), True)


# --- Context and outcomes ---------------------------------------------------------------

@dataclass(frozen=True)
class FrameContext:
    """Trusted, code-supplied values from batch planning; never from model output."""
    original_path: str
    preview_path: str
    session_id: str
    burst_group: str | None
    burst_sequence: str | None
    capture_time: str | None
    original_sha256: str
    batch_index: int


def _check_context(ctx: Any) -> None:
    if not isinstance(ctx, FrameContext):
        raise ValueError('FrameContext required')
    for name in ('original_path', 'preview_path'):
        value = getattr(ctx, name)
        if not _is_nonempty_str(value) or not os.path.isabs(value):
            raise ValueError(f'FrameContext.{name} must be an absolute path')
    if not _is_nonempty_str(ctx.session_id):
        raise ValueError('FrameContext.session_id must be a non-empty string')
    for name in ('burst_group', 'burst_sequence'):
        value = getattr(ctx, name)
        if value is not None and not _is_nonempty_str(value):
            raise ValueError(f'FrameContext.{name} must be a non-empty string or None')
    if ctx.capture_time is not None and not isinstance(ctx.capture_time, str):
        raise ValueError('FrameContext.capture_time must be a string or None')
    if not isinstance(ctx.original_sha256, str) or not _SHA256_RE.fullmatch(ctx.original_sha256):
        raise ValueError('FrameContext.original_sha256 must be 64 lowercase hex characters')
    if not _is_int(ctx.batch_index) or ctx.batch_index < 0:
        raise ValueError('FrameContext.batch_index must be a non-negative integer')


@dataclass(frozen=True)
class Accepted:
    """A frame that passed the S3 checks. In-memory only; never serialized as is."""
    fields: dict
    rating: int
    decision: str
    moment_override: bool
    review_reasons: frozenset
    distinct_moment_claim_valid: bool
    burst_raw: dict | None


@dataclass(frozen=True)
class Rejected:
    """A frame rejected under R; ``entry`` is one spec 14.5 error entry."""
    entry: dict


class _Reject(Exception):
    pass


def _known_entry(ctx: FrameContext, error: str, result: Any) -> dict:
    return make_error_entry(stage='result', error=error, original_path=ctx.original_path,
                            session_id=ctx.session_id, burst_group=ctx.burst_group,
                            burst_sequence=ctx.burst_sequence, capture_time=ctx.capture_time,
                            batch_index=ctx.batch_index, sent_to_model=True, result=result,
                            preview_path=ctx.preview_path)


def _unknown_entry(error: str, item: Any, session_id: str, batch_index: int) -> dict:
    # D3-11 and D3-13: no frame is named, so no identity or preview is invented.
    return make_error_entry(stage='result', error=error, original_path=None,
                            session_id=session_id, burst_group=None, burst_sequence=None,
                            capture_time=None, batch_index=batch_index, sent_to_model=True,
                            result=item, preview_path=None)


# --- Frame validation -------------------------------------------------------------------

class _Frame:
    def __init__(self, raw: dict, ctx: FrameContext):
        self.raw = raw
        self.ctx = ctx
        self.codes: set[str] = set()

    def get(self, key: str) -> Any:
        return self.raw.get(key, _MISSING)

    def add(self, *codes: str) -> None:
        for code in codes:
            if code not in REVIEW_CODES:
                raise AssertionError(f'unknown review code {code!r}')
            self.codes.add(code)

    # Fields validated the same way on every result (V7 capture_type aside).
    def _common(self, fields: dict) -> None:
        flags = self.get('flags')
        if isinstance(flags, list) and all(isinstance(f, str) and f in FLAGS for f in flags):
            fields['flags'] = flags
        else:
            fields['flags'] = None
            self.add('field_invalid')                                    # V15
        checks = self.get('uncertain_checks')
        if isinstance(checks, list) and all(isinstance(c, str) and c in UNCERTAIN_CHECKS for c in checks):
            fields['uncertain_checks'] = checks
            if checks:
                self.add('critical_defect_uncertain')                    # section 10
        else:
            fields['uncertain_checks'] = None
            self.add('field_invalid')                                    # V16
        confidence = self.get('confidence')
        if _valid_confidence(confidence):
            fields['confidence'] = confidence
            if confidence < CONFIDENCE_THRESHOLD:
                self.add('low_confidence')
        else:
            fields['confidence'] = None
            self.add('field_invalid')                                    # V25
        proposed = self.get('proposed_rating')
        fields['proposed_rating'] = proposed if _is_int(proposed) and 0 <= proposed <= 5 else None
        if any(key in self.raw for key in APPROVAL_FIELDS):
            self.add('field_invalid')                                    # D3-5 (H fields)

    def run(self) -> Accepted:
        evaluable = self.get('evaluable')
        if not isinstance(evaluable, bool):
            raise _Reject('V2: evaluable missing or not a bool')
        fields: dict = {'evaluable': evaluable}
        if evaluable:
            rating, override, claim, burst_raw = self._evaluable(fields)
        else:
            rating, override, claim, burst_raw = self._unevaluable(fields)
        if fields['proposed_rating'] is None or fields['proposed_rating'] != rating:
            self.add('rating_rule_mismatch')                             # V13, section 6.6
        if rating == 5:
            self.add('rating_5')
        ctx = self.ctx
        fields.update({
            'path': ctx.original_path, 'original_path': ctx.original_path,
            'preview_path': ctx.preview_path, 'session_id': ctx.session_id,
            'burst_group': ctx.burst_group, 'burst_sequence': ctx.burst_sequence,
            'capture_time': ctx.capture_time, 'original_sha256': ctx.original_sha256,
            'moment_override': override, 'rating': rating, 'decision': DECISIONS[rating],
        })
        return Accepted(fields=fields, rating=rating, decision=DECISIONS[rating],
                        moment_override=override, review_reasons=frozenset(self.codes),
                        distinct_moment_claim_valid=claim, burst_raw=burst_raw)

    def _unevaluable(self, fields: dict):
        reason = self.get('unevaluable_reason')
        if not _is_nonempty_str(reason):
            raise _Reject('V3: unevaluable_reason missing, not a string, or empty')
        for key in ('technical_score', 'editorial_score'):
            if self.get(key) is not None:
                raise _Reject(f'V4: {key} must be null on an unevaluable result')
        technical = self.get('technical')
        if not isinstance(technical, dict):
            raise _Reject('V4/V8: technical missing or not an object')
        if technical.get('motion', _MISSING) is not None:
            raise _Reject('V4: technical.motion must be null on an unevaluable result')
        for key in ('motion_evidence', 'evidence', 'recoverable_in_raw'):
            if not _is_empty_list(technical.get(key, _MISSING)):
                raise _Reject(f'V4: technical.{key} must be [] on an unevaluable result')
        editorial = self.get('editorial')
        if not isinstance(editorial, dict) or not _is_empty_list(editorial.get('evidence', _MISSING)):
            raise _Reject('V4: editorial must be an object with evidence [] on an unevaluable result')
        for key in ('decisive_evidence', 'distinct_moment_evidence', 'not_comparable_evidence', 'fatal_flaws'):
            if not _is_empty_list(self.get(key)):
                raise _Reject(f'V4: {key} must be [] on an unevaluable result')
        for key in ('proposed_moment_override', 'distinct_moment'):
            if self.get(key) is not False:
                raise _Reject(f'V4: {key} must be false on an unevaluable result')
        for key in BURST_NULL_FIELDS:
            if self.get(key) is not None:
                raise _Reject(f'V4: {key} must be null on an unevaluable result')

        capture_type = self.get('capture_type')
        if capture_type is None or (isinstance(capture_type, str) and capture_type in CAPTURE_TYPES):
            fields['capture_type'] = capture_type
        else:
            fields['capture_type'] = None
            self.add('field_invalid')                                    # V7
        self._common(fields)
        self.add('unevaluable')
        fields.update({
            'unevaluable_reason': reason, 'technical_score': None, 'editorial_score': None,
            'technical': {'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []},
            'editorial': {'evidence': []}, 'decisive_evidence': [],
            'proposed_moment_override': False, 'fatal_flaws': [], 'distinct_moment': False,
            'distinct_moment_evidence': [],
        })
        fields.update({key: None for key in BURST_NULL_FIELDS})
        fields['not_comparable_evidence'] = []
        return 0, False, False, None

    def _evaluable(self, fields: dict):
        scores = {}
        for key in ('technical_score', 'editorial_score'):
            value = self.get(key)
            if not _is_int(value) or not 1 <= value <= 5:
                raise _Reject(f'V6: {key} missing, not an integer, or outside 1-5')
            scores[key] = value
        t, e = scores['technical_score'], scores['editorial_score']
        technical = self.get('technical')
        if not isinstance(technical, dict):
            raise _Reject('V8: technical missing or not an object')
        motion = technical.get('motion', _MISSING)
        if not isinstance(motion, str) or motion not in MOTION_CLASSES:
            raise _Reject('V8: technical.motion missing or not an allowed value')

        reason = self.get('unevaluable_reason')
        if reason is None:
            fields['unevaluable_reason'] = None
        else:                                                            # V5, D3-14
            fields['unevaluable_reason'] = reason if isinstance(reason, str) else None
            self.add('field_invalid')
        capture_type = self.get('capture_type')
        if isinstance(capture_type, str) and capture_type in CAPTURE_TYPES:
            fields['capture_type'] = capture_type
        else:
            fields['capture_type'] = None
            self.add('field_invalid')                                    # V7
        fields['technical_score'], fields['editorial_score'] = t, e

        motion_ev = _evidence(technical.get('motion_evidence', _MISSING), motion in INTENTIONAL_MOTION)
        self.add(*motion_ev.codes)                                       # V9
        recoverable = technical.get('recoverable_in_raw', _MISSING)
        if not _is_str_list(recoverable):
            recoverable = None
            self.add('field_invalid')                                    # V10
        tech_ev = _evidence(technical.get('evidence', _MISSING), True)
        self.add(*tech_ev.codes)                                         # V11
        fields['technical'] = {'motion': motion, 'motion_evidence': motion_ev.stored,
                               'recoverable_in_raw': recoverable, 'evidence': tech_ev.stored}
        editorial = self.get('editorial')
        if isinstance(editorial, dict):
            ed_ev = _evidence(editorial.get('evidence', _MISSING), True)
            self.add(*ed_ev.codes)
            fields['editorial'] = {'evidence': ed_ev.stored}
        else:
            fields['editorial'] = None
            self.add('evidence_missing')                                 # V11

        fatal = self.get('fatal_flaws')
        if _is_str_list(fatal):
            fields['fatal_flaws'] = fatal
        else:
            fields['fatal_flaws'] = None
            self.add('field_invalid')                                    # V15
        self._common(fields)

        proposal = self.get('proposed_moment_override')
        if isinstance(proposal, bool):
            fields['proposed_moment_override'] = proposal
        else:
            fields['proposed_moment_override'] = None
            self.add('field_invalid')                                    # V14

        # Sections 6.1-6.3, 9.2 (V12a before the override, V12b after the final rating).
        decisive_raw = self.get('decisive_evidence')
        decisive = _evidence(decisive_raw, True)
        rating, override = BASE_MATRIX[(t, e)], False
        if proposal is True:
            if not decisive.passes:
                self.add(*decisive.codes)                                # V12a
            if (t, e) in OVERRIDE_CELLS and decisive.passes:
                rating, override = OVERRIDE_CELLS[(t, e)], True
                self.add('moment_override')
            else:
                self.add('override_invalid')
        if rating in DECISIVE_RATINGS:
            self.add(*decisive.codes)                                    # V12b
        if proposal is True or rating in DECISIVE_RATINGS:
            fields['decisive_evidence'] = decisive.stored
        else:
            optional = _evidence(decisive_raw, False)                    # V12c
            self.add(*optional.codes)
            fields['decisive_evidence'] = optional.stored

        if abs(t - e) >= SCORE_GAP:
            self.add('score_gap')

        # Section 10.1 (V17).
        checks, flags = fields['uncertain_checks'], fields['flags']
        if checks is None:
            self.add('motion_conflict')
        elif ('motion_intent' in checks) != (motion == 'uncertain'):
            self.add('motion_conflict')
        if motion == 'uncertain':
            self.add('motion_uncertain')
        if flags is not None and 'subject_blur' in flags and motion in SUBJECT_BLUR_CONFLICTS:
            self.add('motion_conflict')

        # Distinct moments (V18, V19, section 7.1).
        claim = self.get('distinct_moment')
        dm_ev_raw = self.get('distinct_moment_evidence')
        claim_valid = False
        if isinstance(claim, bool):
            fields['distinct_moment'] = claim
        else:
            fields['distinct_moment'] = None
            self.add('field_invalid')
        if claim is True:
            dm_ev = _evidence(dm_ev_raw, True)
            fields['distinct_moment_evidence'] = dm_ev.stored
            if not dm_ev.passes:
                self.add(*dm_ev.codes, 'distinct_moment_invalid')
            if e < 2:
                self.add('distinct_moment_invalid')
            claim_valid = dm_ev.passes and e >= 2
        elif _is_empty_list(dm_ev_raw):
            fields['distinct_moment_evidence'] = []
        else:                                                            # V19 without a claim, D3-14
            fields['distinct_moment_evidence'] = dm_ev_raw if isinstance(dm_ev_raw, list) else None
            self.add('field_invalid')

        # Burst fields: outside a burst V20 / B7 here; inside a burst handed to S4.
        if self.ctx.burst_group is None:
            for key in BURST_NULL_FIELDS:
                fields[key] = None
                if self.get(key) is not None:
                    self.add('field_invalid')
            nce = self.get('not_comparable_evidence')
            if _is_empty_list(nce):
                fields['not_comparable_evidence'] = []
            else:
                fields['not_comparable_evidence'] = nce if isinstance(nce, list) else None   # D3-14
                self.add('field_invalid')
            burst_raw = None
        else:
            burst_raw = {key: self.raw.get(key, ABSENT) for key in BURST_MODEL_FIELDS}
        return rating, override, claim_valid, burst_raw


def validate_frame_v3(raw: dict, ctx: FrameContext) -> Accepted | Rejected:
    """Validate one model result for one original (spec 15.4 steps 5-9)."""
    _check_context(ctx)
    if not isinstance(raw, dict):
        raise ValueError('validate_frame_v3 requires an object; V1 handles non-objects')
    raw = copy.deepcopy(raw)
    try:
        return _Frame(raw, ctx).run()
    except _Reject as reason:
        return Rejected(_known_entry(ctx, str(reason), copy.deepcopy(raw)))


def validate_batch_v3(batch_originals: list[str], photos: list,
                      ctx_by_original: dict[str, FrameContext]) -> tuple[dict[str, Accepted], list[dict]]:
    """Validate one batch's returned items (spec 15.4 steps 4-9).

    Returns the accepted results keyed by original path and the error entries: entries that
    name no frame in input order, then entries naming an original sorted by original path.
    Every original ends with exactly one outcome: an accepted result or one error entry.
    """
    if not isinstance(photos, list):
        raise ValueError('photos must be a list')
    if not isinstance(batch_originals, list) or not batch_originals:
        raise ValueError('batch_originals must be a non-empty list')
    if len(set(batch_originals)) != len(batch_originals):
        raise ValueError('batch_originals contains duplicates')
    if not isinstance(ctx_by_original, dict) or set(ctx_by_original) != set(batch_originals):
        raise ValueError('ctx_by_original must hold exactly one context per original')
    for original, ctx in ctx_by_original.items():
        _check_context(ctx)
        if ctx.original_path != original:
            raise ValueError('context key and original_path differ')
    batch_indexes = {ctx.batch_index for ctx in ctx_by_original.values()}
    sessions = {ctx.session_id for ctx in ctx_by_original.values()}
    if len(batch_indexes) != 1 or len(sessions) != 1:
        raise ValueError('all contexts of a batch must share batch_index and session_id')  # D3-10
    batch_index, session_id = batch_indexes.pop(), sessions.pop()

    originals = set(batch_originals)
    unknown: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    for item in photos:
        if not isinstance(item, dict):
            unknown.append(_unknown_entry('V1: non-object result', copy.deepcopy(item), session_id, batch_index))
            continue
        path = item.get('path', _MISSING)
        if not isinstance(path, str) or path not in originals:
            unknown.append(_unknown_entry('V1: path outside this batch', copy.deepcopy(item), session_id, batch_index))
            continue
        if 'original_path' in item:
            claimed = item['original_path']
            if not isinstance(claimed, str) or claimed != path:                       # D3-12
                unknown.append(_unknown_entry('V1: original_path contradicts path',
                                              copy.deepcopy(item), session_id, batch_index))
                continue
        grouped.setdefault(path, []).append(item)

    accepted: dict[str, Accepted] = {}
    named: list[dict] = []
    for original in sorted(originals):
        ctx = ctx_by_original[original]
        items = grouped.get(original, [])
        if not items:
            named.append(_known_entry(ctx, 'V1: no result for this original', None))
        elif len(items) > 1:                                                          # C-5, D3-7
            named.append(_known_entry(ctx, f'V1: duplicate results ({len(items)}) for this original',
                                      copy.deepcopy(items)))
        else:
            outcome = validate_frame_v3(items[0], ctx)
            if isinstance(outcome, Accepted):
                accepted[original] = outcome
            else:
                named.append(outcome.entry)
    return accepted, unknown + named
