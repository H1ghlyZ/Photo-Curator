"""Pure v3 suggestions, review routing and distribution (spec 5g3a sections 7.1, 7.2, 11, 12.1
rows 2-3, 14.1, 14.4, 17; spec 15.4 steps 11-14; implementation step S5).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S5 only):
- pure functions, no file I/O, NOT wired into the CLI or the v2 pipeline;
- consumes the output of S3 (``validate_v3``) and S4 (``bursts_v3.apply_bursts_v3``); never
  re-validates and never changes ``rating``, ``decision``, T, E, ``moment_override``,
  ``distinct_moment_claim_valid``, ``burst_raw``, any S3/S4 field or any existing review code;
- ``reasons`` (S10) and the default approval fields (result assembly) are not written here
  (D5-8). Apply never reads suggestions (V37).

Approved decisions:
- D5-1: every per-item function works on stored fields and review codes only (no
  ``GroupOutcome``), so S11 and S13 can recompute suggestions from a stored result (V37, 7.2).
- D5-2: ``distinct_moment_highlight`` follows the literal 7.2 eligibility (no rating limit): a
  non-winning frame in a ``validated`` group with a valid distinct-moment claim that is
  highlight-eligible gets it, whatever its rating, and therefore goes to review.
- D5-3 (implementation interpretation, next spec revision): V20 withdraws a non-burst
  frame's ``near_duplicate_of`` to ``null`` with ``field_invalid``, leaving no other trace,
  while 7.2 says a claimed near-duplicate can never be a highlight. Fail-closed: a non-burst
  frame with ANY ``field_invalid`` is not highlight-eligible. Known false positives: a
  non-burst frame whose ``field_invalid`` comes from an unrelated field (for example a
  missing ``confidence``) also loses the highlight path until a new analysis; documentation
  suggestion, rating, ``needs_review`` and apply are unaffected. Burst frames are unaffected
  (B5 always records ``near_duplicate_invalid``).
- D5-4 / D5-5: distribution shape ``{"denominator", "counts", "percentages", "rating_0",
  "errors"}``; percentages = 100 * count / denominator, unrounded; ``errors`` = len(errors).
- D5-6: stored ``review_reasons`` are sorted alphabetically.
- D5-7: the 7.2 editorial table is applied literally (a hero with state ``not_required``,
  which valid data cannot produce, is ``not_suggested``).
- D5-9: five new files.
- D5-10 (D4-4b enforcement): the burst status used for eligibility is always derived from the
  stored ``burst_ranking_status`` by ``effective_burst_status_v3``; a caller may only pass a
  revalidated status, which can keep or tighten but never loosen it. A stored ``invalid`` can
  never become ``validated`` or ``not_comparable``. A missing ``burst_ranking_status`` raises
  ``ValueError``.
- D5-11: a rating-0 result without the S3 review reason ``unevaluable`` is a contract
  violation (``ValueError``); the code is never added or repaired here.
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .schema_v3 import (APPROVAL_STATES, BURST_RANKING_STATUS, DOCUMENTATION_SUGGESTIONS,
                        EDITORIAL_SUGGESTIONS, REVIEW_CODES)
from .validate_v3 import Accepted

S5_FIELDS = ('documentation_suggestion', 'editorial_suggestion', 'needs_review', 'review_reasons')
EVIDENCE_CODES = frozenset({'evidence_missing', 'evidence_generic'})
DRIFT_LIMIT_PERCENT = 15
_PROTECTED_FIELDS = frozenset({
    'rating', 'decision', 'moment_override', 'technical_score', 'editorial_score', 'evaluable',
    'distinct_moment', 'distinct_moment_evidence', 'burst_ranking_status', 'best_in_burst',
    'near_duplicate_of', 'burst_group',
})


class _NotGiven:
    def __repr__(self) -> str:
        return 'NOT_GIVEN'


NOT_GIVEN = _NotGiven()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_codes(codes: Iterable[str]) -> None:
    for code in codes:
        if code not in REVIEW_CODES:
            raise AssertionError(f'unknown review code {code!r}')


# --- Documentation (7.1) ----------------------------------------------------------------

def documentation_suggestion_v3(rating: int, claim_valid: bool) -> tuple[str, frozenset]:
    """7.1. Never depends on burst status."""
    if not _is_int(rating) or not 0 <= rating <= 5:
        raise ValueError(f'rating must be an integer 0-5, got {rating!r}')
    if rating == 0:
        return 'unrated', frozenset()
    if rating >= 3:
        return 'retain', frozenset()
    if claim_valid is True:
        return 'retain_low_rating', frozenset({'documentation_keep_low_rating'})
    return 'not_suggested', frozenset()


# --- Burst status and eligibility (7.2, D5-10) -------------------------------------------

def effective_burst_status_v3(stored: Any, revalidated: Any = NOT_GIVEN) -> str | None:
    """Monotone status: the result is the stored status or 'invalid', never looser."""
    given = revalidated is not NOT_GIVEN
    if given and revalidated is not None and not (isinstance(revalidated, str)
                                                  and revalidated in BURST_RANKING_STATUS):
        raise ValueError(f'invalid revalidated burst status {revalidated!r}')              # row 1
    if stored is not None and not (isinstance(stored, str) and stored in BURST_RANKING_STATUS):
        return 'invalid'                                                                   # row 2
    if stored == 'invalid' or (given and revalidated == 'invalid'):
        return 'invalid'                                                                   # row 3
    if not given:
        return stored                                                                      # row 4
    if revalidated != stored:
        return 'invalid'                                                                   # row 5
    return stored                                                                          # row 6


def _require(fields: Mapping, key: str) -> Any:
    if key not in fields:
        raise ValueError(f'stored field {key!r} is missing')
    return fields[key]


def burst_path_v3(fields: Mapping, claim_valid: bool, revalidated: Any = NOT_GIVEN) -> str | None:
    """The 7.2 burst position path: 'single', 'winner', 'distinct_moment', 'not_comparable' or None."""
    stored = _require(fields, 'burst_ranking_status')
    if _require(fields, 'burst_group') is None:
        if revalidated is not NOT_GIVEN and revalidated is not None:
            raise ValueError('a non-burst frame has no burst status to revalidate')
        return 'single' if stored is None else None                     # stored status here is corrupt
    status = effective_burst_status_v3(stored, revalidated)
    if status == 'validated':
        if _require(fields, 'best_in_burst') is True:
            return 'winner'
        return 'distinct_moment' if claim_valid is True else None
    if status == 'not_comparable':
        return 'not_comparable'
    return None


def highlight_eligible_v3(fields: Mapping, review_reasons: Iterable[str], claim_valid: bool,
                          revalidated: Any = NOT_GIVEN) -> bool:
    """7.2 highlight eligibility, plus D5-3 for non-burst frames."""
    reasons = frozenset(review_reasons)
    path = burst_path_v3(fields, claim_valid, revalidated)
    near_duplicate = _require(fields, 'near_duplicate_of')
    if path is None:
        return False
    if reasons & EVIDENCE_CODES:
        return False
    if near_duplicate is not None or 'near_duplicate_invalid' in reasons:
        return False
    if path == 'single' and 'field_invalid' in reasons:                 # D5-3
        return False
    return True


# --- Approval state at analysis and editorial suggestion (7.2, 12.1) ---------------------

def analysis_approval_state_v3(needs_review: bool) -> str:
    """At analysis ``approved`` is always false: 12.1 rows 2 and 3."""
    if not isinstance(needs_review, bool):
        raise ValueError('needs_review must be a bool')
    return 'pending' if needs_review else 'not_required'


def editorial_suggestion_v3(rating: int, eligible: bool, approval_state: str) -> str:
    """The 7.2 table, applied literally (D5-7)."""
    if approval_state not in APPROVAL_STATES:
        raise ValueError(f'unknown approval state {approval_state!r}')
    waiting = approval_state in ('pending', 'invalidated', 'malformed')
    if eligible is True and rating == 5:
        if approval_state == 'approved':
            return 'hero_candidate'
        return 'pending_approval' if waiting else 'not_suggested'
    if eligible is True and rating == 4:
        return 'pending_approval' if waiting else 'highlight'
    return 'not_suggested'


# --- Distribution (17) ------------------------------------------------------------------

@dataclass(frozen=True)
class RunSummary:
    distribution: dict
    calibration_drift_warning: bool | None


def distribution_v3(results: Mapping[str, Accepted], errors: Sequence[Any]) -> RunSummary:
    if not isinstance(errors, (list, tuple)):
        raise ValueError('errors must be a list')
    counts = {str(r): 0 for r in range(1, 6)}
    rating_0 = 0
    for acc in results.values():
        if acc.rating == 0:
            rating_0 += 1
        elif acc.fields.get('evaluable') is True and 1 <= acc.rating <= 5:
            counts[str(acc.rating)] += 1
    denominator = sum(counts.values())
    if denominator:
        percentages = {k: 100 * v / denominator for k, v in counts.items()}
        drift = (counts['4'] + counts['5']) * 100 > DRIFT_LIMIT_PERCENT * denominator
    else:
        percentages = {k: None for k in counts}
        drift = None
    return RunSummary({'denominator': denominator, 'counts': counts, 'percentages': percentages,
                       'rating_0': rating_0, 'errors': len(errors)}, drift)


# --- Whole run (15.4 steps 11-14) -------------------------------------------------------

def _check_input(original: str, acc: Any) -> None:
    if not isinstance(acc, Accepted):
        raise ValueError(f'not an Accepted result: {original!r}')
    if 'burst_ranking_status' not in acc.fields:
        raise ValueError(f'{original!r} has not been through burst comparison (S4)')
    if any(key in acc.fields for key in S5_FIELDS):
        raise ValueError(f'{original!r} already carries S5 fields')
    unevaluable = acc.rating == 0 or acc.fields.get('evaluable') is False
    if unevaluable and not (acc.rating == 0 and acc.fields.get('evaluable') is False):
        raise ValueError(f'{original!r}: rating 0 and evaluable = false must coincide')
    if unevaluable and 'unevaluable' not in acc.review_reasons:
        raise ValueError(f'{original!r}: rating-0 result without review reason "unevaluable"')   # D5-11


def apply_suggestions_v3(results: Mapping[str, Accepted], errors: Sequence[Any]
                         ) -> tuple[dict[str, Accepted], RunSummary]:
    """Suggestions, review routing and the run distribution. Inputs are never modified."""
    if not isinstance(results, Mapping):
        raise ValueError('results must be a mapping')
    if not isinstance(errors, (list, tuple)):
        raise ValueError('errors must be a list')
    for original, acc in results.items():
        _check_input(original, acc)

    out: dict[str, Accepted] = {}
    for original in sorted(results):
        acc = results[original]
        claim = acc.distinct_moment_claim_valid
        documentation, doc_codes = documentation_suggestion_v3(acc.rating, claim)
        path = burst_path_v3(acc.fields, claim)
        eligible = highlight_eligible_v3(acc.fields, acc.review_reasons, claim)
        added = set(doc_codes)
        if path == 'distinct_moment' and eligible:                       # D5-2
            added.add('distinct_moment_highlight')
        _check_codes(added)
        reasons = acc.review_reasons | frozenset(added)
        _check_codes(reasons)
        needs_review = bool(reasons)
        editorial = editorial_suggestion_v3(acc.rating, eligible, analysis_approval_state_v3(needs_review))
        if documentation not in DOCUMENTATION_SUGGESTIONS or editorial not in EDITORIAL_SUGGESTIONS:
            raise AssertionError('suggestion outside its enumeration')
        updates = {'documentation_suggestion': documentation, 'editorial_suggestion': editorial,
                   'needs_review': needs_review, 'review_reasons': sorted(reasons)}
        if _PROTECTED_FIELDS & set(updates):
            raise AssertionError('S5 must not change S3/S4 fields')
        fields = copy.deepcopy(acc.fields)
        fields.update(updates)
        out[original] = dataclasses.replace(acc, fields=fields, review_reasons=reasons)
    return out, distribution_v3(out, errors)
