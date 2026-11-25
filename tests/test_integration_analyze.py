"""Controlled integration test of `photo-curator analyze` (cmd_analyze) end to end.

W-A (X-4, limited change): analyze now runs the v3 pipeline only. The fake Claude answers with
valid v3 results (tests.fake_claude_v3), the gallery is `ratings-review.html` (D-2), and v2-only
checks are replaced by their v3 equivalents (completeness manifest instead of load_results;
`gallery` refusing v3 files, D-1). Every safety assertion is kept.

Isolation and safety:
- Builds a throwaway dataset in a fresh temp directory (honours $TMPDIR); never touches
  any real photo folder or existing project results.
- Real code paths: find_images, Pillow previews for JPG, ExifTool timestamp reads,
  detect_bursts, split_bursts, plan_batches, prompt construction, validate_batch_results,
  JSON/CSV/HTML writers, cmd_gallery, load_results.
- Mocked at core.run / shutil.which only: `darktable-cli` (dummy ARWs are not real RAW)
  and the `claude` CLI (a deterministic fake that parses the real prompt file). Any other
  external command raises, so the real Claude CLI cannot run.
- apply is never called. The temp directory is removed only if every assertion passes.
"""
from __future__ import annotations
import argparse, csv, contextlib, hashlib, io, json, os, re, shutil, subprocess, tempfile, unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from photo_curator import cli, core
from photo_curator.core import SCHEMA_VERSIONS, is_generated, load_results, result_original, result_preview
from photo_curator.output_v3 import check_completeness
from tests.fake_claude_v3 import FakeClaudeV3

FAKE_CLAUDE, FAKE_DARKTABLE = '/fake/bin/claude', '/fake/bin/darktable-cli'
S1, S2 = 'DCIM/100MSDCF', 'DCIM 2'
DAY = '2026:11:01'


def _stamp(sec: float) -> tuple[str, str]:
    """Seconds after 10:00:00 -> (DateTimeOriginal, 3-digit SubSecTimeOriginal)."""
    whole, ms = int(sec), round((sec - int(sec)) * 1000)
    if ms == 1000: whole, ms = whole + 1, 0
    h, rem = divmod(10 * 3600 + whole, 3600); m, s = divmod(rem, 60)
    return f'{DAY} {h:02d}:{m:02d}:{s:02d}', f'{ms:03d}'


# (session, filename, seconds after 10:00:00 or None for "no EXIF timestamp")
LONG = [0.1 * i for i in range(5)] + [0.1 * i + 0.5 for i in range(5, 15)]   # 0.6 s gap after frame 5
DATASET = (
    [(S1, f'DSC{1 + i:05d}.ARW', 0.005 + 0.2 * i) for i in range(4)]          # normal burst, leading-zero subsec
    + [(S1, 'DSC00005.JPG', 30.0), (S1, 'DSC00006.ARW', 60.0)]               # singles
    + [(S1, f'DSC{10 + i:05d}.ARW', 300.0 + t) for i, t in enumerate(LONG)]   # 15-frame burst > cap
    + [(S1, 'DSC00099.JPG', None)]                                            # no timestamp
    + [(S2, f'GAT{1 + i:05d}.ARW', 0.105 + 0.2 * i) for i in range(3)]        # overlaps S1 burst clock
    + [(S2, 'GAT00004.JPG', 45.0), (S2, 'GAT00005.jpeg', 120.0)]
)
SEQ_A, SEQ_LONG, SEQ_B = 'burst-DSC00001-DSC00004', 'burst-DSC00010-DSC00024', 'burst-GAT00001-GAT00003'
EXPECTED_GROUP = {**{f'DSC{1 + i:05d}.ARW': (SEQ_A, SEQ_A) for i in range(4)},
                  **{f'DSC{10 + i:05d}.ARW': (SEQ_LONG + '-p1', SEQ_LONG) for i in range(5)},
                  **{f'DSC{15 + i:05d}.ARW': (SEQ_LONG + '-p2', SEQ_LONG) for i in range(10)},
                  **{f'GAT{1 + i:05d}.ARW': (SEQ_B, SEQ_B) for i in range(3)}}
EXPECTED_BATCHES = [   # sessions in sorted order: 'DCIM 2' < 'DCIM/100MSDCF' (space sorts before '/')
    ['GAT00001.ARW', 'GAT00002.ARW', 'GAT00003.ARW', 'GAT00004.JPG', 'GAT00005.jpeg'],
    ['DSC00001.ARW', 'DSC00002.ARW', 'DSC00003.ARW', 'DSC00004.ARW', 'DSC00005.JPG', 'DSC00006.ARW'],
    [f'DSC{10 + i:05d}.ARW' for i in range(5)],
    [f'DSC{15 + i:05d}.ARW' for i in range(10)],
    ['DSC00099.JPG'],
]
PROMPT_ITEM = re.compile(r'- Original: (?P<orig>.+)\n  Preview to inspect: (?P<prev>.+)\n  session_id: (?P<sid>.+)\n'
                         r'  burst_group: (?P<group>.+)(?:\n  part of longer sequence: (?P<seq>.+))?')


def make_dummy(path: Path, sec: float | None, idx: int):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new('RGB', (96, 64), ((idx * 37) % 256, (idx * 91) % 256, (idx * 53) % 256))
    exif = Image.Exif()
    if sec is not None:
        dto, sub = _stamp(sec)
        ifd = exif.get_ifd(0x8769); ifd[0x9003] = dto; ifd[0x9291] = sub
    im.save(path, 'JPEG', exif=exif)   # dummy "ARW" files are JPEG-encoded; ExifTool reads them by content


def snapshot(root: Path) -> dict[str, tuple]:
    out = {}
    for p in sorted(root.rglob('*')):
        if p.is_file() and not is_generated(p.relative_to(root)):
            st = p.stat(); out[str(p)] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns)
    return out


class _Cards(HTMLParser):
    def __init__(self):
        super().__init__(); self.cards, self.srcs, self.options = [], [], []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'article': self.cards.append(a)
        elif tag == 'img': self.srcs.append(a.get('src'))
        elif tag == 'option': self.options.append(a.get('value'))


class AnalyzeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.checks: list[str] = []
        self.tmp = Path(tempfile.mkdtemp(prefix='photo-curator-it-')).resolve()
        self.root = self.tmp / 'library'
        for i, (sid, name, sec) in enumerate(DATASET): make_dummy(self.root / sid / name, sec, i)
        self.claude_calls: list[list[dict]] = []
        self.commands: list[list[str]] = []

    def check(self, label: str, cond: bool, detail=''):
        self.assertTrue(cond, f'{label} {detail}')
        self.checks.append(label)

    # ---- mocks -------------------------------------------------------------------
    def fake_run(self, real_run):
        def run(cmd, timeout=180):
            self.commands.append(list(cmd))
            exe = cmd[0]
            if exe == FAKE_DARKTABLE:                       # make_preview(RAW): render the dummy with Pillow
                from PIL import Image
                with Image.open(cmd[1]) as im: im.convert('RGB').save(cmd[2], 'JPEG')
                return subprocess.CompletedProcess(cmd, 0, '', '')
            if exe == FAKE_CLAUDE: return self.fake_claude(cmd)
            if Path(exe).name == 'exiftool': return real_run(cmd, timeout)   # read-only metadata
            raise AssertionError(f'Unexpected external command: {cmd[:2]}')
        return run

    def fake_claude(self, cmd):
        prompt_file = re.search(r'Read the prompt file at (\S+)', cmd[cmd.index('-p') + 1]).group(1)
        prompt_text = Path(prompt_file).read_text(encoding='utf-8')
        items = [m.groupdict() for m in PROMPT_ITEM.finditer(prompt_text)]
        self.claude_calls.append(items)
        photos = FakeClaudeV3().photos_for(prompt_text)   # valid v3 answers; first frame of a group wins
        envelope = {'type': 'result', 'result': json.dumps({'photos': photos})}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(envelope), '')

    def fake_which(self, real_which):
        return lambda name, *a, **k: {'claude': FAKE_CLAUDE, 'darktable-cli': FAKE_DARKTABLE}.get(name) or (
            real_which(name, *a, **k) if name == 'exiftool' else None)

    # ---- test --------------------------------------------------------------------
    def test_analyze_pipeline(self):
        if not shutil.which('exiftool'): self.skipTest('exiftool not installed')
        originals = {str((self.root / sid / name).resolve()): (sid, name, sec) for sid, name, sec in DATASET}
        before = snapshot(self.root)
        self.check('dataset: 27 originals, 2 sessions, ARW+JPG+jpeg', len(before) == 27 == len(originals)
                   and {Path(p).suffix for p in before} == {'.ARW', '.JPG', '.jpeg'})

        args = argparse.Namespace(folder=str(self.root), output=None, batch_size=6, max_edge=256, model='mock',
                                  limit=None, max_burst_size=12, burst_window=1.0, no_recursive=False)
        out = io.StringIO()
        with mock.patch.object(core, 'run', self.fake_run(core.run)), \
             mock.patch.object(core.shutil, 'which', self.fake_which(shutil.which)), \
             contextlib.redirect_stdout(out):
            cli.cmd_analyze(args)
        log = out.getvalue()

        work = self.root / '.photo-curator'
        json_path, csv_path, html_path = work / 'ratings-review.json', work / 'ratings-review.csv', work / 'ratings-review.html'
        payload = json.loads(json_path.read_text(encoding='utf-8'))
        results = payload['results']; by_orig = {r['original_path']: r for r in results}

        # Safety of external calls
        self.check('no real claude/darktable executed (only fakes + exiftool)',
                   all(c[0] in (FAKE_CLAUDE, FAKE_DARKTABLE) or Path(c[0]).name == 'exiftool' for c in self.commands))
        self.check('claude mock invoked once per batch (5 batches)', len(self.claude_calls) == 5, len(self.claude_calls))

        # 1. exactly once
        self.check('every original represented exactly once', len(results) == 27 and set(by_orig) == set(originals))
        self.check('no errors recorded', payload['errors'] == [], payload['errors'])
        scheduled = [it['orig'] for call in self.claude_calls for it in call]
        self.check('every original sent to Claude exactly once', sorted(scheduled) == sorted(originals))

        # 2./3. original vs preview paths
        for r in results:
            o, pv = Path(r['original_path']), Path(r['preview_path'])
            self.assertTrue(r['path'] == r['original_path'] and o.is_file() and not is_generated(o.relative_to(self.root)))
            self.assertTrue(pv.is_file() and pv.parent == work / 'previews' and pv != o)
            self.assertEqual(Path(result_original(r)), o); self.assertNotEqual(result_original(r), result_preview(r))
        self.check('original_path == path, exists, outside .photo-curator', True)
        self.check('preview_path exists in .photo-curator/previews and differs from original', True)
        self.check('apply target (result_original) is never a preview', True)
        prompted = {it['orig']: it['prev'] for call in self.claude_calls for it in call}
        self.check('Claude was shown the same preview recorded in results',
                   all(prompted[o] == by_orig[o]['preview_path'] for o in originals))

        # 4. session_id
        self.check('session_id preserved', all(by_orig[o]['session_id'] == sid for o, (sid, _, _) in originals.items()))
        self.check('session_id given to Claude matches', all(it['sid'] == originals[it['orig']][0]
                                                             for call in self.claude_calls for it in call))

        # 5. burst_group / burst_sequence
        for o, (_, name, _) in originals.items():
            group, seq = EXPECTED_GROUP.get(name, (None, None))
            self.assertEqual((by_orig[o]['burst_group'], by_orig[o]['burst_sequence']), (group, seq), name)
        self.check('burst_group and burst_sequence exactly as expected (incl. split p1=5, p2=10)', True)
        self.check('untimed DSC00099.JPG analysed, not grouped',
                   by_orig[str((self.root / S1 / 'DSC00099.JPG').resolve())]['burst_group'] is None)
        self.check('"part of longer sequence" sent to Claude only for split parts',
                   all((it['seq'] == SEQ_LONG) == (it['group'].startswith(SEQ_LONG + '-p'))
                       for call in self.claude_calls for it in call))

        # 6. best_in_burst
        groups: dict[str, list[dict]] = {}
        for r in results:
            if r['burst_group']: groups.setdefault(r['burst_group'], []).append(r)
            else: self.assertIsNone(r['best_in_burst'])
        self.check('best_in_burst is null for every non-burst image', True)
        self.check('exactly one best_in_burst per comparison group (4 groups)',
                   len(groups) == 4 and all(sum(r['best_in_burst'] is True for r in m) == 1 for m in groups.values()))
        self.check('best_in_burst only True/False inside groups',
                   all(r['best_in_burst'] in (True, False) for m in groups.values() for r in m))

        # 7./8. batches
        names = [[Path(it['orig']).name for it in call] for call in self.claude_calls]
        self.check('batches exactly as planned', names == EXPECTED_BATCHES, names)
        self.check('no batch crosses sessions', all(len({originals[it['orig']][0] for it in c}) == 1 for c in self.claude_calls))
        call_of = {it['orig']: i for i, c in enumerate(self.claude_calls) for it in c}
        self.check('each comparison group sent in a single batch',
                   all(len({call_of[r['original_path']] for r in m}) == 1 for m in groups.values()))
        self.check('no group exceeds max_burst_size=12', max(len(m) for m in groups.values()) <= 12)

        # 9. JSON vs CSV
        with csv_path.open(encoding='utf-8', newline='') as f: rows = list(csv.DictReader(f))
        fmt = lambda v: '' if v is None else str(v)
        cols = ['rating', 'technical_score', 'editorial_score', 'decision', 'confidence', 'session_id', 'burst_group',
                'burst_sequence', 'best_in_burst', 'path', 'original_path', 'preview_path']
        self.check('CSV has one row per JSON result', len(rows) == len(results) and {r['original_path'] for r in rows} == set(by_orig))
        self.check('CSV fields match JSON for every record',
                   all(row[c] == fmt(by_orig[row['original_path']][c]) for row in rows for c in cols))

        # 10. gallery written by analyze (v3, ratings-review.html); a decoy file is never referenced
        (work / 'previews' / 'DSC00001__0decoy.jpg').write_bytes(b'')
        page = _Cards(); page.feed(html_path.read_text(encoding='utf-8'))
        expected_srcs = sorted(Path(os.path.relpath(r['preview_path'], work)).as_posix() for r in results)
        self.check('gallery shows one card per result', len(page.cards) == 27)
        self.check('gallery images use exact saved preview_path (decoy ignored)',
                   sorted(page.srcs) == expected_srcs and not any('decoy' in s for s in page.srcs))
        self.check('gallery session filter lists both sessions', set(page.options) == {'', S1, S2})
        self.check('gallery marks 4 best-in-burst cards', html_path.read_text(encoding='utf-8').count('Best in burst') == 4)
        self.check('split sequence recorded for split parts (CSV burst_sequence)',
                   all(row['burst_sequence'] == SEQ_LONG for row in rows if row['burst_group'].startswith(SEQ_LONG + '-p'))
                   and sum(row['burst_group'].startswith(SEQ_LONG + '-p') for row in rows) == 15)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            cli.cmd_gallery(argparse.Namespace(results=str(json_path)))
        self.check('gallery command refuses the v3 results file (D-1)', True)

        # 11. schema compatibility
        self.check('schema_version is 3; readers still support only 1 and 2',
                   payload['schema_version'] == 3 and SCHEMA_VERSIONS == {1, 2})
        self.check('v3 output complete (manifest binds JSON, CSV and gallery)', check_completeness(work).complete)
        legacy_dir = self.tmp / 'legacy'; legacy_dir.mkdir()
        legacy = {'schema_version': 1, 'results': [{k: r[k] for k in ('path', 'rating', 'decision', 'confidence', 'reasons', 'flags')} for r in results], 'errors': []}
        (legacy_dir / 'ratings-review.json').write_text(json.dumps(legacy), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()): cli.cmd_gallery(argparse.Namespace(results=str(legacy_dir / 'ratings-review.json')))
        lp = _Cards(); lp.feed((legacy_dir / 'review.html').read_text(encoding='utf-8'))
        self.check('v1-shaped results load and render in gallery', len(lp.cards) == 27)

        # 12./13. originals untouched, no sidecars
        self.check('originals unchanged (sha256, size, mtime) and no new source files', snapshot(self.root) == before)
        self.check('no XMP sidecars created', not list(self.tmp.rglob('*.xmp*')))
        self.check('analyze log says preview-only', 'No image metadata was changed.' in log and 'Rated: 27 | Errors/skips: 0' in log)

        print(f'\n[integration] {len(self.checks)} checks passed:'); [print('  ✓ ' + c) for c in self.checks]
        shutil.rmtree(self.tmp)
        self.check('temp directory cleaned up', not self.tmp.exists())
        print('  ✓ temp directory cleaned up')

    # ---- custom --output ---------------------------------------------------------
    def run_analyze(self, output: Path | None) -> str:
        args = argparse.Namespace(folder=str(self.root), output=None if output is None else str(output),
                                  batch_size=6, max_edge=256, model='mock', limit=None, max_burst_size=12,
                                  burst_window=1.0, no_recursive=False)
        out = io.StringIO()
        with mock.patch.object(core, 'run', self.fake_run(core.run)), \
             mock.patch.object(core.shutil, 'which', self.fake_which(shutil.which)), \
             contextlib.redirect_stdout(out):
            cli.cmd_analyze(args)
        return out.getvalue()

    def test_analyze_custom_output(self):
        if not shutil.which('exiftool'): self.skipTest('exiftool not installed')
        work = self.tmp / 'review-out'   # sibling of the library inside the same temp dir
        originals = {str((self.root / sid / name).resolve()) for sid, name, _ in DATASET}
        before = snapshot(self.root)
        tree_before = sorted(p.relative_to(self.root) for p in self.root.rglob('*'))
        self.check('[custom] output folder is new and outside the library',
                   not work.exists() and not work.is_relative_to(self.root) and not self.root.is_relative_to(work))

        log = self.run_analyze(work)

        # Markers
        for d, label in ((work, 'output folder'), (work / 'previews', 'previews/ folder')):
            marker = d / core.OUTPUT_MARKER
            self.check(f'[custom] {label} carries the generated-output marker',
                       marker.is_file() and not marker.is_symlink()
                       and marker.read_text(encoding='utf-8') == core.OUTPUT_MARKER_TEXT)

        # Nothing written into the library
        tree_after = sorted(p.relative_to(self.root) for p in self.root.rglob('*'))
        self.check('[custom] no .photo-curator folder created in the library', not (self.root / '.photo-curator').exists())
        self.check('[custom] library contains exactly the same files and folders as before', tree_after == tree_before)

        # Results point at originals in the library and previews in the custom folder
        json_path, csv_path, html_path = work / 'ratings-review.json', work / 'ratings-review.csv', work / 'ratings-review.html'
        payload = json.loads(json_path.read_text(encoding='utf-8'))
        results = payload['results']; by_orig = {r['original_path']: r for r in results}
        self.check('[custom] every original rated exactly once, no errors',
                   len(results) == 27 and set(by_orig) == originals and payload['errors'] == [], payload['errors'])
        self.check('[custom] source_folder is the library, not the output folder', payload['source_folder'] == str(self.root))
        self.check('[custom] original_path == path and lies inside the library',
                   all(r['path'] == r['original_path'] and Path(r['original_path']).is_relative_to(self.root) for r in results))
        preview_files = sorted((work / 'previews').glob('*.jpg'))
        self.check('[custom] every preview_path exists in <output>/previews and is distinct',
                   all(Path(r['preview_path']).parent == work / 'previews' and Path(r['preview_path']).is_file() for r in results)
                   and sorted(Path(r['preview_path']) for r in results) == preview_files and len(preview_files) == 27)

        # Discovery never returns generated previews as originals
        found_src = core.find_images(self.root)
        self.check('[custom] find_images(library) returns exactly the 27 originals',
                   len(found_src) == 27 and {str(p) for p in found_src} == originals)
        self.check('[custom] find_images(output) returns nothing', core.find_images(work) == [])
        self.check('[custom] find_images(output/previews) returns nothing', core.find_images(work / 'previews') == [])
        found_parent = core.find_images(self.tmp)   # parent folder holding both library and output
        self.check('[custom] find_images(parent of both) returns only the 27 originals',
                   len(found_parent) == 27 and {str(p) for p in found_parent} == originals)
        self.check('[custom] every preview is recognised as generated output',
                   all(core.in_generated_output(p) for p in preview_files)
                   and not any(core.in_generated_output(Path(o)) for o in originals))

        # JSON / CSV / gallery
        self.check('[custom] v3 output complete (manifest)', check_completeness(work).complete)
        with csv_path.open(encoding='utf-8', newline='') as f: rows = list(csv.DictReader(f))
        self.check('[custom] CSV matches JSON (one row per original, same preview_path and rating)',
                   len(rows) == 27 and {r['original_path'] for r in rows} == originals
                   and all(row['preview_path'] == by_orig[row['original_path']]['preview_path']
                           and row['rating'] == str(by_orig[row['original_path']]['rating']) for row in rows))
        page = _Cards(); page.feed(html_path.read_text(encoding='utf-8'))
        expected_srcs = sorted(Path(os.path.relpath(r['preview_path'], work)).as_posix() for r in results)
        self.check('[custom] analyze gallery: 27 cards, images are relative previews/ links',
                   len(page.cards) == 27 and sorted(page.srcs) == expected_srcs
                   and all(s.startswith('previews/') for s in page.srcs))
        gallery_before = html_path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            cli.cmd_gallery(argparse.Namespace(results=str(json_path)))
        self.check('[custom] gallery command refuses v3 and leaves the analysis gallery unchanged (D-1)',
                   html_path.read_bytes() == gallery_before and not (work / 'review.html').exists())

        # A rerun into the same (now non-empty, marked) folder is allowed
        self.check('[custom] validate_output_dir accepts the marked folder for a rerun',
                   core.validate_output_dir(self.root, work) == work)

        # Originals untouched, nothing applied
        self.check('[custom] originals unchanged (sha256, size, mtime)', snapshot(self.root) == before)
        self.check('[custom] no XMP sidecars created anywhere', not list(self.tmp.rglob('*.xmp*')))
        self.check('[custom] log names the custom output files and says preview-only',
                   str(json_path) in log and 'No image metadata was changed.' in log and 'Rated: 27 | Errors/skips: 0' in log)

        print(f'\n[integration custom --output] {len(self.checks)} checks passed:'); [print('  ✓ ' + c) for c in self.checks]
        shutil.rmtree(self.tmp)
        self.check('[custom] temp directory cleaned up', not self.tmp.exists())
        print('  ✓ [custom] temp directory cleaned up')

    def tearDown(self):
        if self.tmp.exists(): print(f'\n[integration] FAILED - temp data kept for inspection: {self.tmp}')


if __name__ == '__main__':
    unittest.main()
