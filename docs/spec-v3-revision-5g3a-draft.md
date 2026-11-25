# Photo Curator v3: Final Rating and Curation Specification

**Status:** Draft, revision 5g3a (2026-11-15), waiting for the final audit. Not approved, not frozen. Nothing is implemented.
**Source:** revision 5g3, `docs/spec-v3-revision-5g3-draft.md` (SHA-256 `4ed43994d69021d63b5278dfc0bfe8f69d9e00b9496c4679b202915e4cb0ca12`), which is left unchanged. Revision 5g3a changes only the wording of the schema boundary in 12.9 (22.2, amendment 13). Earlier source: revision 5g2, `docs/spec-v3-revision-5g2-draft.md` (SHA-256 `4e714169e75bf933e7c6f33aac10418af847b5cf48595ce1230c87e050d3a23d`), which is left unchanged. Revision 5g3 closes only two gaps in `recover-archive` (12.9): behaviour without a journal, and its schema-dependency boundary (22.2, amendment 12). Earlier source: revision 5g1, `docs/spec-v3-revision-5g1-draft.md` (SHA-256 `ab9ec316114cf8f584d18c751fd375e43c967ccabf81a5d7a6693bba43ee60fa`), which is left unchanged. Revision 5g2 changes only apply check 2 (13.1) and adds `recover-archive` to the v1/v2 compatibility section (14.7), with the cross-references they need (22.2, amendment 11). Earlier sources: revision 5g, `docs/spec-v3-revision-5g-draft.md` (SHA-256 `07fce419042398f91973f0f05b3d48e23ed59e8eec49a5536a13c90243e3f1a2`; revision 5g1 changed apply check 1 and 14.7, amendment 10), revision 5f, `docs/spec-v3-revision-5f-draft.md` (SHA-256 `8537d4373cea17e349307f52bcb16e4898cc2799cd376696d122ff158563e721`), which is left unchanged. Revision 5g fixes only the cross-reference findings N-1 to N-7 of the final audit of revision 5f (22.2, amendment 9). Earlier sources: revision 5e1, `docs/spec-v3-revision-5e1-draft.md` (SHA-256 `168c4da620867d54cf811804b960c080e960d5347728097beb3e1e6ac387552b`; revision 5f fixed the findings of its full-file audit, amendment 8), revision 5e, `docs/spec-v3-revision-5e-draft.md` (SHA-256 `2aeab32d61b14c2f3eb0c8c742452810d1bacb72def29ea1e0404b3439834ee4`; its `fatal_flaws` rules were changed by revision 5e1, amendment 7, A59), revision 5d, `docs/spec-v3-revision-5d-draft.md` (SHA-256 `a6800954fea75fdbfdaedd9acf53a930f0634a0b8b356df8308e8ff6c18a050f`), which is left unchanged. Earlier sources: revision 5c, `docs/spec-v3-revision-5c-draft.md` (SHA-256 `5298173a75b90bb382487ddbc859c23857918fcdc59fa84d5148bbc49047ccae`), revision 5b, `docs/spec-v3-revision-5b-draft.md` (SHA-256 `3bb565a3e4f641bffb46aa4550583b0eec4220532bee2f75962da082b6dde6f8`), revision 5a, `docs/spec-v3-revision-5a-draft.md` (SHA-256 `17132d1f072108c3ad791b9ca9fce6c581a24b1662f070dc8cbf32e1239ed842`), revision 5, `docs/spec-v3-revision-5-draft.md` (SHA-256 `2cd8d89dc4cbd7730c988193a3c37126507bc556c918e83b1f6055d908303619`), and revision 4, `docs/spec-v3-final-draft.md` (SHA-256 `66be1cdd11264ebb2930530b2ed4d0df813ac12291775abff8722d1ee7e42280`). Changes are listed in section 22: revision 5e's in 22.2, amendment 6; revision 5e1's in amendment 7; revision 5f's in amendment 8; revision 5g's in amendment 9; revision 5g1's in amendment 10; revision 5g2's in amendment 11; revision 5g3's in amendment 12; revision 5g3a's in amendment 13.
**Scope of this step:** specification only. No production code changes, no analysis of GTR or the pilot, no XMP writes, and no changes to original images.
**Open questions:** Q1–Q12 are resolved (section 21). Decisions U1–U18 are all resolved; their resolutions are listed in section 22.3.

---

## 1. Purpose

Photo Curator supports two separate goals, and neither can override the other.

- **A. Event documentation:** keep meaningful coverage of the event: key speakers and participants, activities and interactions, audience reactions, groups and formal shots, venue and details, and distinct moments. A photo must not be thrown away just because it isn't visually exceptional.
- **B. Editorial highlights:** pick the strongest photos for publication, social media, hero and cover images, portfolios and editorial storytelling.

**Release principle:** a technically correct culling system that throws away important event moments has failed.

---

## 2. Who decides what

| Role | Responsible for |
|---|---|
| **Claude (model)** | Capture type, technical score T, editorial score E, motion class, flags, evidence, uncertain checks, a proposed override, a proposed rating, burst comparison (a ranking or a not-comparable claim) with reasons, distinct-moment claims with evidence, and confidence |
| **Code (deterministic)** | Capture time, the analysis-time original hash, sessions and burst groups, overall `rating` and `decision`, validation, consistency checks, review routing, documentation and editorial suggestions, burst-rule enforcement, approval validation, and the approval checks at apply time |
| **Human** | Inspection of the actual image and approval of review items, the final selection, coverage decisions, and calibration ratings |

The model's proposals never become final without passing the code's rules. Code checks and restricts what the model supplies; it never writes observations or evidence on the model's behalf.

### Evaluation steps for each image

1. Capture type
2. Technical score T
3. Editorial score E
4. Override proposal, if any
5. Burst comparison within the image's group
6. Code validates the fields and their consistency (fail closed, section 15)
7. Code computes the base rating, validates any override, and sets the final rating and decision
8. Code re-checks evidence against the final rating
9. Code validates each burst group as a whole, including missing frames
10. Code validates distinct-moment claims and works out the suggestions
11. Code routes the result to human review if needed

Section 15.4 gives the same order in full and is the reference for implementation.

---

## 3. Technical score T (1–5)

T measures what the camera captured, regardless of how editorially important the photo is. T is judged **after** the corrections that RAW development can make (section 3.1): "good exposure" means good once a moderate RAW correction is applied.

| T | Meaning |
|---|---|
| 5 | Excellent focus on the intended subject, motion under control, good detail, no distracting technical defects that RAW can't correct. |
| 4 | Good focus; exposure good or correctable in RAW; minor imperfections that don't affect usefulness. |
| 3 | Acceptable for documentation; slight softness, or residual noise after the applicable RAW noise reduction, is visible but not distracting at normal viewing size. |
| 2 | Flaws get in the way of the message: missed focus, real blur on the subject, subject detail lost to clipping that RAW can't recover, or heavy noise. |
| 1 | Unrecoverable: every important subject out of focus, severe unintended blur, or an accidental, unusable frame. |

### 3.1 What RAW can and can't fix

| Flaw | Recoverable? | May lower T? | May lower E? | Recorded as |
|---|---|---|---|---|
| Exposure off by a moderate amount, subject detail intact | yes | no | no | `recoverable_in_raw` |
| Highlights or shadows clipped on the subject, detail lost | no | yes | only if the loss hurts the message | flag `unrecoverable_exposure` |
| White balance, slight colour cast | yes | no | no | `recoverable_in_raw` |
| Slight tilt that straightening fixes without cutting the subject | yes | no | no | `recoverable_in_raw` |
| Tilt so strong that straightening would crop into the subject | no (the correction costs frame area) | no | yes, as a **composition limitation** | `editorial.evidence` describes the limitation. **Not** `subject_cut_off`, because the subject wasn't cut by the frame edge at capture |
| Loose framing that a tighter crop fixes | yes | no | no: E is judged on the best crop available inside the frame | `recoverable_in_raw` |
| Subject cut off at the frame edge, or cut at a joint, at capture | no (the pixels don't exist) | no | yes | flag `subject_cut_off` or `cut_at_joint` |
| Moderate noise that noise reduction largely handles | partly | only through the **residual** noise left after the applicable RAW noise reduction, and at most to T3 | no | the reducible part in `recoverable_in_raw`; the residual is judged in T |
| Heavy noise that destroys subject detail | no | yes (T ≤ 2) | only if it hurts the message | flag `heavy_noise` |

The model must not assume every flaw in the preview is also in the RAW data. The part of a flaw that is recorded in `recoverable_in_raw` must not also be the reason for a lower T or E. For a flaw that is only partly recoverable (noise), T is judged on what remains **after** the applicable RAW correction, because T is always judged after the corrections RAW development allows (section 3).

### 3.2 Motion class (required for evaluable images)

The model gives each evaluable image exactly one motion class: `frozen`, `intentional_pan`, `intentional_drag`, `camera_shake`, `unintended_subject_blur` or `uncertain`. For an unevaluable image the motion class is `null` (section 6.4).

- Intentional motion blur is never penalised automatically.
- Either `intentional_*` class needs visible evidence of intent in `motion_evidence`.
- If intent can't be established, the class is `uncertain` and `motion_intent` must be in `uncertain_checks`. The image goes to review.
- The motion class must agree with `uncertain_checks` and with the flag `subject_blur` (section 10.1). Code checks both and never corrects any of these fields.

### 3.3 Flags (descriptive only)

Flags describe visible problems. They never change T, E or the rating by themselves; the model reflects them in T and E. An empty list means no flags. Any other value is invalid (V15); in particular, v2's `none` and `poor_exposure` are not v3 flags.

**Frozen enumeration (decision U7).** The twelve flags below are the complete and final v3 vocabulary, spelled exactly as shown: lowercase, underscores, no aliases. Any change to the list or to a flag's meaning requires a new `rubric_version`. Everywhere else in this specification (3.1, 10.1, 14.1, 14.6, V15, V17, section 20) refers to this table and uses the same spelling and meaning. Revision 5e clarified the meanings of `subject_blur` and `subject_cut_off` to match their original intent; the list itself is unchanged.

| Flag | Meaning |
|---|---|
| `missed_focus` | the plane of focus is not on the intended subject |
| `subject_blur` | unintended blur on a key subject that matters for the photo. Used only with motion `camera_shake` or `unintended_subject_blur`. Never for intentional motion blur (`intentional_pan`, `intentional_drag`), never with `frozen`, and never when intent is unclear: then the motion class is `uncertain` with `motion_intent` in `uncertain_checks`, and the image goes to review instead of being marked as a technical fault (section 10.1) |
| `closed_eyes` | a key subject's eyes are closed or half-closed in a way that hurts the frame |
| `awkward_expression` | a key subject's expression or gesture is caught at an unflattering or misleading moment |
| `unrecoverable_exposure` | subject highlight or shadow detail is lost beyond what RAW can recover |
| `heavy_noise` | noise destroys subject detail beyond what noise reduction can recover |
| `clutter` | distracting background or foreground elements compete with the subject |
| `mergers` | a background element merges with the subject, for example a pole growing out of a head |
| `obstruction` | an object or person blocks an important part of the subject |
| `subject_cut_off` | an important part of the subject was already outside the frame edge **at capture** (framing, not tonal clipping). Not for crops caused by straightening, tilt correction or any other correction; those are composition limitations (section 3.1) |
| `cut_at_joint` | the frame edge cuts a person at a joint (wrist, knee, ankle) at capture |
| `duplicate` | near-identical to another frame; redundancy only, never a reason for a lower rating |

**Flags come only from the model.** The code never adds, removes or changes a flag. In particular, it never derives `subject_cut_off` from a crop that a correction would cause. The code checks flags for type (V15) and checks `subject_blur` against the motion class (section 10.1). A contradiction goes to review; it isn't silently accepted or corrected.

### 3.4 Fatal flaws (descriptive only)

`fatal_flaws` is a short, limited, descriptive list. Each entry names, in plain words, a single flaw that on its own makes the frame unusable for both documentation and editorial use: for example "every face is out of focus", "lens cap covers most of the frame" or "frame is almost completely black". It explains a T = 1 or E = 1 score to the human reviewer.

- **Evaluable images only:** filled only when the model sees such a flaw; otherwise `[]`.
- **Unevaluable images:** `fatal_flaws` must be exactly `[]`. A reason why an image couldn't be judged (for example an unreadable or corrupt preview) is **not** a fatal flaw. It is recorded only in `unevaluable_reason` (section 6.4), never in `fatal_flaws`. A non-empty or invalid `fatal_flaws` on an unevaluable result is a contradiction and rejects the result to `errors` (V4).
- **Its content has no effect:** no rule reads the text of the entries. Whatever a valid list contains, and whether it is empty or not, it never changes T, E, `rating`, `decision`, review reasons, documentation or editorial suggestions, approval or apply eligibility, and it isn't checked against T or E.
- **Its validity is checked, and that can have effects:**
  - On an evaluable result, a value that isn't a list of strings is stored as `null` with `field_invalid` (V15). `field_invalid` makes `needs_review = true`, so review routing changes.
  - On an unevaluable result, anything other than `[]` (including `null`, a missing field, or any entry) rejects the whole result to `errors` (V4). The image then has no result: no rating 0 and no `unreadable` decision. If the image belongs to a burst, the rejection is an `errors` entry with `sent_to_model = true`, so B0 makes its comparison group `invalid` (section 8.2), which withholds highlight paths for that group.
  - These effects come only from the field being invalid or breaking the unevaluable rule, never from what a valid list says.
- It isn't evidence, so the specificity check (section 9.3) doesn't apply.
- **Display:** `approve --show` and the gallery may show it as descriptive text.

---

## 4. Editorial score E (1–5)

| E | Meaning |
|---|---|
| 5 | An exceptional decisive moment, meaningful interaction, compelling expression or unique viewpoint; strong storytelling and impact. |
| 4 | Strong editorial photo: clear storytelling, interesting expression or interaction, effective composition; suitable for highlights. |
| 3 | Useful documentation: clearly shows who, what or where; ordinary but meaningful coverage. |
| 2 | Weak or confusing storytelling, awkward timing, distracting composition, or little documentation value. |
| 1 | No meaningful editorial or documentation value; no identifiable useful moment or context. |

**Composition** is judged on the best crop available inside the frame (section 3.1):
- a clear centre of interest
- subject placement and framing
- foreground, middle ground and background
- where subject edges fall at capture (cut-offs a crop can't fix count; loose framing a crop fixes doesn't)
- distracting background elements
- mergers and visual tangents
- separation of subject from background
- whether the frame gets its message across

**Moment** is judged on timing, expression, interaction, gesture and body language, relevance to the event, contribution to the story, and originality.

A photo doesn't need to be dramatic to have documentation value. By definition, an image with E = 1 has no documentation value, so it cannot also claim a distinct moment (section 7.1).

---

## 5. Capture type (context only, never a penalty)

The capture type is one of `candid`, `posed_portrait`, `group`, `detail` or `venue`.

| Type | Judged on |
|---|---|
| candid | timing, interaction, expression, story |
| posed_portrait | expression, eyes, pose, framing |
| group | everyone visible, expressions, arrangement, nobody missing |
| detail | relevance, clarity, contribution to the story |
| venue | sense of place, atmosphere, how clearly it reads |

Posed photos are never penalised for being posed.

---

## 6. Deterministic rating

### 6.1 Base rules (code applies them in this order)

1. The image couldn't be evaluated → **0** (section 6.4)
2. T = 1 or E = 1 → **1**
3. E = 5 and T ≥ 4 → **5**
4. E ≥ 4 and T ≥ 4 → **4**
5. E ≥ 3 and T ≥ 3 → **3**
6. Anything else → **2**

These rules run only on a result that passed field validation (section 15). A result with missing or invalid T or E never reaches this table; it goes to `errors` and stays unrated.

### 6.2 The full matrix (base result, and result with a valid override)

| T \ E | E1 | E2 | E3 | E4 | E5 |
|---|---|---|---|---|---|
| **T1** | 1 | 1 | 1 | 1 | 1 |
| **T2** | 1 | 2 | 2 | 2 → **3**ᵒ | 2 → **3**ᵒ |
| **T3** | 1 | 2 | 3 | 3 | 3 → **4**ᵒ |
| **T4** | 1 | 2 | 3 | 4 | 5 |
| **T5** | 1 | 2 | 3 | 4 | 5 |

ᵒ marks the only three cells where an override is valid.

### 6.3 Override rules

- **Valid cells:** (T2, E4) → 3, (T2, E5) → 3, and (T3, E5) → 4. No other cell.
- **Model's part:** the model proposes `proposed_moment_override = true` and supplies `decisive_evidence` saying why the moment is unusually important or hard to repeat (section 9.2).
- **Valid override:** the cell is valid **and** `decisive_evidence` passes the presence and specificity checks. The code applies the cap, sets `moment_override = true`, and adds review reason `moment_override`.
- **Invalid override:** only when `proposed_moment_override = true` and the cell is not valid, or the evidence is missing or generic. The code ignores the proposal, sets `moment_override = false`, uses the base result, and adds review reason `override_invalid` (plus `evidence_missing` or `evidence_generic` where that was the cause).
- **No proposal, no `override_invalid`:** if `proposed_moment_override` is `false` or `null`, the code never adds `override_invalid`, whatever the evidence. Missing decisive evidence for a final rating of 1, 4 or 5 adds only `evidence_missing` or `evidence_generic` (section 9.2, V12b).
- **Never overridable:** T = 1 and rating 0.
- **No silent overrides:** every applied override is visible in the JSON and gallery and needs individual approval (section 12). An override 4 is not a highlight until that approval is valid (section 7.2).

### 6.4 Rating 0 (unevaluable)

Rating 0 is reserved for previews that exist but can't be read or evaluated. Preview-generation failures are not rating-0 results; they stay in `errors` because there is no preview to show (Q4).

**Exact field values for an unevaluable result.** The model reports `evaluable: false` with a non-empty `unevaluable_reason`. The stored result must then hold exactly these values:

| Field | Value | Source |
|---|---|---|
| `evaluable` | `false` | M |
| `unevaluable_reason` | non-empty string | M |
| `technical_score` | `null` | M |
| `editorial_score` | `null` | M |
| `technical` | object | M |
| `technical.motion` | `null` | M |
| `technical.motion_evidence`, `technical.evidence`, `technical.recoverable_in_raw` | `[]` | M |
| `editorial` | object | M |
| `editorial.evidence`, `decisive_evidence`, `distinct_moment_evidence`, `not_comparable_evidence` | `[]` | M |
| `proposed_moment_override` | `false` | M |
| `distinct_moment` | `false` | M |
| `burst_comparison` | `null`, even when the original belongs to a burst | M |
| `burst_rank` | `null`, even when the original belongs to a burst | M |
| `best_in_burst` | `null`, even when the original belongs to a burst | M |
| `co_best` | `null`, even when the original belongs to a burst | M |
| `near_duplicate_of` | `null`, even when the original belongs to a burst | M |
| `rank_reason` | `null`, even when the original belongs to a burst | M |
| `capture_type` | allowed value or `null` (V7) | M |
| `fatal_flaws` | `[]` exactly. The reason the image couldn't be judged belongs in `unevaluable_reason`, not here (section 3.4) | M |
| `flags`, `uncertain_checks`, `confidence` | validated as for any result (V15, V16, V25); the motion consistency check (section 10.1) does not apply | M |
| `proposed_rating` | `0` (anything else adds `rating_rule_mismatch`, V13) | M |
| `capture_time` | as for any result (section 14.3) | C |
| `original_sha256` | as for any result (section 14.1); an unevaluable result still has an analysis-time hash | C |
| `burst_group`, `burst_sequence` | as set by code; burst membership is kept for display | C |
| `burst_ranking_status` | `null` | C |
| `moment_override` | `false` | C |
| `rating` | `0` | C |
| `decision` | `unreadable` | C |
| `documentation_suggestion` | `unrated` | C |
| `editorial_suggestion` | `not_suggested` | C |
| `needs_review` | `true` | C |
| `review_reasons` | includes `unevaluable` | C |
| `approved` | `false` (permanently; section 12) | H |
| `approved_rating`, `approved_at`, `approved_original_path`, `approved_original_sha256`, `approved_preview_path`, `approved_preview_sha256` | `null` (permanently; section 12) | H |

An unevaluable frame takes no part in burst comparison. Its group is validated over the evaluable frames only (section 8). An unevaluable frame is in `results`, not in `errors`, so it never triggers the missing-frame rule (B0).

**Contradictions fail closed.** If `evaluable` is `false` and any model field other than `capture_type`, `proposed_rating`, `flags`, `uncertain_checks` and `confidence` differs from this table (including a non-empty or invalid `fatal_flaws`), or the reason is missing or empty, the code does **not** overwrite fields to make the result consistent. The whole result goes to `errors` and the image stays unrated (V3, V4). The same applies to `evaluable: true` with missing or invalid T, E or motion (Q7, V6, V8).

**Further rules:**
- A readable but difficult image is never 0. It gets a normal rating and goes to review.
- Rating 0 can never be approved (an approved rating must be 1–5) and is never applied.

### 6.5 Decision (computed by code)

| Rating | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| `decision` | unreadable | reject | weak | usable | strong | exceptional |

### 6.6 Checking the model's own rating

The model returns `proposed_rating`.
- If it differs from the computed rating, the computed rating is used and review reason `rating_rule_mismatch` is added.
- If `proposed_rating` is missing or not a valid integer 0–5 (bool is not an integer), it is stored as `null` and counts as `rating_rule_mismatch` (Q7b). It is never filled in with the computed rating or with any model `rating` field (V26).

---

## 7. Curation suggestions (computed by code; suggestions only)

### 7.1 `documentation_suggestion`

| Value | When |
|---|---|
| `retain` | rating ≥ 3 |
| `retain_low_rating` | rating 1–2 **and** a valid distinct-moment claim (below). Adds review reason `documentation_keep_low_rating` |
| `not_suggested` | rating 1–2 with no valid distinct-moment claim |
| `unrated` | rating 0 |

**Valid distinct-moment claim** means all of these:
- `distinct_moment = true`
- `distinct_moment_evidence` is present and passes the specificity check (section 9)
- `editorial_score ≥ 2`. **An image with E = 1 can never claim documentation retention through `distinct_moment`**, because E = 1 states that it has no documentation value.

A claim that fails any of these is not honoured. The model's stored values are kept unchanged for review, the code adds review reason `distinct_moment_invalid` (plus `evidence_missing` or `evidence_generic` where that was the cause), and the image is treated as having no valid claim for every suggestion. A T = 1 image with E ≥ 2 and a valid claim may be `retain_low_rating`; that is the case documentation retention exists for. Documentation retention never depends on burst status.

### 7.2 `editorial_suggestion`

First, the code decides whether the image **qualifies**:
- **Qualifies as hero:** rating 5 **and** highlight-eligible.
- **Qualifies as highlight:** rating 4 (base or valid override) **and** highlight-eligible.

Then the suggestion depends on the approval state (section 12):

| Value | When |
|---|---|
| `hero_candidate` | qualifies as hero **and** approval state is `approved`. Rating 5 always needs review (Q12), so a hero candidate always has a valid individual approval |
| `highlight` | qualifies as highlight **and** approval state is `not_required` or `approved`. An override 4 (T3/E5) always needs review, so it becomes `highlight` **only after valid individual approval** |
| `pending_approval` | qualifies as hero or highlight, but approval state is `pending`, `invalidated` or `malformed`. Before approval, every override 4 and every rating 5 is here |
| `not_suggested` | everything else |

**Highlight-eligible** means all of these:
- **valid evidence:** the item has no `evidence_missing` or `evidence_generic` review reason, from any evidence field, including `rank_reason` and `not_comparable_evidence`
- **burst position:** exactly one of these paths applies:

  | Path | Condition | Review reason that makes approval necessary |
  |---|---|---|
  | single | the frame is not in a burst | none of its own |
  | winner | group `burst_ranking_status = validated` and the frame is `best_in_burst` (a single winner or a validated co-best) | `co_best_tie` for a tie; otherwise none of its own |
  | distinct moment | group `validated`, the frame is not a winner, and it has a valid distinct-moment claim (section 7.1) | `distinct_moment_highlight` (Q2) |
  | not comparable | group `burst_ranking_status = not_comparable` | `burst_not_comparable` |

  A frame in an `invalid` group has no path. Clearing winner fields under B3 never opens the distinct-moment path, because that path requires a `validated` group.
- **not a near-duplicate:** `near_duplicate_of` is null **and** the frame has no `near_duplicate_invalid` review reason. A frame claimed as a near-duplicate can never be a highlight, even if the claim failed validation.

Approval never restores eligibility that was withheld by validation. An item that lost eligibility through invalid evidence or an invalid burst group can still have its rating approved and applied, but it becomes a highlight only after a new analysis produces valid results.

**When the suggestion is computed:**
- During analysis, `approved` is always `false`, so any qualifying item that needs review is stored as `pending_approval`.
- The approve command (section 12.3) recomputes `editorial_suggestion` for the one approved or revoked item, in the same atomic update, from the stored review reasons and stored burst status.
- The gallery, `review` and the coverage report derive the suggestion for display from the current approval state using these rules (V37). A stored `highlight` or `hero_candidate` on an item whose approval state is not valid is never trusted; it is shown as `pending_approval`.
- **Apply never reads or derives editorial or documentation suggestions.** They play no part in any apply check (13.1) or in what apply writes (V37).

Suggestions never change `rating`.

### 7.3 Coverage report

Coverage is a separate, read-only **coverage report** built only from the results JSON. It never writes to the results file, the source folder or any sidecar, and it **never reads image metadata**: capture times come only from the stored `capture_time` field (section 14.3). An image whose `capture_time` is `null` or invalid (V40) is listed as untimed; it is never given a time from file modification dates or by re-reading the original.

The report never hashes originals. Where it lists individual images, it may show the stored `original_sha256`, always labelled "analysis-time SHA-256, not verified against the current file".

**Preview accounting in the report.** The report shows the manifest's `referenced_preview_count` and `orphan_preview_count`, and its own recount (12.8). It lists the orphan entries by name, labelled "orphan previews: generated output not referenced by any result or error; not deleted". A recount that differs from the manifest is shown as a warning. The report makes no claim about why an orphan exists beyond the matching `stage = original` error, if there is one, and it never deletes or moves anything.

**The first version displays facts only:**
- counts per session: total, evaluable, rating 0, each rating 1–5, and errors. An `errors` entry without a valid `session_id` (14.5 requires it only when `sent_to_model = true`) is still counted, under an explicit row labelled `unknown / no session`; it is never dropped and never assigned to a session by guessing
- counts per capture type, and per capture type × rating
- counts of each documentation and editorial suggestion
- timeline gaps: stretches of at least **10 minutes** within one session with no usable photo (usable = rating ≥ 3), with their start and end capture times (Q5)
- untimed images, listed separately and not placed on the timeline

**Time comparisons.** Gaps are computed within one session on the camera's wall-clock time (the date and time part of `capture_time`). If the images of one session carry different UTC offsets, or a mix of offset and no offset, the report computes **no** gaps for that session, not even partial ones, because any gap could be misleading. It shows the warning "mixed time offsets in session X; timeline gaps not computed" and lists the offsets found (decision U3). Sessions where every timed image has the same offset, or every timed image has no offset, are handled normally.

**It makes no automatic claims.** It does not say that a capture type, a part of the programme or a person is missing or incomplete, and it does not recommend keeping or adding photos. Gaps are worded neutrally, for example "no rating ≥ 3 image between 10:14 and 10:31 in DCIM 2". The human decides what, if anything, is missing.

---

## 8. Burst comparison

### 8.0 Preconditions (code invariants)

These hold before any batch is sent to the model. The code checks them while planning batches; a violation stops the analysis before any Claude call, with an error and no results file written.

| ID | Precondition | Where it comes from today |
|---|---|---|
| P1 | Every comparison group belongs to exactly one session. Bursts are detected separately per session (`burst_scope: "per_session"`) | `detect_bursts` per session |
| P2 | Every comparison group is sent to the model whole, in exactly one batch. A group larger than the batch size becomes its own batch | `plan_batches` treats each group as one unit |
| P3 | Every comparison group has at most `max_burst_size` frames, and for v3 `max_burst_size` is an integer from 2 to 12. `--max-burst-size 0` (unlimited groups) is **rejected** for v3 with an error; splitting can't be turned off | `split_bursts`; today 0 disables splitting |
| P4 | A comparison group is one `burst_group` value: a whole burst, or one part of a longer sequence after splitting. `burst_sequence` names the whole sequence. Frames are compared only within their `burst_group` | `split_bursts` |

Read-time check: V41.

### 8.1 What the model returns

For every evaluable frame in a burst, the model returns `burst_comparison`, either `ranked` or `not_comparable`:

- **`ranked`:** the frames show related moments that can be compared. The frame carries `burst_rank`, `best_in_burst`, `co_best`, `near_duplicate_of` and `rank_reason`; `not_comparable_evidence` is `[]`.
- **`not_comparable`:** the frames are unrelated (different subjects, scenes or moments that happen to fall inside the time window), so no winner is meaningful. The frame carries `not_comparable_evidence` saying what makes it unrelated to the other frames; `burst_rank`, `best_in_burst`, `co_best`, `near_duplicate_of` and `rank_reason` are all `null`. The model must not pick an arbitrary winner.

All evaluable frames in one group must give the same `burst_comparison`. A group is entirely `ranked` or entirely `not_comparable`; a group with mixed modes is `invalid` (C1 (c)). Partly related groups aren't split into sub-groups (decision U1).

`distinct_moment` and `distinct_moment_evidence` are required on **every** evaluable result, in a burst or not (V18, V19).

**Ranking priority** for `ranked` groups:
1. Decisive moment and expression
2. Meaningful gesture or interaction
3. Focus on the intended subject
4. Composition
5. Exposure and technical quality

Absolute ratings are independent of burst comparison in every case: no outcome of this section changes T, E, `rating` or `decision`.

### 8.2 Frame and group rules (per comparison group, after the batch returns)

"Ranked frames" means the evaluable frames in the group that passed V1–V8. Frames in `errors` and unevaluable frames are not ranked frames.

The rules are checked in the order B0, C1, then either C2 (not comparable) or B3, B4, B5, B6 (ranked), each on the values as returned by the model after any clearing by an earlier rule.

| ID | Rule | If broken |
|---|---|---|
| B0 | **No missing frames:** no `errors` entry with `sent_to_model = true` has this `burst_group`, and no `errors` entry whose burst identity can't be established could belong to this group (section 8.2.1) | Group `invalid`; every ranked frame gets `burst_frame_missing`. The other rules are still checked so that all problems are reported |
| C1 | **One comparison mode.** C1 fails if any of these holds: (a) a ranked frame has no `burst_comparison` field; (b) a ranked frame's `burst_comparison` is not exactly the string `ranked` or `not_comparable` (any other string, a different case, `null`, a bool, a number, a list or an object); (c) the ranked frames of the group don't all give the same value, for example one `ranked` and one `not_comparable` | Group `invalid`. A frame with (a) or (b) has `burst_comparison` stored as `null` (W). Every ranked frame gets `burst_comparison_invalid`, and therefore `burst_ranking_invalid` (8.3). With (c), the values are kept as returned (K). B3–B6 and C2 are still checked as far as possible for reporting, but no outcome of theirs can make the group anything other than `invalid` |
| C2 | **Valid not-comparable claim** (when the group's mode is `not_comparable`): the group has at least two ranked frames; on every ranked frame the five ranking fields are `null` and `not_comparable_evidence` is a non-empty list whose entries pass the specificity check (section 9.3) | Group `invalid`; every ranked frame gets `burst_comparison_invalid`, plus `evidence_missing` or `evidence_generic` on frames whose evidence failed. Non-null ranking fields are stored as returned (K) and enable nothing |
| B1 | **Exactly one winner** (ranked mode): one frame has `best_in_burst = true`, `co_best = false` and `burst_rank = 1` | — |
| B2 | **Or a tie** (ranked mode): two or more frames have `best_in_burst = true`, `co_best = true` and `burst_rank = 1` | The tie pattern itself is valid. `co_best_tie` is added to each tied frame **only if the group ends `validated`** |
| B3 | Any other winner pattern (none, several without `co_best`, `co_best` on a single frame), or `best_in_burst` / `co_best` not a bool | Every ranked frame has its winner fields cleared to `best_in_burst = false`, `co_best = false` and gets `burst_winner_invalid` |
| B4 | With N ranked frames and k winners (B1: k = 1; B2: k ≥ 2): every winner has rank 1, and the non-winners hold the ranks k+1 … N exactly once each (standard competition ranking, for example 1, 1, 3, 4). If B3 cleared the winner fields, the ranks can't be checked against a winner and count as broken | Ranks cleared to null, `burst_rank_invalid` on every ranked frame |
| B5 | `near_duplicate_of` is null, or names the original path of a different ranked frame in the **same comparison group** that is **better ranked** (strictly lower `burst_rank`) (Q3). If ranks were cleared under B4, no near-duplicate link can be verified | Field cleared to null, `near_duplicate_invalid`; the frame is **not** highlight-eligible (section 7.2) |
| B6 | Every ranked frame in a `ranked` group has a `rank_reason` that is a non-empty string and passes the specificity check (section 9.3) | **Any** B6 failure on **any** ranked frame makes the **entire group `invalid`**. On the failing frame: missing, not a string, or empty after trimming → stored as `null` (W), `evidence_missing`; generic → kept as returned (K), `evidence_generic`. **Every** ranked frame in the group, including frames whose own `rank_reason` is valid, gets `burst_ranking_invalid` in addition to any frame-specific reason. A valid winner pattern, valid ranks and valid near-duplicate links don't rescue the group |
| B7 | A frame not in any burst has `burst_comparison`, `burst_rank`, `best_in_burst`, `co_best`, `near_duplicate_of` and `rank_reason` all `null`, and `not_comparable_evidence = []` | Any of the six burst fields non-null → stored as `null` (W), `field_invalid`. `not_comparable_evidence` non-empty → kept as returned (K), `field_invalid`. Both send the item to review. No comparison group exists, so no group status is affected (V20, 14.2) |

Clearing a winner, rank or near-duplicate field only withdraws a claim and always records a review reason. It never makes a frame more eligible for anything (section 15.1).

#### 8.2.1 Burst identity of error entries (used by B0)

**Required identity.** Every `errors` entry for a frame that was sent to the model (`stage = batch` or `stage = result`) must preserve four identity fields, copied by the code from batch planning, never from the model's output:
- `original_path`: the absolute original path of a frame in that batch
- `session_id`: that frame's session
- `burst_group`: that frame's comparison group, or `null` only when the frame is in no burst
- `sent_to_model`: `true`

The code also records `batch_index` (section 14.5).

**When identity can't be established**, B0 fails closed. Identity can't be established when an entry's `original_path`, `session_id` or `burst_group` is missing or has the wrong type, or when its `sent_to_model` is missing or not a bool. It also can't be established when the error doesn't name a frame of the batch, for example when the model returned a path outside the batch. Such an entry is treated as possibly belonging to a group, never as harmless. The affected groups are invalidated in this order, using the first scope that can be established:

| Scope known | Groups made `invalid` (each ranked frame gets `burst_frame_missing`) |
|---|---|
| `batch_index` is a valid integer | every comparison group sent in that batch |
| otherwise, `session_id` is a valid session | every comparison group in that session |
| otherwise | every comparison group in the file |

An entry with `sent_to_model = false` (`stage = original` or `stage = preview`; both are recorded before batching, with `batch_index = null`) never triggers B0, because that frame was never shown to the model. If an entry's `sent_to_model` is missing or invalid, it is treated as possibly sent.

The same rules apply at read time (V39, V44). A missing or malformed identity field in an error entry invalidates the affected groups; it doesn't refuse the whole file, and it is never filled in or guessed. Ratings, apply and approval are unaffected, because burst status never changes a rating.

### 8.3 Group status (`burst_ranking_status`)

`burst_ranking_status` is stored on every **evaluable** result in a burst. It is `null` on results outside a burst and on unevaluable results. A group therefore has a stored status only if it has at least one evaluable result.

| Status | When |
|---|---|
| `validated` | B0 and C1 pass, the mode is `ranked`, and every ranked frame passes B1 or B2, B4, B5 and B6 |
| `not_comparable` | B0 and C1 pass, the mode is `not_comparable`, and C2 passes |
| `invalid` | anything else, in particular any failure of B0 (including an error entry of unknown identity, 8.2.1), any C1 failure (a), (b) or (c), any C2 failure, or any failure of B3, B4, B5 or B6 on any ranked frame |

**Groups with zero or one ranked frame:**
- **Zero ranked frames** (every frame unevaluable, or in `errors`): no result carries a status, because every result in the group is unevaluable. There is no stored "not applicable" value. The gallery shows the group as "no evaluable frames".
- **One ranked frame (decision U9):** the group is `validated` **only if every one of these passes**; otherwise it is `invalid`:
  - B0: no frame of the group that was sent to the model is in `errors`, and no error entry of unknown identity affects the group (8.2.1)
  - C1: the frame's `burst_comparison` is exactly `ranked`
  - B1, the single-winner pattern: `best_in_burst = true`, `co_best = false`, `burst_rank = 1`
  - B4: rank 1 is the only rank, so it holds trivially once B1 passes
  - B5: `near_duplicate_of = null`, because there is no other frame to link to
  - B6: a valid `rank_reason`, for example "only readable frame of this burst; the other two previews are unreadable"

  A `not_comparable` claim with one ranked frame breaks C2, because there is nothing to compare, and makes the group `invalid`. The single frame is never treated as a non-burst frame.

**An `invalid` group is not a validated comparison.** Every ranked frame in it gets review reason `burst_ranking_invalid`, in addition to the reason for the specific rule broken. While the status is `invalid`:
- `best_in_burst` values that survived validation are kept for display, labelled "unvalidated" in the gallery, and do **not** make a frame highlight-eligible
- no path to highlight eligibility is open for any frame in the group (section 7.2); a valid distinct-moment claim can still give `retain_low_rating` (section 7.1)
- `co_best_tie` and `weak_burst_best` are not computed for the group

**A `not_comparable` group:** every ranked frame gets review reason `burst_not_comparable`, so the human confirms that the frames really are unrelated before any of them can be a highlight. `co_best_tie` and `weak_burst_best` are not computed. Each frame is judged on its own rating, exactly like a single frame.

**Further rules:**
- A burst winner isn't automatically 4 or 5 stars.
- A frame ranked lower never loses stars because of it.
- Redundancy shows up only in suggestions.
- There are no burst-only ratings and no automatic downgrades.

**Whole burst weak:** in a `validated` group, if the best frame (including co-best frames) has rating ≤ 2, every winner gets review reason `weak_burst_best`.

---

## 9. Evidence requirements

### 9.1 Required evidence

| Field | Required when | Check | Failure outcome |
|---|---|---|---|
| `technical.evidence` | every evaluable image | at least one entry; each entry passes 9.3 | V11 |
| `editorial.evidence` | every evaluable image | at least one entry; each entry passes 9.3 | V11 |
| `decisive_evidence` | see 9.2 | at least one entry; each entry passes 9.3 | V12a, V12b |
| `motion_evidence` | motion is `intentional_*` | at least one entry; each entry passes 9.3 | V9 |
| `distinct_moment_evidence` | `distinct_moment = true` | at least one entry, saying what makes this moment substantively different from the other frames or coverage; each entry passes 9.3 | V19 |
| `rank_reason` | every ranked frame in a `ranked` group | a non-empty string that passes 9.3 | V24 (B6) |
| `not_comparable_evidence` | every ranked frame in a `not_comparable` group | at least one entry, saying what makes this frame unrelated to the others; each entry passes 9.3 | V29 (C2) |

### 9.2 Decisive evidence: two checks, two different triggers

1. **The model supplies it** based on its own proposal: whenever its `proposed_rating` is 1, 4 or 5, or it sets `proposed_moment_override = true`.
2. **The code checks it:**
   - **V12a, before the override decision**, only when `proposed_moment_override = true`. An override is valid only if this check passes (section 6.3), so validity never depends on its own result. A failure adds `evidence_missing` or `evidence_generic` **and** `override_invalid`.
   - **V12b, after computing the final rating**, whenever the final `rating` is 1, 4 or 5. A failure adds `evidence_missing` or `evidence_generic` only. It never adds `override_invalid`.

Review reason codes are unique per result: if both checks fail for the same reason, the code is recorded once. This catches the case where the model proposed, say, 3 and supplied no decisive evidence, but the matrix computed 4. The disagreement also adds `rating_rule_mismatch`.

**Code never invents evidence.** It does not write, copy, summarise or move text between evidence fields, and it does not generate evidence from scores. An item that lacks the required evidence keeps lacking it and goes to review.

### 9.3 Specificity check (basic, not a fact check)

Each entry must:
- be a string of at least **20 characters** after trimming whitespace (Q6)
- not consist only of phrases on the generic list, such as "great photo", "nice composition" or "good exposure"
- mention a concrete subject or part of the frame, like "speaker", "eyes", "left", "background" or "hand", using a keyword heuristic

The minimum length, the generic-phrase list and the keyword list together are the **evidence rules**. They live in code with tests and are adjusted during calibration (Q6). Every change to them requires a new `evidence_rules_version` (section 14.4); a unit test pins a hash of the rules to the version string so a change without a version bump fails the test suite.

**On failure:** missing evidence → `evidence_missing`; generic evidence → `evidence_generic`. Both go to review, and nothing is dropped. Where evidence is a precondition for a claim (override, distinct moment, burst ranking, not-comparable claim, highlight eligibility), the claim is not honoured (sections 6.3, 7.1, 7.2 and 8).

**Stated plainly:** the validator checks that evidence **is present and looks specific**. It does **not** and cannot check that the evidence is visually true. Only a human review of the actual image does that (section 12.3).

**No invention:** if the model can't establish a visual fact, it must say so, for example by adding it to `uncertain_checks`, rather than make one up.

---

## 10. Uncertain checks

`uncertain_checks` is a list drawn from `focus`, `eyes`, `motion_intent`, `exposure`, `expression` and `subject_identification`.

- A non-empty list adds review reason `critical_defect_uncertain`.
- A missing or invalid list, or one with any entry outside the allowed set, is stored as `null`, not `[]`, because an empty list would claim "no uncertainty". Entries are never filtered. It adds `field_invalid` (V16).
- At 1800 px preview size, `focus` and `eyes` are expected to come up often.

### 10.1 Motion consistency: `technical.motion` against `uncertain_checks` and the flag `subject_blur`

Two checks, both under V17: the motion class against `uncertain_checks` (first table), and the motion class against the flag `subject_blur` (second table).

Applies to evaluable results only.

| `technical.motion` | `motion_intent` in `uncertain_checks` | Outcome |
|---|---|---|
| `uncertain` | yes | consistent; `motion_uncertain` and `critical_defect_uncertain` |
| `uncertain` | no | **conflict**: `motion_conflict`, plus `motion_uncertain` |
| `frozen`, `camera_shake`, `unintended_subject_blur`, `intentional_pan` or `intentional_drag` | yes | **conflict**: `motion_conflict`, plus `critical_defect_uncertain`. A definite class contradicts uncertain intent |
| `frozen`, `camera_shake`, `unintended_subject_blur`, `intentional_pan` or `intentional_drag` | no | consistent |
| any | list is `null` (invalid, V16) | consistency can't be verified: `motion_conflict`, plus `field_invalid` |

On a conflict, both fields are kept exactly as the model returned them. The code never adds or removes `motion_intent` and never changes the motion class. A conflicting result always goes to review (V17).

**`subject_blur` against the motion class** (evaluable results with a valid `flags` list only):

| `technical.motion` | `subject_blur` in `flags` | Outcome |
|---|---|---|
| `camera_shake` or `unintended_subject_blur` | yes or no | consistent |
| `intentional_pan` or `intentional_drag` | yes | **conflict**: `motion_conflict`. Intentional motion blur is not a technical fault |
| `uncertain` | yes | **conflict**: `motion_conflict`. Uncertain intent goes to review; it isn't assumed to be a fault |
| `frozen` | yes | **conflict**: `motion_conflict`. A frozen subject has no motion blur |
| any | no | consistent |

If `flags` is `null` (invalid, V15), this check is skipped; `field_invalid` already sends the item to review. On a conflict, the flag and the motion class are kept exactly as returned; the code never removes the flag or changes the class (V17).

---

## 11. Confidence and review routing

`confidence` is a value from 0 to 1 (an integer 0 or 1 is accepted; a bool is not). It is the model's own sense of uncertainty, **not a calibrated probability**. The threshold is fixed at **0.6** for rubric v3: `confidence < 0.6` triggers review, so exactly 0.6 does not (Q11). A missing or invalid confidence is stored as `null` and adds `field_invalid` (V25).

**Read-time use.** Readers use the stored `confidence` and the file's `confidence_threshold` only to verify routing: `low_confidence` must be present exactly when `confidence` is not null and below the threshold, and `field_invalid` must be present when `confidence` is null (V35). A v3 file whose `confidence_threshold` is not exactly 0.6 is refused (V31). Readers never re-route items or change `needs_review` from confidence.

`needs_review = true` whenever `review_reasons` has at least one entry, and `false` only when it is empty. Review reasons are unique codes; their order has no meaning and readers compare them as sets. The possible codes are:

| Code | Trigger |
|---|---|
| `low_confidence` | confidence < 0.6 |
| `rating_5` | computed rating is 5 |
| `moment_override` | valid override applied |
| `override_invalid` | `proposed_moment_override = true` and the cell isn't valid or the decisive evidence failed V12a |
| `rating_rule_mismatch` | `proposed_rating` differs from the computed rating, or is missing or invalid |
| `unevaluable` | rating 0 |
| `motion_uncertain` | motion = `uncertain` |
| `motion_conflict` | `technical.motion` and `uncertain_checks` disagree, consistency can't be verified, or `subject_blur` contradicts the motion class (section 10.1) |
| `critical_defect_uncertain` | `uncertain_checks` isn't empty |
| `evidence_missing` / `evidence_generic` | section 9, including `rank_reason` and `not_comparable_evidence` |
| `score_gap` | \|T − E\| ≥ 3 |
| `co_best_tie` | tied frame in a `validated` group |
| `weak_burst_best` | the best frame in a `validated` group has rating ≤ 2 |
| `burst_winner_invalid` / `burst_rank_invalid` / `near_duplicate_invalid` | section 8.2 (B3, B4, B5) |
| `burst_frame_missing` | a frame of the group that was sent to the model is in `errors` (B0) |
| `burst_comparison_invalid` | mixed or invalid `burst_comparison`, or an invalid not-comparable claim (C1, C2) |
| `burst_ranking_invalid` | the frame's group has `burst_ranking_status = invalid` (section 8.3) |
| `burst_not_comparable` | the frame's group has `burst_ranking_status = not_comparable` |
| `documentation_keep_low_rating` | `retain_low_rating` |
| `distinct_moment_highlight` | non-winning frame in a `validated` group with a valid distinct-moment claim that would be highlight-eligible |
| `distinct_moment_invalid` | `distinct_moment = true` without valid evidence, or with E = 1 (section 7.1) |
| `field_invalid` | a model field was missing, invalid or contradictory and is stored as `null` or kept for review (section 15.1) |

---

## 12. Approval lifecycle

### 12.1 Approval states (derived by code; not stored)

The code derives the state in this order. The first matching row wins.

| # | State | Condition | Apply behaviour |
|---|---|---|---|
| 1 | **malformed** | the approval fields break any rule in 12.2 | Skipped: `[SKIP approval malformed]` |
| 2 | **not_required** | `needs_review = false` and all approval fields are `false` / `null` | Existing workflow: gallery review, then `apply --yes` |
| 3 | **pending** | `needs_review = true` and `approved = false` | Skipped: `[SKIP needs review: not approved]` |
| 4 | **invalidated** | `needs_review = true`, `approved = true`, and any binding check below fails | Skipped. Apply finds binding checks 1, 2, 4–6 at its check 10 and binding check 3 at its check 12 (13.1), so apply reports the first failure **in apply order**, not in the diagnostic order of the table below |
| 5 | **approved** | `needs_review = true`, `approved = true`, and every binding check passes | Can be applied, after all other checks pass |

**Binding checks.** The table lists the six checks in their **diagnostic order**, which `approve --show` uses to report the first failing check. **Apply doesn't run them in this order.** It follows the skip precedence in section 13.1, shown in the last column: binding check 3 needs a full read of the original, so apply runs it as its last per-item check (13.1 check 12), only for an item that is about to be written. Only check 13, the re-hash of the results file, runs after it and before the write. The diagnostic order never makes apply hash an original early or hash every original (details under "Binding checks inside apply" below).

| # | Check | Fails when | Message | Where apply runs it (13.1) |
|---|---|---|---|---|
| 1 | rating | `approved_rating != rating` | `[SKIP approval invalidated: rating changed]` | check 10 |
| 2 | original path | `approved_original_path != original_path` | `[SKIP approval invalidated: original changed]` | check 10 |
| 3 | original content | SHA-256 of the original file, read-only and streamed, ≠ `approved_original_sha256`. (12.2 already requires `approved_original_sha256` to equal the analysis-time `original_sha256`, so this also proves the original is unchanged since analysis) | `[SKIP approval invalidated: original changed]` | check 12, the last per-item check; check 13 (results-file re-hash) follows, then the write |
| 4 | preview path | `approved_preview_path != preview_path` | `[SKIP approval invalidated: preview changed]` | check 10 |
| 5 | preview validity | any preview check PV1–PV6 (section 12.5) fails | `[SKIP approval unverifiable: preview invalid]` | check 10 |
| 6 | preview content | SHA-256 of the preview file ≠ `approved_preview_sha256` | `[SKIP approval invalidated: preview changed]` | check 10 |

The original is opened **read-only**, only to compute its hash. It is never written, never passed to a viewer or editor, and its metadata is never parsed.

**Who runs check 3.** Hashing an original is expensive, so check 3 runs only where a write depends on it:
- **apply** runs check 3 as its last per-item check before writing that item's sidecar (13.1, check 12), and only for items that passed every earlier check; only check 13, the results-file re-hash, comes between it and the write
- **approve** compares the current hash with `original_sha256` before storing an approval (12.3)
- **`approve --show`** hashes the one original it shows

Every other reader (`review`, the gallery, the coverage report) derives the state from checks 1, 2 and 4–6 only. It shows an approval that passes them as "approved (original not verified)", never as proof that the current file is unchanged.

**Binding checks inside apply.** Apply follows section 13.1 exactly:
- It runs checks 1, 2, 4, 5 and 6 at its check 10. These read only the results file and the small preview JPEG, never the original.
- It runs check 3, the original SHA-256, only at its check 12. That happens after the item has passed checks 0a–11a and just before its sidecar would be written.

The diagnostic order of the table above never makes apply hash an original early, and never makes apply hash every original. An item that fails both check 3 and, say, check 4 is skipped by apply with check 4's message at its check 10, and its original is never hashed. `approve --show` for the same item reports check 3 first, because it hashes the one original it shows. Both outcomes are skips; only the reported message can differ. No sidecar is written for an approved item until its check 12 (binding check 3) has passed.

Version 1 and 2 items have no approval fields and are treated as `not_required`, exactly as today (section 14).

### 12.2 Approval field validation

The seven approval fields are `approved`, `approved_rating`, `approved_at`, `approved_original_path`, `approved_original_sha256`, `approved_preview_path` and `approved_preview_sha256`.

- `approved` must be a JSON boolean.
- `approved = true` requires **all** of:
  - `approved_rating`: an integer 1–5 (a bool is not an integer)
  - `approved_at`: an ISO 8601 timestamp in UTC with a `Z` suffix, written by the approve command, for example `2026-11-15T07:12:03Z`
  - `approved_original_path`: an absolute path string
  - `approved_original_sha256`: exactly 64 lowercase hexadecimal characters
  - `approved_preview_path`: an absolute path string
  - `approved_preview_sha256`: exactly 64 lowercase hexadecimal characters
- `approved = true` also requires `approved_original_sha256` to equal the result's analysis-time `original_sha256` and `approved_original_path` to equal `original_path` at the time of writing. The approve command guarantees both; a stored approval where `approved_original_sha256 ≠ original_sha256` can only come from editing the file and is **malformed**, not invalidated.
- `approved = false` requires the other six fields to be `null`.
- Approval fields on an item with `needs_review = false` must be `false` / `null`. An approval there is malformed.
- Approval fields on an item with `rating = 0` must be `false` / `null`. An approval there is malformed, because rating 0 can never be approved (section 6.4).
- Any other combination, a missing field on a v3 item, or a wrong type makes the state **malformed**. Malformed approvals are reported and never repaired, completed or guessed by apply, the gallery, the report or the approve command.
- No user identity is stored (Q9).

### 12.3 The approve command (Q1)

```
photo-curator approve <results.json> <original_path> --show [--open-preview]
photo-curator approve <results.json> <original_path> --rating N --preview-sha256 <64-hex>
photo-curator approve <results.json> <original_path> --revoke
```

**Per item only.** One invocation changes at most one item. There is no bulk mode, no wildcard, no list input and no "approve all".

#### Step 1: inspect the actual image (`--show`, read-only)

`--show` writes nothing, except that it creates the lock file if it is missing (12.6). It takes the shared lock (section 12.6) and refuses if the output is incomplete (12.8) or an archive is in progress (12.9). It runs the preview checks PV1–PV6 and prints, for the one matching item:
- the **absolute original path**, the analysis-time `original_sha256`, the original's **SHA-256 computed now** (read-only), and whether the two match. A mismatch is shown as "original changed since analysis; can't be approved"
- the **absolute preview path**, the result of each preview check, and the preview's **SHA-256**, computed now
- rating, decision, T, E, motion class, flags, `uncertain_checks`, review reasons, the current approval state (with the failing binding check, if any), and all evidence text
- `capture_time`, burst group, `burst_comparison`, `burst_ranking_status` and rank, if any

`--open-preview` additionally opens the **preview** in the desktop's default image viewer, read-only, and only if PV1–PV6 pass. The command **never opens the original in a viewer or editor**, because the default handler for RAW files may be an editor such as darktable that can create or change sidecars. The printed original path lets the human inspect the original in digiKam if the preview isn't enough to judge focus or detail.

The gallery shows, for every review item, the original path, the preview and the preview SHA-256 recorded at analysis time. The approve command always recomputes the hash; a gallery hash that no longer matches the preview makes the approve step refuse.

#### Step 2: approve, bound to what was inspected

The human confirms with `--rating N --preview-sha256 <hash>`, copying the hash from `--show` or the gallery. At write time, the command recomputes the original's SHA-256 and **refuses unless it equals the analysis-time `original_sha256`**. Only then does it store that hash with the original path, the preview path and the preview hash. The approval therefore applies only to the image that was inspected, and to the same original content that was analysed (12.1 binding checks). The preview binding is unchanged and still required. The command cannot prove that a person looked at the image; it guarantees that an approval refers to one specific, unchanged preview and original.

#### Re-approval and revocation

| Current state | `--rating … --preview-sha256 …` | `--revoke` |
|---|---|---|
| `not_required` | refused: nothing to approve | refused: no-op |
| `pending` | approves | refused: no-op |
| `approved` (every binding check passes) | refused: already approved; revoke first to change it | revokes → `pending` |
| `invalidated` (any binding check fails) | approves again directly, replacing all seven fields, after a new `--show` | revokes → `pending` |
| `malformed` | refused: fix by hand first | refused: revoking would be a repair |

#### Refusals (no change, non-zero exit)

The command refuses when:
- the results file is not schema version 3, fails to parse, or fails whole-file read validation (section 15.3)
- the results file is a symlink, or is not inside a Photo Curator output folder (`.photo-curator` or a marked output folder)
- `<original_path>` does not match exactly one item's `original_path` (none or several → refused as ambiguous)
- the item is inconsistent under section 15.3
- any row of the re-approval table says "refused"
- **approve:** the item has rating 0; `--rating` is missing or differs from the item's current `rating`; `--preview-sha256` is missing, isn't 64 lowercase hex characters, or differs from the SHA-256 of the current preview file; any preview check PV1–PV6 fails; the original is missing, not a regular file, or can't be read; or the original's current SHA-256 differs from the analysis-time `original_sha256` ("original changed since analysis": the item can't be approved from these results; analyse again into a new output folder)
- the output is incomplete (12.8) or an archive is in progress or unfinished (12.9)

#### Writing, in this order

1. Take an exclusive, non-blocking lock (section 12.6). If it is held, stop.
2. Read the results file and compute its SHA-256 (H0). Validate it (section 15.3) and resolve the item. Re-check the state and the refusal rules.
3. **Back up:** create `<results.json>.<UTC timestamp with microseconds>.photo-curator.bak` in the same folder with exclusive creation, so an existing backup is never overwritten. Write the bytes read in step 2, fsync, re-read, and confirm the backup's SHA-256 equals H0. On any failure, remove the partial backup and stop.
4. Build the new document in memory. **Approve:** compute the original's SHA-256 now, read-only; if it differs from `original_sha256`, stop. Then set `approved = true`, `approved_rating = N`, `approved_at = <now, UTC>`, `approved_original_path`, `approved_original_sha256` (the hash just computed, equal to `original_sha256`), `approved_preview_path` and `approved_preview_sha256`. **Revoke:** set `approved = false` and the other six to `null`. In both cases recompute that item's `editorial_suggestion` (section 7.2). No other item and no other field changes.
5. Validate the whole new document (section 15.3) and confirm that the only differences from the loaded document are the target item's approval fields and `editorial_suggestion`. If not, stop.
6. Write the new document to a temporary file `<results.json>.tmp-<pid>-<random>` in the same folder, created exclusively. Flush and fsync it, re-read it, and repeat the step 5 checks on what was read back. On any failure, remove the temporary file and stop.
7. Re-hash the results file on disk. If it differs from H0, remove the temporary file and stop.
8. **Replacement:** `os.replace` the temporary file over the results file. If `os.replace` raises an error, the rename didn't happen: remove the temporary file and stop.
9. **Durability:** fsync the folder that holds the results file. If this fails, stop at once: report the durability failure and exit non-zero. **Step 10 isn't run**, because verification is only meaningful once durability has succeeded.
10. **Verification** (only after step 9 succeeded): re-read and validate the results file, and confirm that its content is the document written in step 6. Then report the outcome and the backup path. Release the lock.

**Two failure classes.** The command distinguishes failures before the replacement from failures after it, and reports which class occurred.

| Class | Steps | State of the active results file | What the command does |
|---|---|---|---|
| **Before replacement** | 1–7, and a step 8 `os.replace` that raised an error | **Byte-identical** to what it was before the command ran | Removes the temporary file (if any), keeps the verified backup (if step 3 completed), exits non-zero |
| **After replacement: durability failure** | step 9 fails after step 8 succeeded | Holds the **new** document, as far as the operating system reported. It is **not** byte-identical to the old file, and the change isn't guaranteed to survive a crash or power loss: after one, the file may hold either the old or the new document, both complete | Reports "approval written but not confirmed durable", keeps the verified backup, exits non-zero. **Step 10 isn't run. No automatic rollback** |
| **After replacement: verification failure** | step 10 fails, after steps 8 and 9 succeeded | Unknown or unexpected content; the command doesn't claim either version | Reports "approval written but verification failed", keeps the verified backup, exits non-zero. **No automatic rollback** |

**After a post-replacement failure**, the command never tries to restore or rewrite the file itself: a second replacement could fail in the same way and would hide what happened. The verified backup from step 3 still holds the exact bytes of the file as it was before the command ran. The human checks the item with `approve --show` and, if needed, restores the backup by hand. There is no restore command in v3.

**Backups** are never deleted by the command, except a partial backup removed during a failed step 3. A command that exits successfully has completed steps 1–10.

The command never writes originals, sidecars or XMP. It reads the original only to hash it, and the preview only to hash it and, with `--open-preview`, to hand it to a viewer. It never runs analysis or apply.

### 12.4 Lifecycle rules

- **Invalidation:** an approval becomes **invalidated** if the rating, the original path, the original's content, the preview path or the preview's content changes after approval, and unverifiable if the preview fails PV1–PV6 (12.1).
- **No carry-over (decision U2):** a new analysis never writes into a folder that holds results (section 12.7), so approvals are never silently replaced. Approvals are never copied, migrated or matched into a new analysis, even for an identical original and preview. Every new analysis needs fresh individual approval for every review item.
- **Re-approval and revocation:** see the table in 12.3.
- **Apply never edits the results JSON.** It derives the state, reports invalidated, unverifiable or malformed approvals, and skips those items.
- **No bypass:** approval never skips:
  - `--yes`
  - `--min-rating`
  - the range check for ratings 1–5
  - the check that the original exists
  - the existing-sidecar policy (`--include-existing`)
  - the original/preview path checks
  - the generated-output checks (`in_generated_output`, markers, `.photo-curator`)
  - the read-time consistency checks (section 15.3)
- **Rating 0 and rating 1:** rating 0 is never approved or applied. Rating 1 is applied only if `--min-rating` allows it; the default stays 1 and the documented workflow passes `--min-rating 1` explicitly (Q8).
- **Override 4 as a highlight:** an override 4 (T3/E5) becomes `highlight` only in the **approved** state; before that it is `pending_approval` (section 7.2).
- **Rating 5:** every rating 5 needs individual approval before it is a `hero_candidate` or can be applied (Q12).

### 12.5 Preview checks (PV1–PV6)

Used by `approve --show`, `--open-preview`, the approve step, and apply's binding checks. `<work>` is the folder that contains the results JSON being read (the exact output folder of that analysis).

| ID | Check |
|---|---|
| PV1 | `preview_path` is an absolute path whose parent folder, resolved, is exactly `<work>/previews`, resolved |
| PV2 | `<work>/previews` is a marked output folder (it holds a valid `.photo-curator-output` marker) |
| PV3 | the preview itself is not a symlink (checked with lstat) and is a regular file |
| PV4 | the file name ends in `.jpg` (lowercase, as `make_preview` writes it) |
| PV5 | the file name is exactly the name `make_preview` derives from `original_path`: `<original stem>__<first 10 hex characters of SHA-1 of original_path>.jpg` |
| PV6 | the preview's resolved path is not the original and is not inside the source folder |

A failure is never repaired: apply skips with `[SKIP approval unverifiable: preview invalid]`, and approve refuses. A results file moved away from its `previews` folder (for example an archived one, section 12.7) fails PV1, so its approvals can't be applied.

### 12.6 Locking

- **Lock file:** `<work>/ratings-review.json.lock`, created once with `O_CREAT | O_NOFOLLOW` as a regular file and never deleted. If it exists but is a symlink or not a regular file, every command that uses the lock refuses. A reader that finds no lock file creates it the same way before locking. This applies to output folders of **every** schema version, so a v1/v2 folder gains a lock file the first time any v3-era command uses it (14.7).
- **The only side effect of a read-only command:** a read-only command (`approve --show`, `review`, the coverage report, `recover-archive` in report mode) may create `<work>/ratings-review.json.lock` if it is missing. That is the **only** change it may make. It never creates, changes, moves or deletes any other file or folder: not the results file, CSV, gallery, manifest, previews, journal, backups, originals or sidecars. Wherever this specification says a read-only command "writes nothing" or "makes no change", this one exception applies.
- **Order at start-up, for every command and every schema version:** (1) identify the filesystem type of `<work>` and refuse known network filesystems (below); (2) take the lock (shared or exclusive, per the table); (3) unless the command is `recover-archive`, refuse if `<work>/archive-in-progress.json` exists (12.9, V47); (4) only then read the results file or any preview.
- **Lock type:** `flock`, **always non-blocking**. No command waits for the lock, and there is no `--wait` option. If the lock can't be taken, the command refuses immediately with "output folder busy: another Photo Curator command is using it; retry after it finishes", changes nothing and exits non-zero. The user may simply run the same command again once the other command has finished. The kernel releases the lock when the process exits, including a crash, so a stale lock can't block later runs; the leftover lock file is harmless.
- **Who takes it:**

  | Command | Lock | Held |
  |---|---|---|
  | `analyze`, including `--archive-existing` | exclusive | from just after `--output` validation until the completion manifest is written (12.8) or the run stops |
  | `recover-archive --complete`, `recover-archive --rollback` (12.9) | exclusive | the whole run, from before the journal is read until the last rename or the stop |
  | `recover-archive` without `--complete` or `--rollback` (report only, 12.9) | shared | from before the journal is read until the report is printed |
  | `approve --rating …` and `approve --revoke` (they change an approval) | exclusive | steps 1–10 of 12.3 |
  | `apply` | **exclusive** | from **before** the results file is read until the last sidecar is written and the summary printed |
  | `approve --show`, `review`, coverage report | shared | from before the results file is read until the command's last access to the results file or a preview |

  Every command that changes the output folder, the results file or an approval takes the exclusive lock. Every command that only reads takes the shared lock. No command runs without one of the two.

- **Why apply is exclusive:** apply can then never act on a snapshot that a concurrent approve or revoke has changed, because neither can run while apply holds the lock. It also stops two applies from writing the same sidecars at once. Exclusive-for-writers and shared-for-readers is the whole design; there is no lock upgrade and no waiting.
- **Apply under the lock:** apply reads the results file only after taking the lock and computes its SHA-256 (H0). Before each sidecar write it re-hashes the results file. If the hash differs from H0 (a program outside Photo Curator changed it), apply stops immediately. It reports what it has applied so far and the item it stopped at, and exits non-zero. It never re-reads and continues with the changed file.
- **Readers and archive:** readers hold the shared lock, so `--archive-existing` (exclusive) can't start while any reader is active, and no reader can start while an archive is running. A reader therefore never sees a partly moved `previews` folder or missing previews caused by archiving. JSON reads stay atomic as before: every writer replaces the results file with `os.replace`.
- **The static gallery** (`ratings-review.html` opened in a browser) is not a process and can't take a lock. It references previews by paths relative to the HTML file, and the archive moves the HTML file together with the `previews` folder, so the archived gallery works inside the archive folder. A gallery left open in a browser during an archive may show broken images until it's reopened from its new location; it never affects any data.
- **Scope:** the lock coordinates Photo Curator processes only. It doesn't stop other programs (an editor, a sync tool, a browser); the hash re-checks in 12.3 step 7 and in apply narrow that window, and the verified backup is the manual recovery path for the results file (12.3, failure classes).
- **Local filesystem required (decision U4).** The output folder must be on a local filesystem, because `flock` and atomic `os.replace` aren't reliable on every network filesystem. Every command that takes the lock first identifies the filesystem type of `<work>`. It reads `/proc/self/mountinfo` and takes the mount with the longest mount point that contains the resolved `<work>`. It **refuses** with "output folder is on a network filesystem (<type>); use a local folder" when that type is in the code's list of known network filesystems:
  - `nfs`, `nfs4`
  - `cifs`, `smb3`, `smbfs`
  - `9p`, `afs`, `ceph`, `glusterfs`, `lustre`, `davfs`
  - `fuse.sshfs`, `fuse.rclone`, `fuse.s3fs`, `fuse.gcsfuse`

  The list lives in code with tests and can be extended.
- **Limits of the detection, stated plainly:** detection is best effort. If `/proc/self/mountinfo` can't be read, or the type isn't on the list (for example a generic `fuse` mount, or a network filesystem mounted under an unexpected type), the command proceeds and prints no claim that the folder is local. The specification doesn't promise that every network filesystem is detected; keeping output on a local disk remains the user's responsibility, and the CLI help says so.
- **Leftovers:** a crash can leave `*.tmp-*` files from any atomic write (12.8). They are never read as artifacts and never deleted automatically. Every command that takes the lock lists any it finds, and the human removes them.

### 12.7 Re-analysis into an existing output folder

`analyze` **refuses by default** when `<work>` already contains any of:
- `ratings-review.json`, `ratings-review.csv`, `ratings-review.html` or `ratings-review.complete.json` (12.8)
- any `ratings-review.json.*.photo-curator.bak`
- any `*.tmp-*` leftover from an atomic write
- a `previews` folder that contains any entry (for example after a crash before the results were written)
- `archive-in-progress.json` (12.9); in this case only `recover-archive` is offered

The refusal message names the files found and the number of approved items in any existing results, and offers two workflows:

1. **New output folder (recommended):** run `analyze` again with a different `--output`. The existing folder, its results, approvals and previews stay exactly as they are.
2. **Explicit archive:** `analyze … --archive-existing` moves every item in the refusal list above (except `archive-in-progress.json`, which blocks it) into a new folder `<work>/archive/<UTC timestamp>/`, marked as output, following the journalled procedure in 12.9. It never deletes anything and holds the exclusive lock throughout. It prints how many approvals were archived. Archived approvals are never carried into the new results, and they can't be applied from the archive because their previews fail PV1.

**No new analysis in a mixed folder.** The new analysis starts only after the archive has completed and been verified (12.9 step 6). If any step of the archive fails, analyze stops before generating previews or calling Claude, and the folder stays blocked until `recover-archive` resolves it.

The refusal and the archive step happen before any preview is generated or any Claude call is made.

### 12.8 Analysis output completeness

`analyze` produces four artifacts in `<work>`. **Each one is written atomically on its own** (temporary file `<name>.tmp-<pid>-<random>` in the same folder, flush, fsync, `os.replace`, then fsync the folder). **The four are not atomic as a group.** A crash between two writes leaves some artifacts present and others missing.

**Failure classes for each artifact**, as in 12.3:
- **Before its replacement:** the artifact is absent (or, for a temporary file, left as a listed leftover). Analyze stops and exits non-zero.
- **After its replacement, if the folder fsync fails:** the artifact is present but not guaranteed durable. Analyze reports a durability failure, writes **no further artifact** (in particular no manifest), and exits non-zero. The output is therefore incomplete.
- **If the manifest's own folder fsync fails:** the output may pass the completeness check, but analyze reports a durability failure and exits non-zero. It doesn't claim that the output is durable, and it never rolls back.

**Write order** (all under the exclusive lock, after all previews exist):
1. `ratings-review.json` (results; contains a new `run_id`, section 14.4)
2. `ratings-review.csv`
3. `ratings-review.html`
4. `ratings-review.complete.json`, the **completion manifest**, written last

The manifest holds:

| Field | Value |
|---|---|
| `run_id` | the same `run_id` as in the results file |
| `created_at` | UTC timestamp with `Z` |
| `results_sha256_at_analysis` | SHA-256 of `ratings-review.json` as analyze wrote it (informational; approve later changes the file) |
| `csv_sha256` | SHA-256 of `ratings-review.csv` |
| `html_sha256` | SHA-256 of `ratings-review.html` |
| `referenced_preview_count` | number of **referenced previews** (defined below) when the manifest is written |
| `orphan_preview_count` | number of **orphan previews** (defined below) when the manifest is written |

**Preview accounting.** Both counts are computed by listing `<work>/previews` once, with lstat and without following symlinks, just before the manifest is written. The marker file `.photo-curator-output` is ignored. Every other entry is exactly one of these:
- **Referenced preview:** a regular `.jpg` file (not a symlink) whose absolute path equals the `preview_path` of at least one result in `ratings-review.json` or of at least one `errors` entry (14.5). Each file counts once, however many entries name it.
- **Orphan preview:** any other entry in `<work>/previews`, whether a file, symlink or folder. Examples: the preview of a `stage = original` rejection (14.3a), or anything placed there by hand.

`referenced_preview_count + orphan_preview_count` equals the number of entries listed, excluding the marker. An orphan preview never counts as a referenced preview. A result or error entry whose `preview_path` names a file that doesn't exist adds nothing to either count.

**Orphans and completeness.** Orphan previews **don't make output incomplete** and never invalidate otherwise complete results. The completeness rules below don't depend on either count. The counts are informational: readers that recompute them and get different numbers show a warning (V46) and change nothing. Orphan previews are never deleted automatically, by analyze, `--archive-existing`, `recover-archive` or any other command. An archive moves them along with the rest of `previews`.

**Complete output** (v3 only) means all of these:
- the manifest exists, is a regular file, parses, and has every field above, with both counts as non-negative integers
- its `run_id` equals the results file's `run_id`
- the CSV and the gallery exist, and their SHA-256 values equal the manifest's
- the results file passes V30 and V31

**Incomplete output** is anything else, for example a results file without a manifest after a crash. It is never treated as complete, and nothing repairs it or writes the missing artifacts:

| Command | On incomplete output |
|---|---|
| `apply` | refuses the whole file: "analysis output incomplete"; nothing applied |
| `approve` (all forms, including `--show`) | refuses |
| `review`, coverage report | refuse, naming which artifacts are missing or don't match |
| `analyze` into the same folder | refuses (12.7); the human uses a new output folder or `--archive-existing` |

The results file's later changes by approve don't break completeness, because the manifest binds the results file by `run_id`, not by hash. v1 and v2 outputs have no manifest and are checked as today.

### 12.9 Archive journal and recovery

`--archive-existing` moves several items, and the moves can't be atomic as a group. It is therefore **journalled**:

1. Under the exclusive lock, build the move list: each item in `<work>` from the 12.7 refusal list, its target in `<work>/archive/<UTC timestamp>/`, its kind (file or folder), and for each file its SHA-256. For `previews`, record the sorted list of entry names and their count. Refuse if any target exists.
2. Write `<work>/archive-in-progress.json` atomically, with the archive folder and the move list. From this moment the folder is **blocked**: every Photo Curator command except `recover-archive` refuses while this file exists, **whatever the schema version** of the results file (v1, v2 or v3, or a results file that can't be parsed). The check is made right after taking the lock and before the results file is read (12.6, start-up order), so no command enters a v1/v2 or v3 branch while the journal exists. Apply writes no sidecar in this state. If writing the journal fails before its replacement, nothing has been moved and archive stops. If it fails after the replacement (folder fsync), archive stops, and the journal, which may exist, blocks the folder until `recover-archive` reports on it.
3. Create the archive folder and mark it as output.
4. Rename each item with `os.rename` (same filesystem only), in the journal's order. `previews` moves in one rename, so it is never partly archived.
5. On the **first** failure, stop. Don't roll back and don't retry; leave the journal in place, report which moves are done and which are pending, and exit non-zero. Analyze doesn't start.
6. **Verify:** every target exists; every file's SHA-256 matches the journal; `previews` holds exactly the recorded names; no source remains. Then rename the journal into the archive folder as `archive-journal.json`. The journal is moved, never deleted. Only now may the new analysis start.

**Detecting a partial archive:** `archive-in-progress.json` exists in `<work>`. Each journal entry's state is derived from the filesystem, never assumed:

| Source exists | Target exists | State |
|---|---|---|
| yes | no | pending |
| no | yes | done |
| yes | yes | conflict |
| no | no | lost |

**Recovery** is explicit, never automatic:

```
photo-curator recover-archive <work>                # report only; changes nothing
photo-curator recover-archive <work> --complete     # finish the pending moves
photo-curator recover-archive <work> --rollback     # move the done items back
```

- **Report-only is the default.** Without `--complete` or `--rollback`, it takes the **shared** lock, prints every entry's state and verification result, and makes no change, not even to the journal. Its only possible side effect is creating the lock file if it is missing (12.6). It can run alongside other readers, but not while any exclusive holder is active.
- **Narrow scope.** `--complete` acts only on entries in state `pending`, moving each from source to target. `--rollback` acts only on entries in state `done`, moving each from target back to source. Neither touches any file or folder that isn't named in the journal, and neither re-creates, copies or rewrites anything.
- **Check everything first.** `--complete` and `--rollback` take the **exclusive** lock before reading the journal and hold it until they finish or stop. Before the first rename, they verify the whole journal: it must be well-formed; no entry may be `conflict` or `lost`; every file to be moved must match its SHA-256 in the journal; and the `previews` folder must hold exactly the recorded names. If anything fails, the command refuses without moving anything. The human resolves the folder by hand; nothing is guessed or repaired.
- **Failure during recovery.** If a rename fails partway through, the command stops at once, leaves the journal in place, reports which entries it moved, and exits non-zero. Running report mode again shows the new state, and the human decides the next step. There is no automatic retry and no automatic switch between complete and rollback.
- After a successful `--complete`, step 6 runs, and the journal is moved into the archive folder. After a successful `--rollback`, every item is back at its source, the empty archive folder is kept, and the journal is renamed to `archive-journal.rolled-back.json` inside it. Nothing is deleted in either case.
- `recover-archive` never starts an analysis. The human runs `analyze` again afterwards.
- **No journal.** If `<work>/archive-in-progress.json` doesn't exist (no archive was started, or the last one completed or was rolled back and its journal was moved into the archive folder), there is nothing to recover:
  - **Report mode:** prints "no archive recovery pending in <work>" and exits zero. It makes no change apart from the permitted creation of the lock file (12.6).
  - **`--complete` and `--rollback`:** refuse with "no archive in progress: nothing to complete or roll back" and exit non-zero. They make no rename, create or change no journal, and change no other file; the only possible side effect is the lock file (12.6).
  - Journals already moved into an archive folder (`archive-journal.json`, `archive-journal.rolled-back.json`) are records of finished operations. `recover-archive` never reads or acts on them.
- **Schema boundary (normative).** To determine and verify the state of an archive recovery, `recover-archive` depends only on the archive journal and the filesystem. The list below limits the **inputs to that decision and verification**. It doesn't replace the start-up steps of 12.6, which still apply in every mode: the filesystem-type check through `/proc/self/mountinfo`, and the lock operations (opening or creating `ratings-review.json.lock` and taking the shared or exclusive lock). For determining and verifying recovery state, in every mode it reads only:
  - `<work>/archive-in-progress.json`, the journal, which it parses and checks for well-formedness (above);
  - the existence and type (lstat) of the source and target of each entry named in the journal;
  - the bytes of each file named in the journal, solely to compute its SHA-256 and compare it with the hash recorded in the journal;
  - the entry names inside `previews`, to compare them with the list recorded in the journal.

  It never parses the results file (`ratings-review.json`), the CSV, the gallery or the completion manifest. It doesn't read `schema_version`, `run_id` or any result, and it applies none of the validation rows V30–V46. For files such as the results file, it treats them only as named files in the journal: bytes to hash and paths to rename. Its behaviour is therefore identical in v1, v2 and v3 output folders, and it is unaffected by a results file or manifest that is missing, corrupt or of any schema version.

---

## 13. Writing XMP

v3 apply writes **only `xmp:Rating`**, as it does today:
- to the `original.ext.xmp` sidecar
- after backing up any existing sidecar
- with the write procedure in section 13.2. The approve command's guarantees for the results file (12.3) don't carry over to sidecars automatically; 13.2 states the sidecar procedure on its own.

Documentation and editorial suggestions, review reasons and approval state stay in the JSON and gallery only. **No digiKam labels, keywords or other metadata are written.**

### 13.1 Apply check order and skip messages

Apply checks each item in this order. The first failing check decides the outcome; each skipped item prints exactly one message, and the summary counts skips per message.

| # | Check | Skip message |
|---|---|---|
| 0a | known network filesystem, or the exclusive lock can't be taken (12.6) | refused ("output folder is on a network filesystem" or "output folder busy"), nothing applied |
| 0b | `<work>/archive-in-progress.json` exists (12.9, V47). Checked **before the results file is parsed**, for **every schema version** | refused: "archive in progress; run `recover-archive`", nothing applied, no sidecar written |
| 0c | results file can't be parsed, or `schema_version` not 1, 2 or 3 (V30) | file refused, nothing applied |
| 0d | v3 only: V31 fails, or the output is incomplete (12.8, V46) | file refused, nothing applied |
| 1 | **Target original and ambiguity.** The target original is `original_path` when present, otherwise `path` (v1 files have only `path`). The two are compared **only when both are present**; they differ → skip. A missing `original_path` (v1) is never ambiguous and never a reason to skip (V33) | both present and different: `[SKIP ambiguous original_path/path]` |
| 2 | target is generated, has an unsupported extension, or equals `preview_path`. The comparison with `preview_path` is made **only when that field is present** (V33). A missing `preview_path` (v1 files) is never a reason to skip here. On a v3 file a missing `preview_path` is caught by check 4 (V36), exactly as before | `[SKIP not an original image]` |
| 3 | target is inside generated output | `[SKIP inside generated output]` |
| 4 | v3 only: inconsistent stored result (V34–V36, V45) | `[SKIP inconsistent result]` |
| 5 | rating 0 | `[SKIP rating 0: unevaluable]` |
| 6 | rating below `--min-rating` | `[SKIP below min-rating]` |
| 7 | original missing or not a regular file (lstat only; the file isn't opened) | `[SKIP original missing]` |
| 8 | approval state `malformed` | `[SKIP approval malformed]` |
| 9 | approval state `pending` | `[SKIP needs review: not approved]` |
| 10 | approval state `invalidated` by binding check 1, 2, 4, 5 or 6 (12.1), first failing check | the binding check's message |
| 11 | existing sidecar without `--include-existing` | `[SKIP existing sidecar]` |
| 11a | `<sidecar>` equals the original's path, or exists but is a symlink or not a regular file (13.2 step 1) | `[SKIP sidecar not a regular file]` |
| 12 | v3 only, **original content**, the last per-item check, run only for an item that passed checks 0a–11a and is about to be written: the original is opened read-only and its SHA-256 computed once. Skip if it can't be read, if the hash ≠ `original_sha256`, or (approved items) if the hash ≠ `approved_original_sha256`, which is binding check 3 | unreadable: `[SKIP original unreadable]`. Approved item, mismatch: `[SKIP approval invalidated: original changed]`. Any other item, mismatch: `[SKIP original changed since analysis]` |
| 13 | runs after check 12 and immediately before each write: re-hash of the results file ≠ H0 (12.6) | apply **stops** (not a skip): reports what it applied so far and exits non-zero |
| — | all checks pass | the sidecar is written immediately by the procedure in 13.2: `[OK] <name>: N stars -> <sidecar>` on success; on a write failure apply stops as described there |

Checks 1–3 and 11 keep today's messages. Checks 5 and 6 replace today's silent skip with a message. Checks 0a, 0b and 0c apply to **every** schema version; the version branch starts only after 0c. For v1/v2 files, checks 0d, 4, 8–10 and 12 don't apply. Every sidecar write, for every version, follows section 13.2.

**Original hashing at apply time.** Apply never hashes all originals. It hashes an original only at check 12, after the item has passed every other per-item check (0a–11a) and would otherwise be written, so items skipped for any earlier reason are never hashed. Check 12 runs for every v3 item that reaches it, approved or not, and **no sidecar is written for an item until its check 12 has passed**. Each original is hashed at most once per apply run.

**What check 12 proves, and what it doesn't.** Check 12 proves that the original's bytes, read moments before the sidecar is written, equal the bytes hashed at analysis (and, for approved items, at approval). It can't prove that the file doesn't change after it was read; writing the sidecar never touches the original.

### 13.2 Sidecar write procedure

This applies to every sidecar apply writes, for every schema version. `<sidecar>` is `<original path>.xmp`, in the original's folder.

**Before writing:**
1. `<sidecar>` must differ from the original's path. If `<sidecar>` exists, it must be a regular file and not a symlink (lstat). Apply checks this at 13.1 check 11a, before the original is hashed; a failure skips the item with `[SKIP sidecar not a regular file]`, and nothing is written.
2. **Backup** (only when `<sidecar>` exists and `--include-existing` allows overwriting it): create `<sidecar>.<UTC timestamp with microseconds>.photo-curator.bak` in the same folder with exclusive creation, write the existing bytes, fsync, re-read, and confirm its SHA-256 equals the existing sidecar's. If any part fails, remove the partial backup, leave `<sidecar>` unchanged, and **stop apply** (see the "Before replacement" row of "Guarantees and failure classes" below). Backups are never deleted.

**Writing:**

3. Build the new sidecar content in memory, setting only `xmp:Rating` as today.
4. Write it to a temporary file `<sidecar>.tmp-<pid>-<random>` in the same folder, created exclusively; flush, fsync, re-read, and confirm that it parses and holds the intended rating.
5. **Replacement:** `os.replace` the temporary file over `<sidecar>`.
6. **Durability:** fsync the folder.
7. **Verification** (only after step 6 succeeded): re-read `<sidecar>` and confirm that it parses and holds the intended rating.

**Guarantees and failure classes:**

| Class | Steps | State of `<sidecar>` | What apply does |
|---|---|---|---|
| **Before replacement** | 2–4, and a step 5 `os.replace` that raised an error (a step 1 failure is a skip at check 11a, not a write failure) | Unchanged: byte-identical to before, or still absent if there was none | Removes the temporary file (if any), keeps any verified backup, prints `[ERROR sidecar write failed] <name>`, **stops**, reports what it applied so far, exits non-zero |
| **After replacement: durability failure** | step 6 fails after step 5 succeeded | Holds the new content as far as the operating system reported, but isn't guaranteed to survive a crash or power loss; after one it may hold either the old or the new content | Prints `[ERROR sidecar written but not confirmed durable] <name>`, keeps the backup, **stops**, exits non-zero. Step 7 isn't run. **No automatic rollback** |
| **After replacement: verification failure** | step 7 fails, after steps 5 and 6 succeeded | Unknown or unexpected content | Prints `[ERROR sidecar written but verification failed] <name>`, keeps the backup, **stops**, exits non-zero. **No automatic rollback** |

**What this does and doesn't guarantee:**
- On a local filesystem that supports atomic same-directory replacement, a reader opening the sidecar during replacement sees either the complete old file or the complete new file, never a partially written file. Durability after a crash is guaranteed only when step 6 succeeds.
- Apply stops at the first sidecar failure of any class, rather than continuing with other items, because a write failure usually means a filesystem problem. Items already written stay written; apply never undoes them.
- There is no automatic restore. After a failure the human compares `<sidecar>` with its backup and, if needed, restores it by hand.
- **The original RAW or JPEG file is never opened for writing**, never renamed and never replaced. Only `<sidecar>`, its backup and its temporary file are created or changed.
- A crash can leave a `<sidecar>.tmp-*` file next to the original. Its name doesn't end in a supported image extension, so it's never scanned as an original. It's never read as a sidecar and never deleted automatically. Apply prints the path of any temporary file it leaves behind after a failure.

---

## 14. JSON schema v3

Source key: **M** = model, **C** = code, **H** = human (through the approve command). Every v3 result has every field. A required field is never filled with a guessed default (section 15.1). The validation column names the rows in section 15.

### 14.1 Result fields

| Field | Source | Type and allowed values | Validation |
|---|---|---|---|
| `path`, `original_path` | C | absolute original path (code is the authority, as now) | V1, V33 (path checks, every version), V36 (presence and type, v3) |
| `preview_path` | C | absolute generated preview path (section 12.5) | V1, V33 (when present), V36 (presence and type, v3), V42 |
| `session_id` | C | as now | V36 (presence and type, v3), V41 |
| `capture_time` | C | section 14.3 | V40 |
| `original_sha256` | C | 64 lowercase hex characters: SHA-256 of the original file's bytes, computed read-only (streamed) during analysis, before **and** after its preview is generated (14.3a). Never null on a result. A result exists only if both hashes succeeded and were equal, and the supplementary lstat checks found no change. It records the content **at analysis time**; readers that don't re-hash must label it "analysis-time SHA-256, not verified against the current file" | V45 |
| `evaluable` | M | bool | V2 |
| `unevaluable_reason` | M | see 14.2 | V3, V5 |
| `capture_type` | M | see 14.2 | V7 |
| `technical_score`, `editorial_score` | M | int 1–5 if evaluable; null if unevaluable | V4, V6 |
| `technical` | M | object `{motion, motion_evidence, recoverable_in_raw, evidence}`; never null | V8 |
| `technical.motion` | M | enum (section 3.2) if evaluable; null if unevaluable | V4, V8 |
| `technical.motion_evidence` | M | see 14.2 | V9 |
| `technical.recoverable_in_raw` | M | see 14.2 | V10 |
| `technical.evidence` | M | see 14.2 | V11 |
| `editorial` | M | object `{evidence}`, or null (see 14.2) | V11 |
| `editorial.evidence` | M | see 14.2 | V11 |
| `decisive_evidence` | M | see 14.2 | V12a (override proposed), V12b (final rating 1, 4 or 5), V12c (otherwise, type only) |
| `proposed_rating` | M | int 0–5, or null if missing or invalid | V13 |
| `proposed_moment_override` | M | bool, or null if missing or invalid | V14 |
| `moment_override` | C | bool (valid and applied) | V34 |
| `rating` | C | int 0–5 (section 6) | V34 |
| `decision` | C | enum (section 6.5) | V34 |
| `flags` | M | list of flags from section 3.3, or null if invalid | V15 |
| `fatal_flaws` | M | evaluable: [str], or null if invalid; unevaluable: exactly `[]`. Descriptive only (section 3.4) | V4, V15 |
| `uncertain_checks` | M | list of values from section 10, or null if invalid | V16, V17 |
| `distinct_moment` | M | bool, or null if invalid | V18 |
| `distinct_moment_evidence` | M | see 14.2 | V19 |
| `burst_group`, `burst_sequence` | C | as now; kept for unevaluable frames; null outside a burst | V36 (presence and type, v3), V41 |
| `burst_comparison` | M | `ranked` or `not_comparable` for an evaluable frame in a burst; null outside a burst, if unevaluable, or if invalid | V4, V20, V28 |
| `burst_rank` | M, validated by C | see 14.2 | V4, V20, V22 |
| `best_in_burst` | M, validated by C | see 14.2 | V4, V20, V21 |
| `co_best` | M, validated by C | see 14.2 | V4, V20, V21 |
| `near_duplicate_of` | M, validated by C | see 14.2 | V4, V20, V23 |
| `rank_reason` | M | see 14.2 | V4, V20, V24 |
| `not_comparable_evidence` | M | see 14.2 | V4, V20, V29 |
| `burst_ranking_status` | C | `validated`, `not_comparable` or `invalid` on evaluable results in a burst; null otherwise (section 8.3) | V39 |
| `documentation_suggestion` | C | section 7.1 | V37 |
| `editorial_suggestion` | C | section 7.2 (recomputed by the approve command for its item) | V37 |
| `confidence` | M | number 0–1 (int 0 or 1 accepted, bool not), or null if missing or invalid | V25, V35 |
| `needs_review` | C | bool | V35 |
| `review_reasons` | C | list of unique codes from section 11 | V35 |
| `approved` | H | bool; `false` when first written | V38 |
| `approved_rating` | H | int 1–5 if approved, else null | V38 |
| `approved_at` | H | ISO 8601 UTC timestamp with `Z` if approved, else null | V38 |
| `approved_original_path` | H | absolute original path if approved, else null | V38 |
| `approved_original_sha256` | H | 64 lowercase hex characters if approved, else null | V38 |
| `approved_preview_path` | H | absolute preview path if approved, else null | V38 |
| `approved_preview_sha256` | H | 64 lowercase hex characters if approved, else null | V38 |
| `reasons` | C | combined list of the model's evidence text, kept so the existing gallery still works; built only from model text, never generated; may be `[]` | V37 |

The approval state itself is derived (section 12.1) and not stored. At analysis time, every result is written with `approved = false` and the other six approval fields `null`.

### 14.2 Stored shapes of model fields (valid, withheld, kept, unevaluable)

"W" means withheld under 15.1 (stored as `null`); "K" means kept as returned and flagged. A stored W or K shape is valid only together with the review reason shown; without it the result is inconsistent (V36).

| Field | Valid shape | W shape (reason) | K shape (reason) | Unevaluable |
|---|---|---|---|---|
| `unevaluable_reason` | evaluable: `null`; unevaluable: non-empty string | — | evaluable: string as returned (`field_invalid`) | non-empty string |
| `capture_type` | allowed value | `null` (`field_invalid`) | — | allowed value or `null` |
| `technical.motion_evidence` | list of strings; non-empty when motion is `intentional_*`; `[]` allowed otherwise | `null` (`evidence_missing` if required, else `field_invalid`) | generic entries as returned (`evidence_generic`) | `[]` |
| `technical.recoverable_in_raw` | list of strings, may be `[]` | `null` (`field_invalid`) | — | `[]` |
| `technical.evidence` | non-empty list of strings | `null` (`evidence_missing`) | `[]` as returned (`evidence_missing`); generic entries as returned (`evidence_generic`) | `[]` |
| `editorial` | object with key `evidence` | `null` when missing or not an object (`evidence_missing`); `editorial.evidence` is then absent | — | object |
| `editorial.evidence` | non-empty list of strings | `null` (`evidence_missing`) | `[]` as returned (`evidence_missing`); generic entries as returned (`evidence_generic`) | `[]` |
| `decisive_evidence` | list of strings; non-empty when required (9.2); `[]` allowed otherwise | `null` (`evidence_missing` if required, else `field_invalid`) | `[]` as returned when required (`evidence_missing`); generic entries as returned when required (`evidence_generic`) | `[]` |
| `proposed_rating` | int 0–5 | `null` (`rating_rule_mismatch`) | — | `0`, or other value with `rating_rule_mismatch` |
| `proposed_moment_override` | bool | `null` (`field_invalid`) | — | `false` |
| `flags` | list of section 3.3 flags, may be `[]` | `null` (`field_invalid`) | — | as valid, or W |
| `fatal_flaws` | evaluable: list of strings, may be `[]` (section 3.4) | evaluable: `null` (`field_invalid`) | — | `[]` only; anything else rejects the result (V4) |
| `uncertain_checks` | list of section 10 values, may be `[]` | `null` (`field_invalid`, and `motion_conflict` if evaluable) | — | as valid, or W |
| `distinct_moment` | bool | `null` (`field_invalid`) | — | `false` |
| `distinct_moment_evidence` | non-empty list of strings when `distinct_moment = true`; `[]` otherwise | `null` (`evidence_missing` + `distinct_moment_invalid`) | `[]` or generic entries as returned with a claim (`evidence_missing` or `evidence_generic`, + `distinct_moment_invalid`); non-empty list without a claim (`field_invalid`) | `[]` |
| `burst_comparison` | `ranked` or `not_comparable` in a burst; `null` outside | `null` in a burst (`burst_comparison_invalid`); `null` outside a burst after a non-null value (`field_invalid`) | — | `null` |
| `burst_rank` | int ≥ 1 in a `ranked` group; `null` otherwise | `null` (`burst_rank_invalid`, or `field_invalid` outside a burst) | value as returned in a `not_comparable` claim that failed C2 (`burst_comparison_invalid`) | `null` |
| `best_in_burst`, `co_best` | bool in a `ranked` group; `null` in a `not_comparable` group and outside a burst | `false` after B3 (`burst_winner_invalid`); `null` outside a burst (`field_invalid`) | value as returned in a failed C2 claim (`burst_comparison_invalid`) | `null` |
| `near_duplicate_of` | original path of a better-ranked frame in the same group, or `null` | `null` (`near_duplicate_invalid`, or `field_invalid` outside a burst) | value as returned in a failed C2 claim (`burst_comparison_invalid`) | `null` |
| `rank_reason` | non-empty specific string in a `ranked` group; `null` otherwise | `null` (`evidence_missing`, or `field_invalid` outside a burst) | generic string as returned (`evidence_generic`); value as returned in a failed C2 claim (`burst_comparison_invalid`) | `null` |
| `not_comparable_evidence` | non-empty list of specific strings in a `not_comparable` group; `[]` otherwise | `null` (`evidence_missing` + `burst_comparison_invalid`) | `[]` or generic entries as returned in a `not_comparable` group (`evidence_missing` or `evidence_generic` + `burst_comparison_invalid`); non-empty list in a `ranked` group or outside a burst (`field_invalid`) | `[]` |
| `confidence` | number 0–1 | `null` (`field_invalid`) | — | as valid, or W |

`technical` is never stored as null: a missing or non-object `technical` rejects the result (V8). A missing or non-object `editorial` is stored as `editorial: null`; the code never creates an `editorial` object that the model didn't return. For an unevaluable result, any deviation from the "Unevaluable" column, other than the fields marked "as valid, or W", rejects the result (V4).

### 14.3 `capture_time`

- **Source:** the original's EXIF `DateTimeOriginal`, `SubSecTimeOriginal` and `OffsetTimeOriginal`, read once during analysis with ExifTool, in the same read that burst detection uses. Burst detection and `capture_time` use the same values.
- **Format:** `YYYY-MM-DDTHH:MM:SS[.fff][±HH:MM]`.
  - Sub-seconds are kept to at most 3 digits; extra digits are truncated, not rounded.
  - The source is always the camera's capture-time metadata (decision U6). The explicit UTC offset comes from `OffsetTimeOriginal`. The `±HH:MM` offset is present **only** when that field exists and is a valid offset. It is then copied exactly; the time is not converted to UTC.
  - Without a valid offset, the value is **timezone-unknown** camera local time with no offset (for example `2026-11-15T10:14:03.250`). The code never substitutes the computer's local timezone, UTC, or any other zone. `OffsetTime`, `OffsetTimeDigitized`, GPS time and manual offsets are not used.
- **Null:** `null` when `DateTimeOriginal` is missing, unparseable, or a placeholder such as `0000:00:00 00:00:00`. The code never falls back to file modification time or any other source.
- **Burst detection** compares wall-clock times within one session (the date-time part, ignoring any offset), so it no longer depends on the computer's timezone. Images with `capture_time = null` are never burst-grouped, as today.
- **Readers** never re-read metadata. The coverage report uses this field only (section 7.3).

### 14.3a Original integrity during preview generation

**SHA-256 is the content-integrity mechanism.** Size and modification time only help detect races; they are never a substitute for the hash.

For each original, analysis does this, in order:
1. `lstat` the original (S1: size, `mtime_ns`, inode, device). It must be a regular file.
2. Compute SHA-256 read-only, streamed (H1).
3. Generate the preview (darktable-cli or the existing JPEG path).
4. `lstat` the original again (S2).
5. Compute SHA-256 read-only, streamed, again (H2).

**Accept:** only if both hashes succeeded, H1 = H2, and S1 = S2 (size, `mtime_ns`, inode and device). Then `original_sha256 = H1`, and the image may be sent to the model.

**Reject:** in every other case, the image gets an `errors` entry with `stage = original`, `sent_to_model = false`, and no result:

| Situation | `error` text |
|---|---|
| step 1: missing or not a regular file | "original missing or not a regular file" |
| step 2 or 5: read or hash failure | "original could not be read for hashing" |
| H1 ≠ H2 | "original content changed during preview generation" |
| S1 ≠ S2 with H1 = H2 | "original file metadata changed during preview generation" |

The generated preview is left in `previews`, because generated output is never deleted automatically. Neither the result list nor the error entry refers to it: the `stage = original` entry has `preview_path = null` (14.5). It is therefore counted as an **orphan preview** (12.8).

**Limits of the guarantee, stated plainly:**
- H1 = H2 proves the original had the same bytes when it was read before and after preview generation. It does not prove that the preview generator read those same bytes. A change that is made and reverted between steps 2 and 5 can go unnoticed by the hashes. The S1/S2 comparison catches it only if the size, `mtime_ns` or inode changed, and `mtime_ns` can be set back by other programs.
- This detects accidental changes, such as a sync tool or an editor writing the file during analysis. It is not protection against a deliberate attempt to hide a change.
- **Intentional integrity cost.** Each original is read three times during analysis: two SHA-256 hashes and the preview generator. For the first full GTR analysis (3,467 originals, about 50 MB each), the two hashes add roughly **340 GB** of reading on top of preview generation. This cost is accepted deliberately as the price of the content guarantee above (decision A37). There is no size/mtime-only or "fast" mode; the lstat comparison stays supplementary and never replaces either hash.
- Only analysis (twice per original), approve (once per approved item), `approve --show` (once per item shown) and apply check 12 (once per item about to be written) hash originals. `review`, the gallery and the coverage report never re-hash originals (12.1, 7.3, 14.6).

### 14.4 File level

| Field | Type | Validation |
|---|---|---|
| `schema_version` | `3` | V30 |
| `rubric_version` | `"v3"` | V31 |
| `evidence_rules_version` | non-empty string; the initial value is `"v3.0"` (decision U11); changes with every change to the evidence rules (section 9.3) | V31, V43 |
| `run_id` | random UUID (version 4) chosen by analyze for this run; repeated in the completion manifest (12.8) | V31, V46 |
| `confidence_threshold` | number, exactly `0.6` | V31 |
| `max_burst_size` | integer 2–12 (section 8.0, P3) | V31 |
| `burst_window_seconds` | number ≥ 0, as today | V31 |
| `burst_scope` | `"per_session"` | V31 |
| `results` | list of result objects | V31 |
| `errors` | list of error entries (section 14.5) | V31 |
| `distribution` | summary (section 17) | V32 |
| `calibration_drift_warning` | bool, or null when there are no evaluable rated results | V32 |
| `created_at`, `source_folder`, `application` | kept exactly as v2 writes them today | display only |
| `rating_style` | `"strict_v3"` (v2 writes `"strict_dual_score_burst"`) | display only |

### 14.5 Error entries

Every v3 `errors` entry describes **one original** (a failed batch produces one entry per original in it) and has these fields:

| Field | Type |
|---|---|
| `stage` | `original` (the original was missing, couldn't be read or hashed, or changed during preview generation; section 14.3a), `preview` (preview generation failed), `batch` (the Claude call for the batch failed), or `result` (the original's result was missing, rejected or invalid). `original` and `preview` entries always have `sent_to_model = false` |
| `error` | non-empty string |
| `original_path` | absolute original path; `null` only for an entry that names no frame of the batch (for example a model result for a path outside the batch), which B0 treats as identity unknown (8.2.1) |
| `session_id` | as for results; required on every entry with `sent_to_model = true` |
| `burst_group`, `burst_sequence` | as for results; `null` outside a burst. `burst_group` is required (present, string or `null`) on every entry with `sent_to_model = true` |
| `capture_time` | as for results (section 14.3) |
| `batch_index` | integer index of the batch for `stage = batch` and `stage = result`; `null` for `stage = original` and `stage = preview`, which are recorded before batching |
| `sent_to_model` | bool: `true` when the original was included in a batch whose Claude call was started; always `false` for `stage = original` and `stage = preview`, always `true` for `batch` and `result` |
| `result` | the rejected model result as returned, for `stage = result` when there was one; otherwise null |
| `preview_path` | absolute path of the preview that was sent to the model, for `stage = batch` and `stage = result`; `null` for `stage = preview` (no preview exists) and for `stage = original` (the preview was deliberately not used; 14.3a). Used only for preview accounting (12.8). An invalid value is treated as no reference, with a warning; it never refuses the file |

B0 uses `sent_to_model`, `burst_group`, `session_id` and `batch_index`. Section 8.2.1 says what happens when the identity fields are missing or malformed. A failed batch also leaves its groups without results, so no status is stored for them.

### 14.6 Other outputs of analyze

- **CSV** (`ratings-review.csv`): keeps its existing columns and adds `capture_time`, `needs_review`, `review_reasons`, `documentation_suggestion`, `editorial_suggestion`, `moment_override`, `burst_comparison` and `burst_ranking_status`. `flags` uses the section 3.3 names. It is a read-only export written at analysis time; it never shows approval state and is never read back by apply or approve.
- **Gallery** (`ratings-review.html`): adds review reasons, the approval state at analysis time (always pending or not required), the original path, the preview SHA-256 for review items, the stored `original_sha256` labelled "analysis-time SHA-256, not verified against the current file", `capture_time`, and burst status with "unvalidated" and "not comparable" labels. **Every preview reference** (`img src`, links) is a relative path from the HTML file, `previews/<file name>`, never an absolute path or a `file://` URL. The gallery and its `previews` folder can therefore move together (12.7, 12.9) and still display correctly.
- **`review` command:** for v3 files it prints the review reasons, the derived approval state (an approval passing checks 1, 2 and 4–6 is shown as "approved (original not verified)", 12.1), and the stored `original_sha256` with the same "not verified" label. It never hashes originals. Its closing hint points to `photo-curator approve … --show` instead of "edit ratings-review.json", because a hand-edited rating breaks V34 and a hand-written approval without matching hashes is invalidated or malformed.
- **Completion manifest** (`ratings-review.complete.json`): written last; section 12.8.
- **CLI help:** documents `approve` and its three forms, `--archive-existing`, `recover-archive` and its three forms, the refusal on existing or incomplete output, the "output folder busy" refusal (no waiting; retry after the other command finishes), the local-filesystem requirement and the best-effort network-filesystem refusal with its limits, the `stage = original` errors, which apply items are hashed (check 12 only), the 2–12 range of `--max-burst-size`, the apply skip messages, the archive-journal refusal for every schema version, the v1/v2 differences in 14.7, and the sidecar write errors and stop behaviour in 13.2.

### 14.7 Backward compatibility

- Versions 1 and 2 remain readable.
- **What stays the same:** v1/v2 items have no `needs_review`, so they are treated as **not_required**. Rating interpretation is unchanged: when apply runs to completion on a v1/v2 file, it writes the same ratings to the same items and skips the same items as today.
- **What changes operationally.** v1/v2 output folders are **not** used exactly as before. The table lists every intentional difference, the commands it affects and why (decision U8). Each one adds messages or safety, and none changes which rating an item gets:

  | # | Difference | Commands affected on v1/v2 folders | Effect | Why |
  |---|---|---|---|---|
  | 1 | Rating-0 and below-`--min-rating` skips print `[SKIP rating 0: unevaluable]` and `[SKIP below min-rating]` (13.1, checks 5 and 6) | `apply` | extra output lines; same items skipped | every skip is reported |
  | 2 | Lock acquisition per 12.6: **exclusive** for `apply`, `approve --rating`, `approve --revoke`, `recover-archive --complete` and `recover-archive --rollback`; **shared** for `review`, `approve --show`, the coverage report and `recover-archive` in report mode | all seven commands in the per-command table below | any of them may refuse with "output folder busy; retry after it finishes" | no command may read or write while another changes the folder |
  | 3 | Lock-file creation: the first lock-taking command creates `ratings-review.json.lock` in the folder if it's missing (12.6) | all seven commands | one new, harmless, never-deleted file in the output folder. For the read-only commands (`review`, `approve --show`, coverage report, `recover-archive` in report mode) this is their only side effect | `flock` needs a file |
  | 4 | Known network filesystems are refused (12.6, start-up step 1, before the lock) | all seven commands | may refuse where today's command would run | locks and atomic replace aren't reliable there |
  | 5 | Archive journal refusal (12.6, start-up step 3, after the lock and before the results file is read; 12.9, V47, 13.1 check 0b) | every command in the table **except** `recover-archive`, which start-up step 3 exempts because it is the only command allowed to act on a partial archive | refused; nothing read or written apart from a possible new lock file; no sidecar written | a partially archived folder must not be used |
  | 6 | Apply re-hashes the results file before each write and stops if it changed (13.1, check 13) | `apply` | may stop early where today's apply would continue | no write from a file changed mid-run |
  | 7 | Sidecar writes follow 13.2: the sidecar must be a regular file (check 11a); apply stops at the first write, durability or verification failure and reports it | `apply` | may skip an item with a non-regular sidecar, or stop early on a filesystem error | defined failure behaviour for XMP writes |

  v1/v2 files skip every v3-only check (13.1: 0d, 4, 8–10 and 12). Messages are not made uniform by changing v1/v2 skip decisions, and no v3 validation is applied to v1/v2 items. Of the read-time validation rows in 15.3, only V30 (file level), V33 (path checks, including check 1's target rule) and V47 (journal) apply to them. Their apply eligibility checks (13.1 checks 5–7, 11 and 11a) work as listed in the per-command table below.

  **Per command on a v1/v2 output folder.** Every command runs the 12.6 start-up order first, whatever the schema version: (1) refuse a known network filesystem; (2) take its lock, creating the lock file if missing, or refuse "busy"; (3) refuse if `archive-in-progress.json` exists; (4) only then read the results file.

  | Command | Lock (12.6) | After the start-up order, on a v1/v2 results file | Possible side effects |
  |---|---|---|---|
  | `apply` | exclusive | runs with the v1/v2 rules: checks 0c, 1–3, 5–7, 11, 11a and 13; the sidecar procedure in 13.2 | sidecars and their backups, as today; the lock file |
  | `review` | shared | displays the file as today, tolerating missing v3 fields | the lock file only |
  | `approve --show` | shared | refuses: approve accepts schema version 3 only (12.3) | the lock file only |
  | `approve --rating …` | exclusive | refuses at 12.3 writing step 2 (schema version 3 only), before any backup or write | the lock file only |
  | `approve --revoke` | exclusive | refuses at 12.3 writing step 2, before any backup or write | the lock file only |
  | coverage report | shared | refuses: the report accepts schema version 3 only | the lock file only |
  | `recover-archive` (report mode) | shared | start-up step 3 doesn't apply. It reports the journal entries' states as defined in 12.9, or, with no journal, "no archive recovery pending" (exit zero). Under the 12.9 schema boundary it never parses the results file or the manifest, so it is the same on v1/v2 and v3 folders | the lock file only |
  | `recover-archive --complete` / `--rollback` | exclusive | start-up step 3 doesn't apply. With no journal it refuses with no change (12.9). Otherwise it verifies the whole journal before the first rename, then renames only the entries named in the journal (12.9). Same on v1/v2 and v3 folders under the 12.9 schema boundary | with a journal: the renames listed in it, the journal moved or renamed into the archive folder (12.9), and the lock file; nothing is deleted. Without a journal: the lock file only |

  So `approve` and the coverage report never process a v1/v2 file. They still run the start-up order and may create the lock file before refusing. `recover-archive` is new; it has no earlier behaviour to preserve. On a v1/v2 folder a journal can only exist if a v3-era `analyze --archive-existing` was run there (12.7). Without a journal, `recover-archive` follows the "No journal" rule in 12.9.
- The gallery and review commands tolerate missing v3 fields on v1/v2 files. On a v3 file, missing fields follow section 15.
- These stay as they are: original and preview path handling, session separation, burst grouping and splitting, and review-only analysis. The XMP safety checks are kept and extended by 13.2.

---

## 15. Validation

### 15.1 General rule: fail closed

A required field that is missing, has the wrong type, holds a value outside its allowed set, or contradicts another field is **never silently replaced with a guessed value or a default**. Every response is recorded, and there are only these responses:

| Code | Response |
|---|---|
| **R** | **Reject:** the result goes to `errors` (`stage = result`) and the image stays unrated |
| **W** | **Withhold:** keep the result, store the invalid model field as `null` (B3 alone stores `false`), add a review reason, and withhold whatever the field would have enabled: a claim, a suggestion, an approval or an apply |
| **K** | **Keep and flag:** keep the model's value exactly as returned for human review, add a review reason, and never let the value enable anything |
| **S** | **Skip at apply time** with a `[SKIP ...]` message (13.1) |
| **F** | **Refuse:** the approve command makes no change and exits non-zero; apply, the coverage report and approve refuse a whole file they can't trust |

The code may withdraw or ignore a model claim (an override, a distinct moment, a burst winner, a near-duplicate link, a burst ranking, a not-comparable claim); it never adds a claim, never upgrades a value, never filters list entries and never writes evidence. Every withdrawal leaves a review reason or an `errors` entry. Booleans are never accepted as integers, and `NaN` or infinite numbers are never valid. Section 14.2 lists the stored shape for every W and K outcome.

B3 is the one case where a withheld value is stored as `false` rather than `null`. That is allowed only because `false` removes a claim (a burst winner) and can never enable anything.

### 15.2 Model fields, checked during analysis

| ID | Field or condition | Failure | Response |
|---|---|---|---|
| V1 | result object, `path` / `original_path` | not an object, path outside the batch, duplicate, or missing result | **R** (existing checks) |
| V2 | `evaluable` | missing or not bool | **R** |
| V3 | `unevaluable_reason` when `evaluable = false` | missing, not a string, or empty after trimming | **R** |
| V4 | unevaluable result: every model field in the 6.4 table except `capture_type`, `proposed_rating`, `flags`, `uncertain_checks` and `confidence` | any value other than the one in the table (including any non-null burst field, any non-empty evidence list, and a `fatal_flaws` other than `[]`) | **R** |
| V5 | `unevaluable_reason` when `evaluable = true` | not null | **K**, `field_invalid` |
| V6 | `technical_score`, `editorial_score` when evaluable | missing, not an integer, or outside 1–5 | **R** (Q7) |
| V7 | `capture_type` | evaluable: missing or not an allowed value. Unevaluable: neither an allowed value nor null | **W**, `field_invalid` |
| V8 | `technical` object; `technical.motion` when evaluable | `technical` missing or not an object; motion missing or not an allowed value | **R** (Q7) |
| V9 | `technical.motion_evidence` | wrong type; or, when motion is `intentional_*`, empty or generic | wrong type: **W** (`evidence_missing` if required, else `field_invalid`). Empty when required: **K**, `evidence_missing`. Generic when required: **K**, `evidence_generic` |
| V10 | `technical.recoverable_in_raw` | missing or not a list of strings | **W**, `field_invalid` |
| V11 | evaluable result: `editorial` object; `technical.evidence`, `editorial.evidence` | `editorial` missing or not an object; evidence missing, not a list of strings, empty, or generic | `editorial` invalid: **W** (`editorial: null`), `evidence_missing`. Evidence missing or wrong type: **W**, `evidence_missing`. Empty: **K**, `evidence_missing`. Generic: **K**, `evidence_generic`. Each withholds highlight eligibility |
| V12a | `decisive_evidence` when `proposed_moment_override = true` | missing, not a list, empty, or generic | wrong type: **W**; empty or generic: **K**. Always `evidence_missing` or `evidence_generic` **and** `override_invalid`; the override is not applied |
| V12b | `decisive_evidence` when the final rating is 1, 4 or 5 | missing, not a list, empty, or generic | wrong type: **W**; empty or generic: **K**. `evidence_missing` or `evidence_generic` only; never `override_invalid`. Withholds highlight eligibility |
| V12c | `decisive_evidence` when neither V12a nor V12b applies | missing or not a list of strings | **W**, `field_invalid` |
| V13 | `proposed_rating` | missing, not an integer, or outside 0–5 | **W**, `rating_rule_mismatch` (Q7b) |
| V14 | `proposed_moment_override` | missing or not bool | **W**, `field_invalid`; no override, and no `override_invalid` |
| V15 | `flags` (any result); `fatal_flaws` (evaluable results only; on unevaluable results V4 applies) | missing, not a list, or any entry not a section 3.3 flag (for `flags`) or not a string (for `fatal_flaws`) | **W**, `field_invalid`; entries are never filtered |
| V16 | `uncertain_checks` | missing, not a list, or any entry not an allowed value | **W**, `field_invalid`; V17 then counts as a conflict |
| V17 | consistency of `technical.motion` with `uncertain_checks` and with the flag `subject_blur` (evaluable only) | any conflict in either 10.1 table, or `uncertain_checks` is null | **K** (all fields as returned), `motion_conflict` |
| V18 | `distinct_moment` | missing or not bool | **W**, `field_invalid`; no distinct-moment claim |
| V19 | `distinct_moment_evidence` | with a claim: missing, not a list, empty, or generic, or E = 1. Without a claim (`false` or null): not `[]` | with a claim: **W** (wrong type) or **K**, with `evidence_missing` or `evidence_generic` (for E = 1 alone, neither), always `distinct_moment_invalid`; the claim is not honoured. Without a claim: **K**, `field_invalid` |
| V20 | `burst_comparison`, `burst_rank`, `best_in_burst`, `co_best`, `near_duplicate_of`, `rank_reason` on a frame not in a burst; `not_comparable_evidence` not `[]` there | any non-null value; any non-empty `not_comparable_evidence` | burst fields: **W**, `field_invalid` (B7). `not_comparable_evidence`: **K**, `field_invalid` |
| V21 | winner pattern in a `ranked` group | breaks B1/B2, or wrong types | **W** to `false`/`false` per B3, `burst_winner_invalid`; group `invalid` |
| V22 | `burst_rank` in a `ranked` group | breaks B4 | **W**, `burst_rank_invalid`; group `invalid` |
| V23 | `near_duplicate_of` in a `ranked` group | breaks B5 | **W**, `near_duplicate_invalid`; not highlight-eligible; group `invalid` |
| V24 | `rank_reason` on a frame in a `ranked` group | missing, not a string, empty, or generic (B6) | missing, wrong type or empty: **W**, `evidence_missing`. Generic: **K**, `evidence_generic`. In every case the entire group is `invalid` and every ranked frame gets `burst_ranking_invalid` |
| V25 | `confidence` | missing, not a number, a bool, or outside 0–1 | **W**, `field_invalid` |
| V26 | a model `rating` field | present | ignored and not stored; never substituted for `proposed_rating` |
| V27 | any field not in the schema, at any level | present | ignored and not stored; it enables nothing |
| V28 | `burst_comparison` on an evaluable frame in a burst | C1 (a) missing; (b) not exactly `ranked` or `not_comparable`; (c) the group's frames disagree | (a), (b): **W**. (c): **K**. In every case the group is `invalid`, and every ranked frame gets `burst_comparison_invalid` and `burst_ranking_invalid` |
| V29 | `not_comparable_evidence` | in a `not_comparable` group: missing, not a list, empty, or generic (C2). In a `ranked` group: not `[]` | `not_comparable` group: **W** (wrong type) or **K** (empty or generic), with `evidence_missing` or `evidence_generic` and `burst_comparison_invalid`; group `invalid`. `ranked` group: **K**, `field_invalid` |
| V-B0 | missing frame | an `errors` entry with `sent_to_model = true` names this `burst_group`, or an entry of unknown identity falls within the group's scope (8.2.1) | `burst_frame_missing` on every ranked frame of every affected group; each affected group `invalid` |
| V-C2 | ranking fields in a `not_comparable` group | any of the five ranking fields non-null, or fewer than two ranked frames | **K** (values as returned), `burst_comparison_invalid`; group `invalid` |

Rows are listed by ID: V1–V29 in number order, then the group rules V-B0 and V-C2 in the order section 8.2 applies them. The ID is a label; the order in which checks run is given in 15.4 and 8.2.

### 15.3 Stored results, checked when read (apply, approve, gallery, coverage report)

Readers do not trust code-computed fields blindly. A results file can be edited by hand or damaged after analysis.

| ID | Field or condition | Failure | Response |
|---|---|---|---|
| V30 | file parse, `schema_version` | not valid JSON, or `schema_version` not 1, 2 or 3 | **F** (existing behaviour) |
| V31 | v3 file-level fields (14.4) and every `errors` entry (14.5) | `rubric_version` ≠ `"v3"`; `evidence_rules_version` missing or empty; `run_id` missing or not a UUID; `confidence_threshold` ≠ 0.6; `max_burst_size` not 2–12; `burst_scope` ≠ `"per_session"`; `burst_window_seconds` invalid; `results` or `errors` not a list; any error entry not an object, or with a missing or invalid `stage` or `error` | **F** for the whole file. Problems with an error entry's identity fields (`original_path`, `session_id`, `burst_group`, `sent_to_model`, `batch_index`) are handled by V44, not here |
| V32 | `distribution`, `calibration_drift_warning` | missing, wrong type, or different from a recomputation over `results` | display only: the gallery and report show the recomputed values with a warning; apply and approve don't use them |
| V33 | **path checks, every schema version.** The target original is `original_path` if present, otherwise `path` (v1 files have only `path`). `preview_path` is used only if present (v1 files have none) | `original_path` and `path` both present and different; the target is generated, equals `preview_path` (when present), has an unsupported extension, or is inside generated output | **S** with the messages in 13.1 checks 1–3; approve **F**. V33 never requires a field that the file's schema version doesn't have: on v1/v2 files, missing `original_path`, `preview_path`, `session_id`, `burst_group` or `burst_sequence` is not a failure |
| V34 | `rating`, `decision`, `moment_override` | `rating` not an integer 0–5; `decision` ≠ the 6.5 mapping; `moment_override = true` outside the three valid cells; `rating` ≠ the matrix result recomputed from the stored T, E and `moment_override`; or an unevaluable item with `rating` ≠ 0 | **S** `[SKIP inconsistent result]`; approve **F**; gallery flags the item |
| V35 | `needs_review`, `review_reasons`, `confidence` | `needs_review` not bool; `review_reasons` not a list of unique known codes; `needs_review` ≠ (`review_reasons` non-empty); `low_confidence` present ≠ (`confidence` not null and < 0.6); or `confidence` null without `field_invalid` | **S** `[SKIP inconsistent result]`; approve **F**; gallery flags the item |
| V36 | every v3 result field, including the presence and type of `path`, `original_path`, `preview_path`, `session_id`, `burst_group` and `burst_sequence` | missing, or not matching a shape in 14.1/14.2, or a W/K shape without its review reason | **S** `[SKIP inconsistent result]`; approve **F** |
| V37 | `documentation_suggestion`, `editorial_suggestion`, `reasons` | any value | never trusted: readers recompute the suggestions from sections 7.1 and 7.2; a difference is shown as a warning; apply doesn't use suggestions |
| V38 | the seven approval fields | any rule in 12.2 broken | state `malformed`: **S** `[SKIP approval malformed]`; approve **F**; never repaired |
| V39 | `burst_ranking_status` | not an allowed value; not the same on every evaluable result of one group; or different from a re-run of B0, C1, C2 and B1–B6 on the group's stored fields and the file's `errors` entries | treated as `invalid` for eligibility: no highlight path is open and the gallery flags the group. Ratings, apply and approval are unaffected |
| V40 | `capture_time` | not null and not matching 14.3 | display only: the report lists the image as untimed with a warning; apply and approve don't use the field |
| V41 | burst preconditions P1, P3 on stored data | results sharing a `burst_group` have different `session_id`s, or a group has more than `max_burst_size` results plus `sent_to_model` errors | the group is treated as `invalid` for eligibility and the gallery flags it; ratings, apply and approval are unaffected |
| V42 | `preview_path` for `--show`, `--open-preview`, approve and apply binding | any of PV1–PV6 fails (12.5) | apply **S** `[SKIP approval unverifiable: preview invalid]` for approved items; approve **F**; `--open-preview` refuses to open |
| V43 | `evidence_rules_version` | differs from the reader's own evidence rules version | readers don't re-run evidence checks (9.3, B6, C2); V39 compares status against a re-run of the non-evidence rules only; suggestions are shown labelled "evidence rules changed since analysis"; apply is unaffected; approve recomputes the suggestion from the stored review reasons |
| V44 | identity fields of an `errors` entry (8.2.1) | `original_path`, `session_id` or `burst_group` missing or of the wrong type on an entry that is or may be `sent_to_model`; `sent_to_model` missing or not a bool; or an entry that names no frame | identity unknown: the affected groups (8.2.1 scope table) are treated as `invalid` for eligibility, with `burst_frame_missing`; the gallery flags them; nothing is filled in. Ratings, apply and approval are unaffected |
| V45 | `original_sha256` | missing, null, or not 64 lowercase hex characters | **S** `[SKIP inconsistent result]`; approve **F**; never recomputed into the file |
| V46 | completion manifest (12.8) | missing, malformed (including a missing, negative or non-integer `referenced_preview_count` or `orphan_preview_count`), `run_id` mismatch, or CSV/gallery hash mismatch | output incomplete: apply **F**, approve (all forms) **F**, `review` and coverage report refuse. **A recount** of referenced or orphan previews that differs from the manifest is display only: a warning in `review`, the gallery and the coverage report, never incompleteness, and nothing is changed or deleted |
| V47 | `archive-in-progress.json` present in `<work>` (12.9) | present | every command except `recover-archive` refuses (**F**), for **every schema version**. Checked right after the lock is taken and **before** the results file is read or parsed (12.6 start-up order; 13.1 check 0b), so it applies even when V30 would fail. Apply writes no sidecar |

Rows are listed in ID order, V30–V47. The ID is a label; when a command runs these checks is given in 12.6 (start-up order), 13.1 (apply) and 12.3 (approve). Applicability: V30, V33 and V47 apply to files of every schema version. V33 contains only the path checks of 13.1 checks 1–3 and requires no field that the file's version lacks. All other rows (V31, V32, V34–V46) apply to v3 files only. In particular, the presence and type checks for `original_path`, `preview_path`, `session_id`, `burst_group` and `burst_sequence` are part of V36 and apply to v3 files only.

### 15.4 Analysis steps

1. **Preconditions:** validate `--output`, `--batch-size` and `--max-burst-size` (2–12). Then follow the 12.6 start-up order: identify the filesystem type of `<work>` and refuse known network filesystems; take the **exclusive** lock (non-blocking; refuse "busy" if held); refuse if `archive-in-progress.json` exists. Then refuse or archive per 12.7, plan batches and check P1–P4 (section 8.0). Any failure stops the run before previews or Claude calls.
2. **Capture time, original hashes and previews:** read capture times once (14.3). For each original, run the 14.3a procedure: lstat, hash, preview, lstat, hash. Any rejection goes to `errors` with `stage = original`, `sent_to_model = false`, and the image isn't sent to the model. Preview failures go to `errors` with `stage = preview`, `sent_to_model = false`.
3. **Per batch:** send the batch; a failed call adds one `stage = batch` entry per original, `sent_to_model = true`.
4. **Existing result checks:** paths outside the batch, duplicates, non-object results, and missing results go to `errors` with `stage = result` (V1). Nothing is silently dropped.
5. **Field validation:** the code checks every model field in table 15.2 (V2–V29).
6. **Consistency:** the code checks motion against `uncertain_checks` and against the flag `subject_blur` (V17, section 10.1).
7. **Base rating:** the code computes the base rating from the matrix (section 6.1). A model `rating` field is ignored (V26).
8. **Overrides:** if `proposed_moment_override = true`, the code checks `decisive_evidence` (V12a) and the cell (section 6.3), then sets `moment_override` and the final `rating` and `decision`.
9. **Evidence:** the code re-checks decisive evidence against the final rating (V12b) and compares `proposed_rating` with the final rating (section 6.6).
10. **Bursts:** after every batch has returned, the code applies B0, C1, C2 and B1–B6 to each group, using the complete `errors` list, and sets `burst_ranking_status` (section 8).
11. **Distinct moments:** the code validates each claim (section 7.1).
12. **Suggestions:** the code computes both suggestions (section 7).
13. **Review routing:** the code builds `review_reasons` (unique) and `needs_review` (section 11).
14. **Run summary:** the code computes the rating distribution and the drift warning (section 17).
15. **Write:** results JSON, CSV, gallery and finally the completion manifest, each atomically, in that order (12.8); release the lock.

---

## 16. Calibration

- **Separate datasets:** the calibration and final evaluation sets must be different. If calibration frames come from GTR, they are held out and excluded from the final GTR evaluation. Separation is checked by path **and** by SHA-256, read-only; a copy of the same photo in another folder counts as overlap (Q10).
- **Where human ratings live:** a calibration file **outside the source folder**, for example `calibration-ratings.json` with `{original_path: {human_rating, reason, capture_type}}`.
- **Read-only and marked:** calibration previews go to a marked `--output` folder. Calibration never writes XMP.
- **digiKam ratings:** existing ratings may be **read** as extra information, but they are never treated as ground truth automatically.
- **Recommended set:** 50–100 photos, a mix of keepers and rejects, covering all five capture types, and including at least one burst whose frames are unrelated (to exercise `not_comparable`).
- **Traceability:** every calibration run records the `rubric_version` and `evidence_rules_version` it used; results from different evidence rules versions are never pooled without saying so.
- **Metrics:**
  - exact agreement
  - ±1-star agreement
  - keeper recall
  - **false-reject rate**: the main safety number, meaning human keepers the model rated ≤ 2
  - confusion matrix
  - agreement by capture type
  - agreement on T and on E separately
  - how often groups end `validated`, `not_comparable` and `invalid`, and why

---

## 17. Distribution diagnostics

- **Denominator:** every percentage is computed over **evaluable results with ratings 1–5** in the run. Rating-0 results and `errors` entries are excluded from the denominator and reported separately as counts.
- **Reported:** the count and percentage of each rating 1–5, plus the counts of rating 0 and errors.
- **Guidance:** 5 stars about 1–2%, 4 stars about 5–10%.
- **Drift warning:** `calibration_drift_warning = true` when (count of 4 + count of 5) ÷ denominator > 15%; otherwise `false`. If the denominator is 0, the percentages and the warning are `null`, not 0 or `false`.
- **Never quotas:** a rating is never lowered to hit a target.

---

## 18. Batch size and turn limits (to be measured, not assumed)

**Current values:**
- batch size 6 by default (allowed 1–12)
- `--max-turns max(24, 2n+4)`
- timeout `max(360, 100n)` seconds
- comparison groups are kept whole in one batch (P2); a group larger than the batch size becomes its own batch of at most `max_burst_size` frames
- `--max-burst-size`: default 12, allowed 2–12 for v3; 0 (unlimited) is rejected (P3)

**Concern:** v3 roughly doubles to triples the output per image, so large bursts are the risk: up to 12 frames, each with per-frame evidence, a comparison mode, ranking or not-comparable evidence.

**Evaluation plan:** run on the **calibration set only**, with batch sizes 4, 6 and 8, and the largest burst group. Measure:
- output length per image
- truncated or invalid JSON
- missing results
- turns used
- wall-clock time

**Decision rule:** pick the largest batch size with zero truncations and zero missing results. If 12-frame bursts fail, lower the v3 maximum for `max_burst_size` and update P3, V31 and the CLI help together.

**No default changes until measured.**

---

## 19. Safety invariants (non-negotiable)

1. Never modify original RAW or JPEG data.
2. Never delete original photographs.
3. Never write XMP without explicit human approval. For ordinary items, that means the gallery review followed by `apply --yes`. For `needs_review` items, it means valid individual approval as well.
4. Never apply ratings during analysis.
5. Never treat previews as original files.
6. Never scan generated output as source photographs.
7. Never allow generated-output paths as apply targets.
8. Keep sessions separate.
9. Keep the output-folder marker protections.
10. Report missing or invalid results; never silently ignore them.
11. Fail closed: never replace an invalid required field with a guessed or default value, and never invent evidence or observations (section 15.1).
12. The approve command changes only the approval fields and the editorial suggestion of one item in a results JSON inside a Photo Curator output folder, after a verified backup and with an atomic replace. A failure **before** the replacement leaves the results file byte-identical. A failure **after** the replacement (folder fsync or verification) is reported as such, with the verified backup kept and no automatic rollback (12.3). It reads originals only to hash them, never opens them in a viewer or editor, and never writes originals, sidecars or XMP.
13. Every approval is bound to the exact original path, original SHA-256, preview path and preview SHA-256 at approval time. Any change invalidates it.
14. Analysis never overwrites existing results or approvals. Re-analysis needs a new output folder or an explicit, journalled archive, which moves and never deletes. A partial archive blocks the folder until `recover-archive` resolves it explicitly.
15. Readers never re-read image metadata; capture time comes only from the stored `capture_time`.
16. No rating is applied or approved for an original whose content differs from the analysis-time `original_sha256`. Originals are only ever read, never written.
17. No command acts on incomplete analysis output (12.8) or, for any schema version, on a partially archived folder (12.9); only `recover-archive` may act on a partial archive. Apply and the approval-changing forms of approve hold the exclusive lock, and readers hold the shared lock (12.6), so apply never uses an approval snapshot that has since changed.
18. Generated previews, including orphan previews, are never deleted automatically. Orphans are counted separately and never counted as referenced previews.
19. Every XMP sidecar write follows section 13.2. A failure before the replacement leaves the sidecar unchanged. A failure after it is reported, with any backup kept and no automatic rollback. Apply stops at the first sidecar failure, and the original RAW or JPEG is never opened for writing.

---

## 20. Implementation order (after approval only)

Every step is test-first, uses synthetic data, temporary folders and the fake Claude only, and never touches GTR, the pilot, originals or XMP. Each failure test asserts the exact response code, stored value, review reason or skip message.

1. **Constants and schema:** decision table, override cells, flags (3.3), review codes (11), evidence rules with `evidence_rules_version` and the pinning test (9.3), the stored-shape table (14.2), error entries (14.5).
2. **Rating, validation and routing**, table-driven:
   - all 25 T×E cells; the 3 override cells; invalid overrides (wrong cell, missing evidence, generic evidence)
   - `override_invalid` only with `proposed_moment_override = true`: (T4, E4) without a proposal and with missing decisive evidence → `evidence_missing`, **no** `override_invalid`; (T2, E4) with a proposal and missing evidence → `override_invalid` + `evidence_missing`, rating 2; (T3, E5) with valid evidence → rating 4, `moment_override`; proposal `null` → `field_invalid`, no `override_invalid`
   - rating 0 with every field in the 6.4 table, including null burst fields for a frame inside a burst, and each contradiction that goes to `errors`: among them an unevaluable result with `fatal_flaws = ["preview unreadable"]`, `fatal_flaws = null` or a missing `fatal_flaws` → rejected to `errors`; the same result with `fatal_flaws = []` and the reason in `unevaluable_reason` → accepted as rating 0
   - `rating_rule_mismatch`, including missing `proposed_rating` and a model `rating` field that must not be substituted
   - one test per row V1–V29, V-B0 and V-C2, each asserting the stored shape from 14.2
   - review reasons are unique and compared as sets
3. **Evidence and uncertain checks**, including V12a vs V12b, the decisive-evidence re-check when the final rating differs from the proposal, and a change to the evidence rules without a version bump failing the pinning test.
4. **Motion consistency:** every row of both 10.1 tables, asserting that neither field is changed. `subject_blur` with `intentional_pan`, `intentional_drag`, `uncertain` and `frozen` → `motion_conflict`, flag and class kept. `subject_blur` with `camera_shake` and `unintended_subject_blur` → no conflict. `flags = null` → the `subject_blur` check skipped, `field_invalid` present.
5. **Flags:**
   - every section 3.3 flag accepted; `none`, `poor_exposure`, `subject_clipped`, case variants such as `Missed_Focus`, and unknown values → `flags: null`, `field_invalid`; entries never filtered
   - a freeze test asserts that the code's flag set equals exactly the twelve names in 3.3, and that the prompt, the CSV writer and the validator all use that one constant
   - the code never adds, removes or changes a flag: a model result with a strong tilt and no `subject_cut_off` is stored with exactly the model's flags
   - `fatal_flaws` on evaluable results: a list of strings is stored unchanged and never affects T, E, `rating`, `decision`, review reasons, suggestions, approval or apply eligibility (identical outcomes with `[]` and with a non-empty list); a wrong type → `null`, `field_invalid`; no specificity check is applied
   - `fatal_flaws` on unevaluable results: only `[]` is accepted (V4; see step 2)
   - `fatal_flaws` validity effects (3.4): an evaluable result with `fatal_flaws = "x"` (not a list) → `null`, `field_invalid`, `needs_review = true`. An unevaluable frame inside a burst with `fatal_flaws = ["x"]` → result rejected, error entry with `sent_to_model = true`, its group `invalid` under B0. Changing only the text of a valid list never changes any outcome
6. **Distinct moments:** valid claim; missing or generic evidence; E = 1 with a claim; a claim on a single frame; a non-winner with a valid claim in an `invalid` group (`retain_low_rating` where rating is 1–2, never highlight-eligible, no `distinct_moment_highlight`).
7. **Burst preconditions:** P1–P4 at planning time (a group across sessions or over the size limit stops the run before any Claude call); `--max-burst-size 0` and 1 rejected, 2 and 12 accepted, 13 rejected.
8. **Burst comparison:**
   - single winner, co-best tie, invalid winner patterns, ranks, `near_duplicate_of` (same group, better ranked)
   - competition ranking under B4: 1, 1, 3 valid; 1, 1, 2 and 1, 2, 2 invalid; B3 failure clears ranks under B4 and then near-duplicate links under B5
   - B0: a group that lost its **lowest-ranked** frame to `errors` (no rank gap) → `invalid`, `burst_frame_missing`; a group with a preview-failure frame (`sent_to_model = false`) → can be `validated`
   - 8.2.1 identity: every `batch` and `result` error entry carries `original_path`, `session_id`, `burst_group` and `sent_to_model = true` from batch planning. A model result for a path outside the batch → every group in that batch `invalid`. An entry missing `burst_group` → every group in its batch `invalid`. An entry missing `burst_group` and `batch_index` → every group in its session `invalid`. An entry missing those and `session_id` → every group in the file `invalid`. `sent_to_model` missing → treated as possibly sent. Read-time V44 gives the same results, and the whole file is not refused
   - C1: (a) `burst_comparison` absent on one frame; (b) values `"Ranked"`, `"none"`, `null`, `true`, `1`, `[]`; (c) one `ranked` and one `not_comparable` in the same group. Each → group `invalid`, `burst_comparison_invalid` and `burst_ranking_invalid` on every ranked frame, stored shapes per 14.2
   - B6 makes the whole group invalid: one frame with a generic `rank_reason` in an otherwise perfect group (valid winner, ranks and links) → group `invalid`; every ranked frame gets `burst_ranking_invalid`; only the failing frame gets `evidence_generic`
   - `not_comparable`: valid claim with two and with five frames → status `not_comparable`, `burst_not_comparable` on every frame, no winner, ratings unchanged; mixed modes → `invalid`, `burst_comparison_invalid`; claim with one ranked frame → `invalid`; missing or generic `not_comparable_evidence` → `invalid`; ranking fields set in a `not_comparable` group → kept, `burst_comparison_invalid`, `invalid`; `not_comparable_evidence` non-empty in a `ranked` group → `field_invalid`
   - `rank_reason` missing, empty, non-string and generic on a winner and on a non-winner → `invalid`, `burst_ranking_invalid` on every ranked frame
   - `co_best_tie` only in `validated` groups: a valid tie in a group that fails B6 gets no `co_best_tie`
   - a winner in an `invalid` group is not highlight-eligible; `weak_burst_best` only in `validated` groups
   - groups with zero ranked frames (no status stored on any result) and with one ranked frame (validated only as B1)
9. **Rating independence:** absolute rating, decision and T/E are identical whether a group is `validated`, `not_comparable` or `invalid`.
10. **Suggestions:** every highlight path in the 7.2 table, across every approval state, including override 4 before and after approval and a `not_comparable` frame before and after approval.
11. **Distribution:** denominator, including a run with no evaluable rated results.
12. **Capture time:** EXIF with offset, without offset, with 1, 3 and 6 sub-second digits (truncation), missing, placeholder zeros, unparseable; burst detection on wall-clock time independent of the computer's timezone (run the tests under two different `TZ` values); untimed images never burst-grouped.
13. **Read-time checks:** one test per row V30–V47 (V44 is also tested in step 8, V45 in 16c, V46 in 16a and V47 in 16b), including hand-edited ratings, decisions, `needs_review`, suggestions, confidence vs `low_confidence`, `confidence_threshold` ≠ 0.6, W/K shapes without their review reason, malformed error entries, a status that differs from a re-run including B0, sessions mixed in one group, an invalid `capture_time`, and an `evidence_rules_version` mismatch.
14. **Error entries:** one entry per original for each of the four stages: `original` (14.3a; also covered in 16c), `preview`, `batch` and `result`. Each is checked for the correct `sent_to_model` (`false` for `original` and `preview`, `true` for `batch` and `result`), `batch_index` (`null` for `original` and `preview`, an integer otherwise), `burst_group`, `capture_time` and `preview_path`.
15. **Analyze refusal and archive (12.7):** existing `ratings-review.json` → refused before previews or Claude calls, folder byte-identical; existing backups only → refused; `--archive-existing` moves exactly the listed files and `previews` into a marked archive folder, deletes nothing, refuses on a name collision, prints the archived approval count; archived approvals fail PV1; a new analysis after an archive starts every item at `approved = false`, even for byte-identical originals and previews (U2); results written atomically (a simulated crash leaves no partial results file).
16. **Locking (12.6):**
    - the exclusive holders (analyze, `approve --rating`, `approve --revoke`, apply, `recover-archive --complete`, `recover-archive --rollback`) exclude each other and every reader; a second analyze is refused
    - **apply vs revoke:** while apply holds the lock, revoke and approve are refused ("busy"); while approve holds it, apply is refused before reading the results file
    - apply stops at the next item when the results file is changed by an outside program during the run, reports what it applied so far, and exits non-zero
    - the shared holders (`approve --show`, `review`, coverage report, `recover-archive` in report mode) run together; each is refused while any exclusive holder is active; `--archive-existing` and `recover-archive --complete` / `--rollback` are refused while any shared holder is active
    - `approve --show` takes the shared lock: it runs alongside `review`, and is refused while approve, revoke, apply or analyze holds the lock
    - **v1/v2 folders (14.7):** on a v1 and a v2 fixture folder, each of `apply`, `review`, `approve --show`, `approve --rating`, `approve --revoke` and the coverage report:
      - creates the lock file if it's missing, and takes the lock type listed in the 14.7 per-command table
      - refuses "busy" under contention, refuses a known network filesystem, and refuses when `archive-in-progress.json` exists, in the 12.6 start-up order
      - `approve` (all forms) and the coverage report then refuse because the file isn't v3, and leave the folder listing unchanged apart from a possible new lock file: no backup, temporary file or results change
    - **start-up order (12.6):** for every command, the network-filesystem check comes before the lock, and the lock before the journal check; no results file or preview is opened before all three pass
    - **network filesystems (U4):** with a synthetic `mountinfo`, each listed network type for `<work>` → every lock-taking command refuses before any change; a local type (`ext4`, `btrfs`, `xfs`, `tmpfs`) → proceeds; generic `fuse` or an unknown type → proceeds without claiming the folder is local; unreadable `mountinfo` → proceeds; nested mounts → the longest containing mount point decides
    - no command ever waits and no `--wait` option exists; the "busy" message tells the user to retry after the other command finishes, and the same command succeeds when re-run after the lock is released; a lock file that is a symlink is refused; a killed process doesn't block the next run; leftover `*.tmp-*` files are listed and never deleted or read
16a. **Output completeness (12.8):** simulated crashes after each of the four writes → output incomplete; apply, approve, `--show`, `review` and the report refuse; analyze into the folder refuses; nothing is repaired. Manifest with a wrong `run_id`, or CSV or gallery hash mismatch → incomplete. Approve changing the results file doesn't make complete output incomplete.
    - **Failure classes:** a folder fsync failure after the results, CSV or gallery replacement → durability failure reported, no later artifact and no manifest written, exit non-zero, output incomplete. A folder fsync failure after the manifest replacement → durability failure reported, exit non-zero, no rollback, no claim that the output is durable.
    - **Preview accounting:**
      - a run with only accepted images → `orphan_preview_count = 0` and `referenced_preview_count` = number of results + `batch`/`result` error entries with a preview
      - one `stage = original` rejection → its preview is counted as an orphan and not as referenced, its error entry has `preview_path = null`, and the output is still complete
      - a hand-placed extra file, a symlink and a sub-folder in `previews` → each counted as an orphan; the marker is ignored
      - a `preview_path` naming a missing file → adds to neither count
      - two entries naming the same file → counted once
      - the two counts always sum to the number of entries excluding the marker
      - a manifest missing either count, or with a negative or non-integer count → incomplete (V46)
      - an orphan added after analysis → recount differs, warning only, output still complete, nothing deleted
      - no command ever deletes an orphan; `--archive-existing` and `recover-archive` move orphans with `previews`
16b. **Archive journal and recovery (12.9):** a simulated rename failure at each position → journal left, folder blocked for every command except `recover-archive`, no analysis started, nothing deleted.
    - **Journal blocks every schema version (B-1):** with `archive-in-progress.json` present, (1) apply on a **v1** and on a **v2** results file is refused at 13.1 check 0b; (2) apply on a **v3** results file is refused at check 0b; (3) in both cases no sidecar is written or changed (the hashes and listing of every original's folder are identical before and after) and no backup or temporary file is created; (4) a results file that can't be parsed still gets the journal refusal, proving that 0b runs before 0c; (5) `review`, `approve` (all forms), the coverage report and `analyze` are refused too; (6) `recover-archive` in report mode runs, and `--complete` / `--rollback` run, so it remains the only command that can act
    - **Journal write:** a journal write that fails before its replacement → nothing moved; one that fails at the folder fsync → archive stops and the folder is blocked
    - **Report mode:** takes the shared lock; the journal, every archived and unarchived item, and the folder listing are unchanged, except that the lock file may be created if it was missing (12.6)
    - **Locks during recovery:** `--complete` and `--rollback` take the exclusive lock and are refused while report mode or another reader holds the shared lock
    - **Successful recovery:** `--complete` and `--rollback` succeed from each partial state; `--complete` touches only `pending` entries and `--rollback` only `done` entries; nothing outside the journal is touched; after success the journal is moved into the archive folder, never deleted
    - **Refusals before any move:** `conflict`, `lost`, a malformed journal, a file hash mismatch or a `previews` listing mismatch → refused **without moving anything** (checked before the first rename)
    - **Failure during recovery:** a rename failure stops at once and leaves the journal; there is no automatic retry
    - `previews` is never found half-moved
    - **v1/v2 folders (14.7):** with a journal in a v1 and in a v2 output folder, `recover-archive` in report mode and with `--complete` / `--rollback` behaves exactly as on a v3 folder: same lock types, network-filesystem refusal, no journal refusal for itself, the same renames, and the results file is only moved, never parsed or changed
    - **No journal (12.9):** in a folder without `archive-in-progress.json`, both with no archive history and after a completed or rolled-back archive: report mode prints "no archive recovery pending" and exits zero. `--complete` and `--rollback` exit non-zero with "no archive in progress". In all three cases the folder listing and every file hash are unchanged except a possibly new lock file, and `archive-journal.json` / `archive-journal.rolled-back.json` are not read
    - **Schema boundary (12.9):** with a valid journal, recovery gives the same result when the results file is (a) v1, (b) v2, (c) v3, (d) not valid JSON, or (e) has an unknown `schema_version`, and when the manifest is missing or corrupt. A test double asserts that the results file, CSV, gallery and manifest are never parsed: they are only hashed as raw bytes and renamed. No V30–V46 check is run
16c. **Original hash (14.1, 14.3a, 12.1, 12.3, 13.1):**
    - analysis (14.3a), using a test double for the preview generator: unchanged original → accepted, `original_sha256 = H1`; content changed during preview generation (H1 ≠ H2) → `stage = original`, "content changed"; metadata changed with the same content (S1 ≠ S2, H1 = H2) → `stage = original`, "metadata changed"; read failure at step 2 or 5 → `stage = original`; not a regular file at step 1 → `stage = original`; every rejection has `sent_to_model = false`; the orphan preview is not deleted and no result refers to it
    - approve refuses when the current hash differs from `original_sha256`; a stored approval with `approved_original_sha256 ≠ original_sha256` → `malformed`; V45 for a missing or malformed `original_sha256`
    - apply check 12, with a counting test double for the hash function: items skipped at checks 1–11a are **never** hashed; an eligible item is hashed exactly once; no sidecar exists before its check 12 passes; mismatch on an unapproved item → `[SKIP original changed since analysis]`; mismatch on an approved item → `[SKIP approval invalidated: original changed]`; unreadable → `[SKIP original unreadable]`
    - `review`, the gallery and the coverage report never call the hash function, and label the stored hash "not verified"; an approval passing checks 1, 2 and 4–6 is shown as "approved (original not verified)"
    - analysis hashes each accepted original exactly twice (counting test double) and offers no option to skip or weaken the hashes
    - the preview binding tests in step 21 still pass unchanged
17. **Strict prompt and response format,** including `burst_comparison`, `not_comparable_evidence`, the flags list with the 3.3 meanings, the RAW-recoverable rules, and these definitions: `subject_cut_off` only for capture-time cut-offs; `subject_blur` only for unintended blur; residual noise judged after RAW noise reduction; `fatal_flaws` as described in 3.4, including that an unevaluable image returns `fatal_flaws = []` and states why it couldn't be judged only in `unevaluable_reason`. Tested against the fake Claude only.
18. **Gallery, CSV and review output:**
    - new CSV columns in the stated order; flags names
    - gallery labels for review reasons, approval state, original path, preview hash, the "analysis-time SHA-256, not verified" label, capture time, "unvalidated" and "not comparable"; the v3 `review` hint pointing to `approve --show`
    - **relative preview paths (code change):** the gallery writes every preview reference as `previews/<file name>`, relative to the HTML file. Tests parse every `img src` and link in a generated gallery and assert that none is absolute or a `file://` URL
    - **archived gallery:** generate a gallery in a temporary work folder with synthetic previews, archive it with `--archive-existing` (12.7, 12.9), then resolve every preview reference relative to the **archived** HTML file's location and assert that each one exists, is a regular file and is the expected preview (matching SHA-256). Also assert that none resolves into the new work folder's `previews`
    - the same check after `recover-archive --rollback`: the gallery back at its original location resolves every preview again
19. **Apply check order (13.1):**
    - one test per row, including check 12 as the last per-item check, followed by check 13 (the results-file re-hash) immediately before the write, and the stop at check 13
    - items that fail several checks print only the first message, and the summary counts skips per message
    - an approved item that fails both binding check 4 and the original hash is skipped at check 10 with check 4's message, and its original is never hashed (12.1, "Binding checks inside apply")
    - for a set of v1 and v2 fixture files run to completion, the items written and their ratings are identical to today's apply, and only the differences documented in 14.7 appear
    - apply never reads or derives suggestions: changing `documentation_suggestion` or `editorial_suggestion` in a v3 file changes no apply outcome (7.2, V37)
    - **v1 regression (V33, N-1):** a v1 fixture whose items have only `path`, `rating` and the other v1 fields, with no `original_path`, `preview_path`, `session_id`, `burst_group` or `burst_sequence`:
      - every valid item is written exactly as today's apply writes it
      - no item is skipped as `[SKIP inconsistent result]`, and none for a missing field
      - check 1 uses `path` as the target; no item is skipped as `[SKIP ambiguous original_path/path]`, because `original_path` is absent
      - check 2 makes no `preview_path` comparison; no item is skipped as `[SKIP not an original image]` because `preview_path` is absent. An item whose `path` is generated or has an unsupported extension is still skipped with that message
      - the same item in a v3 fixture without `preview_path` passes check 2 and is skipped at check 4 (V36), so v3 behaviour is unchanged
      - V33 path checks still apply: an item whose `path` is inside generated output, is generated, or has an unsupported extension is skipped with the same message as today
      - the same file converted to a v3 fixture with those fields removed → every item `[SKIP inconsistent result]` (V36), proving that the field requirement applies to v3 only
    - **v2 regression:** a v2 fixture with `original_path`, `preview_path` and `session_id` but no `burst_sequence` → written as today; `original_path` ≠ `path` → `[SKIP ambiguous original_path/path]`
19a. **Sidecar write procedure (13.2), for v1/v2 and v3 items:**
    - success: sidecar written by temporary file and `os.replace`, folder fsynced, re-read verified; an existing sidecar under `--include-existing` is backed up first, and the backup's hash is verified
    - check 11a: an existing sidecar that is a symlink, a directory, or a path equal to the original → `[SKIP sidecar not a regular file]`, original never hashed, nothing written
    - backup failure (unwritable folder, existing backup name, hash mismatch) → partial backup removed, sidecar byte-identical, apply stops, exit non-zero
    - before-replacement failures (temporary write, its fsync, its read-back check, an `os.replace` that raises) → temporary file removed, sidecar byte-identical (or still absent), `[ERROR sidecar write failed]`, apply stops, exit non-zero, items written earlier untouched
    - durability failure (folder fsync after a successful replace) → `[ERROR sidecar written but not confirmed durable]`, verification not run (test double asserts no re-read), backup kept, no second replace, apply stops, exit non-zero
    - verification failure → `[ERROR sidecar written but verification failed]`, backup kept, no rollback, apply stops, exit non-zero
    - in every case the original is never opened for writing (a test double asserts read-only opens only) and its bytes are unchanged; a temporary file left behind is reported and isn't deleted
20. **Preview checks (12.5):** each of PV1–PV6 failing on its own (wrong folder, unmarked folder, symlink, wrong extension, wrong derived name, preview equal to the original) → apply skip message and approve refusal; `--open-preview` refuses to open.
21. **Approval validation, the approve command and apply blocking.** Every failure test asserts that the command exits non-zero. Failure tests **before the replacement** (12.3 steps 1–7 and a failing `os.replace`) also assert that the results file is byte-identical afterwards (by SHA-256). Failure tests **after the replacement** assert the behaviour in the 12.3 failure-class table instead:
    - **rating changes:** approve, then change the rating → `invalidated: rating changed`, apply skips; re-approval with the new rating succeeds directly from `invalidated`
    - **original changes:** original content changed after approval (synthetic file) → `invalidated: original changed`; original path changed → same message
    - **re-approval table (12.3):** every cell, including approve on `approved` refused and approve on `invalidated` allowed
    - **revocation:** revoke an approved and an invalidated item → all seven fields reset, state `pending`, suggestion recomputed, backup created; revoke refused on `pending`, `not_required` and `malformed`
    - **stale approvals:** preview content changed, preview path changed, preview missing, preview replaced by a symlink → the matching message; approve refused when `--preview-sha256` or `--rating` doesn't match the current state
    - **malformed fields:** each rule in 12.2 broken on its own (non-bool `approved`, string `"true"`, missing `approved_rating`, bool or out-of-range `approved_rating`, `approved_at` without `Z` or unparseable, relative paths, wrong-length or uppercase hashes, `approved = false` with any non-null field, an approval on a `needs_review = false` item, an approval on a rating-0 item, a missing approval field) → `malformed`, apply skips, approve refuses, nothing repaired
    - **concurrent modifications:** results file changed between read and replace → refused, and the other writer's content is still in place; lock already held → refused
    - **backup failures:** unwritable folder, existing backup name, backup hash mismatch → no write, partial backup removed, results file unchanged
    - **before-replacement failures:** simulated failure of the temporary write, its fsync, its read-back validation, and an `os.replace` that raises → temporary file removed, results file byte-identical, backup kept
    - **after-replacement durability failure:** `os.replace` succeeds, then the folder fsync fails (simulated) → message "approval written but not confirmed durable", exit non-zero, step 10 not run (a test double asserts no re-read of the results file), results file holds the new document (not byte-identical to the old one), backup byte-identical to the original, no second replacement attempted, no temporary file left
    - **after-replacement verification failure:** the final re-read or validation fails (simulated) → message "approval written but verification failed", exit non-zero, backup byte-identical to the original, no rollback attempted
    - a successful run completes steps 1–10 and exits zero
    - **isolation:** only the target item's approval fields and `editorial_suggestion` differ after a successful run
    - **safety:** originals are only read (fake originals and sidecars hashed before and after; a test double asserts the original is opened read-only and never passed to a viewer); refuses a symlinked results file, a results file outside an output folder, and ambiguous or unknown original paths; accepts no bulk input
    - **`--show`:** prints the exact original path and hash, preview path, preview checks and preview hash, and writes nothing else. The folder listing and every file hash are unchanged; the only permitted new entry is `ratings-review.json.lock`, created if it was missing (12.6). A second run with the lock file already present leaves the listing fully unchanged. It takes the **shared** lock: it is refused ("busy") while an exclusive holder is active, runs alongside another shared holder, and blocks approve, revoke and apply while it runs. It reports binding checks in the 12.1 diagnostic order
22. **Coverage report:** counts and timeline gaps from stored `capture_time` only (a test double asserts no metadata reader is called); `errors` entries without a `session_id` (for example `stage = original` or `preview`) counted under `unknown / no session`, with the total error count equal to the number of `errors` entries; the only file the report may create is the lock file (12.6); untimed list; mixed offsets in a session → gaps not computed, warning shown; no automatic claims in the wording; referenced and orphan preview counts from the manifest plus a recount, orphan names listed with the "not deleted" label, a differing recount shown as a warning; no hash function and no delete or move operation is ever called.
23. **Documentation:** update the CLI help, `README.md`, `CLAUDE.md` (commands section: `approve`, `--archive-existing`, `recover-archive`, the existing-results and incomplete-output refusals, the "busy" refusal, `--max-burst-size` range) and the photo-curator skill documentation to match this specification. These files are changed only in this step, after approval.
24. Read-only calibration on a separate set, plus the batch-size measurements (section 18).
25. Compare against the human ratings and accept the results.
26. Only then, plan a staged analysis of the full GTR collection.

---

## 21. Resolved questions (proposed defaults accepted on 2026-11-15)

| # | Question | Decision | Where it applies |
|---|---|---|---|
| **Q1** | How is approval recorded? | A per-item `photo-curator approve` command with JSON backup and atomic update; no bulk mode | 12.3 |
| **Q2** | Does a non-winning burst frame claimed as a distinct moment need review before it can be a highlight? | Yes: `distinct_moment_highlight`, approval required | 7.2, 11 |
| **Q3** | Must `near_duplicate_of` point to a better-ranked frame? | Yes; the same or a worse rank is invalid | 8.2 (B5) |
| **Q4** | Should preview-generation failures also appear as rating-0 results? | No; they stay in `errors` (`stage = preview`). Rating 0 is for previews that exist but can't be evaluated | 6.4, 14.5 |
| **Q5** | Coverage report parameters | Gap = no rating ≥ 3 photo for 10 minutes in a session; usable = rating ≥ 3; untimed images listed separately | 7.3 |
| **Q6** | Evidence thresholds | 20 characters; generic-phrase list in code with tests; adjusted during calibration under a new `evidence_rules_version` | 9.3 |
| **Q7** | T or E missing or invalid on an evaluable result | Reject to `errors`, left unrated | 6.4, V6 |
| **Q7b** | `proposed_rating` missing | Stored as null; counts as `rating_rule_mismatch` | 6.6, V13 |
| **Q8** | Keep apply's `--min-rating` default of 1? | Yes | 12.4 |
| **Q9** | Should approval store who approved? | No; only `approved_at` | 12.2 |
| **Q10** | How calibration and evaluation are kept separate | Path and SHA-256, read-only; copies count as overlap | 16 |
| **Q11** | Confidence boundary | `< 0.6` triggers review; exactly 0.6 does not | 11 |
| **Q12** | Does every rating 5 need individual approval? | Yes | 7.2, 12.4 |

---

## 22. Revision history and decision status

### 22.1 Earlier revisions

| Revision | Main changes |
|---|---|
| 1 | First complete v3 specification: deterministic T×E matrix, overrides, suggestions, burst ranking, evidence, review routing, approval, schema v3 |
| 2 | Override 4 becomes `highlight` only after approval; `distinct_moment_evidence`; two decisive-evidence checks; exact unevaluable values; approval field rules and the approve command; distribution denominator; fail-closed rule; Q1–Q12 accepted |
| 3 | `rank_reason` validation and `burst_ranking_status`; motion vs `uncertain_checks` consistency; null burst fields for unevaluable frames; approval binding to original path, preview path and preview hash; `--show`; the full validation tables V1–V39 |
| 4 | Standard competition ranking (1, 1, 3); distinct-moment path closed in invalid groups; decisive-evidence check before the override decision; rating-0 approvals malformed; lock scope; CSV and review-hint changes; file-level display fields. Its header referred to a section 22 that didn't exist |

### 22.2 Revision 5: audit findings and corrections

| # | Audit finding (revision 4) | Correction in revision 5 | Sections |
|---|---|---|---|
| A1 | Header referred to a missing section 22 | Section 22 added with the full revision history and this mapping; the source revision and its hash are named in the header | header, 22 |
| A2 | A group that lost its lowest-ranked frame to `errors` could still validate | B0: any `errors` entry with `sent_to_model = true` for the group makes it `invalid` (`burst_frame_missing`); error entries now carry `burst_group` and `sent_to_model`; V39 re-runs B0 at read time | 8.2, 8.3, 14.5, 15.2, 15.3 |
| A3 | Capture time not stored; the coverage report would re-read metadata | Code-generated `capture_time` with explicit offset rules and null for untimed; the report uses only the stored field; invariant 15 | 7.3, 14.3, 19 |
| A4 | W/K outcomes not allowed by the schema | Stored-shape table for every model field (valid, W, K, unevaluable); `editorial: null` defined; V36 checks shapes and their review reasons | 14.2, 15.1, 15.3 |
| A5 | `not_applicable` could never be stored | Removed. Zero-ranked-frame groups store no status; one-ranked-frame groups validate only as B1; new `not_comparable` status | 8.3 |
| A6 | Forced winners for unrelated frames | Explicit `not_comparable` mode with required evidence (C1, C2), review reason `burst_not_comparable`, no winner, ratings unchanged | 8.1–8.3, 7.2, 11 |
| A7 | V12 could add `override_invalid` without a proposal | V12 split into V12a (proposal), V12b (final rating) and V12c (type only); `override_invalid` only with `proposed_moment_override = true` | 6.3, 9.2, 11, 15.2 |
| A8 | Re-analysis silently wiped approvals | `analyze` refuses when results or backups exist; new-output and `--archive-existing` workflows; atomic results write; invariant 14 | 12.7, 15.4, 19 |
| A9 | `preview_path` not checked against the output folder | PV1–PV6, including the exact `<work>/previews` folder, marker, no symlink, regular `.jpg`, derived name and hash; used by `--show`, `--open-preview`, approve and apply | 12.5, 12.1, V42 |
| A10 | Implementation order missed production changes | Section 20 rewritten: every rule, CLI change, output change and documentation update has a step and tests | 20 |
| A11 | Evidence rules not versioned | `evidence_rules_version` at file level; pinning test; V43 defines reader behaviour on mismatch; calibration records it | 9.3, 14.4, 15.3, 16 |
| A12 | `--max-burst-size 0` allowed unlimited groups | Rejected for v3; allowed range 2–12 (P3, V31, CLI help) | 8.0, 14.4, 18 |
| A13 | B2 added `co_best_tie` unconditionally | Only when the group ends `validated` | 8.2, 11 |
| A14 | Whether readers use stored confidence | Readers use it only to verify routing (V35); threshold fixed at exactly 0.6 (V31); readers never re-route | 11, 15.3 |
| A15 | Crop and exposure vs E and T unclear | RAW-recoverability table; T judged after RAW correction; E judged on the best crop inside the frame; capture cut-offs count | 3, 3.1, 4 |
| A16 | Apply skip order undefined | Ordered check table with one message per skipped item | 13.1 |
| A17 | Re-approving an approved item undefined | Re-approval and revocation table for every state | 12.3 |
| A18 | Changed originals not detected | `approved_original_sha256` stored at approval and checked at apply; original read-only for hashing only | 12.1–12.3, 14.1, 19 |
| A19 | Flags list incomplete and ambiguous | Complete flags list with definitions; `none`, `poor_exposure` and `subject_clipped` replaced | 3.3, V15 |
| A20 | Session and batch preconditions implicit | P1–P4 as explicit preconditions, checked at planning and at read time (V41) | 8.0, 15.3 |
| A21 | Lock scope and recovery | Lock file rules, holders table, crash behaviour, leftover temp files, local-filesystem requirement | 12.6 |
| A22 | Documentation, CSV, help and skill not planned | CSV columns, gallery, `review` behaviour and CLI help listed; README, CLAUDE.md and skill documentation updated in step 23 after approval | 14.6, 20 |

**Revision 5, amendment 1 (Section 8 correction, 2026-11-15):**

| # | Change | Sections |
|---|---|---|
| A23 | Error entries for frames sent to the model must keep `original_path`, `session_id`, `burst_group` and `sent_to_model`, copied from batch planning. If burst identity can't be established, the affected groups are invalidated by batch, then session, then the whole file. An identity problem no longer refuses the whole file (V31 narrowed; new V44) | 8.2 (B0), 8.2.1, 8.3, 14.5, 15.2 (V-B0), 15.3 (V31, V44), 20 |
| A24 | B6 states explicitly that any `rank_reason` failure (missing, wrong type, empty or generic) makes the whole group invalid, with `burst_ranking_invalid` on every ranked frame in addition to the failing frame's evidence reason | 8.2 (B6), 8.3, 15.2 (V24), 20 |
| A25 | C1 failures are listed explicitly: (a) missing `burst_comparison`, (b) any value other than exactly `ranked` or `not_comparable`, (c) mixed values in one group. Each invalidates the group | 8.2 (C1), 8.3, 15.2 (V28), 20 |

**Revision 5a, amendment 2 (sections 12.6–12.9 and original binding, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A26 | Apply took no lock, so it could act on an approval snapshot after a concurrent revoke (blocking) | Apply takes the exclusive lock before reading and holds it to the end. Every command uses non-blocking locks and refuses on contention. Apply re-hashes the results file before each write and stops on an outside change | 12.6, 13.1 (0a), 19 (17), 20 (16) |
| A27 | Readers could see a partly archived `previews` folder | Readers (`--show`, `review`, coverage report) take the shared lock. Archive is exclusive and moves `previews` in one rename. Defines the static gallery's behaviour | 12.3, 12.6, 20 (16) |
| A28 | JSON, CSV and gallery weren't atomic as a group | Each is atomic on its own. A completion manifest, written last and tied to the results by `run_id`, defines complete output. Incomplete output is detected and refused by every command, and never repaired | 12.8, 14.4 (`run_id`), 14.6, 15.3 (V46), 15.4 (15), 20 (16a) |
| A29 | A partial archive had no defined failure behaviour | The archive is journalled: it stops on the first failure, and the folder stays blocked while `archive-in-progress.json` exists. Entry states come from the filesystem. Explicit `recover-archive` with report, `--complete` and `--rollback`, which only rename and never delete. The refusal list is extended | 12.7, 12.9, 15.3 (V47), 19 (14), 20 (16b) |
| A30 | The approval's original hash had no analysis-time counterpart | `original_sha256`, computed read-only at analysis with a size and mtime check around preview generation, and a new error stage `original`. Approve requires the current hash to equal it. Apply checks it for every v3 item. A mismatched stored approval is malformed. The preview binding is unchanged | 2, 6.4, 12.1, 12.2, 12.3, 13.1 (7b), 14.1, 14.5, 15.3 (V45), 15.4 (2), 19 (16), 20 (16c) |

**Revision 5b, amendment 3 (U12–U16 and the gallery plan, 2026-11-15):**

| # | Decision | Change | Sections |
|---|---|---|---|
| A31 | U12: no hashing of all originals on apply | Original hash moved to apply check 12, run only for items that passed checks 0–11, once per item, before any sidecar write. Binding check 3 is evaluated there. Old check 7b removed. Statement of what check 12 proves | 12.1, 13.1, 20 (16c, 19) |
| A32 | U13: review and coverage show the stored hash unverified | `review`, the gallery and the coverage report never hash originals. They show `original_sha256` labelled "analysis-time SHA-256, not verified against the current file", and show approvals as "approved (original not verified)" | 7.3, 12.1, 14.1, 14.6, 20 (16c) |
| A33 | U14: no `--wait` | Non-blocking, immediate refusal kept; the message and CLI help tell the user to retry | 12.6, 14.6, 20 (16) |
| A34 | U15: keep `recover-archive` | Report-only default with no changes; `--complete` only `pending` entries and `--rollback` only `done` entries; full verification before the first rename; stop on a rename failure with no retry; nothing deleted | 12.9, 20 (16b) |
| A35 | U16: SHA-256 as the integrity mechanism | New 14.3a: lstat, hash, preview, lstat, hash. Accept only if H1 = H2 and S1 = S2. Rejection table; orphan preview kept; limits of the guarantee stated | 14.1, 14.3a, 14.5, 15.4 (2), 20 (16c) |
| A36 | Gallery plan omission | Step 18 requires relative `previews/<name>` references (a code change) and tests resolving previews from the archived and the rolled-back gallery locations | 14.6, 20 (18) |

**Revision 5c, amendment 4 (U17 and U18, 2026-11-15):**

| # | Decision | Change | Sections |
|---|---|---|---|
| A37 | U17: accept the hashing cost | The roughly 340 GB of extra reading for the first full GTR analysis is documented as an intentional integrity cost. No size/mtime-only or fast mode. The list of which commands hash originals is explicit; `review`, the gallery and coverage never re-hash. The SHA-256 procedure is unchanged | 14.3a, 20 (16c) |
| A38 | U18: separate orphan previews | Manifest field `previews_count` replaced by `referenced_preview_count` and `orphan_preview_count`, with exact definitions. New `preview_path` on error entries (set for `batch` and `result`, null for `original` and `preview`). Orphans never count as referenced and never make output incomplete; recount differences are warnings only. Orphans are never deleted (new invariant 18). The coverage report shows and lists them | 7.3, 12.8, 14.3a, 14.5, 15.3 (V46), 19 (18), 20 (16a, 22) |

**Revision 5d, amendment 5 (U1–U11, 2026-11-15):**

| # | Decision | Change | Sections |
|---|---|---|---|
| A39 | U1: whole-group comparison mode | Stated as a decision: a group is entirely `ranked` or entirely `not_comparable`; mixed is `invalid` under C1 (c); no sub-groups | 8.1 |
| A40 | U2: no approval carry-over | Approvals are never copied, migrated or matched into a new analysis; fresh individual approval is always required. Test added | 12.4, 20 (15) |
| A41 | U3: mixed offsets | No gaps at all, not even partial, for a session with mixed offsets; explicit warning text | 7.3 |
| A42 | U4: local filesystem | Best-effort refusal of known network filesystem types from `/proc/self/mountinfo` by every lock-taking command; the limits are stated and there is no claim of universal detection. CLI help and tests added | 12.6, 14.6, 20 (16) |
| A43 | U5: apply-time hashing | Revision 5c approach kept unchanged (check 12 only) | 13.1 (no change) |
| A44 | U6: time source | Camera capture-time metadata; explicit offset from `OffsetTimeOriginal`; otherwise timezone-unknown; never the computer's timezone. Other offset sources listed as unused | 14.3 |
| A45 | U7: frozen flags | The twelve 3.3 flags frozen; changes need a new `rubric_version`; a freeze test added. **Corrected in revision 5e (A53):** revision 5d stated that every reference had been audited and found consistent. That claim didn't hold, because the 3.1 tilt row used `subject_cut_off` for a crop caused by straightening, which contradicts the 3.3 meaning "outside the frame at capture" | 3.3, 20 (5) |
| A46 | U8: skip messages | New messages kept. The four intentional v1/v2 differences are listed; v1/v2 skip decisions and written ratings unchanged. Test added | 14.7, 20 (19) |
| A47 | U9: one ranked frame | Validated only if B0, C1 (`ranked`), B1, B4, B5 (`null`) and B6 all pass; never treated as a non-burst frame | 8.3 |
| A48 | U10: `--archive-existing` | Name and scope kept unchanged | 12.7 (no change) |
| A49 | U11: evidence rules version | Initial value fixed at `"v3.0"` | 14.4 |

**Revision 5e, amendment 6 (audit blockers, clarifications and document fixes, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A50 | B1: the approve "byte-identical on failure" guarantee was false once `os.replace` had succeeded | Steps split into replacement (8), durability (9) and verification (10). Failure-class table: failures before the replacement leave the file byte-identical; durability and verification failures after it are reported as such, with the verified backup kept and no automatic rollback; manual restore only. The same classes are stated for analyze artifacts and the archive journal. Invariant 12 and the tests are corrected | 12.3, 12.6, 12.8, 12.9, 19 (12, 17), 20 (16a, 16b, 21) |
| A51 | B2: `recover-archive` was exclusive in the lock table but shared in report mode | Report mode is shared; `--complete` and `--rollback` are exclusive, with all conflict and hash checks still done before the first rename. Lock table, 12.9 and tests aligned | 12.6, 12.9, 20 (16, 16b) |
| A52 | B3: the tests said `approve --show` takes no lock | `approve --show` takes the shared lock everywhere; the approval-changing forms are exclusive. The lock table names the forms explicitly; the test is now a shared-lock test | 12.3, 12.6, 20 (16, 21) |
| A53 | B4: `subject_cut_off` was used for straightening crops | `subject_cut_off` only for capture-time cut-offs. Correction crops are composition limitations, described in `editorial.evidence`. The code never adds flags. A45 corrected | 3.1, 3.3, 20 (5, 17), 22.2 (A45) |
| A54 | I1: `subject_blur` vs intentional motion blur | `subject_blur` only for unintended, relevant blur, and never with intentional motion, `frozen` or `uncertain`. New consistency table routes contradictions to review (`motion_conflict`, V17) without changing any field | 3.3, 10.1, 11, 15.2 (V17), 15.4 (6), 20 (4, 17) |
| A55 | I2: moderate noise and T3 | T is judged on residual noise after the applicable RAW noise reduction. The reducible part goes in `recoverable_in_raw`; only the residual can limit T, and at most to T3 | 3 (T3 row), 3.1, 20 (17) |
| A56 | I3: binding-check order vs apply order | The 12.1 order is diagnostic, used by `approve --show`. Apply runs binding checks 1, 2, 4–6 at its check 10 and check 3 (original SHA-256) only at its check 12, just before writing; the diagnostic order never causes extra hashing | 12.1, 20 (19) |
| A57 | I5: `fatal_flaws` had no defined meaning | Kept as a limited descriptive field (new 3.4): a single flaw that alone makes the frame unusable; `[]` otherwise; never affects any score, decision, suggestion, approval or apply; type-checked only. Kept rather than removed because it is already in the schema, unevaluable rules, V4, V15 and the tests, and it gives the reviewer the reason behind a T = 1 or E = 1 without adding behaviour. **Later corrected:** by A59 (revision 5e1), which requires `[]` on unevaluable results; and by A61 (revision 5f), which narrows "never affects" to the field's content and states the effects of an invalid value | 3.4, 20 (5, 17) |
| A58 | N1–N4: document fixes | Header no longer says decisions are open; 22.3 retitled, with the empty placeholder table removed; A45 corrected (A53); step 13 covers V30–V47; step 19 reworded | header, 20 (13, 19), 22.2, 22.3 |

**Revision 5e1, amendment 7 (`fatal_flaws` on unevaluable images, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A59 | Revision 5e (A57) let an unevaluable image store its failure reason in `fatal_flaws`, mixing "couldn't be judged" with "judged and found fatally flawed" | `fatal_flaws` is for evaluable images only. On an unevaluable image it must be exactly `[]`, and the reason goes only in `unevaluable_reason`. Anything else on an unevaluable result rejects it under V4 (so `fatal_flaws` leaves the V4 exception list), while V15 keeps applying to evaluable results. `fatal_flaws` still never affects T, E, `rating`, `decision`, review routing, suggestions, approval or apply eligibility. **Later corrected by A61 (revision 5f):** the last sentence holds for the field's content only; an invalid value or a breach of the unevaluable rule does affect acceptance, review routing and burst status | 3.4, 6.4, 14.1, 14.2, 15.2 (V4, V15), 20 (2, 5, 17) |

**Revision 5f, amendment 8 (full-file audit of revision 5e1, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A60 | B-1: apply on v1/v2 files skipped the archive-journal check, contradicting V47 and invariant 17 | A start-up order for every command and every schema version: network filesystem, then lock, then journal, then the results file. Apply's checks become 0a (filesystem, lock), 0b (journal, every version, before parsing), 0c (V30), 0d (v3: V31 and completeness). V47, 12.9 step 2 and invariant 17 state "every schema version". Tests prove v1, v2 and v3 apply are refused with no sidecar written, and that `recover-archive` is the only command that acts | 12.6, 12.9, 13.1, 14.7, 15.3 (V47), 19 (17), 20 (16, 16b) |
| A61 | I-1: `fatal_flaws` was said to "never" affect anything, contradicting V4/V15 | 3.4 separates content (no effect) from validity (`field_invalid` and routing on evaluable results; rejection, and through B0 group invalidation, on unevaluable results). Unevaluable `[]` rule unchanged. A57 and A59 marked as corrected | 3.4, 20 (5), 22.2 (A57, A59) |
| A62 | I-2: v1/v2 compatibility listed apply only | 14.7 split into what stays the same (rating interpretation) and seven operational differences, each with the commands affected and the reason: messages, lock acquisition, lock-file creation, network-filesystem refusal, journal refusal, results re-hash, sidecar procedure. 12.6 states lock-file creation for every version | 12.6, 14.7, 20 (16, 19) |
| A63 | M-1, M-2, M-3: error stages | 8.2.1 names `original` and `preview` as not sent; `batch_index` defined for all four stages; step 14 tests all four stages | 8.2.1, 14.5, 20 (14) |
| A64 | M-4: B7 vs V20 and 14.2 | B7 now matches V20 and 14.2: the six burst fields are W (`null`), non-empty `not_comparable_evidence` is K; both `field_invalid`; no group affected | 8.2 (B7) |
| A65 | M-5: 7.2 said apply derives suggestions | Only the gallery, `review` and the report derive them for display; apply never reads or derives them. Test added | 7.2, 20 (19) |
| A66 | M-6: step 10 after a failed step 9 | Step 9 failure stops at once; step 10 runs only after step 9 succeeds; table and tests aligned | 12.3, 20 (21) |
| A67 | M-7: 3.2 and the 10.1 title omitted `subject_blur` | Both now name `uncertain_checks` and `subject_blur` | 3.2, 10.1 |
| A68 | M-8, M-9: header and history wording | Header names amendments 6, 7 and 8; 22.3 names the revisions; A57 and A59 point to their corrections | header, 22.2, 22.3 |
| A69 | M-10: `decisive_evidence` validation ID | 14.1 lists V12a, V12b and V12c with their triggers | 14.1 |
| A70 | M-11: diagnostic order vs apply order | The 12.1 binding-check table gains a "where apply runs it" column and the state table states apply order | 12.1 |
| A71 | M-12: 15.4 step 1 vs 12.6 | Step 1 follows the 12.6 start-up order explicitly | 15.4 |
| A72 | M-13: XMP sidecar writes had no defined failure behaviour | New 13.2: regular-file check (new apply check 11a), verified backup, temporary file, `os.replace`, folder fsync, verification; three failure classes; apply stops at the first failure; no automatic rollback; original never opened for writing. New invariant 19 and test step 19a | 13, 13.1, 13.2, 14.6, 14.7, 19 (19), 20 (19a) |
| A73 | M-14: validation ID order | 15.2 lists V-B0 before V-C2; 15.3 lists V30–V47 in order; both tables state that the ID is a label and where the run order is defined, plus which rows apply to v1/v2 files | 15.2, 15.3 |

**Revision 5g, amendment 9 (final audit of revision 5f, findings N-1 to N-7, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A74 | N-1: V33 was declared to apply to every version but required fields v1 files don't have | V33 now holds only the cross-version path checks (13.1 checks 1–3), using `original_path` if present and otherwise `path`, and `preview_path` only if present. The presence and type of the v3 path, session and burst fields are part of V36 (v3 only). 13.1 check 4 lists V34–V36 and V45. Applicability note rewritten. The 14.1 validation column now points the session and burst fields to V36 instead of V33. v1 and v2 regression tests added | 13.1, 14.1, 15.3 (V33, V36, applicability note), 20 (19) |
| A75 | N-2: read-only commands said "writes nothing" but may create the lock file | 12.6 states that creating the lock file is the only side effect a read-only command may have. `--show` and report mode updated, and tests permit only that new entry | 12.3, 12.6, 12.9, 20 (16b, 21, 22) |
| A76 | N-3: errors without `session_id` in per-session counts | Counted under `unknown / no session`; never dropped or guessed; test added | 7.3, 20 (22) |
| A77 | N-4: dangling "On failure" reference in 13.2 | Points to the "Before replacement" row of "Guarantees and failure classes" | 13.2 |
| A78 | N-5: 16b recovery tests were merged into the B-1 bullet | 16b split into separate bullets; content unchanged | 20 (16b) |
| A79 | N-6: check 12 was called "last before the write" although check 13 follows | Check 12 is "the last per-item check"; check 13 (results-file re-hash) runs after it, immediately before the write | 12.1, 13.1, 20 (19) |
| A80 | N-7: notation "0–11a" vs "0a–11a" | Both 12.1 and 13.1 use "0a–11a" | 13.1 |

**Revision 5g1, amendment 10 (apply check 1 and v1/v2 compatibility table, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A81 | Apply check 1 compared `original_path` with `path` unconditionally, which could make a v1 item without `original_path` look ambiguous | Check 1 defines the target (`original_path` if present, otherwise `path`) and compares the two only when both are present. A missing `original_path` is never ambiguous and never a skip. Aligned with V33; v1 regression test extended | 13.1, 20 (19) |
| A82 | The 14.7 table claimed to list every v1/v2 operational difference but covered only `apply` and `review` | Rows 2–5 now cover all six relevant commands, with lock types per 12.6. New per-command table: start-up order, lock type, outcome on a v1/v2 file, and side effects. The lock file is the only side effect of read-only commands and of refused `approve` runs. No compatibility behaviour changed; no v3 validation applied to v1/v2. Test 16 extended | 14.7, 20 (16) |

**Revision 5g2, amendment 11 (apply check 2 and `recover-archive` in 14.7, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A83 | Apply check 2 said "equals the preview" without saying that `preview_path` may be absent on v1 files | Check 2 compares with `preview_path` only when the field is present. A missing `preview_path` is never a skip at check 2; on v3 files it is still caught by check 4 (V36). Aligned with V33; v1 regression test extended | 13.1, 20 (19) |
| A84 | 14.7 didn't cover `recover-archive` | Added to difference rows 2–4 and to the per-command table, with lock types, preflight and side effects taken from 12.6 and 12.9. Row 5 names its exemption from the journal refusal and the reason. The undefined no-journal case is stated as not defined rather than invented. Test 16b extended to v1/v2 folders | 14.7, 20 (16b) |

**Revision 5g3, amendment 12 (`recover-archive` without a journal, and its schema boundary, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A85 | 12.9 didn't define `recover-archive` without a journal (marked "not defined" in 5g2) | Report mode prints "no archive recovery pending" and exits zero; `--complete` and `--rollback` refuse with no rename and no file change; journals already moved into an archive folder are never read. Only side effect: the lock file. 14.7 updated; tests added | 12.9, 14.7, 20 (16b) |
| A86 | 12.9 didn't state normatively whether `recover-archive` parses analysis outputs | Normative boundary: it reads only the journal, the lstat state of named entries, the bytes of named files (to hash them only) and the `previews` entry names. It never parses the results file, CSV, gallery or manifest, never reads `schema_version` or `run_id`, and runs no V30–V46 check, so it behaves the same in v1, v2 and v3 folders. 14.7 now cites this boundary; tests added | 12.9, 14.7, 20 (16b) |

**Revision 5g3a, amendment 13 (schema boundary wording, 2026-11-15):**

| # | Finding | Change | Sections |
|---|---|---|---|
| A87 | The 12.9 schema boundary said "in every mode it reads only", which could be read as excluding the 12.6 start-up steps | The list now explicitly limits the inputs for determining and verifying recovery state. The 12.6 `/proc/self/mountinfo` check and the lock operations still apply in every mode. The ban on parsing the results file, CSV, gallery and manifest is unchanged | 12.9 |

### 22.3 Resolution status of decisions U1–U18

All decisions U1–U18 are resolved. Revisions 5e, 5e1, 5f, 5g, 5g1, 5g2, 5g3 and 5g3a change none of them.

| # | Decision | Resolution |
|---|---|---|
| U1 | Partly related groups | **Resolved (A39):** whole-group mode only; mixed is `invalid` |
| U2 | Approval carry-over | **Resolved (A40):** never; fresh individual approval for every new analysis |
| U3 | Mixed UTC offsets | **Resolved (A41):** no gaps computed; clear warning |
| U4 | Network filesystems | **Resolved (A42):** local filesystem required; known network types refused; detection is best effort and documented as such |
| U5 | Apply-time hashing | **Resolved (A43):** only originals that reach apply check 12 |
| U6 | Time source | **Resolved (A44):** camera metadata; `OffsetTimeOriginal` when valid; otherwise timezone-unknown |
| U7 | Flag names | **Resolved (A45):** the twelve 3.3 flags, frozen |
| U8 | Skip messages and v1/v2 | **Resolved (A46):** messages kept; v1/v2 differences documented (four in A46, completed to seven by A62 in revision 5f; the decision itself is unchanged) |
| U9 | One ranked frame | **Resolved (A47):** validated only when every listed single-winner condition passes |
| U10 | `--archive-existing` | **Resolved (A48):** name and scope unchanged |
| U11 | Initial evidence rules version | **Resolved (A49):** `"v3.0"` |
| U12 | Apply-time hashing | **Resolved (A31):** only items that reach check 12 are hashed |
| U13 | Hashes in `review` and coverage | **Resolved (A32):** stored hash shown, labelled not verified; no hashing. Incomplete output is still refused by both (12.8), unchanged |
| U14 | Lock waiting | **Resolved (A33):** no `--wait`; retry after "busy" |
| U15 | Archive recovery | **Resolved (A34):** `recover-archive` kept, report-only by default, narrow explicit actions |
| U16 | Original integrity during preview generation | **Resolved (A35):** two SHA-256 hashes plus supplementary lstat checks |
| U17 | Full analysis hashing cost | **Resolved (A37):** about 340 GB of extra reading accepted as an intentional integrity cost; no weaker mode |
| U18 | Orphan preview accounting | **Resolved (A38):** separate `referenced_preview_count` and `orphan_preview_count`; orphans never deleted and never cause incompleteness |
