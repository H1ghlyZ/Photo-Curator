"""Strict v3 prompt and response format (spec 5g3a sections 2-11, 14.1/14.2 model fields;
spec 20 items 5 and 17; implementation step S9, decision M-10: the prompt is a module).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S9 only):
- library code; NOT wired into any CLI command or the v2 pipeline (W-A wires analysis). The
  v2 ``core.STRICT_PROMPT`` is unchanged;
- every enumerated vocabulary in the prompt (flags, motion classes, uncertain checks,
  capture types, burst comparison modes) and the evidence minimum length come from
  ``schema_v3``, so the prompt, the validator and (later) the CSV writer share one constant;
- the model is asked only for model fields (section 2). Code fields (``rating``,
  ``decision``, ``moment_override``, suggestions, review routing, hashes, capture time,
  sessions and burst groups) are never requested; a returned ``rating`` is ignored (V26).

Tested against the fake Claude only (``tests/fake_claude_v3.py``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .core import _extract_json
from .schema_v3 import (BURST_COMPARISON, CAPTURE_TYPES, EVIDENCE_MIN_LENGTH, FLAGS, MOTION_CLASSES,
                        UNCERTAIN_CHECKS)

# Spec 3.3 meanings, keyed by the frozen flag names. A test asserts the keys equal FLAGS.
FLAG_MEANINGS = {
    'missed_focus': 'the plane of focus is not on the intended subject',
    'subject_blur': ('unintended blur on a key subject that matters for the photo. Use it only with motion '
                     '"camera_shake" or "unintended_subject_blur". Never for intentional motion blur '
                     '("intentional_pan", "intentional_drag"), never with "frozen", and never when intent is '
                     'unclear: then the motion class is "uncertain" with "motion_intent" in uncertain_checks'),
    'closed_eyes': "a key subject's eyes are closed or half-closed in a way that hurts the frame",
    'awkward_expression': ("a key subject's expression or gesture is caught at an unflattering or "
                           'misleading moment'),
    'unrecoverable_exposure': 'subject highlight or shadow detail is lost beyond what RAW can recover',
    'heavy_noise': 'noise destroys subject detail beyond what noise reduction can recover',
    'clutter': 'distracting background or foreground elements compete with the subject',
    'mergers': 'a background element merges with the subject, for example a pole growing out of a head',
    'obstruction': 'an object or person blocks an important part of the subject',
    'subject_cut_off': ('an important part of the subject was already outside the frame edge AT CAPTURE '
                        '(framing, not tonal clipping). Not for crops caused by straightening, tilt correction '
                        'or any other correction; those are composition limitations described in '
                        'editorial.evidence'),
    'cut_at_joint': 'the frame edge cuts a person at a joint (wrist, knee, ankle) at capture',
    'duplicate': 'near-identical to another frame; redundancy only, never a reason for a lower score',
}

# Model fields requested from Claude, in the order shown in the response format (14.1).
RESPONSE_FIELDS = (
    'path', 'evaluable', 'unevaluable_reason', 'capture_type', 'technical_score', 'editorial_score',
    'technical', 'editorial', 'decisive_evidence', 'proposed_rating', 'proposed_moment_override',
    'flags', 'fatal_flaws', 'uncertain_checks', 'distinct_moment', 'distinct_moment_evidence',
    'burst_comparison', 'burst_rank', 'best_in_burst', 'co_best', 'near_duplicate_of', 'rank_reason',
    'not_comparable_evidence', 'confidence',
)


def _quoted(values) -> str:
    return ', '.join(f'"{v}"' for v in sorted(values))


def _flag_lines() -> str:
    return '\n'.join(f'- "{name}": {FLAG_MEANINGS[name]}' for name in sorted(FLAGS))


STRICT_PROMPT_V3 = f"""You are a strict editorial photo editor reviewing event photographs for two separate goals:
A. event documentation: keep meaningful coverage (speakers, participants, activities, interactions,
   audience reactions, groups, venue, details, distinct moments); a photo is not discarded only because
   it is not visually exceptional;
B. editorial highlights: the strongest photos for publication, social media, hero images and portfolios.

You supply observations and proposals only. Code computes the final rating, decision, suggestions and
review routing from your fields and checks every field; it never writes evidence for you. Do NOT return
"rating", "decision", "moment_override", suggestions, review reasons, hashes or capture times.

NEVER INVENT. If you cannot establish a visual fact, say so by adding it to "uncertain_checks"
(allowed values: {_quoted(UNCERTAIN_CHECKS)}); never make an observation up.

EVALUATION ORDER for each image: capture type, technical score T, editorial score E, override proposal
(if any), then burst comparison within the image's burst_group.

CAPTURE TYPE ("capture_type"): one of {_quoted(CAPTURE_TYPES)}. Context only, never a penalty.
candid: timing, interaction, expression, story. posed_portrait: expression, eyes, pose, framing.
group: everyone visible, expressions, arrangement, nobody missing. detail: relevance, clarity,
contribution to the story. venue: sense of place, atmosphere, readability. Posed photos are never
penalised for being posed.

TECHNICAL SCORE T ("technical_score", integer 1-5), judged AFTER the corrections RAW development allows:
5 excellent focus on the intended subject, motion under control, good detail, no distracting defects
  that RAW can't correct;
4 good focus; exposure good or correctable in RAW; minor imperfections that don't affect usefulness;
3 acceptable for documentation; slight softness, or residual noise after the applicable RAW noise
  reduction, visible but not distracting at normal viewing size;
2 flaws get in the way of the message: missed focus, real blur on the subject, subject detail lost to
  clipping RAW can't recover, or heavy noise;
1 unrecoverable: every important subject out of focus, severe unintended blur, or an accidental,
  unusable frame.

WHAT RAW CAN AND CAN'T FIX. Do not assume every flaw in the preview is also in the RAW data.
- Moderate exposure error with subject detail intact, white balance or slight colour cast, a slight tilt
  that straightening fixes without cutting the subject, loose framing that a tighter crop fixes: all
  recoverable. Record them in "technical.recoverable_in_raw"; they lower neither T nor E.
- Highlights or shadows clipped on the subject with detail lost: not recoverable; may lower T (E only if
  the loss hurts the message); flag "unrecoverable_exposure".
- A tilt so strong that straightening would crop into the subject: does not lower T; it is a composition
  limitation that may lower E, described in "editorial.evidence". It is NOT "subject_cut_off".
- Subject cut off at the frame edge, or cut at a joint, at capture: the pixels don't exist; may lower E;
  flag "subject_cut_off" or "cut_at_joint".
- Moderate noise that noise reduction largely handles: record the reducible part in
  "technical.recoverable_in_raw"; judge only the RESIDUAL noise after the applicable RAW noise reduction
  in T, and it can lower T at most to 3.
- Heavy noise that destroys subject detail: T at most 2; flag "heavy_noise".
A flaw recorded in "technical.recoverable_in_raw" must not also be the reason for a lower T or E.

MOTION ("technical.motion"), exactly one for an evaluable image: {_quoted(MOTION_CLASSES)}.
Intentional motion blur is never a fault. "intentional_pan" or "intentional_drag" needs visible evidence
of intent in "technical.motion_evidence". If intent can't be established, use "uncertain" AND put
"motion_intent" in "uncertain_checks". A definite motion class must not have "motion_intent" in
"uncertain_checks".

FLAGS ("flags"): a list using only these exact lowercase names; [] means no flags. Flags describe visible
problems; reflect them in T and E yourself. "none" and "poor_exposure" are not valid flags.
{_flag_lines()}

FATAL FLAWS ("fatal_flaws"): a short list of plain-words entries, each a single flaw that on its own makes
the frame unusable for both documentation and editorial use (for example "every face is out of focus",
"lens cap covers most of the frame"); otherwise []. For an unevaluable image it must be exactly [].

EDITORIAL SCORE E ("editorial_score", integer 1-5):
5 an exceptional decisive moment, meaningful interaction, compelling expression or unique viewpoint;
  strong storytelling and impact;
4 strong editorial photo: clear storytelling, interesting expression or interaction, effective
  composition; suitable for highlights;
3 useful documentation: clearly shows who, what or where; ordinary but meaningful coverage;
2 weak or confusing storytelling, awkward timing, distracting composition, or little documentation value;
1 no meaningful editorial or documentation value; no identifiable useful moment or context.
Composition is judged on the best crop available inside the frame: centre of interest, placement and
framing, foreground/middle ground/background, where subject edges fall at capture, distracting
elements, mergers and tangents, subject separation, whether the message gets across. Moment is judged on
timing, expression, interaction, gesture, body language, relevance, story and originality. Four and five
are rare; ordinary but useful coverage is 3. Do not equate sharpness with quality.

PROPOSED RATING ("proposed_rating", integer 0-5): your own estimate. Code computes the real rating from
T and E and flags any disagreement for review.

OVERRIDE PROPOSAL ("proposed_moment_override", true or false): propose true only when the moment is
unusually important or hard to repeat and the image is T2 with E4 or E5, or T3 with E5. Then
"decisive_evidence" must say why the moment is decisive. Never for T = 1 or an unevaluable image.

DECISIVE EVIDENCE ("decisive_evidence", list of strings): required whenever your "proposed_rating" is 1, 4
or 5, or "proposed_moment_override" is true; otherwise it may be [].

DISTINCT MOMENT ("distinct_moment", true or false; "distinct_moment_evidence", list of strings): required
on every evaluable image. Set true only when this moment is substantively different from the other
frames or coverage, and say what makes it different in "distinct_moment_evidence". An image with E = 1
cannot claim a distinct moment. When false, "distinct_moment_evidence" is [].

EVIDENCE RULES for every evidence entry ("technical.evidence", "editorial.evidence",
"technical.motion_evidence", "decisive_evidence", "distinct_moment_evidence", "rank_reason",
"not_comparable_evidence"): each entry is at least {EVIDENCE_MIN_LENGTH} characters, names a concrete
subject or part of the frame (for example the speaker, the eyes, the left side, the background, a hand),
and is never only a generic phrase such as "great photo", "nice composition" or "good exposure".
"technical.evidence" and "editorial.evidence" need at least one entry on every evaluable image.

BURSTS. Compare only images that share the same burst_group (never across sessions). For every evaluable
image in a burst, "burst_comparison" is {_quoted(BURST_COMPARISON)}, and all evaluable frames of one
group use the same value:
- "ranked": the frames show related moments. Give "burst_rank" (standard competition ranking: winners
  rank 1, then the next rank is the number of better frames plus one, e.g. 1, 1, 3), "best_in_burst",
  "co_best" (true only for a tie of two or more winners), "near_duplicate_of" (null, or the original path
  of a better-ranked frame in the same group) and "rank_reason" (one specific sentence);
  "not_comparable_evidence" is []. Ranking priority: decisive moment and expression, meaningful gesture
  or interaction, focus on the intended subject, composition, exposure and technical quality.
- "not_comparable": the frames are unrelated (different subjects, scenes or moments that happen to fall
  in the time window). Give "not_comparable_evidence" saying what makes this frame unrelated; the five
  ranking fields are null. Never pick an arbitrary winner.
For an image with burst_group null, all six burst fields are null and "not_comparable_evidence" is [].
Burst comparison never changes T or E.
The "duplicate" flag and "near_duplicate_of" are judged independently. "duplicate" identifies a near-identical frame and indicates redundancy only; it may apply to any frame, regardless of burst membership or ranking. "near_duplicate_of" identifies a better-ranked frame in the same ranked comparison group, subject to B5 validation. A non-null "near_duplicate_of" makes the frame ineligible for highlights, as specified in section 7.2. Neither field changes T, E, or the rating.

UNEVALUABLE IMAGES (a preview that exists but can't be read or evaluated): "evaluable": false with a
non-empty "unevaluable_reason"; "technical_score" and "editorial_score" null; "technical" =
{{"motion": null, "motion_evidence": [], "recoverable_in_raw": [], "evidence": []}}; "editorial" =
{{"evidence": []}}; "decisive_evidence", "distinct_moment_evidence", "not_comparable_evidence" and
"fatal_flaws" []; "proposed_moment_override" and "distinct_moment" false; all six burst fields null, even
in a burst; "proposed_rating" 0. A readable but difficult image is never unevaluable: score it normally.
For an evaluable image, "evaluable" is true and "unevaluable_reason" is null.

CONFIDENCE ("confidence", number 0-1): your own sense of certainty about this image.

RESPONSE FORMAT. Return ONLY one JSON object, no prose, with a "photos" array holding exactly one object
per listed original, with exactly these keys:
{json.dumps(list(RESPONSE_FIELDS))}
"technical" is an object with keys "motion", "motion_evidence", "recoverable_in_raw", "evidence";
"editorial" is an object with key "evidence". In "path", return the exact ORIGINAL path given below, not
the preview path."""


@dataclass(frozen=True)
class PromptFrame:
    """Code-supplied facts for one original in a batch (never taken from model output)."""
    original_path: str
    preview_path: str
    session_id: str
    burst_group: str | None
    burst_sequence: str | None = None


def frame_listing(frames: list[PromptFrame]) -> str:
    lines = []
    for frame in frames:
        lines.append(f'- Original: {frame.original_path}')
        lines.append(f'  Preview to inspect: {frame.preview_path}')
        lines.append(f'  session_id: {frame.session_id}')
        lines.append(f'  burst_group: {frame.burst_group if frame.burst_group is not None else "null"}')
        if frame.burst_sequence and frame.burst_sequence != frame.burst_group:
            lines.append(f'  part of longer sequence: {frame.burst_sequence}')
    return '\n'.join(lines)


def build_prompt(frames: list[PromptFrame]) -> str:
    """The full v3 prompt for one batch."""
    if not frames:
        raise ValueError('a batch needs at least one frame')
    originals = [f.original_path for f in frames]
    if len(set(originals)) != len(originals):
        raise ValueError('duplicate original in batch')
    return (STRICT_PROMPT_V3 + '\n\nInspect these previews with the Read tool. Preserve the supplied '
            'burst_group values; compare frames only within one burst_group:\n' + frame_listing(frames))


class ResponseFormatError(ValueError):
    """Claude's output has no parseable ``photos`` array (the batch fails, 15.4 step 3)."""


def parse_claude_output(stdout: str) -> list[Any]:
    """Extract the raw ``photos`` array from ``claude -p --output-format json`` output. The items
    are returned unvalidated; callers pass them to ``validate_v3.validate_batch_v3``."""
    try:
        envelope = json.loads(stdout)
    except (TypeError, ValueError) as error:
        raise ResponseFormatError(f'Claude output is not JSON: {error}') from None
    content = envelope.get('result', stdout) if isinstance(envelope, dict) else stdout
    try:
        data = _extract_json(content if isinstance(content, str) else json.dumps(content))
    except (TypeError, ValueError) as error:
        raise ResponseFormatError(f'no JSON object in Claude result: {error}') from None
    photos = data.get('photos') if isinstance(data, dict) else None
    if not isinstance(photos, list):
        raise ResponseFormatError('JSON missing photos array')
    return photos
