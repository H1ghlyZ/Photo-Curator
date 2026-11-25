"""Photo Curator schema v3: constants, field inventory, evidence rules and error entries.

Implementation step S1 of the approved specification
``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S1 only):
- pure data and pure functions; no file I/O, no CLI or pipeline wiring;
- schema v3 is NOT enabled anywhere: ``core.SCHEMA_VERSIONS`` stays {1, 2}
  (decision A-1), so no existing command reads or writes v3 files;
- the field inventory records names and sources only; executable stored-shape
  validation belongs to later steps (decision A-3);
- error entries are built and classified here but not produced by the existing
  v2 analysis pipeline (decision A-4).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# --- File-level constants (spec 14.4, 11, 8.0 P3) ------------------------------------

SCHEMA_VERSION_V3 = 3
RUBRIC_VERSION = 'v3'
RATING_STYLE_V3 = 'strict_v3'
BURST_SCOPE = 'per_session'
CONFIDENCE_THRESHOLD = 0.6
MAX_BURST_SIZE_RANGE = (2, 12)          # inclusive; 0 (unlimited) is not allowed in v3
EVIDENCE_RULES_VERSION = 'v3.1'         # decision U11 (initial v3.0); M-27 / S16: v3.1, not calibrated

# --- Enumerations ----------------------------------------------------------------------

# Spec 3.3: the frozen twelve-flag vocabulary (decision U7).
FLAGS = frozenset({
    'missed_focus', 'subject_blur', 'closed_eyes', 'awkward_expression',
    'unrecoverable_exposure', 'heavy_noise', 'clutter', 'mergers',
    'obstruction', 'subject_cut_off', 'cut_at_joint', 'duplicate',
})

# Spec 10.
UNCERTAIN_CHECKS = frozenset({
    'focus', 'eyes', 'motion_intent', 'exposure', 'expression', 'subject_identification',
})

# Spec 3.2.
MOTION_CLASSES = frozenset({
    'frozen', 'intentional_pan', 'intentional_drag', 'camera_shake',
    'unintended_subject_blur', 'uncertain',
})

# Spec 5.
CAPTURE_TYPES = frozenset({'candid', 'posed_portrait', 'group', 'detail', 'venue'})

# Spec 6.5: decision computed by code from the rating.
DECISIONS = {0: 'unreadable', 1: 'reject', 2: 'weak', 3: 'usable', 4: 'strong', 5: 'exceptional'}

# Spec 7.1 and 7.2.
DOCUMENTATION_SUGGESTIONS = frozenset({'retain', 'retain_low_rating', 'not_suggested', 'unrated'})
EDITORIAL_SUGGESTIONS = frozenset({'hero_candidate', 'highlight', 'pending_approval', 'not_suggested'})

# Spec 12.1 (derived, never stored).
APPROVAL_STATES = frozenset({'malformed', 'not_required', 'pending', 'invalidated', 'approved'})

# Spec 8.1 and 8.3.
BURST_COMPARISON = frozenset({'ranked', 'not_comparable'})
BURST_RANKING_STATUS = frozenset({'validated', 'not_comparable', 'invalid'})

# Spec 11: every review reason code.
REVIEW_CODES = frozenset({
    'low_confidence', 'rating_5', 'moment_override', 'override_invalid',
    'rating_rule_mismatch', 'unevaluable', 'motion_uncertain', 'motion_conflict',
    'critical_defect_uncertain', 'evidence_missing', 'evidence_generic', 'score_gap',
    'co_best_tie', 'weak_burst_best', 'burst_winner_invalid', 'burst_rank_invalid',
    'near_duplicate_invalid', 'burst_frame_missing', 'burst_comparison_invalid',
    'burst_ranking_invalid', 'burst_not_comparable', 'documentation_keep_low_rating',
    'distinct_moment_highlight', 'distinct_moment_invalid', 'field_invalid',
})

# --- Decision table as data (spec 6.1, 6.2) ----------------------------------------------
# Base rating for every (T, E) cell. Computing a result's rating is a later step (S3).

BASE_MATRIX = {
    (1, 1): 1, (1, 2): 1, (1, 3): 1, (1, 4): 1, (1, 5): 1,
    (2, 1): 1, (2, 2): 2, (2, 3): 2, (2, 4): 2, (2, 5): 2,
    (3, 1): 1, (3, 2): 2, (3, 3): 3, (3, 4): 3, (3, 5): 3,
    (4, 1): 1, (4, 2): 2, (4, 3): 3, (4, 4): 4, (4, 5): 5,
    (5, 1): 1, (5, 2): 2, (5, 3): 3, (5, 4): 4, (5, 5): 5,
}

# Spec 6.3: the only three cells where a valid override applies, and the capped rating.
OVERRIDE_CELLS = {(2, 4): 3, (2, 5): 3, (3, 5): 4}

# --- Field inventory (spec 14.1, 12.2) ---------------------------------------------------
# Field name -> source: 'M' model, 'C' code, 'H' human (approve command).
# Fields marked "M, validated by C" in the spec are recorded as 'M'.
# Names and sources only; shapes are validated in later steps (decision A-3).

RESULT_FIELDS = {
    'path': 'C',
    'original_path': 'C',
    'preview_path': 'C',
    'session_id': 'C',
    'capture_time': 'C',
    'original_sha256': 'C',
    'evaluable': 'M',
    'unevaluable_reason': 'M',
    'capture_type': 'M',
    'technical_score': 'M',
    'editorial_score': 'M',
    'technical': 'M',
    'editorial': 'M',
    'decisive_evidence': 'M',
    'proposed_rating': 'M',
    'proposed_moment_override': 'M',
    'moment_override': 'C',
    'rating': 'C',
    'decision': 'C',
    'flags': 'M',
    'fatal_flaws': 'M',
    'uncertain_checks': 'M',
    'distinct_moment': 'M',
    'distinct_moment_evidence': 'M',
    'burst_group': 'C',
    'burst_sequence': 'C',
    'burst_comparison': 'M',
    'burst_rank': 'M',
    'best_in_burst': 'M',
    'co_best': 'M',
    'near_duplicate_of': 'M',
    'rank_reason': 'M',
    'not_comparable_evidence': 'M',
    'burst_ranking_status': 'C',
    'documentation_suggestion': 'C',
    'editorial_suggestion': 'C',
    'confidence': 'M',
    'needs_review': 'C',
    'review_reasons': 'C',
    'approved': 'H',
    'approved_rating': 'H',
    'approved_at': 'H',
    'approved_original_path': 'H',
    'approved_original_sha256': 'H',
    'approved_preview_path': 'H',
    'approved_preview_sha256': 'H',
    'reasons': 'C',
}

# Nested keys of the `technical` and `editorial` objects (spec 14.1).
TECHNICAL_SUBFIELDS = ('motion', 'motion_evidence', 'recoverable_in_raw', 'evidence')
EDITORIAL_SUBFIELDS = ('evidence',)

# Spec 12.2: the seven approval fields.
APPROVAL_FIELDS = (
    'approved', 'approved_rating', 'approved_at', 'approved_original_path',
    'approved_original_sha256', 'approved_preview_path', 'approved_preview_sha256',
)

MODEL_FIELDS = frozenset(k for k, v in RESULT_FIELDS.items() if v == 'M')
CODE_FIELDS = frozenset(k for k, v in RESULT_FIELDS.items() if v == 'C')
HUMAN_FIELDS = frozenset(k for k, v in RESULT_FIELDS.items() if v == 'H')

# --- Evidence rules (spec 9.3, Q6, U11) --------------------------------------------------
# v3.0 held exactly the examples given in spec 9.3 (decision A-2). v3.1 (M-27, S16) extends
# both lists; it is NOT calibrated (calibration is S17 / M-26). Matching is unchanged: whole
# casefolded tokens, no stemming, so singular and plural forms are listed explicitly.
# Every change needs a new EVIDENCE_RULES_VERSION and a new pin.

EVIDENCE_MIN_LENGTH = 20
GENERIC_PHRASES = (
    'great photo', 'nice composition', 'good exposure',
    'nice shot', 'good shot', 'great shot', 'nice photo', 'good photo', 'beautiful photo',
    'great composition', 'good composition', 'well composed', 'good lighting', 'nice lighting',
    'great moment', 'nice moment', 'great image', 'good image',
)
CONCRETE_KEYWORDS = (
    # people and roles
    'speaker', 'speakers', 'presenter', 'moderator', 'panelist', 'panelists', 'host', 'guest', 'guests',
    'audience', 'crowd', 'attendee', 'attendees', 'participant', 'participants', 'person', 'people',
    'man', 'woman', 'men', 'women', 'child', 'children', 'couple', 'bride', 'groom', 'group', 'team',
    'performer', 'performers', 'singer', 'musician', 'official', 'officials',
    # body and expression
    'eyes', 'eye', 'face', 'faces', 'hand', 'hands', 'arm', 'arms', 'mouth', 'smile', 'expression',
    'gesture', 'head',
    # position and parts of the frame
    'left', 'right', 'center', 'centre', 'top', 'bottom', 'foreground', 'background', 'corner', 'edge',
    'horizon', 'sky',
    # venue and objects
    'stage', 'podium', 'lectern', 'microphone', 'screen', 'banner', 'table', 'chair', 'chairs', 'venue',
    'room', 'hall', 'door', 'window', 'logo', 'sign',
)


def evidence_rules_canonical(min_length: int = EVIDENCE_MIN_LENGTH,
                             generic_phrases: tuple[str, ...] | list[str] = GENERIC_PHRASES,
                             concrete_keywords: tuple[str, ...] | list[str] = CONCRETE_KEYWORDS) -> bytes:
    """Canonical bytes of the evidence rules (code-defined; the spec defines none).

    Canonicalization: a JSON object with exactly the keys ``concrete_keywords``,
    ``generic_phrases`` and ``min_length``; both lists sorted; keys sorted; separators
    ``,`` and ``:`` with no whitespace; UTF-8 encoding without ASCII escaping.
    """
    rules = {
        'min_length': min_length,
        'generic_phrases': sorted(generic_phrases),
        'concrete_keywords': sorted(concrete_keywords),
    }
    return json.dumps(rules, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def evidence_rules_digest(min_length: int = EVIDENCE_MIN_LENGTH,
                          generic_phrases: tuple[str, ...] | list[str] = GENERIC_PHRASES,
                          concrete_keywords: tuple[str, ...] | list[str] = CONCRETE_KEYWORDS) -> str:
    """SHA-256 (lowercase hex) of the canonical evidence rules."""
    return hashlib.sha256(evidence_rules_canonical(min_length, generic_phrases, concrete_keywords)).hexdigest()


# Version -> pinned digest. A rules change without a version bump fails the test suite.
EVIDENCE_RULES_PINS = {
    'v3.0': '08d30b381400dfa7850a020bc0b53d2477d9e536c13edff0ee15faecbf49401f',
    'v3.1': 'b66f76715a784e56b9b5df7ae06bdd0ad4519ec35689a201a45b8a5c7fcd0df3',
}

# --- Error entries (spec 14.5, 8.2.1) ----------------------------------------------------

ERROR_STAGES = ('original', 'preview', 'batch', 'result')
UNSENT_STAGES = frozenset({'original', 'preview'})     # recorded before batching
SENT_STAGES = frozenset({'batch', 'result'})

ERROR_ENTRY_FIELDS = (
    'stage', 'error', 'original_path', 'session_id', 'burst_group', 'burst_sequence',
    'capture_time', 'batch_index', 'sent_to_model', 'result', 'preview_path',
)
IDENTITY_FIELDS = ('original_path', 'session_id', 'burst_group', 'sent_to_model')


class ErrorEntryError(ValueError):
    """A requested error entry would break a spec 14.5 rule."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ''


def make_error_entry(*, stage: str, error: str, original_path: str | None,
                     session_id: str | None = None, burst_group: str | None = None,
                     burst_sequence: str | None = None, capture_time: str | None = None,
                     batch_index: int | None = None, sent_to_model: bool,
                     result: Any = None, preview_path: str | None = None) -> dict[str, Any]:
    """Build one spec 14.5 error entry for one original. Raises ErrorEntryError on any
    rule violation; never fills in or corrects a value."""
    if stage not in ERROR_STAGES:
        raise ErrorEntryError(f'unknown stage {stage!r}')
    if not _is_nonempty_str(error):
        raise ErrorEntryError('error must be a non-empty string')
    if not isinstance(sent_to_model, bool):
        raise ErrorEntryError('sent_to_model must be a bool')
    if stage in UNSENT_STAGES:
        if sent_to_model:
            raise ErrorEntryError(f'stage {stage!r} requires sent_to_model = False')
        if batch_index is not None:
            raise ErrorEntryError(f'stage {stage!r} requires batch_index = None')
        if preview_path is not None:
            raise ErrorEntryError(f'stage {stage!r} requires preview_path = None')
        if not _is_nonempty_str(original_path):
            raise ErrorEntryError(f'stage {stage!r} requires an original_path')
        if result is not None:
            raise ErrorEntryError(f'stage {stage!r} requires result = None')
    else:
        if not sent_to_model:
            raise ErrorEntryError(f'stage {stage!r} requires sent_to_model = True')
        if not _is_int(batch_index) or batch_index < 0:
            raise ErrorEntryError(f'stage {stage!r} requires a non-negative integer batch_index')
        if not _is_nonempty_str(session_id):
            raise ErrorEntryError(f'stage {stage!r} requires a session_id')
        if burst_group is not None and not _is_nonempty_str(burst_group):
            raise ErrorEntryError('burst_group must be a non-empty string or None')
        if original_path is None:
            if stage != 'result':
                raise ErrorEntryError('original_path may be None only for stage "result"')
        elif not _is_nonempty_str(original_path):
            raise ErrorEntryError('original_path must be a non-empty string or None')
        if stage == 'batch' and result is not None:
            raise ErrorEntryError('stage "batch" requires result = None')
    return {
        'stage': stage, 'error': error, 'original_path': original_path,
        'session_id': session_id, 'burst_group': burst_group, 'burst_sequence': burst_sequence,
        'capture_time': capture_time, 'batch_index': batch_index, 'sent_to_model': sent_to_model,
        'result': result, 'preview_path': preview_path,
    }


def classify_error_entry(entry: Any) -> dict[str, Any]:
    """Classify a stored error entry without modifying it.

    Returns ``{'file_refusal': [...], 'identity_unknown': [...], 'warnings': [...]}``:
    - file_refusal (V31): not an object, or missing/invalid ``stage`` or ``error``;
    - identity_unknown (V44, 8.2.1): an identity field missing or of the wrong type on an
      entry that is or may be sent, ``sent_to_model`` missing or not a bool, or an entry
      that names no frame;
    - warnings: an invalid ``preview_path`` (treated as no reference, spec 14.5).
    """
    findings: dict[str, list[str]] = {'file_refusal': [], 'identity_unknown': [], 'warnings': []}
    if not isinstance(entry, dict):
        findings['file_refusal'].append('entry is not an object')
        return findings
    if entry.get('stage') not in ERROR_STAGES:
        findings['file_refusal'].append('stage missing or invalid')
    if not _is_nonempty_str(entry.get('error')):
        findings['file_refusal'].append('error missing or invalid')

    sent = entry.get('sent_to_model')
    if not isinstance(sent, bool):
        findings['identity_unknown'].append('sent_to_model missing or not a bool')
    possibly_sent = sent is not False
    if possibly_sent:
        op = entry.get('original_path', ...)
        if op is None:
            findings['identity_unknown'].append('entry names no frame')
        elif op is ... or not _is_nonempty_str(op):
            findings['identity_unknown'].append('original_path missing or of the wrong type')
        if not _is_nonempty_str(entry.get('session_id')):
            findings['identity_unknown'].append('session_id missing or of the wrong type')
        if 'burst_group' not in entry:
            findings['identity_unknown'].append('burst_group missing')
        elif entry['burst_group'] is not None and not _is_nonempty_str(entry['burst_group']):
            findings['identity_unknown'].append('burst_group of the wrong type')

    pp = entry.get('preview_path')
    if pp is not None and not _is_nonempty_str(pp):
        findings['warnings'].append('preview_path invalid; treated as no reference')
    return findings


def identity_scope(entry: dict[str, Any]) -> tuple[str, Any]:
    """Spec 8.2.1 scope for an entry whose identity can't be established: the first
    scope that can be established, in the order batch, session, file."""
    batch_index = entry.get('batch_index')
    if _is_int(batch_index) and batch_index >= 0:
        return ('batch', batch_index)
    session_id = entry.get('session_id')
    if _is_nonempty_str(session_id):
        return ('session', session_id)
    return ('file', None)
