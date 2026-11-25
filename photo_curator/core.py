from __future__ import annotations
import hashlib, json, os, re, shutil, subprocess, tempfile, time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .capture_time import CaptureTime, parse_capture, wall_clock_seconds

SUPPORTED = {'.jpg','.jpeg','.png','.webp','.tif','.tiff','.arw','.cr2','.cr3','.nef','.nrw','.raf','.rw2','.orf','.dng'}
RAW = {'.arw','.cr2','.cr3','.nef','.nrw','.raf','.rw2','.orf','.dng'}
GENERATED_DIR = '.photo-curator'  # default working folder for previews/reviews; never a source
# Marker file written into every generated output folder (and its previews/ subfolder).
# Any directory containing it - whatever its name or location - is never scanned for
# source images, and apply refuses targets inside it. Stable name: do not change.
OUTPUT_MARKER = '.photo-curator-output'
OUTPUT_MARKER_TEXT = ('Photo Curator generated output (previews, review JSON/CSV/HTML).\n'
                      'Files below this directory are never treated as source images, and\n'
                      '`photo-curator apply` refuses to write ratings for anything inside it.\n'
                      'Do not store original photos here. Do not delete this file.\n')
SCHEMA_VERSIONS = {1, 2}          # 2 adds original_path/preview_path/session_id
NS = {'x':'adobe:ns:meta/', 'rdf':'http://www.w3.org/1999/02/22-rdf-syntax-ns#', 'xmp':'http://ns.adobe.com/xap/1.0/'}
for k,v in NS.items(): ET.register_namespace(k,v)

STRICT_PROMPT = """You are a strict professional event-photo editor. Inspect every supplied preview visually using the Read tool. Evaluate only visible evidence.

Return TWO independent scores for every image:
- technical_score: 1..5 for focus/sharpness appropriate to intent, exposure, motion handling, lighting, and technical execution.
- editorial_score: 1..5 for moment, expression/interaction, storytelling, composition, context, and uniqueness.
Then assign an overall rating 1..5 conservatively.

Strictness: a clean but ordinary record shot is usually 3, not 4. Technical polish alone must not create a high editorial score. Overall 4-5 normally requires both scores >=4; an exceptional documentary moment may receive overall 4 with technical_score=3. Overall 5 requires both >=4. Do not punish intentional motion blur, shallow depth of field, unusual angles, high-contrast lighting, or creative framing when they clearly support the image. Event coverage value matters: meaningful interactions can score highly editorially even when not conventionally beautiful.

Sessions: each image belongs to a session_id (a separate shoot/card folder). Never compare or group images across sessions.

Burst comparison: images sharing burst_group were captured close together and must be compared as a sequence. Mark the strongest frame(s) best_in_burst=true and others false. If no meaningful burst group exists, best_in_burst=null. Penalize near-duplicates only relative to alternatives in their group. Do not force a winner if frames are unrelated or indistinguishable.

Return ONLY valid JSON, no markdown:
{\"photos\":[{\"path\":\"EXACT_ORIGINAL_PATH\",\"rating\":1,\"technical_score\":1,\"editorial_score\":1,\"decision\":\"reject|weak|usable|strong|exceptional\",\"confidence\":0.0,\"burst_group\":\"group-id-or-null\",\"best_in_burst\":true,\"reasons\":[\"visible evidence\"],\"flags\":[\"blur|missed_focus|closed_eyes|duplicate|poor_exposure|awkward_expression|clutter|none\"]}]}
Use integer scores 1..5 and confidence 0..1. Include exactly one result per image. For unreadable images use rating/technical_score/editorial_score 0 and decision unreadable. The \"path\" value must be the exact ORIGINAL path, never the preview path. Preserve supplied burst_group values."""


def run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def is_generated(path: Path) -> bool:
    """True for anything inside a .photo-curator working folder (previews, reviews)."""
    return GENERATED_DIR in Path(path).parts


def _marked_dir_checker():
    """Cached 'does this directory contain OUTPUT_MARKER?' test for one scan."""
    cache: dict[Path,bool] = {}
    def marked(d: Path) -> bool:
        if d not in cache: cache[d] = (d / OUTPUT_MARKER).is_file()
        return cache[d]
    return marked


def in_generated_output(path: Path) -> bool:
    """True if `path` lies inside generated output: a `.photo-curator` folder, or any
    directory (at any depth, checked both as given and after resolving symlinks) that
    contains OUTPUT_MARKER."""
    p = Path(path).expanduser()
    if not p.is_absolute(): p = Path.cwd() / p
    resolved = p.resolve()
    if is_generated(p) or is_generated(resolved): return True
    marked = _marked_dir_checker()
    return any(marked(d) for d in (*p.parents, *resolved.parents))


def mark_output_dir(directory: Path) -> Path:
    """Create `directory` if needed and atomically place OUTPUT_MARKER in it. Idempotent."""
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / OUTPUT_MARKER
    if marker.is_file(): return marker
    if marker.exists() or marker.is_symlink():
        raise RuntimeError(f'Cannot create output marker; {marker} exists and is not a regular file')
    tmp = directory / f'{OUTPUT_MARKER}.{os.getpid()}.tmp'
    tmp.write_text(OUTPUT_MARKER_TEXT, encoding='utf-8')
    os.replace(tmp, marker)
    return marker


def validate_output_dir(source_root: Path, output: Path | str) -> Path:
    """Resolve and vet a user-supplied --output folder. Raises ValueError if unsafe:
    inside (or equal to) the source folder, containing the source folder, an existing
    non-directory, or an existing non-empty folder that is not marked Photo Curator output."""
    src = Path(source_root).expanduser().resolve()
    out = Path(output).expanduser().resolve()   # follows symlinks in every existing component
    if out == src or out.is_relative_to(src):
        raise ValueError(f'--output resolves to {out}, which is inside the source folder {src}. '
                         'Generated previews there could later be mistaken for original photos. '
                         f'Choose a folder outside {src}, or omit --output to use the default {src / GENERATED_DIR}.')
    if src.is_relative_to(out):
        raise ValueError(f'--output resolves to {out}, which contains the source folder {src}. '
                         'Marking it as generated output would hide your originals from future scans '
                         'and block apply. Choose a separate folder.')
    if out.exists():
        if not out.is_dir(): raise ValueError(f'--output {out} exists and is not a directory.')
        if not (out / OUTPUT_MARKER).is_file() and any(out.iterdir()):
            raise ValueError(f'--output {out} already exists, is not empty, and is not a Photo Curator output folder '
                             f'(no {OUTPUT_MARKER} marker). Refusing to mark or overwrite an arbitrary directory; '
                             'choose a new or empty folder.')
    return out


def find_images(root: Path, recursive: bool=True) -> list[Path]:
    """Source images only. Excludes `.photo-curator` folders and any directory containing
    OUTPUT_MARKER, at any depth, checked on the path as found and after resolving symlinks.
    Scanning a root that is itself inside generated output returns nothing."""
    root = root.resolve()
    marked = _marked_dir_checker()
    if is_generated(root) or any(marked(d) for d in (root, *root.parents)): return []
    it = root.rglob('*') if recursive else root.iterdir()
    found = []
    for p in it:
        if p.suffix.lower() not in SUPPORTED: continue
        rel = p.relative_to(root)
        if is_generated(rel) or any(marked(root / a) for a in rel.parents): continue
        if not p.is_file(): continue
        rp = p.resolve()
        if is_generated(rp) or any(marked(d) for d in rp.parents): continue   # symlink into generated output
        found.append(rp)
    return sorted(found)


def session_for(path: Path, root: Path | None=None) -> str:
    """Session id = the image's folder relative to the source root.

    e.g. DCIM/100MSDCF/DSC01289.ARW -> 'DCIM/100MSDCF', DCIM 2/GAT04686.ARW -> 'DCIM 2'.
    Files directly in root get '.'; without a root the absolute parent is used.
    """
    parent = Path(path).resolve().parent
    if root is None: return parent.as_posix()
    try: return parent.relative_to(Path(root).resolve()).as_posix()
    except ValueError: return parent.as_posix()


def result_original(r: dict[str,Any]) -> str:
    """Original image path for a result; falls back to legacy 'path'."""
    return str(r.get('original_path') or r.get('path') or '')


def result_preview(r: dict[str,Any]) -> str:
    """Saved preview path for a result ('preview' is a legacy key)."""
    return str(r.get('preview_path') or r.get('preview') or '')


def make_preview(src: Path, preview_dir: Path, max_edge: int=1800) -> Path:
    preview_dir.mkdir(parents=True, exist_ok=True)
    # Deterministic name (builtin hash() is randomized per process).
    digest = hashlib.sha1(str(src).encode('utf-8')).hexdigest()[:10]
    out = preview_dir / (src.stem + "__" + digest + ".jpg")
    if out.exists(): out.unlink()  # stale generated preview; avoids darktable-cli _01 renames
    if src.suffix.lower() in RAW:
        exe = shutil.which('darktable-cli')
        if not exe:
            raise RuntimeError('RAW file found but darktable-cli is missing. Install darktable, then retry.')
        p = run([exe, str(src), str(out), '--width', str(max_edge), '--height', str(max_edge), '--hq', 'true'], timeout=300)
        if p.returncode != 0 or not out.exists():
            raise RuntimeError('darktable-cli failed: ' + p.stderr[-1000:])
        return out
    try:
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            im.save(out, 'JPEG', quality=90, optimize=True)
    except Exception as e:
        raise RuntimeError(f'Cannot create preview: {e}') from e
    return out


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try: return json.loads(text)
    except json.JSONDecodeError: pass
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise ValueError('Claude did not return valid JSON')



def capture_records(paths: list[Path]) -> dict[str, CaptureTime]:
    """Read capture times with ONE ExifTool call (spec 14.3). Returns path -> CaptureTime
    for every image whose DateTimeOriginal parses; missing or unparseable ones are absent
    (untimed). No fallback source is ever used."""
    exe=shutil.which('exiftool')
    if not exe or not paths: return {}
    p=run([exe,'-json','-DateTimeOriginal','-SubSecTimeOriginal','-OffsetTimeOriginal',*map(str,paths)],
          timeout=max(120,len(paths)//5))
    # exiftool exits non-zero if any single file fails but still reports the rest.
    try: rows=json.loads(p.stdout) if p.stdout.strip() else []
    except json.JSONDecodeError: return {}
    if not isinstance(rows,list): return {}
    requested={str(Path(p).resolve()) for p in paths}
    out: dict[str,CaptureTime]={}
    for row in rows:
        if not isinstance(row,dict): continue
        name=row.get('SourceFile')
        if not isinstance(name,str) or not name: continue
        resolved=str(Path(name).resolve())
        if resolved not in requested: continue   # ignore rows for paths that were not requested
        record=parse_capture(row.get('DateTimeOriginal'),row.get('SubSecTimeOriginal'),row.get('OffsetTimeOriginal'))
        if record is not None: out[resolved]=record
    return out


def capture_times(paths: list[Path]) -> dict[str, float]:
    """Wall-clock capture times (seconds) for burst detection; untimed images are absent.
    Derived from the same single read as capture_records, independent of the computer's
    timezone; any offset is ignored (spec 14.3)."""
    return {path: wall_clock_seconds(record) for path, record in capture_records(paths).items()}

def group_by_session(paths: list[Path], root: Path | None=None) -> dict[str,list[Path]]:
    sessions: dict[str,list[Path]]={}
    for p in paths: sessions.setdefault(session_for(p,root),[]).append(p)
    return sessions


def session_capture_times(paths: list[Path], root: Path | None=None) -> dict[str,float]:
    """Capture times for all paths, read from the originals one session at a time."""
    times: dict[str,float]={}
    for members in group_by_session(paths,root).values(): times.update(capture_times(members))
    return times


def detect_bursts(paths: list[Path], window_seconds: float=1.0, root: Path | None=None,
                  times: dict[str,float] | None=None) -> dict[str,str]:
    """Group adjacent captures within a short timestamp window, separately per session.

    Timestamps come from the ORIGINAL files (DateTimeOriginal + SubSecTimeOriginal);
    pass `times` to reuse already-read values. Images without a timestamp are never
    grouped. Images from different sessions are never grouped, even if clocks overlap.
    """
    if times is None: times=session_capture_times(paths,root)
    result={}
    for sid,members in sorted(group_by_session(paths,root).items()):
        ordered=sorted(((times[str(p)],p) for p in members if str(p) in times),key=lambda x:(x[0],str(x[1])))
        current=[]
        def save(group):
            if len(group)>1:
                gid=f'burst-{group[0][1].stem}-{group[-1][1].stem}'
                if gid in result.values():  # keep ids unique if stems repeat across sessions
                    gid+='-'+hashlib.sha1(sid.encode('utf-8')).hexdigest()[:6]
                for _,path in group: result[str(path)]=gid
        for item in ordered:
            if current and item[0]-current[-1][0]>window_seconds:
                save(current); current=[]
            current.append(item)
        save(current)
    return result


def chronological(paths: list[Path], times: dict[str,float]) -> list[Path]:
    """Sort by capture time (DateTimeOriginal+SubSec); untimed images go last, by path."""
    return sorted(paths,key=lambda p:(str(p) not in times, times.get(str(p),0.0), str(p)))


def _partition_sizes(gaps_ms: list[int], max_size: int) -> list[int]:
    """Split a chronological run of len(gaps_ms)+1 frames into the fewest contiguous
    pieces of <= max_size frames, cutting where the summed timestamp gaps are largest.
    Equal gaps are broken by preferring evenly sized pieces. Returns piece sizes in order."""
    n=len(gaps_ms)+1
    k=-(-n//max_size)
    if k<=1: return [n]
    # dp[j][i]: best (gap_sum, -sum_sq_sizes) for the first i frames in j pieces.
    dp=[[None]*(n+1) for _ in range(k+1)]; back=[[0]*(n+1) for _ in range(k+1)]
    dp[0][0]=(0,0)
    for j in range(1,k+1):
        for i in range(j,n+1):
            best=None
            for s in range(1,min(max_size,i)+1):
                prev=dp[j-1][i-s]
                if prev is None: continue
                cut=gaps_ms[i-s-1] if i-s>0 else 0   # gap just before this piece starts
                score=(prev[0]+cut, prev[1]-s*s)
                if best is None or score>best: best=score; back[j][i]=s
            dp[j][i]=best
    sizes=[]; i=n
    for j in range(k,0,-1): s=back[j][i]; sizes.append(s); i-=s
    return sizes[::-1]


def split_bursts(burst_map: dict[str,str], times: dict[str,float],
                 max_size: int | None) -> tuple[dict[str,str], dict[str,str]]:
    """Cap burst size for comparison. Returns (group_map, sequence_map):

    - group_map: path -> comparison group (<= max_size frames). Used as burst_group.
    - sequence_map: path -> original detected burst (the full sequence).
    Bursts within the cap keep their id in both maps. Oversized bursts are split in
    chronological order into '<sequence>-p1', '-p2', ... Bursts never span sessions
    (detect_bursts guarantees it), so neither do their parts. max_size of 0/None disables.
    """
    sequence_map=dict(burst_map)
    if not max_size: return dict(burst_map), sequence_map
    if max_size<2: raise ValueError('max_burst_size must be >= 2 (or 0 to disable)')
    members: dict[str,list[str]]={}
    for p,gid in burst_map.items(): members.setdefault(gid,[]).append(p)
    group_map: dict[str,str]={}
    for gid,paths in members.items():
        ordered=sorted(paths,key=lambda p:(p not in times, times.get(p,0.0), p))
        if len(ordered)<=max_size:
            for p in ordered: group_map[p]=gid
            continue
        t=[times.get(p) for p in ordered]
        gaps=[round((b-a)*1000) if a is not None and b is not None else 0 for a,b in zip(t,t[1:])]
        i=0
        for part,size in enumerate(_partition_sizes(gaps,max_size),1):
            for p in ordered[i:i+size]: group_map[p]=f'{gid}-p{part}'
            i+=size
    if set(group_map)!=set(burst_map): raise RuntimeError('Burst split error: images lost or added')
    return group_map, sequence_map


def plan_batches(paths: list[Path], batch_size: int, root: Path | None=None,
                 times: dict[str,float] | None=None, burst_map: dict[str,str] | None=None) -> list[list[Path]]:
    """Build analysis batches that never mix sessions and never split a burst group.

    Images are ordered by session_id, then capture time. Each burst group is an
    indivisible unit; a unit that does not fit the current batch starts a new one,
    and a burst larger than batch_size becomes its own (oversized) batch. Non-burst
    images fill the remaining capacity. Every input path appears exactly once.
    """
    if batch_size<1: raise ValueError('batch_size must be >= 1')
    times=times or {}; burst_map=burst_map or {}
    batches: list[list[Path]]=[]
    for _sid,members in sorted(group_by_session(paths,root).items()):
        units: list[list[Path]]=[]; unit_for_group: dict[str,list[Path]]={}
        for p in chronological(members,times):
            gid=burst_map.get(str(p))
            if gid is None: units.append([p])
            elif gid in unit_for_group: unit_for_group[gid].append(p)
            else: unit_for_group[gid]=[p]; units.append(unit_for_group[gid])
        current: list[Path]=[]
        for unit in units:
            if current and len(current)+len(unit)>batch_size:
                batches.append(current); current=[]
            current.extend(unit)
            if len(current)>=batch_size:
                batches.append(current); current=[]
        if current: batches.append(current)
    flat=[str(p) for b in batches for p in b]
    if len(flat)!=len(set(flat)) or set(flat)!={str(p) for p in paths}:
        raise RuntimeError('Batch planning error: images not scheduled exactly once')
    return batches


def analyze_batch(paths: list[Path], preview_map: dict[str,Path], model: str='sonnet', burst_map: dict[str,str] | None=None, session_map: dict[str,str] | None=None, sequence_map: dict[str,str] | None=None) -> list[dict[str,Any]]:
    """Ask Claude to inspect previews. Returns Claude's raw 'photos' array (unvalidated);
    callers must pass it through validate_batch_results()."""
    claude=shutil.which('claude')
    if not claude: raise RuntimeError('Claude Code CLI not found. Install/authenticate Claude Code first.')
    burst_map=burst_map or {}; session_map=session_map or {}; sequence_map=sequence_map or {}
    def seq_note(p):  # only shown when a long sequence was split into comparison parts
        s=sequence_map.get(str(p)); return f'\n  part of longer sequence: {s}' if s and s!=burst_map.get(str(p)) else ''
    listed='\n'.join(f'- Original: {p}\n  Preview to inspect: {preview_map[str(p)]}\n  session_id: {session_map.get(str(p), "unknown")}\n  burst_group: {burst_map.get(str(p), "null")}{seq_note(p)}' for p in paths)
    prompt=STRICT_PROMPT+'\n\nInspect these previews with the Read tool. Compare images sharing a burst_group (never across sessions). In "path", return the exact ORIGINAL path, not the preview path, and preserve supplied burst_group values:\n'+listed
    with tempfile.NamedTemporaryFile('w',suffix='.txt',encoding='utf-8',delete=False) as f:
        f.write(prompt); prompt_file=f.name
    try:
        cli_prompt='Read the prompt file at '+prompt_file+' and follow its instructions exactly.'
        cmd=[claude,'-p',cli_prompt,'--model',model,'--output-format','json','--max-turns',str(max(24,len(paths)*2+4)),'--allowedTools','Read','--disallowedTools','Bash,Edit,Write']
        proc=run(cmd,timeout=max(360,len(paths)*100))
        if proc.returncode: raise RuntimeError('Claude Code failed: '+(proc.stderr or proc.stdout)[-2000:])
        envelope=json.loads(proc.stdout); content=envelope.get('result',proc.stdout)
        data=_extract_json(content if isinstance(content,str) else json.dumps(content))
        photos=data.get('photos')
        if not isinstance(photos,list): raise ValueError('JSON missing photos array')
        return photos
    finally: os.unlink(prompt_file)


def validate_batch_results(batch: list[Path], photos: list[Any], preview_map: dict[str,Path],
                           burst_map: dict[str,str], session_map: dict[str,str],
                           sequence_map: dict[str,str] | None=None) -> tuple[list[dict[str,Any]], list[dict[str,Any]]]:
    """Check Claude's 'photos' against the batch. Nothing is dropped silently: every
    rejected, duplicate, unknown or missing item is reported in the returned errors."""
    originals={str(p) for p in batch}
    preview_to_original={str(preview_map[o]):o for o in originals if o in preview_map}
    results, errors, seen = [], [], set()

    def ctx(orig: str) -> dict[str,Any]:
        return {'path':orig,'original_path':orig,'preview_path':str(preview_map.get(orig,'')),'session_id':session_map.get(orig)}

    for r in photos:
        if not isinstance(r,dict):
            errors.append({'error':'Claude returned a non-object result','result':r}); continue
        returned=str(r.get('path',''))
        orig=returned if returned in originals else preview_to_original.get(returned)
        if orig is None:
            errors.append({'error':'Claude returned a path outside this batch','result':r}); continue
        if orig in seen:
            errors.append({**ctx(orig),'error':'Duplicate result for this image; first kept','result':r}); continue
        rating=r.get('rating')
        if isinstance(rating,bool) or not isinstance(rating,int) or rating not in range(0,6):
            errors.append({**ctx(orig),'error':'Invalid rating','result':r}); continue
        seen.add(orig)
        if returned!=orig: r['path_corrected_from_preview']=True
        for score_key in ('technical_score','editorial_score'):
            score=r.get(score_key, rating)
            r[score_key]=score if isinstance(score,int) and not isinstance(score,bool) and score in range(0,6) else 0
        r.update(ctx(orig))  # authoritative mapping from our side, not Claude's
        r['burst_group']=burst_map.get(orig)
        r['burst_sequence']=(sequence_map or {}).get(orig, r['burst_group'])
        if r['burst_group'] and r.get('best_in_burst') not in (True,False): r['best_in_burst']=False
        if not r['burst_group']: r['best_in_burst']=None
        results.append(r)
    for orig in sorted(originals-seen):
        if not any(e.get('original_path')==orig for e in errors):
            errors.append({**ctx(orig),'error':'Claude returned no result for this image; left unrated'})
    return results, errors


def write_rating_sidecar(image: Path, rating: int, backup: bool=True) -> Path:
    """Write XMP Rating to digiKam's default filename.ext.xmp sidecar; never touch source image."""
    if rating not in range(1,6): raise ValueError('Rating must be 1..5')
    sidecar = Path(str(image) + '.xmp')
    if sidecar.exists() and backup:
        backup_path = sidecar.with_name(sidecar.name + '.photo-curator.bak')
        if not backup_path.exists(): shutil.copy2(sidecar, backup_path)
    if sidecar.exists():
        try: tree = ET.parse(sidecar); root = tree.getroot()
        except ET.ParseError: raise RuntimeError(f'Existing sidecar is invalid XML; left untouched: {sidecar}')
    else:
        root = ET.Element('{adobe:ns:meta/}xmpmeta')
        tree = ET.ElementTree(root)
        rdf = ET.SubElement(root, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF')
        desc = ET.SubElement(rdf, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description', {'{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about':''})
    descriptions = root.findall('.//rdf:Description', NS)
    if not descriptions:
        rdf = root.find('.//rdf:RDF', NS)
        if rdf is None: rdf = ET.SubElement(root, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF')
        desc = ET.SubElement(rdf, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description', {'{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about':''})
    else: desc = descriptions[0]
    desc.set('{http://ns.adobe.com/xap/1.0/}Rating', str(rating))
    tmp = sidecar.with_name(sidecar.name + '.tmp')
    tree.write(tmp, encoding='utf-8', xml_declaration=True)
    os.replace(tmp, sidecar)
    return sidecar


def load_results(path: Path) -> dict[str,Any]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') not in SCHEMA_VERSIONS: raise ValueError('Unsupported results schema')
    if not isinstance(data.get('results'),list): raise ValueError('Results file missing top-level results array')
    return data
