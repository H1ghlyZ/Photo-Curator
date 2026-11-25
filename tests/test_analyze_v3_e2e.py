"""W-A: ``photo-curator analyze`` end to end on the v3 pipeline (spec 15.4, 12.6, 12.7 with DW-1,
12.8, 14.3a, 14.5; CLI-1..3).

Every external tool is a fake reached through ``core.run`` / ``core.shutil.which``: ExifTool
returns synthetic capture times, darktable renders the dummy RAW with Pillow, and Claude is
``tests.fake_claude_v3.FakeClaudeV3``. No real photo, no real Claude, no XMP.
"""
from __future__ import annotations
import argparse, contextlib, hashlib, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import analyze_v3, cli, core, hashing_v3, locking_v3
from photo_curator.analyze_v3 import WORKFLOW_ARCHIVE, WORKFLOW_NEW_OUTPUT, AnalyzeError
from photo_curator.locking_v3 import LOCK_NAME, BUSY_MESSAGE, JOURNAL_NAME, acquire_lock
from photo_curator.output_v3 import check_completeness
from tests.fake_claude_v3 import FakeClaudeV3

CLAUDE, DARKTABLE, EXIFTOOL = '/fake/bin/claude', '/fake/bin/darktable-cli', '/fake/bin/exiftool'
S1, S2 = 'DCIM/100MSDCF', 'DCIM/101MSDCF'
# (session, name, seconds after 10:00:00 or None for untimed)
DATASET = [(S1, 'DSC00001.JPG', 0.0), (S1, 'DSC00002.JPG', 0.2), (S1, 'DSC00003.JPG', 0.4),
           (S1, 'DSC00004.JPG', 60.0), (S1, 'DSC00005.ARW', 120.0), (S2, 'GAT00001.JPG', None)]


def make_image(path: Path, idx: int) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (80, 60), (idx * 40 % 256, 90, 160)).save(path, 'JPEG')


def tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob('*')):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-wa-e2e-')
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.root = self.tmp / 'library'
        self.times = {}
        for i, (sid, name, sec) in enumerate(DATASET):
            path = self.root / sid / name
            make_image(path, i)
            self.times[str(path)] = sec
        self.work = self.tmp / 'out'
        self.claude = FakeClaudeV3()
        self.commands: list[list[str]] = []
        self.darktable_ok = True
        self.on_darktable = None

    # ---- fakes -----------------------------------------------------------------------------
    def fake_run(self, cmd, timeout=180):
        self.commands.append(list(cmd))
        if cmd[0] == EXIFTOOL:
            rows = []
            for path in cmd[5:]:              # exiftool -json -DateTimeOriginal -SubSec... -Offset... <paths>

                sec = self.times.get(path)
                row = {'SourceFile': path}
                if sec is not None:
                    whole, ms = int(sec), round((sec - int(sec)) * 1000)
                    h, rem = divmod(10 * 3600 + whole, 3600)
                    m, s = divmod(rem, 60)
                    row.update(DateTimeOriginal=f'2026:11:15 {h:02d}:{m:02d}:{s:02d}', SubSecTimeOriginal=f'{ms:03d}')
                rows.append(row)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), '')
        if cmd[0] == DARKTABLE:
            if not self.darktable_ok:
                return subprocess.CompletedProcess(cmd, 1, '', 'darktable failed')
            from PIL import Image
            with Image.open(cmd[1]) as im:
                im.convert('RGB').save(cmd[2], 'JPEG')
            if self.on_darktable:
                self.on_darktable(Path(cmd[1]))
            return subprocess.CompletedProcess(cmd, 0, '', '')
        if cmd[0] == CLAUDE:
            return self.claude(cmd, timeout)
        raise AssertionError(f'unexpected external command: {cmd[:2]}')

    def fake_which(self, name, *a, **k):
        return {'claude': CLAUDE, 'darktable-cli': DARKTABLE, 'exiftool': EXIFTOOL}.get(name)

    @contextlib.contextmanager
    def fakes(self):
        with mock.patch.object(core, 'run', side_effect=self.fake_run), \
             mock.patch.object(core.shutil, 'which', side_effect=self.fake_which):
            yield

    def args(self, **over):
        base = dict(folder=str(self.root), output=str(self.work), batch_size=6, max_edge=128, model='mock',
                    limit=None, max_burst_size=12, burst_window=1.0, no_recursive=False)
        base.update(over)
        return argparse.Namespace(**base)

    def analyze(self, **over) -> tuple[dict, str]:
        out = io.StringIO()
        with self.fakes(), contextlib.redirect_stdout(out):
            doc = cli.cmd_analyze(self.args(**over))
        return doc, out.getvalue()

    def main(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with self.fakes(), mock.patch.object(sys, 'argv', ['photo-curator', *argv]), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as exit_:
                code = exit_.code if isinstance(exit_.code, int) else 1
        return code, out.getvalue(), err.getvalue()


class HappyPathTest(Base):
    def test_complete_v3_output(self):
        library_before = tree(self.root)
        doc, log = self.analyze()
        self.assertEqual(doc['schema_version'], 3)
        self.assertEqual(len(doc['results']), 6)
        self.assertEqual(doc['errors'], [])
        result = check_completeness(self.work)
        self.assertTrue(result.applicable and result.complete, result.problems)
        names = set(os.listdir(self.work))
        self.assertTrue({'ratings-review.json', 'ratings-review.csv', 'ratings-review.html',
                         'ratings-review.complete.json', LOCK_NAME, 'previews', core.OUTPUT_MARKER} <= names)
        self.assertNotIn('review.html', names)                                       # D-2
        self.assertEqual(tree(self.root), library_before)                           # originals untouched
        self.assertFalse(list(self.tmp.rglob('*.xmp*')))
        self.assertIn('No image metadata was changed.', log)
        self.assertIn('Rated: 6 | Errors/skips: 0', log)

    def test_results_bind_originals_previews_and_hashes(self):
        doc, _ = self.analyze()
        for r in doc['results']:
            original = Path(r['original_path'])
            self.assertEqual(r['path'], r['original_path'])
            self.assertEqual(r['original_sha256'], hashlib.sha256(original.read_bytes()).hexdigest())
            self.assertEqual(Path(r['preview_path']).parent, self.work / 'previews')
            self.assertEqual(Path(r['preview_path']).name, analyze_v3.preview_name(original))
            self.assertEqual(r['session_id'], S1 if 'DSC' in original.name else S2)

    def test_capture_time_bursts_and_batches(self):
        doc, _ = self.analyze()
        by_name = {Path(r['original_path']).name: r for r in doc['results']}
        group = by_name['DSC00001.JPG']['burst_group']
        self.assertIsNotNone(group)
        self.assertEqual({by_name[n]['burst_group'] for n in ('DSC00002.JPG', 'DSC00003.JPG')}, {group})
        self.assertEqual(by_name['DSC00001.JPG']['burst_ranking_status'], 'validated')
        self.assertIsNone(by_name['DSC00004.JPG']['burst_group'])
        self.assertEqual(by_name['DSC00002.JPG']['capture_time'], '2026-11-15T10:00:00.200')
        self.assertIsNone(by_name['GAT00001.JPG']['capture_time'])
        self.assertEqual(len(self.claude.calls), 2)                                  # one batch per session

    def test_only_fake_tools_and_v3_prompt(self):
        self.analyze()
        self.assertTrue(all(c[0] in (CLAUDE, DARKTABLE, EXIFTOOL) for c in self.commands))
        self.assertTrue(all('NEVER INVENT.' in call['prompt'] for call in self.claude.calls))

    def test_default_output_folder(self):
        doc, _ = self.analyze(output=None)
        self.assertTrue(check_completeness(self.root / '.photo-curator').complete)


class FailureEntriesTest(Base):
    def test_original_changed_during_preview(self):
        raw = self.root / S1 / 'DSC00005.ARW'
        self.on_darktable = lambda path: path.write_bytes(path.read_bytes() + b'edited')
        doc, _ = self.analyze()
        entry = next(e for e in doc['errors'] if e['original_path'] == str(raw))
        self.assertEqual((entry['stage'], entry['error'], entry['sent_to_model'], entry['preview_path'], entry['batch_index']),
                         ('original', hashing_v3.ERROR_CONTENT, False, None, None))
        self.assertNotIn(str(raw), [p for call in self.claude.calls for p in call['prompt'].split()])
        manifest = json.loads((self.work / 'ratings-review.complete.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['orphan_preview_count'], 1)                        # orphan kept, never deleted
        self.assertTrue((self.work / 'previews' / analyze_v3.preview_name(raw)).is_file())
        self.assertTrue(check_completeness(self.work).complete)

    def test_preview_failure(self):
        self.darktable_ok = False
        doc, _ = self.analyze()
        entry = next(e for e in doc['errors'] if e['original_path'].endswith('DSC00005.ARW'))
        self.assertEqual((entry['stage'], entry['sent_to_model'], entry['preview_path']), ('preview', False, None))

    def test_preview_name_collision_never_overwrites(self):
        previews = self.work / 'previews'
        previews.mkdir(parents=True)
        (self.work / core.OUTPUT_MARKER).write_text(core.OUTPUT_MARKER_TEXT, encoding='utf-8')
        target = self.root / S1 / 'DSC00004.JPG'
        with self.assertRaises(analyze_v3.PreviewError):
            existing = previews / analyze_v3.preview_name(target)
            existing.write_bytes(b'keep me')
            analyze_v3.make_preview_v3(target, previews, 64)
        self.assertEqual(existing.read_bytes(), b'keep me')

    def test_batch_failure(self):
        self.claude = FakeClaudeV3(returncode=1)
        doc, log = self.analyze()
        self.assertEqual(doc['results'], [])
        self.assertEqual(len(doc['errors']), 6)
        self.assertTrue(all(e['stage'] == 'batch' and e['sent_to_model'] is True and e['preview_path']
                            and isinstance(e['batch_index'], int) for e in doc['errors']))
        self.assertTrue(check_completeness(self.work).complete)


class RefusalTest(Base):
    def test_rerun_refused_with_full_12_7_text_before_previews_or_claude(self):
        """W-B (CLI-8, X-4): the temporary DW-1 text is withdrawn; the full 12.7 refusal offers both
        workflows. Same safety assertions as before: refused before previews or Claude, folder unchanged."""
        self.analyze()
        before = tree(self.work)
        calls = len(self.claude.calls)
        code, out, err = self.main('analyze', str(self.root), '--output', str(self.work))
        self.assertEqual(code, 1)
        self.assertIn('ratings-review.json', err)                                  # artifacts named
        self.assertIn('approvals in existing results: 0 passing', err)             # approvals named
        self.assertIn(WORKFLOW_NEW_OUTPUT, err)                                    # workflow 1
        self.assertIn(WORKFLOW_ARCHIVE, err)                                       # workflow 2
        self.assertIn('--archive-existing', err)
        self.assertNotIn('not available in this build', err)                      # DW-1 withdrawn
        self.assertEqual(tree(self.work), before)                                 # nothing changed
        self.assertEqual(len(self.claude.calls), calls)                           # no Claude call

    def test_dw1_triggers_unchanged(self):
        for name in ('ratings-review.csv', 'ratings-review.json.x.photo-curator.bak', 'x.tmp-1'):
            with self.subTest(name=name):
                self.work.mkdir(exist_ok=True)
                (self.work / core.OUTPUT_MARKER).write_text(core.OUTPUT_MARKER_TEXT, encoding='utf-8')
                (self.work / name).write_bytes(b'')
                with self.assertRaises(AnalyzeError) as ctx:
                    self.analyze()
                self.assertIn(name, ctx.exception.message)
                (self.work / name).unlink()

    def test_max_burst_size_range(self):
        for value in ('0', '1', '13'):
            with self.subTest(value=value):
                code, _, err = self.main('analyze', str(self.root), '--output', str(self.work), '--max-burst-size', value)
                self.assertEqual(code, 2)                                            # argparse error (M-23)
                self.assertIn('--max-burst-size must be 2..12', err)
                self.assertFalse(self.work.exists())
        for value in (0, 1, 13, True):
            with self.subTest(direct=value), self.assertRaises(AnalyzeError):
                self.analyze(max_burst_size=value)
            self.assertFalse(self.work.exists())

    def test_negative_burst_window_refused_before_writing(self):
        with self.assertRaises(AnalyzeError):
            self.analyze(burst_window=-1.0)
        self.assertFalse(self.work.exists())

    def marked_work(self):
        self.work.mkdir()
        (self.work / core.OUTPUT_MARKER).write_text(core.OUTPUT_MARKER_TEXT, encoding='utf-8')

    def test_busy_refused_exit_1(self):
        self.marked_work()
        lock = acquire_lock(self.work, 'exclusive')
        self.addCleanup(lock.release)
        code, _, err = self.main('analyze', str(self.root), '--output', str(self.work))
        self.assertEqual(code, 1)
        self.assertIn(BUSY_MESSAGE, err)
        self.assertEqual(sorted(os.listdir(self.work)), sorted([core.OUTPUT_MARKER, LOCK_NAME]))
        self.assertEqual(self.claude.calls, [])

    def test_journal_refused_exit_1(self):
        self.marked_work()
        (self.work / JOURNAL_NAME).write_bytes(b'{}')
        code, _, err = self.main('analyze', str(self.root), '--output', str(self.work))
        self.assertEqual(code, 1)
        self.assertIn('recover-archive', err)
        self.assertEqual(sorted(os.listdir(self.work)), sorted([core.OUTPUT_MARKER, JOURNAL_NAME, LOCK_NAME]))

    def test_network_filesystem_refused_before_mkdir(self):
        with mock.patch.object(locking_v3, 'filesystem_type', return_value='nfs4'):
            code, _, err = self.main('analyze', str(self.root), '--output', str(self.work))
        self.assertEqual(code, 1)
        self.assertIn('network filesystem (nfs4)', err)
        self.assertFalse(self.work.exists())

    def test_unsafe_output_and_missing_images_create_nothing(self):
        inside = self.root / 'out'
        with self.assertRaises(SystemExit) as ctx:
            self.analyze(output=str(inside))
        self.assertIn('Unsafe --output', str(ctx.exception))
        empty = self.tmp / 'empty'
        empty.mkdir()
        with self.assertRaises(SystemExit):
            self.analyze(folder=str(empty), output=str(self.tmp / 'out2'))
        self.assertFalse((self.tmp / 'out2').exists())

    def test_startup_order_lock_before_journal_check(self):
        order = []
        real_fs, real_lock, real_journal = (locking_v3.check_filesystem, locking_v3.acquire_lock,
                                            locking_v3.check_no_archive_journal)
        with mock.patch.object(locking_v3, 'check_filesystem', side_effect=lambda *a, **k: (order.append('fs'), real_fs(*a, **k))[1]), \
             mock.patch.object(locking_v3, 'acquire_lock', side_effect=lambda *a, **k: (order.append('lock'), real_lock(*a, **k))[1]), \
             mock.patch.object(locking_v3, 'check_no_archive_journal',
                               side_effect=lambda *a, **k: (order.append('journal'), real_journal(*a, **k))[1]):
            self.analyze()
        self.assertEqual(order[:3], ['fs', 'lock', 'journal'])


if __name__ == '__main__':
    unittest.main()
