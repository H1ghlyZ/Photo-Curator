"""Assembly of the v3 results document (spec 5g3a sections 14.1, 14.4, 14.5, 15.4 steps 10-15;
implementation step S10, decision M-3: document assembly lives in S10).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S10 only):
- library code; NOT wired into any CLI command or the v2 pipeline (W-A wires analysis);
- consumes the S3 output (``validate_v3``), runs S4 (``bursts_v3.apply_bursts_v3``) and S5
  (``suggestions_v3.apply_suggestions_v3``) unchanged, and adds only what 14.1 leaves to
  assembly: the default approval fields (``approved = false``, the other six ``null``) and
  ``reasons``;
- the document is checked against V30/V31 (``output_v3``) before it is returned.

Decisions (S10):
- D10-1 ``reasons``: the model's evidence text only, in this order: ``technical.evidence``,
  ``technical.motion_evidence``, ``editorial.evidence``, ``decisive_evidence``,
  ``distinct_moment_evidence``. Only string entries of stored lists are copied; nothing is
  generated, summarised or deduplicated. It may be ``[]``.
- D10-2 result key order follows ``schema_v3.RESULT_FIELDS``; results are sorted by original
  path; ``errors`` keep the order given by the caller.
- D10-3 ``created_at`` keeps the v2 format (``%Y-%m-%dT%H:%M:%S%z``, local time) and
  ``application = "preview_only"`` (14.4 "kept exactly as v2 writes them").
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from typing import Any, Mapping, Sequence

from .bursts_v3 import apply_bursts_v3, validate_max_burst_size_v3
from .output_v3 import v30_problems, v31_problems
from .schema_v3 import (APPROVAL_FIELDS, BURST_SCOPE, CONFIDENCE_THRESHOLD, EVIDENCE_RULES_VERSION,
                        RESULT_FIELDS, RUBRIC_VERSION, SCHEMA_VERSION_V3)
from .suggestions_v3 import RunSummary, apply_suggestions_v3
from .validate_v3 import Accepted, FrameContext

RATING_STYLE_V3 = 'strict_v3'
APPLICATION = 'preview_only'
FILE_KEY_ORDER = ('schema_version', 'rubric_version', 'evidence_rules_version', 'run_id', 'created_at',
                  'source_folder', 'application', 'rating_style', 'confidence_threshold', 'max_burst_size',
                  'burst_window_seconds', 'burst_scope', 'distribution', 'calibration_drift_warning',
                  'results', 'errors')
_ASSEMBLY_FIELDS = frozenset(APPROVAL_FIELDS) | {'reasons'}


class DocumentError(ValueError):
    """The inputs can't form a valid v3 document (a contract violation by the caller)."""


def _strings(value: Any) -> list[str]:
    return [entry for entry in value if isinstance(entry, str)] if isinstance(value, list) else []


def reasons_from_model_text(fields: Mapping[str, Any]) -> list[str]:
    """D10-1: the combined model evidence text, never generated."""
    technical = fields.get('technical') if isinstance(fields.get('technical'), dict) else {}
    editorial = fields.get('editorial') if isinstance(fields.get('editorial'), dict) else {}
    return (_strings(technical.get('evidence')) + _strings(technical.get('motion_evidence'))
            + _strings(editorial.get('evidence')) + _strings(fields.get('decisive_evidence'))
            + _strings(fields.get('distinct_moment_evidence')))


def build_result(acc: Accepted) -> dict[str, Any]:
    """One 14.1 result from an S5 output. Raises ``DocumentError`` on a missing field."""
    if not isinstance(acc, Accepted):
        raise DocumentError('not an Accepted result')
    fields = acc.fields
    missing = [key for key in RESULT_FIELDS if key not in _ASSEMBLY_FIELDS and key not in fields]
    if missing:
        raise DocumentError(f'{fields.get("original_path")!r}: missing fields {missing} (S3-S5 not complete)')
    if _ASSEMBLY_FIELDS & set(fields):
        raise DocumentError(f'{fields.get("original_path")!r}: approval fields or reasons already present')
    result = {key: copy.deepcopy(fields[key]) for key in RESULT_FIELDS if key not in _ASSEMBLY_FIELDS}
    result.update({'approved': False, 'approved_rating': None, 'approved_at': None,
                   'approved_original_path': None, 'approved_original_sha256': None,
                   'approved_preview_path': None, 'approved_preview_sha256': None,
                   'reasons': reasons_from_model_text(fields)})
    return {key: result[key] for key in RESULT_FIELDS}


def build_document(results: Mapping[str, Accepted], errors: Sequence[dict], summary: RunSummary, *,
                   source_folder: str, max_burst_size: int, burst_window_seconds: float,
                   run_id: str | None = None, created_at: str | None = None) -> dict[str, Any]:
    """The full v3 document from S5 outputs. Checked against V30/V31 before it is returned."""
    if not isinstance(summary, RunSummary):
        raise DocumentError('summary must be the S5 RunSummary')
    doc = {
        'schema_version': SCHEMA_VERSION_V3,
        'rubric_version': RUBRIC_VERSION,
        'evidence_rules_version': EVIDENCE_RULES_VERSION,
        'run_id': run_id or str(uuid.uuid4()),
        'created_at': created_at or time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'source_folder': str(source_folder),
        'application': APPLICATION,
        'rating_style': RATING_STYLE_V3,
        'confidence_threshold': CONFIDENCE_THRESHOLD,
        'max_burst_size': validate_max_burst_size_v3(max_burst_size),
        'burst_window_seconds': burst_window_seconds,
        'burst_scope': BURST_SCOPE,
        'distribution': copy.deepcopy(summary.distribution),
        'calibration_drift_warning': summary.calibration_drift_warning,
        'results': [build_result(results[original]) for original in sorted(results)],
        'errors': copy.deepcopy(list(errors)),
    }
    problems = v31_problems(doc)
    if problems:
        raise DocumentError('document fails V31: ' + '; '.join(problems))
    return {key: doc[key] for key in FILE_KEY_ORDER}


def assemble_document(accepted: Mapping[str, Accepted], errors: Sequence[dict],
                      contexts: Mapping[str, FrameContext], **file_fields) -> dict[str, Any]:
    """15.4 steps 10-14 then assembly: bursts (S4), suggestions and routing (S5), document."""
    after_bursts, _outcomes = apply_bursts_v3(accepted, errors, contexts)
    after_s5, summary = apply_suggestions_v3(after_bursts, errors)
    return build_document(after_s5, errors, summary, **file_fields)


def document_bytes(doc: Mapping[str, Any]) -> bytes:
    """The exact bytes written as ``ratings-review.json`` (checked again: V30 and V31)."""
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    problems, parsed = v30_problems(data)
    if problems or parsed.get('schema_version') != SCHEMA_VERSION_V3 or v31_problems(parsed):
        raise DocumentError('serialized document fails V30/V31')
    return data
