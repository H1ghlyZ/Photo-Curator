from __future__ import annotations
import argparse, csv, html, json, os, shutil, sys, time
from collections import Counter
from urllib.parse import quote
from pathlib import Path
from .core import (find_images, make_preview, analyze_batch, validate_batch_results, load_results,
                   write_rating_sidecar, detect_bursts, split_bursts, session_for, session_capture_times, plan_batches, is_generated, result_original,
                   result_preview, SUPPORTED, in_generated_output, mark_output_dir, validate_output_dir)
from .analyze_v3 import AnalyzeError, run_analyze
from .apply_v3 import ApplyRefused, run_apply
from .approve_v3 import ApproveFailure, ApproveRefusal, approve_item, revoke_item, show_item
from .archive_v3 import (ArchiveFailure, ArchiveRefusal, RecoveryFailure, RecoveryRefusal, recover_complete,
                         recover_report, recover_rollback)
from .artifacts_v3 import review_lines
from .coverage_v3 import CoverageRefusal, build_report
from .locking_v3 import StartupRefusal, startup_v3
from .reader_v3 import read_results

# W-B: lock mode and journal exemption per subcommand (R5 lock matrix, 12.6). None = no lock.
# The approve and recover-archive commands take their locks inside approve_v3 / archive_v3 with
# exactly these modes; analyze inside analyze_v3 (filesystem check -> mkdir -> start-up).
COMMAND_LOCK_MODES = {
    'scan': None,
    'analyze': ('exclusive', False),
    'apply': ('exclusive', False),
    'approve --show': ('shared', False),
    'approve --rating': ('exclusive', False),
    'approve --revoke': ('exclusive', False),
    'review': ('shared', False),
    'gallery': ('exclusive', False),
    'coverage': ('shared', False),
    'recover-archive': ('shared', True),
    'recover-archive --complete': ('exclusive', True),
    'recover-archive --rollback': ('exclusive', True),
}


def cmd_scan(a):
    root=Path(a.folder).expanduser().resolve()
    if not root.is_dir(): raise SystemExit(f'Folder not found: {root}')
    imgs=find_images(root, not a.no_recursive)
    print(f'Found {len(imgs)} supported images in {root}')
    for p in imgs: print(p)
    sessions=Counter(session_for(p,root) for p in imgs)
    print(f'\nSessions ({len(sessions)}):')
    for sid,n in sorted(sessions.items()): print(f'  {sid}: {n} images')


def write_review_html(all_results, preview_map, work: Path) -> Path:
    """Create a local review gallery with rating sort, session and best-in-burst filters.

    preview_map is keyed by original path. Results without a usable preview are still
    shown (with a placeholder) so nothing disappears from review.
    """
    html_path=work/'review.html'
    cards=[]; sessions=set()
    for r in all_results:
        original=Path(result_original(r))
        preview=preview_map.get(str(original))
        session=str(r.get('session_id') or '—'); sessions.add(session)
        if preview and preview.exists():
            rel=quote(Path(os.path.relpath(preview, work)).as_posix(), safe='/')
            img_html=f'<img loading="lazy" src="{rel}" alt="Preview of {html.escape(original.name, quote=True)}">'
        else:
            img_html=f'<div class="missing">Preview missing<br><small>{html.escape(str(preview or "no preview_path saved"))}</small></div>'
        rating=r.get('rating',0)
        if not isinstance(rating,int) or rating not in range(0,6): rating=0
        stars='★'*rating+'☆'*(5-rating) if rating else 'UNRATED'
        reasons=''.join(f'<li>{html.escape(str(x))}</li>' for x in r.get('reasons',[]))
        try: confidence_text=f'{float(r.get("confidence",0)):.2f}'
        except (TypeError, ValueError): confidence_text='—'
        technical=r.get('technical_score', '—')
        editorial=r.get('editorial_score', '—')
        burst_html='<strong class="winner">★ Best in burst</strong>' if r.get('best_in_burst') is True else ''
        if r.get('burst_group'):
            burst_html += ' <span class="burst">Group: '+html.escape(str(r.get('burst_group')))+' </span>'
        seq=r.get('burst_sequence')
        if seq and seq!=r.get('burst_group'):  # comparison group is one part of a longer sequence
            burst_html += ' <span class="burst">Sequence: '+html.escape(str(seq))+' </span>'
        best='1' if r.get('best_in_burst') is True else '0'
        cards.append(
            f'<article class="photo-card" data-rating="{rating}" '
            f'data-name="{html.escape(original.name.lower(), quote=True)}" '
            f'data-session="{html.escape(session, quote=True)}" data-best="{best}">'
            f'{img_html}'
            f'<div class="details"><div class="rating-line"><b>{stars}</b> '
            f'<span>· {html.escape(str(r.get("decision","?")))}</span> '
            f'<span>· confidence {confidence_text}</span></div>'
            f'<p>{html.escape(original.name)}</p>'
            f'<div class="scores">Session: <b class="session">{html.escape(session)}</b> &nbsp; '
            f'Technical: <b>{html.escape(str(technical))}/5</b> &nbsp; Editorial: <b>{html.escape(str(editorial))}/5</b> &nbsp; {burst_html}</div>'
            f'<ul>{reasons}</ul>'
            f'<small>{html.escape(str(original))}</small></div></article>'
        )
    session_options=''.join(f'<option value="{html.escape(s, quote=True)}">{html.escape(s)}</option>' for s in sorted(sessions))
    page = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Photo Curator Review</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{font:15px system-ui,sans-serif;margin:0;padding:24px;background:#111;color:#eee}
h1{font-size:24px;margin:0 0 8px}.intro{color:#aaa;margin:0 0 18px}.toolbar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px;margin:0 0 14px;background:#191919;border:1px solid #333;border-radius:12px}
.toolbar-label{font-weight:600;margin-right:4px}.sort-button{border:1px solid #555;background:#252525;color:#eee;border-radius:8px;padding:9px 13px;font:inherit;cursor:pointer}.sort-button:hover{background:#333}.sort-button[aria-pressed="true"]{background:#eee;color:#111;border-color:#eee}.count{margin-left:auto;color:#aaa;font-size:13px}
article{display:grid;grid-template-columns:minmax(220px,38%) 1fr;gap:16px;padding:16px;margin:12px 0;background:#202020;border:1px solid #2d2d2d;border-radius:12px}article img{width:100%;height:auto;max-height:680px;object-fit:contain;align-self:start;background:#000;border-radius:8px}.rating-line{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.rating-line b{letter-spacing:2px;color:#ffd76a}.scores{font-size:13px;color:#bbb;margin:8px 0}.winner{color:#8fe0a2}.burst{color:#9bc7ff}p{font-weight:600}.details ul{padding-left:20px}li{margin:6px 0;line-height:1.45}small{color:#aaa;overflow-wrap:anywhere}.session{color:#e0c08f}.missing{display:flex;flex-direction:column;justify-content:center;align-items:center;min-height:200px;background:#000;border-radius:8px;color:#f99;text-align:center;padding:12px}
.filter{display:flex;align-items:center;gap:6px}.filter select{border:1px solid #555;background:#252525;color:#eee;border-radius:8px;padding:8px;font:inherit}.photo-card[hidden]{display:none}
@media(max-width:700px){body{padding:12px}article{grid-template-columns:1fr;padding:12px}.count{width:100%;margin-left:0}}
</style></head><body>
<h1>Photo Curator — Review</h1><p class="intro">Preview only. No image metadata was changed. Review the results before applying ratings.</p>
<div class="toolbar" role="group" aria-label="Sort photos by rating">
<span class="toolbar-label">Sort rating:</span>
<button class="sort-button" id="sort-desc" type="button" aria-pressed="true">Top to bottom · 5 → 1</button>
<button class="sort-button" id="sort-asc" type="button" aria-pressed="false">Bottom to top · 1 → 5</button>
<label class="filter">Session: <select id="session-filter"><option value="">All sessions</option>__SESSIONS__</select></label>
<label class="filter"><input type="checkbox" id="best-only"> Best in Burst Only</label>
<span class="count" id="photo-count"></span></div>
<main id="photo-list">__CARDS__</main>
<script>
(() => {
  const list = document.getElementById('photo-list');
  const desc = document.getElementById('sort-desc');
  const asc = document.getElementById('sort-asc');
  const count = document.getElementById('photo-count');
  const sessionFilter = document.getElementById('session-filter');
  const bestOnly = document.getElementById('best-only');
  const cards = Array.from(list.querySelectorAll('.photo-card'));
  function filter() {
    const session = sessionFilter.value;
    let shown = 0;
    cards.forEach(card => {
      const visible = (!session || card.dataset.session === session) && (!bestOnly.checked || card.dataset.best === '1');
      card.hidden = !visible;
      if (visible) shown++;
    });
    count.textContent = `${shown} of ${cards.length} photo${cards.length === 1 ? '' : 's'} · preview only`;
  }
  function sort(direction) {
    cards.sort((a, b) => {
      const ratingA = Number(a.dataset.rating || 0);
      const ratingB = Number(b.dataset.rating || 0);
      const byRating = direction === 'desc' ? ratingB - ratingA : ratingA - ratingB;
      return byRating || a.dataset.name.localeCompare(b.dataset.name);
    });
    cards.forEach(card => list.appendChild(card));
    desc.setAttribute('aria-pressed', String(direction === 'desc'));
    asc.setAttribute('aria-pressed', String(direction === 'asc'));
  }
  desc.addEventListener('click', () => sort('desc'));
  asc.addEventListener('click', () => sort('asc'));
  sessionFilter.addEventListener('change', filter);
  bestOnly.addEventListener('change', filter);
  sort('desc');
  filter();
})();
</script></body></html>"""
    page=page.replace('__SESSIONS__',session_options).replace('__CARDS__',''.join(cards))
    html_path.write_text(page,encoding='utf-8')
    return html_path


def cmd_analyze(a):
    """`photo-curator analyze`: the v3 pipeline only (W-A, CLI-1). No v2 path, flag or selector."""
    return run_analyze(a)


def _legacy_cmd_analyze_v2(a):
    """v2 analysis, NOT reachable from any command (W-A, CLI-1). Kept only for test
    compatibility: test_csv_v3 pins the v2 CSV header written here."""
    root=Path(a.folder).expanduser().resolve()
    if not root.is_dir(): raise SystemExit(f'Folder not found: {root}')
    # Validate --output before anything is written (resolves symlinks and nesting).
    if a.output:
        try: work=validate_output_dir(root, a.output)
        except ValueError as e: raise SystemExit(f'Unsafe --output: {e}')
    else: work=root/'.photo-curator'
    images=find_images(root, not a.no_recursive)
    if a.limit: images=images[:a.limit]
    if not images: raise SystemExit('No supported images found.')
    previews=work/'previews'
    mark_output_dir(work); mark_output_dir(previews)   # generated output is never rescanned as source
    preview_map={}; errors=[]
    print(f'Preparing previews for {len(images)} images...')
    for i,p in enumerate(images,1):
        try: preview_map[str(p)]=make_preview(p,previews,a.max_edge)
        except Exception as e: errors.append({'path':str(p),'error':str(e)}); print(f'[SKIP] {p.name}: {e}')
        if i%25==0: print(f'  prepared {i}/{len(images)}')
    usable=[p for p in images if str(p) in preview_map]
    session_map={str(p):session_for(p,root) for p in images}
    for e in errors:  # preview failures keep their session for traceability
        if 'path' in e: e.update(original_path=e['path'], session_id=session_map.get(e['path']))
    all_results=[]
    times=session_capture_times(usable, root)
    if len(times)<len(usable): print(f'{len(usable)-len(times)} image(s) lack a capture timestamp; they are analyzed but never burst-grouped.')
    raw_bursts=detect_bursts(usable, a.burst_window, root, times) if a.burst_window > 0 else {}
    # burst_group = comparison group (<= max_burst_size); burst_sequence = full detected burst.
    burst_map,sequence_map=split_bursts(raw_bursts, times, a.max_burst_size)
    if raw_bursts:
        print(f'Detected {len(set(raw_bursts.values()))} burst sequence(s) across {len(raw_bursts)} images (per session); '
              f'{len(set(burst_map.values()))} comparison group(s) after max size {a.max_burst_size or "unlimited"}.')
    # Batches: one session each, chronological, burst groups never split.
    batches=plan_batches(usable, a.batch_size, root, times, burst_map)
    start=0
    for batch in batches:
        sid=session_map.get(str(batch[0]))
        oversize=f' (burst larger than batch size {a.batch_size}, kept whole)' if len(batch)>a.batch_size else ''
        print(f'Claude reviewing {start+1}-{start+len(batch)} of {len(usable)} [{sid}]{oversize}...')
        batch_start=start; start+=len(batch)
        try: photos=analyze_batch(batch,preview_map,a.model,burst_map,session_map,sequence_map)
        except Exception as e:
            errors.append({'batch_start':batch_start,'error':str(e),'paths':[str(p) for p in batch]}); print(f'[ERROR] {e}'); continue
        results,batch_errors=validate_batch_results(batch,photos,preview_map,burst_map,session_map,sequence_map)
        for e in batch_errors: print(f'[WARN] {Path(e.get("original_path") or "?").name}: {e["error"]}')
        all_results.extend(results); errors.extend(batch_errors)
    payload={'schema_version':2,'created_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'source_folder':str(root),'rating_style':'strict_dual_score_burst','burst_window_seconds':a.burst_window,'max_burst_size':a.max_burst_size,'burst_scope':'per_session','application':'preview_only','results':all_results,'errors':errors}
    result_path=work/'ratings-review.json'; result_path.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8')
    csv_path=work/'ratings-review.csv'
    with csv_path.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['rating','technical_score','editorial_score','decision','confidence','session_id','burst_group','burst_sequence','best_in_burst','path','original_path','preview_path','reasons','flags'])
        for r in sorted(all_results,key=lambda x:(-x.get('rating',0),x.get('path',''))): w.writerow([r.get('rating'),r.get('technical_score'),r.get('editorial_score'),r.get('decision'),r.get('confidence'),r.get('session_id'),r.get('burst_group'),r.get('burst_sequence'),r.get('best_in_burst'),r.get('path'),r.get('original_path'),r.get('preview_path'),'; '.join(map(str,r.get('reasons') or [])),'; '.join(map(str,r.get('flags') or []))])
    html_path=write_review_html(all_results, preview_map, work)
    print(f'\nReview files created:\n  {result_path}\n  {csv_path}\n  {html_path}\nNo image metadata was changed.')
    print(f'Rated: {len(all_results)} | Errors/skips: {len(errors)}')


def cmd_gallery(a):
    """CLI-6 (W-B): exclusive lock (12.6, M-8); v1/v2 unchanged; v3 refused by load_results (D-1)."""
    results_path=Path(a.results).expanduser().resolve()
    startup=startup_v3(results_path.parent, COMMAND_LOCK_MODES['gallery'][0])
    try:
        _gallery_v1v2(results_path)
    finally:
        startup.lock.release()


def _gallery_v1v2(results_path):
    data=load_results(results_path)
    work=results_path.parent
    preview_dir=work/'previews'
    preview_map={}
    candidates=sorted(preview_dir.glob('*.jpg')) if preview_dir.is_dir() else []
    for result in data.get('results',[]):
        original=Path(result_original(result))
        # A saved preview_path is authoritative: use it exactly, never guess, even if
        # the file has since gone missing (the card then shows a placeholder).
        saved=result_preview(result)
        if saved:
            preview_map[str(original)]=Path(saved)
            continue
        # Legacy reviews only: a 'path' that is itself a generated preview, else stem match.
        if is_generated(original) and original.is_file():
            preview_map[str(original)]=original
            continue
        matches=[p for p in candidates if p.name.startswith(original.stem+'__')]
        if matches:
            preview_map[str(original)]=matches[0]
    html_path=write_review_html(data.get('results',[]),preview_map,work)
    print(f'Review gallery updated: {html_path}')
    print('No Claude analysis was rerun; no image metadata was changed.')


class CommandRefusal(Exception):
    """A W-B command refuses: message to stderr, exit 1 (M-23)."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _schema_version(path: Path):
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data.get('schema_version') if isinstance(data, dict) else None


def cmd_review(a):
    """CLI-5 (W-B): shared lock. v1/v2 print exactly as before; v3 prints artifacts_v3.review_lines
    after the read-time checks (V30/V31/V46/V47 refuse). review never writes HTML."""
    path=Path(a.results).expanduser().resolve()
    startup=startup_v3(path.parent, COMMAND_LOCK_MODES['review'][0])
    try:
        version=_schema_version(path)
        if version==3 and not isinstance(version, bool):
            report=read_results(path)
            if report.refusal:
                raise CommandRefusal(report.refusal)
            for line in review_lines(report.doc): print(line)
            return
        _review_v1v2(path)
    finally:
        startup.lock.release()


def cmd_coverage(a):
    """CLI-10 (W-C): shared lock; start-up (filesystem -> lock -> journal) before the results file is
    read; prints coverage_v3.build_report lines to stdout (warnings included, exit 0). v3 only;
    every refusal raises CoverageRefusal (exit 1 via REFUSALS). Writes nothing but the lock file."""
    path=Path(a.results).expanduser().resolve()
    startup=startup_v3(path.parent, COMMAND_LOCK_MODES['coverage'][0])
    try:
        report=build_report(path)
        for line in report.lines: print(line)
    finally:
        startup.lock.release()


def _review_v1v2(path):
    data=load_results(path)
    rows=sorted(data['results'],key=lambda x:(-x.get('rating',0),result_original(x)))
    for r in rows:
        stars='★'*r.get('rating',0)+'☆'*(5-r.get('rating',0))
        extra=f"  [{r['session_id']}]" if r.get('session_id') else ''
        if r.get('best_in_burst') is True: extra+='  ★best-in-burst'
        print(f"{stars}  {r.get('decision','?'):12}  conf={r.get('confidence',0):.2f}  {Path(result_original(r)).name}{extra}")
        for reason in r.get('reasons',[]): print('    - '+reason)
        if r.get('flags'): print('    flags: '+', '.join(r['flags']))
    print(f"\n{len(rows)} results. To approve, edit ratings-review.json or use --min-rating when applying.")


def apply_command(a):
    """CLI-4 (W-B): `photo-curator apply` for every schema version through apply_v3 (13.1/13.2/14.7,
    K-14a/b/c, S14-d). Exclusive lock (inside run_apply). Exit 1 when apply stops."""
    report=run_apply(Path(a.results).expanduser().resolve(), min_rating=a.min_rating,
                     include_existing=a.include_existing, yes=a.yes)
    for line in report.lines: print(line)
    if report.exit_code: sys.exit(report.exit_code)


def cmd_approve(a):
    """CLI-7 (W-B): --show shared lock; --rating / --revoke exclusive lock (inside approve_v3)."""
    if a.show:
        report=show_item(a.results, a.original_path, open_preview=a.open_preview)
        for line in report.lines: print(line)
        if a.open_preview and not report.opened: raise CommandRefusal(report.open_refusal)
        return
    if a.open_preview: raise CommandRefusal('--open-preview is only valid with --show')
    if a.revoke:
        result=revoke_item(a.results, a.original_path)
    else:
        if a.rating is None or a.preview_sha256 is None:
            raise CommandRefusal('approve needs --rating N and --preview-sha256 <hash> (or --show / --revoke)')
        result=approve_item(a.results, a.original_path, rating=a.rating, preview_sha256_arg=a.preview_sha256)
    print(f'{result.action}: {result.original_path} ({result.state_before} -> {result.state_after}); backup: {result.backup}')


def cmd_recover_archive(a):
    """CLI-9 (W-B): report shared lock, --complete / --rollback exclusive lock; exempt_journal (archive_v3)."""
    if a.complete or a.rollback:
        result=(recover_complete if a.complete else recover_rollback)(a.work)
        print(f'recover-archive --{result.mode}: moved {len(result.moved)} item(s); journal moved to {result.journal_moved_to}')
        return
    report=recover_report(a.work)
    print(report.message)
    for problem in report.problems: print(f'  problem: {problem}')
    for e in report.entries:
        print(f'  {e.state:9} {e.source} -> {e.target}  verified={e.verified}{"  " + e.detail if e.detail else ""}')


def cmd_apply(a):
    """Today's v2 apply, NOT reachable from any command since W-B (the `apply` subcommand runs
    apply_command). Kept unchanged as the reference implementation for the S14 v1/v2 equivalence test."""
    path=Path(a.results).expanduser().resolve(); data=load_results(path)
    if not a.yes:
        raise SystemExit('Safety stop: applying writes XMP sidecars. Re-run with --yes only after reviewing the JSON/CSV.')
    count=0; skipped=0
    for r in data['results']:
        rating=r.get('rating',0)
        # Always target the ORIGINAL image; preview_path is never a write target.
        img=Path(result_original(r))
        preview=result_preview(r)
        if r.get('original_path') and r.get('path') and r['original_path']!=r['path']:
            skipped+=1; print(f'[SKIP ambiguous original_path/path] {img.name}'); continue
        if is_generated(img) or (preview and Path(preview)==img) or img.suffix.lower() not in SUPPORTED:
            skipped+=1; print(f'[SKIP not an original image] {img}'); continue
        # Marker-based check: catches custom --output folders, nesting and symlinks.
        if in_generated_output(img) or (preview and Path(preview).resolve()==img.resolve()):
            skipped+=1; print(f'[SKIP inside generated output] {img}'); continue
        if not isinstance(rating,int) or not img.is_file() or rating<a.min_rating or rating not in range(1,6): skipped+=1; continue
        if not a.include_existing and Path(str(img)+'.xmp').exists():
            skipped+=1; print(f'[SKIP existing sidecar] {img.name}'); continue
        sidecar=write_rating_sidecar(img,rating,backup=True)
        count+=1; print(f'[OK] {img.name}: {rating} stars -> {sidecar.name}')
    print(f'Applied {count}; skipped {skipped}. Original photo files were not modified.')


REFUSALS = (StartupRefusal, AnalyzeError, CommandRefusal, ApplyRefused, ApproveRefusal, ApproveFailure,
            ArchiveRefusal, ArchiveFailure, RecoveryRefusal, RecoveryFailure, CoverageRefusal)


def main():
    p=argparse.ArgumentParser(prog='photo-curator',description='Strict Claude photo culling; preview first, explicit XMP apply.')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('scan',help='List supported images in a folder (read-only; takes no lock)'); s.add_argument('folder'); s.add_argument('--no-recursive',action='store_true'); s.set_defaults(func=cmd_scan)
    a=sub.add_parser('analyze',help='Create previews and v3 recommendations (JSON, CSV, gallery, manifest); never touches originals or metadata'); a.add_argument('folder'); a.add_argument('--output',help='Output folder: new, empty, or an existing marked Photo Curator output folder outside the source (default: <folder>/.photo-curator)'); a.add_argument('--batch-size',type=int,default=6); a.add_argument('--max-edge',type=int,default=1800); a.add_argument('--model',default='sonnet'); a.add_argument('--limit',type=int,help='Analyze at most this many images (for test runs)'); a.add_argument('--max-burst-size',type=int,default=12,help='Split longer bursts into comparison groups of at most this many frames (2-12)'); a.add_argument('--burst-window',type=float,default=1.0,help='Group captures within this many seconds; use 0 to disable'); a.add_argument('--no-recursive',action='store_true'); a.set_defaults(func=cmd_analyze)
    a.add_argument('--archive-existing',action='store_true',help='Move existing output into <output>/archive/<UTC timestamp>/ (journalled, nothing deleted), then analyze')
    g=sub.add_parser('gallery',help='Regenerate the interactive HTML review from existing v1/v2 results'); g.add_argument('results'); g.set_defaults(func=cmd_gallery)
    r=sub.add_parser('review',help='Print the results for human review (read-only; v1/v2 and v3)'); r.add_argument('results'); r.set_defaults(func=cmd_review)
    cv=sub.add_parser('coverage',help='Read-only coverage report from v3 results (facts only)'); cv.add_argument('results'); cv.set_defaults(func=cmd_coverage)
    w=sub.add_parser('apply',help='Write approved ratings to XMP sidecars (original.ext.xmp) only; requires --yes'); w.add_argument('results'); w.add_argument('--min-rating',type=int,default=1,help='Apply only ratings at or above this value (default 1)'); w.add_argument('--include-existing',action='store_true',help='Also update existing sidecars (backed up before the first change)'); w.add_argument('--yes',action='store_true',help='Required explicit confirmation; without it nothing is written'); w.set_defaults(func=apply_command)
    ap=sub.add_parser('approve',help='Inspect (--show) or approve / revoke one review item (v3 only; per item)')
    ap.add_argument('results'); ap.add_argument('original_path')
    mode=ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--show',action='store_true',help='Show the item and its preview SHA-256 (read-only)'); mode.add_argument('--rating',type=int,help='Approve the item with this rating (needs --preview-sha256)'); mode.add_argument('--revoke',action='store_true',help='Revoke an existing approval')
    ap.add_argument('--preview-sha256',help='SHA-256 of the preview inspected with --show; binds the approval to it'); ap.add_argument('--open-preview',action='store_true',help='With --show: open the preview in the image viewer if it passes the preview checks'); ap.set_defaults(func=cmd_approve)
    ra=sub.add_parser('recover-archive',help='Report on, complete or roll back an interrupted --archive-existing')
    ra.add_argument('work'); rmode=ra.add_mutually_exclusive_group()
    rmode.add_argument('--complete',action='store_true',help='Finish the interrupted archive (journalled renames only; nothing deleted)'); rmode.add_argument('--rollback',action='store_true',help='Undo the interrupted archive (journalled renames only; nothing deleted)'); ra.set_defaults(func=cmd_recover_archive)
    args=p.parse_args()
    if args.command=='analyze' and not 2<=args.max_burst_size<=12: p.error('--max-burst-size must be 2..12')
    if getattr(args,'batch_size',1)<1 or getattr(args,'batch_size',1)>12: p.error('--batch-size must be 1..12')
    try:
        args.func(args)
    except REFUSALS as e:   # CLI-3 / M-23: refusals and failures exit 1
        print(f'photo-curator: {e.message}', file=sys.stderr)
        sys.exit(1)

if __name__=='__main__': main()
