# Photo Curator — Claude Code + digiKam (CachyOS)

Strict AI photo culling with a human approval gate. The analysis phase creates previews and JSON/CSV recommendations only. It does not modify source photos or metadata. A separate `apply` command writes approved ratings into XMP sidecars.

> **Status: experimental.** The v3 ratings have not yet been calibrated against human ratings. Treat every rating as a suggestion, review the results yourself, and try it on copies of your photos first. Licensed under the MIT License (see `LICENSE`).

## 1. Install dependencies (CachyOS / Arch)

```bash
sudo pacman -Syu
sudo pacman -S --needed python python-pillow darktable exiftool digikam
```

Install and authenticate Claude Code using Anthropic's current official installation instructions. Confirm:

```bash
claude --version
claude
```

Complete sign-in once, then exit the interactive session. `claude -p` is used by the curator for non-interactive visual analysis.

## 2. Install this project

From the extracted project directory, no pip installation is required. The launcher uses the system Python and the Pillow package installed above:

```bash
./photo-curator --help
```

You can optionally add this directory to your PATH or create a symlink to the launcher. No API key is stored in this project.

## 3. Configure digiKam (important)

Open **Settings → Configure digiKam → Metadata**:

- In **Behavior**, enable writing/reading Rating metadata and prefer XMP for rating metadata.
- In **Sidecars**, enable reading sidecar files and configure sidecar handling so `filename.ext.xmp` files are recognized.
- Keep the default `filename.ext.xmp` naming convention (do not enable the alternate commercial-compatible `filename.xmp` convention for this first version).
- Do not enable writing metadata into RAW files for this workflow.

digiKam's metadata settings distinguish writing to sidecar only from writing into the image and sidecar. This project always writes sidecars only.

## 4. Run a safe test first

Use a copied folder with 10–20 representative photos, including ARW if available:

```bash
./photo-curator scan ~/Pictures/test-event
./photo-curator analyze ~/Pictures/test-event --limit 20
```

The first run creates (schema version 3):

```text
~/Pictures/test-event/.photo-curator/
├── .photo-curator-output          # marker: never scanned as a source
├── previews/
├── ratings-review.json            # canonical results
├── ratings-review.csv
├── ratings-review.html            # static gallery
└── ratings-review.complete.json   # completion manifest (written last)
```

`ratings-review.json.lock` appears the first time a command locks the folder; it is harmless and never deleted. Output without a valid completion manifest is **incomplete**: `review`, `coverage`, `approve` and `apply` refuse it with "analysis output incomplete", and nothing repairs it. Re-run into a new folder instead.

Review the CSV in a spreadsheet, the HTML gallery in a browser, or the JSON. `rating=0` means unevaluable/unrated. The preview images are resized copies; originals remain untouched. Items marked `needs_review` must be approved individually before `apply` will write them (section 5).

```bash
./photo-curator review ~/Pictures/test-event/.photo-curator/ratings-review.json
./photo-curator coverage ~/Pictures/test-event/.photo-curator/ratings-review.json
```

`coverage` is a read-only facts report (v3 only): counts per session, capture type and rating, suggestions, timeline gaps of at least 10 minutes without a rating ≥ 3 image, untimed images, and preview accounting. It never reads image metadata, never hashes originals and makes no claims about what is missing.

Claude Code must be authenticated and able to use its Read tool. The script invokes `claude -p` with Read allowed and Bash/Edit/Write disallowed for the analysis subprocess. It sends only generated previews to Claude, not original RAW files.

## 5. Approve and apply ratings (manual, separate step)

**v3 results: do not hand-edit `ratings-review.json`.** A hand-edited rating, decision or suggestion no longer matches its stored scores; it is reported as inconsistent and apply skips it (`[SKIP inconsistent result]`). Change or confirm a rating with `approve` instead, one item at a time, after inspecting the actual image:

```bash
R=~/Pictures/test-event/.photo-curator/ratings-review.json
./photo-curator approve "$R" /path/to/original.ARW --show                # read-only; prints the preview SHA-256
./photo-curator approve "$R" /path/to/original.ARW --show --open-preview # also opens the preview if it passes the checks
./photo-curator approve "$R" /path/to/original.ARW --rating 3 --preview-sha256 <hash from --show>
./photo-curator approve "$R" /path/to/original.ARW --revoke
```

An approval is bound to the original and the preview you inspected. Items with `needs_review` are skipped by apply with `[SKIP needs review: not approved]` until they carry a valid approval. `approve` accepts schema version 3 only. (v1/v2 results have no approvals and are applied as before; for those files, editing the JSON remains the way to change a rating.)

Then apply only the chosen threshold. Example: apply 4–5 stars only:

```bash
./photo-curator apply ~/Pictures/test-event/.photo-curator/ratings-review.json --min-rating 4 --yes
```

Without `--yes`, nothing is written. Every skipped item is reported with a `[SKIP ...]` message.

By default, if an XMP sidecar already exists, it is skipped to avoid overwriting metadata. To intentionally update an existing sidecar, use `--include-existing`; the first pre-existing sidecar is backed up as `.photo-curator.bak` before the new rating is written.

After applying, in digiKam refresh/rescan the album or use the metadata synchronization/read-from-sidecar action so the database imports the new XMP rating. Verify one image before processing a full event.

## 6. Re-analysis, archive and recovery

`analyze` refuses an output folder that already holds results (or their backups), before creating previews or calling Claude. Two workflows:

1. **Recommended:** run `analyze` again with a different `--output` folder; the existing folder stays exactly as it is.
2. Run `analyze` again with `--archive-existing`: the existing outputs and `previews/` are moved into `<output>/archive/<UTC timestamp>/` (journalled; nothing is deleted). Archived approvals are never carried into the new results.

```bash
./photo-curator analyze ~/Pictures/test-event --archive-existing
```

If an archive is interrupted, `archive-in-progress.json` stays in the output folder and every other command that uses that folder refuses with "archive in progress; run `recover-archive`". Inspect and resolve it:

```bash
./photo-curator recover-archive ~/Pictures/test-event/.photo-curator              # report only (read-only)
./photo-curator recover-archive ~/Pictures/test-event/.photo-curator --complete   # finish the archive
./photo-curator recover-archive ~/Pictures/test-event/.photo-curator --rollback   # undo it
```

`--complete` and `--rollback` only perform the renames listed in the journal and never delete anything.

## 7. Locking and refusals

- Commands lock the output folder: `analyze`, `apply`, `approve --rating`, `approve --revoke`, `gallery` and `recover-archive --complete/--rollback` exclusively; `review`, `coverage`, `approve --show` and `recover-archive` (report) shared. `scan` takes no lock. No command waits: if the folder is in use, it refuses with "output folder busy: another Photo Curator command is using it; retry after it finishes". Run it again once the other command has finished.
- The output folder must be on a local filesystem. Known network filesystems are refused: "output folder is on a network filesystem (<type>); use a local folder".
- `--max-burst-size` accepts 2–12 (default 12); `--batch-size` accepts 1–12.
- `gallery` regenerates the HTML only for v1/v2 results; v3 analysis writes `ratings-review.html` itself.
- Refusals and failures exit with status 1; argument errors exit with status 2. A read-only command (`review`, `coverage`, `approve --show`, `recover-archive` in report mode) never changes anything except possibly creating the lock file.

## Rating policy (strict)

| Stars | Meaning | Guidance |
|---|---|---|
| 1 | Reject | Unusable frame, severe focus/motion issue, accidental capture |
| 2 | Weak | Compromised, awkward, redundant, or low selection value |
| 3 | Usable | Competent documentary coverage; ordinary but useful |
| 4 | Strong | Compelling moment, clean execution, strong composition/story |
| 5 | Exceptional | Rare standout; excellent moment, composition, expression and execution |

The model is instructed not to reward sharpness alone and not to penalize intentional creative blur, unusual angles, shallow depth of field, or expressive lighting.

## Operational notes

- Supported image types: JPEG, PNG, WebP, TIFF, Sony ARW, Canon CR2/CR3, Nikon NEF/NRW, Fuji RAF, Panasonic RW2, Olympus ORF, DNG.
- RAW previews require `darktable-cli` (provided by the `darktable` package). If it cannot decode a particular camera RAW, export a JPEG preview with darktable and analyze that instead.
- JPEG/PNG/TIFF/WebP previews are generated with Pillow and EXIF orientation correction.
- Start with `--batch-size 4` or `6`. Larger batches can increase context pressure and make series comparisons harder. Use `--limit` for initial tests.
- Generated output is marked with a `.photo-curator-output` file, written into the output folder and its `previews/` subfolder. Any directory containing this marker, whatever its name or location, is never scanned for source images, and `apply` refuses targets inside it. Do not delete the marker or store originals in a marked folder. The default `.photo-curator/` folder is also excluded by name.
- `--output` must resolve (after following symlinks) to a folder outside the source folder, and must not contain the source folder. It must be new, empty, or an existing marked Photo Curator output folder. Otherwise `analyze` stops before writing anything.
- AI ratings are subjective recommendations, not objective truth. Human approval is required before writing metadata.
- The first implementation uses local Claude Code CLI. Image contents are transmitted to Anthropic for model analysis under the terms of the Claude account/service you use. Do not process confidential images unless your organization permits that.

## Troubleshooting

**`claude` not found** — ensure Claude Code is installed and available in PATH in the same shell where the venv is active.

**Claude cannot inspect a preview** — run `claude` interactively and confirm it can read a local JPEG by path. Re-authenticate if needed. The batch script uses the `Read` tool.

**RAW conversion fails** — open the RAW in darktable; if it is unsupported or damaged, export a JPEG preview manually.

**Ratings do not appear in digiKam** — verify sidecar reading is enabled, confirm the sidecar filename is `photo.ARW.xmp`, refresh the album metadata, and check the XMP Rating field in digiKam's Metadata sidebar.

**Existing sidecar skipped** — this is intentional. Inspect it first; use `--include-existing` only if replacing its rating is intended. A backup is created.

## Claude Code slash command

This repository includes `.claude/skills/photo-curator/SKILL.md`. Open Claude Code in the project directory and invoke `/photo-curator /path/to/event-folder` to start the guided workflow. Claude will scan, analyze, and prepare a preview report; the skill explicitly does not apply ratings, approve or revoke items, or archive output unless you later ask for that action.
