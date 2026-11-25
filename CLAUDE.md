

# Photo Curator — Claude Code project instructions

This repository implements a strict, local-first photo culling workflow for CachyOS and digiKam.

## Non-negotiable safety rules
- Analysis is read-only. Never edit, move, rename, delete, or write metadata to original image files.
- The default workflow is `photo-curator analyze` followed by human review. Never run `photo-curator apply` unless the user explicitly requests it.
- Applying ratings requires a separate explicit `--yes` flag. Never bypass it.
- Ratings are written only to XMP sidecars named `original.ext.xmp`; existing sidecars are backed up before modification.
- Never invent image observations. If a file cannot be inspected, leave it unrated.

## Rating style
Use the strict editorial rubric in `photo_curator/core.py`. Four and five stars are rare. Ordinary but useful event coverage is three stars. Evaluate moment, expression, composition, focus, exposure, storytelling, and redundancy; do not equate sharpness with quality.
For schema version 3 analysis (the only path `analyze` uses), the rubric is the approved specification as implemented in `photo_curator/prompt_v3.py` (model instructions), `photo_curator/schema_v3.py` (constants, evidence rules) and `photo_curator/validate_v3.py` (deterministic rating and validation); the rubric in `core.py` is the v1/v2 rubric, kept for compatibility.

## Commands
- `photo-curator scan /path/to/photos`
- `photo-curator analyze /path/to/photos`
- `photo-curator review /path/to/photos/.photo-curator/ratings-review.json`
- `photo-curator apply /path/to/photos/.photo-curator/ratings-review.json --min-rating 1 --yes`
- `photo-curator coverage /path/to/photos/.photo-curator/ratings-review.json` (read-only facts report; v3 only)
- `photo-curator approve <results.json> <original> --show [--open-preview]` (read-only; prints the preview SHA-256)
- `photo-curator approve <results.json> <original> --rating N --preview-sha256 <hash>` and `photo-curator approve <results.json> <original> --revoke` (v3 only; only when the user explicitly requests that approval change)
- `photo-curator analyze /path/to/photos --archive-existing` (moves existing output into `<output>/archive/<UTC timestamp>/`; nothing deleted; only on explicit request)
- `photo-curator recover-archive <output folder>` (report, read-only); `--complete` / `--rollback` only on explicit request
- `analyze` refuses an output folder that already holds results; the two workflows are a new `--output` folder (recommended) or `--archive-existing`.
- Incomplete output (no valid `ratings-review.complete.json`) is refused by `review`, `coverage`, `approve` and `apply` with "analysis output incomplete"; nothing repairs it.
- A locked output folder is refused immediately with "output folder busy: another Photo Curator command is using it; retry after it finishes"; no command waits.
- `--max-burst-size` accepts 2–12 (default 12).
- `needs_review` items are written by `apply` only after a valid individual approval.

## digiKam integration
Use digiKam's default `filename.ext.xmp` sidecar convention. In digiKam, enable reading XMP sidecars and set rating metadata behavior to read XMP. Refresh/rescan metadata after applying. Never recommend writing ratings into RAW files for this workflow.
## Photo Curator v3 Development Protocol

### Authoritative specification
- The approved implementation target is `docs/spec-v3-revision-5g3a-draft.md`.
- Its approved SHA-256 is `364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25`.
- Never modify, rewrite, or replace the approved specification without explicit user authorization.
- Earlier specification revisions are historical references, not implementation targets.

### Implementation discipline
- Follow the approved S1–S17 implementation sequence.
- Work on only one implementation stage at a time.
- Before each stage, present an implementation plan listing exact files, requirements, tests, risks, and non-goals.
- Wait for explicit user approval before modifying files.
- Never proceed automatically to the next stage.
- Do not expand the approved scope or silently resolve specification ambiguities.

### Change boundaries
- Modify only files explicitly approved for the current stage.
- Preserve v1/v2 compatibility unless the approved specification explicitly requires a behavior change.
- Do not wire incomplete v3 functionality into the production CLI.
- Never modify historical backups or specification files without authorization.
- Stop and request approval if implementation requires changes outside the approved scope.

### Validation and reporting
- Run targeted tests for every stage.
- Run the complete test suite after each stage.
- Verify the approved specification SHA-256 before and after implementation.
- Report all created, modified, and deleted files.
- Report test counts, failures, deviations, and unresolved risks.
- Never claim a test passed unless it was actually executed.
- Do not declare a stage complete until its implementation and verification have been reviewed.

### Human approval
- The user is the final authority for specification decisions, implementation scope, and stage completion.
- Never perform destructive operations or apply ratings to real photo libraries without explicit authorization.
- A successful test suite does not independently authorize the next stage.
