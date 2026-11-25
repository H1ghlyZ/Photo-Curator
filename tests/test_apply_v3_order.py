"""S14: apply check order for v3 files (spec 13.1 rows 0a-13, 12.1 binding checks inside apply;
spec 20 items 19 and 16c apply part). Every original is a small synthetic file in a temporary
library; sidecars are written only there."""
from __future__ import annotations
import hashlib, json, os, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from photo_curator import apply_v3, hashing_v3
from photo_curator.apply_v3 import (SAFETY_STOP, SKIP_AMBIGUOUS, SKIP_APPROVED_CHANGED, SKIP_BELOW_MIN, SKIP_CHANGED,
                                    SKIP_EXISTING, SKIP_GENERATED, SKIP_INCONSISTENT, SKIP_NOT_ORIGINAL,
                                    SKIP_ORIGINAL_MISSING, SKIP_RATING_0, SKIP_UNREADABLE, STOP_RESULTS_CHANGED,
                                    ApplyRefused, run_apply)
from photo_curator.approval_v3 import BINDING_MESSAGES, MALFORMED_MESSAGE, PENDING_MESSAGE
from photo_curator.approve_v3 import approve_item
from photo_curator.core import OUTPUT_MARKER
from photo_curator.locking_v3 import (JOURNAL_NAME, ArchiveInProgressRefusal, NetworkFilesystemRefusal,
                                      OutputBusyRefusal, acquire_lock)
from photo_curator.output_v3 import MANIFEST_NAME
from photo_curator.preview_checks_v3 import expected_preview_name
from photo_curator.sidecar_v3 import SKIP_NOT_REGULAR
from tests.test_document_v3 import original
from tests.test_reader_v3_file import Output

NOW = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ApplyFixture:
    """A complete v3 output whose every result points at a real synthetic original."""

    def __init__(self, root: Path):
        self.root = root
        self.work = root / 'out'
        self.work.mkdir()
        self.out = Output(self.work)
        self.library = root / 'library'
        self.library.mkdir()
        self.paths = {}
        for k, r in enumerate(self.out.doc['results']):
            n = int(Path(r['original_path']).name[3:8])
            real = self.library / f'DSC{n:05d}.ARW'
            real.write_bytes(b'raw %d' % n)
            preview = Path(r['preview_path']).with_name(expected_preview_name(str(real)))
            os.replace(r['preview_path'], preview)
            r.update(path=str(real), original_path=str(real), preview_path=str(preview), original_sha256=sha(real.read_bytes()))
            self.paths[n] = real
        self.out.doc['source_folder'] = str(self.library)
        self.out.rewrite()
        self.results = self.work / 'ratings-review.json'

    def doc(self) -> dict:
        return json.loads(self.results.read_text(encoding='utf-8'))

    def index(self, n: int) -> int:
        return next(k for k, r in enumerate(self.doc()['results']) if r['original_path'] == str(self.paths[n]))

    def edit(self, n: int, **changes):
        doc = self.doc()
        doc['results'][self.index(n)].update(changes)
        self.out.doc = doc
        self.out.rewrite()

    def approve(self, n: int, now=NOW):
        r = self.doc()['results'][self.index(n)]
        return approve_item(self.results, str(self.paths[n]), rating=r['rating'],
                            preview_sha256_arg=sha(Path(r['preview_path']).read_bytes()), now=now)

    def apply(self, **kw):
        kw.setdefault('yes', True)
        kw.setdefault('now', NOW)
        return run_apply(self.results, **kw)

    def sidecars(self) -> list[str]:
        return sorted(p.name for p in self.library.glob('*.xmp'))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s14-ap-')
        self.addCleanup(tmp.cleanup)
        self.fx = ApplyFixture(Path(os.path.realpath(tmp.name)))

    def line_for(self, report, n: int) -> str:
        name = f'DSC{n:05d}.ARW'
        return next(line for line in report.lines if name in line)


class CleanRunTest(Base):
    def test_writes_not_required_items_and_skips_review_items(self):
        report = self.fx.apply()
        self.assertEqual(report.exit_code, 0)
        doc = self.fx.doc()
        for r in doc['results']:
            n = int(Path(r['original_path']).name[3:8])
            line = self.line_for(report, n)
            if r['rating'] == 0:
                self.assertTrue(line.startswith(SKIP_RATING_0))
            elif r['needs_review']:
                self.assertTrue(line.startswith(PENDING_MESSAGE), line)
            else:
                self.assertEqual(line, f'[OK] DSC{n:05d}.ARW: {r["rating"]} stars -> DSC{n:05d}.ARW.xmp')
        self.assertTrue(report.lines[-1].startswith(f'Applied {report.applied}; skipped {report.skipped}.'))
        self.assertTrue(any(line.startswith(f'  {PENDING_MESSAGE}: ') for line in report.lines))   # per-message summary

    def test_originals_never_written(self):
        before = {n: (p.read_bytes(), os.stat(p).st_mtime_ns) for n, p in self.fx.paths.items()}
        self.fx.apply()
        self.assertEqual({n: (p.read_bytes(), os.stat(p).st_mtime_ns) for n, p in self.fx.paths.items()}, before)


class FileLevelTest(Base):
    def test_0a_network_filesystem_and_busy(self):
        with mock.patch('photo_curator.locking_v3.filesystem_type', return_value='nfs'), \
             self.assertRaises(NetworkFilesystemRefusal):
            self.fx.apply()
        lock = acquire_lock(self.fx.work, 'shared')
        self.addCleanup(lock.release)
        with self.assertRaises(OutputBusyRefusal):
            self.fx.apply()
        self.assertEqual(self.fx.sidecars(), [])

    def test_0b_journal_before_parsing(self):
        (self.fx.work / JOURNAL_NAME).write_bytes(b'{}')
        self.fx.results.write_bytes(b'{not json')                                  # 0b runs before 0c
        with self.assertRaises(ArchiveInProgressRefusal):
            self.fx.apply()
        self.assertEqual(self.fx.sidecars(), [])

    def test_0c_parse_and_schema(self):
        for raw in (b'{not json', json.dumps({'schema_version': 9, 'results': []}).encode()):
            with self.subTest(raw=raw[:12]):
                self.fx.results.write_bytes(raw)
                with self.assertRaises(ApplyRefused):
                    self.fx.apply()

    def test_yes_required(self):
        with self.assertRaises(SystemExit) as ctx:
            self.fx.apply(yes=False)
        self.assertEqual(str(ctx.exception), SAFETY_STOP)
        self.assertEqual(self.fx.sidecars(), [])

    def test_0d_incomplete_output(self):
        os.unlink(self.fx.work / MANIFEST_NAME)
        with self.assertRaises(ApplyRefused) as ctx:
            self.fx.apply()
        self.assertIn('analysis output incomplete', ctx.exception.message)


class PerItemTest(Base):
    def assert_first(self, n: int, message: str, **kw):
        report = self.fx.apply(**kw)
        self.assertTrue(self.line_for(report, n).startswith(message), self.line_for(report, n))
        return report

    def test_1_ambiguous(self):
        self.fx.edit(8, path=str(self.fx.paths[2]))
        self.assert_first(8, SKIP_AMBIGUOUS)

    def test_2_not_an_original_image(self):
        target = self.fx.library / 'notes.txt'
        target.write_bytes(b'x')
        self.fx.edit(8, path=str(target), original_path=str(target))
        report = self.fx.apply()
        self.assertTrue(any(line.startswith(f'{SKIP_NOT_ORIGINAL} {target}') for line in report.lines))

    def test_3_inside_generated_output(self):
        marked = self.fx.root / 'marked'
        marked.mkdir()
        (marked / OUTPUT_MARKER).write_text('m', encoding='utf-8')
        target = marked / 'DSC00008.ARW'
        target.write_bytes(b'x')
        self.fx.edit(8, path=str(target), original_path=str(target))
        report = self.fx.apply()
        self.assertTrue(any(line.startswith(f'{SKIP_GENERATED} {target}') for line in report.lines))

    def test_4_inconsistent(self):
        self.fx.edit(8, decision='exceptional')
        self.assert_first(8, SKIP_INCONSISTENT)

    def test_4_v3_without_preview_path(self):
        doc = self.fx.doc()
        for r in doc['results']:
            del r['preview_path']
        self.fx.out.doc = doc
        self.fx.out.rewrite()
        report = self.fx.apply()
        self.assertEqual(report.applied, 0)
        self.assertEqual(report.skips_by_message[SKIP_INCONSISTENT], len(doc['results']))

    def test_5_rating_0_and_6_below_min(self):
        self.assert_first(9, SKIP_RATING_0)
        self.assert_first(8, SKIP_BELOW_MIN, min_rating=4)

    def test_7_original_missing(self):
        self.fx.paths[8].unlink()
        self.assert_first(8, SKIP_ORIGINAL_MISSING)

    def test_7_symlinked_original_skipped_on_v3(self):
        real = self.fx.paths[8]
        os.replace(real, self.fx.root / 'elsewhere.ARW')
        os.symlink(self.fx.root / 'elsewhere.ARW', real)
        self.assert_first(8, SKIP_ORIGINAL_MISSING)

    def test_8_malformed_9_pending_10_invalidated(self):
        self.assert_first(1, PENDING_MESSAGE)
        self.fx.approve(1)
        self.fx.edit(1, approved_preview_path=str(self.fx.work / 'previews' / 'other.jpg'))
        self.assert_first(1, BINDING_MESSAGES[4])
        self.fx.edit(1, approved='true')
        self.assert_first(1, MALFORMED_MESSAGE)

    def test_11_existing_sidecar(self):
        Path(str(self.fx.paths[8]) + '.xmp').write_bytes(b'<x/>')
        self.assert_first(8, SKIP_EXISTING)

    def test_11a_sidecar_not_regular_never_hashes(self):
        sidecar = Path(str(self.fx.paths[8]) + '.xmp')
        sidecar.mkdir()
        hashed = []
        report = self.fx.apply(include_existing=True, hasher=lambda p: (hashed.append(p), hashing_v3.sha256_file(p))[1])
        self.assertTrue(self.line_for(report, 8).startswith(SKIP_NOT_REGULAR))
        self.assertNotIn(str(self.fx.paths[8]), hashed)

    def test_approved_item_is_written(self):
        self.fx.approve(1)
        report = self.fx.apply()
        self.assertTrue(self.line_for(report, 1).startswith('[OK] DSC00001.ARW'))


class Check12Test(Base):
    def test_hashed_exactly_once_only_when_eligible(self):
        hashed = []

        def hasher(path):
            self.assertFalse(Path(path + '.xmp').exists(), 'sidecar exists before check 12')
            hashed.append(path)
            return hashing_v3.sha256_file(path)
        report = self.fx.apply(hasher=hasher)
        written = [o for o, _ in report.written]
        self.assertEqual(sorted(hashed), sorted(written))                       # skipped items never hashed
        self.assertEqual(len(hashed), len(set(hashed)))

    def test_unreadable_and_changed(self):
        self.assert_first = PerItemTest.assert_first.__get__(self)
        with mock.patch.object(hashing_v3, 'sha256_file', side_effect=hashing_v3.HashError('x')):
            self.assert_first(8, SKIP_UNREADABLE)
        self.fx.paths[8].write_bytes(b'edited')
        self.assert_first(8, SKIP_CHANGED)

    def test_approved_changed(self):
        self.fx.approve(1)
        self.fx.paths[1].write_bytes(b'edited after approval')
        report = self.fx.apply()
        self.assertTrue(self.line_for(report, 1).startswith(SKIP_APPROVED_CHANGED))

    def test_check_4_beats_original_hash(self):
        """12.1: an approved item failing binding check 4 and the original hash is skipped at check 10
        with check 4's message, and its original is never hashed."""
        self.fx.approve(1)
        self.fx.edit(1, approved_preview_path=str(self.fx.work / 'previews' / 'other.jpg'))
        self.fx.paths[1].write_bytes(b'edited')
        hashed = []
        report = self.fx.apply(hasher=lambda p: (hashed.append(p), hashing_v3.sha256_file(p))[1])
        self.assertTrue(self.line_for(report, 1).startswith(BINDING_MESSAGES[4]))
        self.assertNotIn(str(self.fx.paths[1]), hashed)


class Check13Test(Base):
    def test_results_changed_mid_run_stops(self):
        calls = []

        def hasher(path):
            calls.append(path)
            if len(calls) == 2:
                with open(self.fx.results, 'ab') as handle:
                    handle.write(b'\n')                                          # outside program edits
            return hashing_v3.sha256_file(path)
        report = self.fx.apply(hasher=hasher)
        self.assertEqual(report.stopped, STOP_RESULTS_CHANGED)
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report.applied, 1)
        self.assertTrue(any('applied so far: 1' in line for line in report.lines))


class MessagesTest(Base):
    def test_only_first_message_and_summary_counts(self):
        self.fx.edit(8, decision='exceptional', path=str(self.fx.paths[2]))      # checks 1 and 4 fail
        report = self.fx.apply()
        lines8 = [line for line in report.lines if 'DSC00008.ARW' in line]
        self.assertEqual(len(lines8), 1)
        self.assertTrue(lines8[0].startswith(SKIP_AMBIGUOUS))
        self.assertEqual(sum(report.skips_by_message.values()), report.skipped)

    def test_suggestions_never_change_the_outcome(self):
        first = self.fx.apply()
        self.setUp()
        doc = self.fx.doc()
        for r in doc['results']:
            r['editorial_suggestion'] = 'hero_candidate'
            r['documentation_suggestion'] = 'retain_low_rating'
        self.fx.out.doc = doc
        self.fx.out.rewrite()
        second = self.fx.apply()
        names = lambda report: sorted((Path(o).name, rating) for o, rating in report.written)
        self.assertEqual(names(first), names(second))                            # two temp folders: compare names


if __name__ == '__main__':
    unittest.main()
