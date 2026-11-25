"""Pure v3 burst comparison (spec 5g3a sections 8.0-8.3, 8.2.1, V21-V24, V28, V29, V-B0,
V-C2; implementation step S4).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S4 only):
- pure functions, no file I/O, NOT wired into the CLI or the v2 pipeline (D4-8); v2
  planning (``detect_bursts``, ``split_bursts``, ``plan_batches``) is unchanged and v2 still
  accepts ``--max-burst-size 0``;
- burst comparison never changes ``rating``, ``decision``, T, E, ``moment_override`` or any
  distinct-moment field produced by S3;
- suggestions, highlight eligibility, ``distinct_moment_highlight`` (D4-6),
  ``retain_low_rating`` and ``needs_review`` belong to S5. ``GroupOutcome`` is the hand-off.

Preconditions P1-P4 (``check_burst_preconditions_v3``):
1. They verify only the CONSISTENCY of the planning data supplied (sessions, groups,
   sequences, batches and sizes against each other and against ``planned_originals``).
2. They do NOT verify that ``detect_bursts`` / ``split_bursts`` grouped frames correctly.
3. A key missing from ONE of the group/sequence maps is detected: ``burst_group`` and
   ``burst_sequence`` no longer agree (P4a).
4. A key missing from BOTH maps is NOT detectable: the frame looks exactly like a non-burst
   frame (both ``None``). Telling them apart needs an independent source of burst
   membership, which S4 does not have.
5. P1-P4 therefore never claim to find every burst member that planning missed.

B0 and error-entry access contract (D3-11, D4-7):
- every entry is classified with ``classify_error_entry`` BEFORE ``burst_group`` is read;
- an entry with ``file_refusal`` findings raises ``ValueError``;
- for an entry whose identity is unknown, ``burst_group`` is never read; the invalidation
  scope comes from ``identity_scope`` (batch, then session, then file);
- for an entry whose identity is known: ``sent_to_model = true`` invalidates the named group
  (``burst_group = null`` then means "not in a burst"); ``sent_to_model = false`` never does;
- entries are read only by key access; they are never copied or iterated.

Group membership: members come from ``contexts`` (every frame sent to the model), so
unevaluable frames and frames in ``errors`` stay members. Ranked frames are the evaluable
``Accepted`` members only; unevaluable members keep their S3 burst fields unchanged, get
``burst_ranking_status = null``, are never counted in N and are never a near-duplicate target.

Decisions applied:
- D4-1 / D4-1a: every ranked frame falls into exactly one category by its own
  ``burst_comparison``: R (exactly ``ranked``), NC (exactly ``not_comparable``) or X (absent
  or invalid). B3-B6 and V29 run on the R subset only, C2 on the NC subset only; a frame is
  never given a mode it did not declare. X frames run no mode rule: their ranking fields and
  ``not_comparable_evidence`` are kept as returned (K), an absent key stored as ``null``, with
  ``burst_comparison_invalid`` only (APPROVED DEVIATION, next spec revision).
- D4-2: the B1/B2 patterns include ``burst_rank = 1``; anything else is B3.
- D4-3: ``co_best = true`` without ``best_in_burst = true`` is an invalid winner pattern (B3).
- D4-4 (APPROVED DEVIATION, next spec revision): in C2 an absent ranking field is not
  ``null``, so C2 fails; it is stored as ``null`` with ``burst_comparison_invalid``. In the
  ranked mode a missing or non-list ``not_comparable_evidence`` is stored as ``null`` with
  ``field_invalid``.
- D4-4b (contract for S11 / V39, not implemented here): after normalisation the stored data
  cannot show that a ranking field was absent, so a re-run may compute ``not_comparable``
  where ``invalid`` is stored. S11 must treat the effective status monotonically (invalid if
  either the stored status or the re-run is invalid; any other difference is invalid), must
  never upgrade a stored ``invalid``, and must label such a mismatch neutrally (cause not
  determinable from stored data). No sentinel or schema field is added.
- D4-5: ``weak_burst_best`` only in a ``validated`` group, when the highest rating among its
  winners is <= 2.
- D4-7: a ``file_refusal`` error entry raises ``ValueError``.
- D4-9: frames are processed in original-path order.
"""
from __future__ import annotations

import copy
import dataclasses
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .schema_v3 import (BURST_COMPARISON, MAX_BURST_SIZE_RANGE, REVIEW_CODES,
                        classify_error_entry, identity_scope)
from .validate_v3 import ABSENT, Accepted, BURST_MODEL_FIELDS, FrameContext, is_specific

RANKING_FIELDS = ('burst_rank', 'best_in_burst', 'co_best', 'near_duplicate_of', 'rank_reason')
STATUS_FIELD = 'burst_ranking_status'
_PROTECTED_FIELDS = frozenset({
    'rating', 'decision', 'moment_override', 'technical_score', 'editorial_score',
    'distinct_moment', 'distinct_moment_evidence', 'evaluable',
})


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ''


def _is_abs(value: Any) -> bool:
    return _is_nonempty_str(value) and os.path.isabs(value)


def _stored(value: Any) -> Any:
    """An absent key cannot be stored as absent; it is stored as null."""
    return None if value is ABSENT else copy.deepcopy(value)


# --- Preconditions P1-P4 (spec 8.0) ------------------------------------------------------

class BurstPreconditionError(ValueError):
    """Planning data breaks P1-P4; ``violations`` lists every problem found."""

    def __init__(self, violations: Sequence[str]):
        self.violations = tuple(violations)
        super().__init__('; '.join(self.violations))


@dataclass(frozen=True)
class PlannedFrame:
    """One planned original, burst member or not. ``None`` group and sequence = not in a burst."""
    original_path: str
    session_id: str
    burst_group: str | None
    burst_sequence: str | None


def validate_max_burst_size_v3(value: Any) -> int:
    """P3: an integer (bool rejected) from 2 to 12."""
    low, high = MAX_BURST_SIZE_RANGE
    if not _is_int(value) or not low <= value <= high:
        raise BurstPreconditionError([f'P3: max_burst_size must be an integer from {low} to {high}, got {value!r}'])
    return value


def check_burst_preconditions_v3(planned_originals: Sequence[str], frames: Sequence[PlannedFrame],
                                 batches: Sequence[Sequence[str]], max_burst_size: Any) -> None:
    """Check P1-P4 on planning data before previews or model calls (see module docstring for
    what this can and cannot detect). Raises BurstPreconditionError listing every violation."""
    violations: list[str] = []
    low, high = MAX_BURST_SIZE_RANGE
    size_ok = _is_int(max_burst_size) and low <= max_burst_size <= high
    if not size_ok:
        violations.append(f'P3: max_burst_size must be an integer from {low} to {high}, got {max_burst_size!r}')

    planned: set[str] = set()
    if not isinstance(planned_originals, (list, tuple)):
        violations.append('planned_originals must be a list')
    else:
        for path in planned_originals:
            if not _is_abs(path):
                violations.append(f'planned original is not an absolute path: {path!r}')
            elif path in planned:
                violations.append(f'duplicate planned original: {path}')
            else:
                planned.add(path)

    rows: dict[str, PlannedFrame] = {}
    if not isinstance(frames, (list, tuple)):
        violations.append('frames must be a list')
        frames = []
    for frame in frames:
        if not isinstance(frame, PlannedFrame):
            violations.append(f'frame is not a PlannedFrame: {frame!r}')
            continue
        path = frame.original_path
        if not _is_abs(path):
            violations.append(f'frame original_path is not an absolute path: {path!r}')
            continue
        if path in rows:
            violations.append(f'duplicate planning row for {path}')
            continue
        rows[path] = frame
        if not _is_nonempty_str(frame.session_id):
            violations.append(f'invalid session_id for {path}: {frame.session_id!r}')
        for name in ('burst_group', 'burst_sequence'):
            value = getattr(frame, name)
            if value is not None and not _is_nonempty_str(value):
                violations.append(f'invalid {name} for {path}: {value!r}')
    for path in sorted(planned - set(rows)):
        violations.append(f'planned original has no planning row: {path}')
    for path in sorted(set(rows) - planned):
        violations.append(f'planning row for an original that is not planned: {path}')

    groups: dict[str, list[PlannedFrame]] = {}
    sequences: dict[str, list[PlannedFrame]] = {}
    for path in sorted(rows):
        frame = rows[path]
        group_ok = _is_nonempty_str(frame.burst_group)
        sequence_ok = _is_nonempty_str(frame.burst_sequence)
        if (frame.burst_group is None) != (frame.burst_sequence is None):
            violations.append(f'P4: burst_group and burst_sequence disagree for {path} '
                              f'({frame.burst_group!r}, {frame.burst_sequence!r})')
        if group_ok:
            groups.setdefault(frame.burst_group, []).append(frame)
        if sequence_ok:
            sequences.setdefault(frame.burst_sequence, []).append(frame)
    for group in sorted(groups):
        members = groups[group]
        seqs = {m.burst_sequence for m in members}
        if len(seqs) > 1:
            violations.append(f'P4: group {group} spans several sequences: {sorted(map(repr, seqs))}')
        sessions = {m.session_id for m in members}
        if len(sessions) > 1:
            violations.append(f'P1: group {group} spans several sessions: {sorted(map(repr, sessions))}')
        if size_ok and len(members) > max_burst_size:
            violations.append(f'P3: group {group} has {len(members)} frames, more than {max_burst_size}')
    for sequence in sorted(sequences):
        sessions = {m.session_id for m in sequences[sequence]}
        if len(sessions) > 1:
            violations.append(f'P1: sequence {sequence} spans several sessions: {sorted(map(repr, sessions))}')

    position: dict[str, int] = {}
    if not isinstance(batches, (list, tuple)):
        violations.append('batches must be a list')
        batches = []
    for index, batch in enumerate(batches):
        if not isinstance(batch, (list, tuple)) or not batch:
            violations.append(f'batch {index} is empty or not a list')
            continue
        for path in batch:
            if not isinstance(path, str) or path not in planned:
                violations.append(f'batch {index} contains an original that is not planned: {path!r}')
            elif path in position:
                violations.append(f'P2: {path} is scheduled more than once (batches {position[path]} and {index})')
            else:
                position[path] = index
    for path in sorted(planned - set(position)):
        violations.append(f'P2: planned original is not scheduled in any batch: {path}')
    for group in sorted(groups):
        indexes = {position[m.original_path] for m in groups[group] if m.original_path in position}
        if len(indexes) > 1:
            violations.append(f'P2: group {group} is split across batches {sorted(indexes)}')

    if violations:
        raise BurstPreconditionError(violations)


# --- B0 (spec 8.2, 8.2.1) ----------------------------------------------------------------

def _group_maps(contexts: Mapping[str, FrameContext]):
    by_batch: dict[int, set[str]] = {}
    by_session: dict[str, set[str]] = {}
    every: set[str] = set()
    for ctx in contexts.values():
        if ctx.burst_group is None:
            continue
        by_batch.setdefault(ctx.batch_index, set()).add(ctx.burst_group)
        by_session.setdefault(ctx.session_id, set()).add(ctx.burst_group)
        every.add(ctx.burst_group)
    return by_batch, by_session, every


def b0_affected_groups(errors: Sequence[Any], contexts: Mapping[str, FrameContext]) -> frozenset[str]:
    """Every comparison group that B0 invalidates, following the access contract above."""
    if not isinstance(errors, (list, tuple)):
        raise ValueError('errors must be a list')
    by_batch, by_session, every = _group_maps(contexts)
    affected: set[str] = set()
    for entry in errors:
        findings = classify_error_entry(entry)          # always first (D3-11)
        if findings['file_refusal']:
            raise ValueError(f'error entry refused: {findings["file_refusal"]}')       # D4-7
        if findings['identity_unknown']:
            kind, value = identity_scope(entry)         # burst_group is never read here
            if kind == 'batch':
                affected |= by_batch.get(value, set())
            elif kind == 'session':
                affected |= by_session.get(value, set())
            else:
                affected |= every
            continue
        if entry['sent_to_model'] is not True:          # known identity: a bool
            continue
        group = entry['burst_group']                    # known identity: read only now
        if group is not None:
            affected.add(group)
    return frozenset(affected)


# --- Group rules (spec 8.2, 8.3) ---------------------------------------------------------

@dataclass(frozen=True)
class GroupOutcome:
    """In-memory hand-off for S5 and tests; never serialized."""
    burst_group: str
    status: str | None
    members: tuple[str, ...]
    ranked: tuple[str, ...]
    unevaluable: tuple[str, ...]
    missing: tuple[str, ...]
    winners: tuple[str, ...]
    categories: dict
    failed_rules: frozenset


class _Group:
    def __init__(self, ranked: list[str], accepted: Mapping[str, Accepted]):
        self.ranked = ranked
        self.raw = {o: accepted[o].burst_raw for o in ranked}
        self.rating = {o: accepted[o].rating for o in ranked}
        self.stored: dict[str, dict[str, Any]] = {o: {} for o in ranked}
        self.codes: dict[str, set[str]] = {o: set() for o in ranked}
        self.failed: set[str] = set()

    def add(self, original: str, *codes: str) -> None:
        for code in codes:
            if code not in REVIEW_CODES:
                raise AssertionError(f'unknown review code {code!r}')
            self.codes[original].add(code)

    def add_all(self, frames, *codes: str) -> None:
        for original in frames:
            self.add(original, *codes)

    # C1 and categories (D4-1)
    def categorise(self) -> dict[str, str]:
        categories = {}
        for o in self.ranked:
            bc = self.raw[o]['burst_comparison']
            if isinstance(bc, str) and bc in BURST_COMPARISON:
                categories[o] = 'R' if bc == 'ranked' else 'NC'
                self.stored[o]['burst_comparison'] = bc
            else:
                categories[o] = 'X'
                self.stored[o]['burst_comparison'] = None                 # W, C1 (a)/(b)
        if 'X' in categories.values() or len(set(categories.values())) > 1:
            self.failed.add('C1')
            self.add_all(self.ranked, 'burst_comparison_invalid')
        return categories

    # D4-1a: no mode rule for frames without a valid declaration
    def keep_undeclared(self, frames: list[str]) -> None:
        for o in frames:
            for key in RANKING_FIELDS + ('not_comparable_evidence',):
                self.stored[o][key] = _stored(self.raw[o][key])

    # C2 on the NC subset
    def check_not_comparable(self, frames: list[str]) -> bool:
        ok = len(frames) >= 2
        for o in frames:
            raw = self.raw[o]
            for key in RANKING_FIELDS:
                value = raw[key]
                self.stored[o][key] = _stored(value)
                if value is not None:                                     # ABSENT is not null (D4-4)
                    ok = False
            nce = raw['not_comparable_evidence']
            if nce is ABSENT or not isinstance(nce, list) or not all(isinstance(e, str) for e in nce):
                self.stored[o]['not_comparable_evidence'] = None
                self.add(o, 'evidence_missing')
                ok = False
            elif not nce:
                self.stored[o]['not_comparable_evidence'] = []
                self.add(o, 'evidence_missing')
                ok = False
            elif not all(is_specific(e) for e in nce):
                self.stored[o]['not_comparable_evidence'] = copy.deepcopy(nce)
                self.add(o, 'evidence_generic')
                ok = False
            else:
                self.stored[o]['not_comparable_evidence'] = copy.deepcopy(nce)
        if not ok:
            self.failed.add('C2')
            self.add_all(frames, 'burst_comparison_invalid')
        return ok

    # B1-B6 and V29 on the R subset
    def check_ranked(self, frames: list[str]) -> tuple[bool, list[str], bool]:
        raw = self.raw
        best = {o: raw[o]['best_in_burst'] for o in frames}
        cob = {o: raw[o]['co_best'] for o in frames}
        rank = {o: raw[o]['burst_rank'] for o in frames}
        winners = [o for o in frames if best[o] is True]

        def rank_one(o):
            return _is_int(rank[o]) and rank[o] == 1

        pattern_ok = (all(isinstance(best[o], bool) and isinstance(cob[o], bool) for o in frames)
                      and not any(cob[o] is True and best[o] is not True for o in frames)       # D4-3
                      and ((len(winners) == 1 and cob[winners[0]] is False and rank_one(winners[0]))  # B1
                           or (len(winners) >= 2 and all(cob[o] is True and rank_one(o) for o in winners))))  # B2
        if pattern_ok:
            for o in frames:
                self.stored[o]['best_in_burst'], self.stored[o]['co_best'] = best[o], cob[o]
        else:                                                             # B3
            self.failed.add('B3')
            winners = []
            for o in frames:
                self.stored[o]['best_in_burst'], self.stored[o]['co_best'] = False, False
            self.add_all(frames, 'burst_winner_invalid')
        tie = pattern_ok and len(winners) >= 2

        ranks_ok = pattern_ok and all(_is_int(rank[o]) for o in frames)   # B4
        if ranks_ok:
            k, n = len(winners), len(frames)
            ranks_ok = (all(rank[o] == 1 for o in winners)
                        and sorted(rank[o] for o in frames if o not in winners) == list(range(k + 1, n + 1)))
        if ranks_ok:
            for o in frames:
                self.stored[o]['burst_rank'] = rank[o]
        else:
            self.failed.add('B4')
            for o in frames:
                self.stored[o]['burst_rank'] = None
            self.add_all(frames, 'burst_rank_invalid')

        subset = set(frames)
        for o in frames:                                                  # B5
            link = raw[o]['near_duplicate_of']
            if link is None:
                self.stored[o]['near_duplicate_of'] = None
            elif (ranks_ok and isinstance(link, str) and link in subset and link != o
                  and rank[link] < rank[o]):
                self.stored[o]['near_duplicate_of'] = link
            else:
                self.failed.add('B5')
                self.stored[o]['near_duplicate_of'] = None
                self.add(o, 'near_duplicate_invalid')

        for o in frames:                                                  # B6
            reason = raw[o]['rank_reason']
            if not isinstance(reason, str) or not reason.strip():
                self.failed.add('B6')
                self.stored[o]['rank_reason'] = None
                self.add(o, 'evidence_missing')
            elif not is_specific(reason):
                self.failed.add('B6')
                self.stored[o]['rank_reason'] = reason
                self.add(o, 'evidence_generic')
            else:
                self.stored[o]['rank_reason'] = reason

        for o in frames:                                                  # V29 ranked, D4-4
            nce = raw[o]['not_comparable_evidence']
            if isinstance(nce, list) and not nce:
                self.stored[o]['not_comparable_evidence'] = []
            elif isinstance(nce, list):
                self.stored[o]['not_comparable_evidence'] = copy.deepcopy(nce)
                self.add(o, 'field_invalid')
            else:
                self.stored[o]['not_comparable_evidence'] = None
                self.add(o, 'field_invalid')

        ok = not ({'B3', 'B4', 'B5', 'B6'} & self.failed)
        return ok, winners, tie


def _check_inputs(accepted: Mapping[str, Accepted], contexts: Mapping[str, FrameContext]) -> None:
    if not isinstance(accepted, Mapping) or not isinstance(contexts, Mapping):
        raise ValueError('accepted and contexts must be mappings')
    for original, ctx in contexts.items():
        if not isinstance(ctx, FrameContext) or ctx.original_path != original:
            raise ValueError(f'invalid context for {original!r}')
    for original, acc in accepted.items():
        if not isinstance(acc, Accepted):
            raise ValueError(f'not an Accepted result: {original!r}')
        ctx = contexts.get(original)
        if ctx is None:
            raise ValueError(f'accepted result without a context: {original!r}')
        if acc.fields.get('burst_group') != ctx.burst_group:
            raise ValueError(f'burst_group of {original!r} differs from its context')
        if ctx.burst_group is not None and acc.fields.get('evaluable') is True:
            if not isinstance(acc.burst_raw, dict) or set(acc.burst_raw) != set(BURST_MODEL_FIELDS):
                raise ValueError(f'evaluable burst frame without burst_raw: {original!r}')
    groups: dict[str, set] = {}
    for ctx in contexts.values():
        if ctx.burst_group is not None:
            groups.setdefault(ctx.burst_group, set()).add((ctx.session_id, ctx.batch_index))
    for group, keys in groups.items():
        if len(keys) > 1:
            raise ValueError(f'group {group} spans several sessions or batches in contexts')


def _replace(acc: Accepted, updates: dict, codes: set[str]) -> Accepted:
    if _PROTECTED_FIELDS & set(updates):
        raise AssertionError('burst comparison must not change S3 fields')
    fields = copy.deepcopy(acc.fields)
    fields.update(updates)
    return dataclasses.replace(acc, fields=fields, review_reasons=acc.review_reasons | frozenset(codes))


def apply_bursts_v3(accepted: Mapping[str, Accepted], errors: Sequence[Any],
                    contexts: Mapping[str, FrameContext]) -> tuple[dict[str, Accepted], dict[str, GroupOutcome]]:
    """Apply B0, C1, C2 and B1-B6 to every comparison group and set ``burst_ranking_status``
    (spec 15.4 step 10). Inputs are never modified; new Accepted values are returned."""
    _check_inputs(accepted, contexts)
    affected = b0_affected_groups(errors, contexts)

    members: dict[str, list[str]] = {}
    for original in sorted(contexts):
        group = contexts[original].burst_group
        if group is not None:
            members.setdefault(group, []).append(original)

    result: dict[str, Accepted] = {}
    for original in sorted(accepted):
        acc = accepted[original]
        if contexts[original].burst_group is None or acc.fields.get('evaluable') is not True:
            result[original] = _replace(acc, {STATUS_FIELD: None}, set())   # unevaluable / non-burst

    outcomes: dict[str, GroupOutcome] = {}
    for group in sorted(members):
        everyone = members[group]
        ranked = [o for o in everyone if o in accepted and accepted[o].fields.get('evaluable') is True]
        unevaluable = tuple(o for o in everyone if o in accepted and accepted[o].fields.get('evaluable') is not True)
        missing = tuple(o for o in everyone if o not in accepted)
        if not ranked:
            outcomes[group] = GroupOutcome(group, None, tuple(everyone), (), unevaluable, missing, (), {},
                                           frozenset({'B0'} if group in affected else ()))
            continue

        g = _Group(ranked, accepted)
        if group in affected:
            g.failed.add('B0')
            g.add_all(ranked, 'burst_frame_missing')
        categories = g.categorise()
        r_frames = [o for o in ranked if categories[o] == 'R']
        nc_frames = [o for o in ranked if categories[o] == 'NC']
        x_frames = [o for o in ranked if categories[o] == 'X']
        g.keep_undeclared(x_frames)
        ranked_ok = c2_ok = False
        winners: list[str] = []
        tie = False
        if r_frames:
            ranked_ok, winners, tie = g.check_ranked(r_frames)
        if nc_frames:
            c2_ok = g.check_not_comparable(nc_frames)

        if 'B0' in g.failed or 'C1' in g.failed:
            status = 'invalid'
        elif r_frames:
            status = 'validated' if ranked_ok else 'invalid'
        else:
            status = 'not_comparable' if c2_ok else 'invalid'

        if status == 'invalid':
            g.add_all(ranked, 'burst_ranking_invalid')
        elif status == 'not_comparable':
            g.add_all(ranked, 'burst_not_comparable')
        else:
            if tie:
                g.add_all(winners, 'co_best_tie')
            if winners and max(g.rating[o] for o in winners) <= 2:              # D4-5
                g.add_all(winners, 'weak_burst_best')

        for o in ranked:
            updates = {key: g.stored[o][key] for key in BURST_MODEL_FIELDS}
            updates[STATUS_FIELD] = status
            result[o] = _replace(accepted[o], updates, g.codes[o])
        shown_winners = tuple(o for o in r_frames if g.stored[o].get('best_in_burst') is True)
        outcomes[group] = GroupOutcome(group, status, tuple(everyone), tuple(ranked), unevaluable, missing,
                                       shown_winners, dict(categories), frozenset(g.failed))
    return result, outcomes
