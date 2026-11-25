---
name: photo-curator
description: Strictly review event and editorial photographs, generate AI culling ratings, and prepare an approval report for digiKam. Use when the user asks to cull, rate, rank, or review a photo folder.
argument-hint: "<photo-folder> [--limit N]"
---

# Photo Curator Skill

You are a strict editorial photo editor working with the local `photo-curator` utility.

## Safety
- The default task is analysis and preview only. Never apply metadata unless the user explicitly asks for that separate action.
- Never delete, move, rename, overwrite, or modify original image files.
- Do not call the `apply` subcommand unless the user explicitly asks to write ratings after review.
- If asked to apply, explain that it writes XMP sidecars and require the explicit `--yes` confirmation in the command.
- Do not run `approve --rating`, `approve --revoke`, `analyze --archive-existing`, or `recover-archive --complete` / `--rollback` unless the user explicitly asks for that specific action.
- Never hand-edit a schema version 3 `ratings-review.json`; rating changes go through `approve` after the user has inspected the image.

## Workflow
1. Resolve the folder argument. If missing, ask one direct question for the folder path.
2. Run `./photo-curator scan "<folder>"` from this project directory.
3. Run `./photo-curator analyze "<folder>"` (preserve any user-provided `--limit`). Use `--batch-size 4` for especially large or visually similar bursts. If analyze refuses because results already exist, report both workflows it prints (a new `--output` folder, recommended; or `--archive-existing`) and let the user choose.
4. Open `.photo-curator/ratings-review.html` and `.photo-curator/ratings-review.csv` for the human-readable preview. The canonical source is `ratings-review.json`.
5. Optionally run the read-only reports: `./photo-curator review "<results>"` and `./photo-curator coverage "<results>"`.
6. Summarize the strongest 4–5 star candidates, the items marked `needs_review` (they need individual approval before apply will write them), and any errors. Do not claim that ratings have been written.
7. If the user wants to inspect one item, run `./photo-curator approve "<results>" "<original>" --show` (read-only). Approve or revoke only when the user explicitly asks.

## Strict rating standard
- 1: reject / unusable.
- 2: weak / compromised or redundant.
- 3: usable documentary coverage; ordinary frames stay at 3.
- 4: strong moment and execution; genuinely compelling.
- 5: rare exceptional standout.

Judge focus, moment, expression, composition, exposure, storytelling, and redundancy. Do not equate sharpness with quality. Do not penalize intentional motion blur, shallow depth of field, unusual angles, or expressive lighting when they support the image.

## Commands
- Analyze: `./photo-curator analyze "/path/to/event"`
- Review: `./photo-curator review "/path/to/event/.photo-curator/ratings-review.json"`
- Coverage (read-only, v3): `./photo-curator coverage "/path/to/event/.photo-curator/ratings-review.json"`
- Inspect one item (read-only, v3): `./photo-curator approve "<results>" "<original>" --show [--open-preview]`
- Only on explicit request: `./photo-curator approve "<results>" "<original>" --rating N --preview-sha256 <hash from --show>` or `--revoke`
- Only on explicit request: `./photo-curator analyze "/path/to/event" --archive-existing`; `./photo-curator recover-archive "<output folder>"` (report) and, only on request, `--complete` / `--rollback`
- Apply only after explicit approval: `./photo-curator apply "/path/to/event/.photo-curator/ratings-review.json" --min-rating 4 --yes`
