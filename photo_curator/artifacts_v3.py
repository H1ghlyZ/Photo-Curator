"""v3 CSV, gallery and ``review`` text (spec 5g3a sections 14.6, 12.1 reader rule, 12.3 gallery
hash, 3.3 flag names; spec 20 item 18; implementation step S10).

Approved specification: ``docs/spec-v3-revision-5g3a-draft.md`` (SHA-256
364233c9d37d097aba0f2028199885dd6eb4ba68a725e2e741f077c62e8d2d25).

Scope (S10 only):
- pure renderers returning bytes/text; writing goes through ``output_v3.write_analysis_outputs``.
  NOT wired into any CLI command; the v2 ``write_review_html`` / CSV writer are unchanged;
- these renderers read the results document and, for the gallery's preview SHA-256, the
  preview files. They never hash, open or stat an original (``hashing_v3`` is not used);
- per-item read-time checks (V32-V46), PV1-PV6 and the full 12.1 approval derivation are the
  reader's (S11). Until then the ``review`` text derives approval state from stored fields
  only (``output_v3.approval_state_without_files``) and says so explicitly.

Decisions (S10):
- D10-4 CSV (M-12): the 14 v2 columns in v2 order, then the 8 new columns in 14.6 order. Lists
  are joined with "; ", ``None`` is an empty cell, rows sorted as v2 (rating desc, then path).
  ``flags`` uses the 3.3 names exactly as stored (the shared ``schema_v3.FLAGS`` vocabulary).
- D10-5 gallery: every preview reference is ``previews/<file name>`` (URL-quoted), never
  absolute and never ``file://``. Burst status labels: ``validated`` -> "validated",
  ``not_comparable`` -> "not comparable", ``invalid`` -> "unvalidated".
- D10-6 gallery approval state at analysis time: "pending" when ``needs_review`` else
  "not required" (12.1 rows 2-3; every result is written unapproved).
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import os
import stat
import urllib.parse
from pathlib import PurePosixPath
from typing import Any, Mapping

from .output_v3 import approval_state_without_files
from .schema_v3 import FLAGS

V2_CSV_COLUMNS = ('rating', 'technical_score', 'editorial_score', 'decision', 'confidence', 'session_id',
                  'burst_group', 'burst_sequence', 'best_in_burst', 'path', 'original_path', 'preview_path',
                  'reasons', 'flags')
NEW_CSV_COLUMNS = ('capture_time', 'needs_review', 'review_reasons', 'documentation_suggestion',
                   'editorial_suggestion', 'moment_override', 'burst_comparison', 'burst_ranking_status')
CSV_COLUMNS = V2_CSV_COLUMNS + NEW_CSV_COLUMNS
NOT_VERIFIED_LABEL = 'analysis-time SHA-256, not verified against the current file'
BURST_LABELS = {'validated': 'validated', 'not_comparable': 'not comparable', 'invalid': 'unvalidated'}
REVIEW_HINT = ('To approve a review item, inspect it first:\n'
               '  photo-curator approve <results.json> <original_path> --show\n'
               'Do not edit ratings-review.json by hand: a hand-edited rating is inconsistent (V34) '
               'and a hand-written approval without matching hashes is invalidated or malformed.')
_APPROVAL_TEXT = {
    'passing': 'approved (original not verified; preview checks not run)',
    'invalidated': 'invalidated', 'malformed': 'malformed',
    'undetermined': 'undetermined (approval state cannot be derived from stored fields)',
}


def _sort_key(result: Mapping[str, Any]):
    rating = result.get('rating')
    return (-(rating if isinstance(rating, int) and not isinstance(rating, bool) else 0),
            str(result.get('path') or ''))


def _cell(value: Any) -> Any:
    if value is None:
        return ''
    if isinstance(value, list):
        return '; '.join(str(v) for v in value)
    return value


# --- CSV (14.6) -------------------------------------------------------------------------------

def csv_bytes(doc: Mapping[str, Any]) -> bytes:
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for result in sorted(doc.get('results') or [], key=_sort_key):
        writer.writerow([_cell(result.get(column)) for column in CSV_COLUMNS])
    return buffer.getvalue().encode('utf-8')


# --- Preview hashing for the gallery (never an original) ---------------------------------------

def preview_sha256(path: str) -> str | None:
    """SHA-256 of a regular, non-symlink preview file; None if unavailable."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except (OSError, TypeError, ValueError):
        return None
    digest, ok = hashlib.sha256(), True
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            ok = False
        else:
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        ok = False
    finally:
        try:
            os.close(fd)
        except OSError:
            ok = False
    return digest.hexdigest() if ok else None


def relative_preview_src(preview_path: Any) -> str | None:
    """``previews/<file name>`` for the gallery, or None without a usable name."""
    if not isinstance(preview_path, str) or not preview_path:
        return None
    name = PurePosixPath(preview_path).name
    if not name or name in ('.', '..'):
        return None
    return 'previews/' + urllib.parse.quote(name)


# --- Gallery (14.6, 12.3) ------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape('' if value is None else str(value), quote=True)


def _card(result: Mapping[str, Any]) -> str:
    original = result.get('original_path') or result.get('path')
    name = PurePosixPath(str(original)).name if original else '?'
    src = relative_preview_src(result.get('preview_path'))
    image = (f'<img loading="lazy" src="{_e(src)}" alt="Preview of {_e(name)}">' if src
             else '<div class="missing">Preview missing</div>')
    needs_review = result.get('needs_review') is True
    approval = 'pending' if needs_review else 'not required'                        # D10-6
    lines = [
        f'<p class="name">{_e(name)}</p>',
        f'<p class="scores">Rating: <b>{_e(result.get("rating"))}</b> · {_e(result.get("decision"))} · '
        f'Technical: <b>{_e(result.get("technical_score"))}/5</b> · Editorial: <b>{_e(result.get("editorial_score"))}/5</b></p>',
        f'<p class="original">Original: <code>{_e(original)}</code></p>',
        f'<p class="hash">Original SHA-256: <code>{_e(result.get("original_sha256"))}</code> '
        f'<span class="label">({NOT_VERIFIED_LABEL})</span></p>',
        f'<p class="capture">Capture time: {_e(result.get("capture_time") or "unknown")}</p>',
        f'<p class="session">Session: <b class="session-id">{_e(result.get("session_id"))}</b></p>',
        f'<p class="approval">Approval state (at analysis): <b>{approval}</b></p>',
    ]
    if needs_review:
        digest = preview_sha256(result.get('preview_path'))
        lines.append(f'<p class="preview-hash">Preview SHA-256: <code>{_e(digest or "unavailable")}</code></p>')
        reasons = ''.join(f'<li>{_e(code)}</li>' for code in result.get('review_reasons') or [])
        lines.append(f'<div class="review">Review reasons:<ul>{reasons}</ul></div>')
    group, status = result.get('burst_group'), result.get('burst_ranking_status')
    if group is not None:
        label = BURST_LABELS.get(status, 'unvalidated' if status is not None else 'not ranked')
        star = ' <strong class="winner">★ Best in burst</strong>' if (
            status == 'validated' and result.get('best_in_burst') is True) else ''
        lines.append(f'<p class="burst">Burst {_e(group)}: <span class="burst-status">{_e(label)}</span>{star}</p>')
    flags = result.get('flags')
    if isinstance(flags, list) and flags:
        lines.append('<p class="flags">Flags: ' + ', '.join(_e(f) for f in flags if f in FLAGS) + '</p>')
    evidence = ''.join(f'<li>{_e(r)}</li>' for r in result.get('reasons') or [])
    if evidence:
        lines.append(f'<ul class="evidence">{evidence}</ul>')
    return (f'<article class="card" data-session="{_e(result.get("session_id"))}" '
            f'data-review="{str(needs_review).lower()}">{image}<div class="meta">{"".join(lines)}</div></article>')


def gallery_html(doc: Mapping[str, Any]) -> str:
    results = sorted(doc.get('results') or [], key=_sort_key)
    cards = '\n'.join(_card(r) for r in results)
    sessions = sorted({str(r.get('session_id')) for r in results if r.get('session_id') is not None})
    options = ''.join(f'<option value="{_e(s)}">{_e(s)}</option>' for s in sessions)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Photo Curator v3 review</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px;background:#111;color:#eee}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}}
.card{{background:#1c1c1c;border-radius:8px;overflow:hidden}}.card img{{width:100%;display:block}}
.meta{{padding:8px;font-size:13px}}code{{word-break:break-all}}.label{{color:#aaa}}
.missing{{padding:40px;text-align:center;color:#c66}}.winner{{color:#fc3}}</style></head>
<body><h1>Photo Curator v3 review</h1>
<p>Run {_e(doc.get('run_id'))} · {len(results)} results · {len(doc.get('errors') or [])} errors.
Ratings are suggestions until applied; nothing has been written to any photo.</p>
<label>Session <select id="session"><option value="">all</option>{options}</select></label>
<label><input type="checkbox" id="review"> review items only</label>
<div class="grid" id="grid">
{cards}
</div>
<script>
const s=document.getElementById('session'),r=document.getElementById('review');
function f(){{for(const c of document.querySelectorAll('.card')){{
c.style.display=((!s.value||c.dataset.session===s.value)&&(!r.checked||c.dataset.review==='true'))?'':'none';}}}}
s.onchange=f;r.onchange=f;
</script></body></html>
"""


def gallery_bytes(doc: Mapping[str, Any]) -> bytes:
    return gallery_html(doc).encode('utf-8')


# --- review command text for v3 (14.6) ---------------------------------------------------------

def review_lines(doc: Mapping[str, Any]) -> list[str]:
    """Lines printed by ``review`` for a v3 document. Never hashes or opens any file."""
    lines = []
    results = sorted(doc.get('results') or [], key=_sort_key)
    for r in results:
        rating = r.get('rating') if isinstance(r.get('rating'), int) and not isinstance(r.get('rating'), bool) else 0
        rating = max(0, min(5, rating))
        name = PurePosixPath(str(r.get('original_path') or r.get('path') or '?')).name
        confidence = r.get('confidence')
        conf = f'{confidence:.2f}' if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else 'n/a'
        lines.append(f"{'★' * rating}{'☆' * (5 - rating)}  {str(r.get('decision', '?')):12}  conf={conf}  {name}"
                     f"  [{r.get('session_id')}]")
        state = approval_state_without_files(r)
        if state == 'not_approved':
            state_text = 'pending' if r.get('needs_review') is True else 'not required'
        else:
            state_text = _APPROVAL_TEXT[state]
        lines.append(f'    approval: {state_text}')
        lines.append(f"    original SHA-256: {r.get('original_sha256')} ({NOT_VERIFIED_LABEL})")
        for code in r.get('review_reasons') or []:
            lines.append(f'    - {code}')
        if r.get('flags'):
            lines.append('    flags: ' + ', '.join(str(f) for f in r['flags']))
    lines.append('')
    lines.append(f'{len(results)} results.')
    lines.append(REVIEW_HINT)
    return lines
