"""S14: v1/v2 apply equivalence (spec 14.7, 13.1; spec 20 item 19 'v1/v2 fixture files run to
completion', 'v1 regression (V33, N-1)', 'v2 regression') with the approved interpretations
K-14a (silent missing original), K-14b (is_file follows symlinks) and K-14c (today's rating rules).

Two identical copies of each synthetic fixture: today's ``cli.cmd_apply`` runs on copy A, the new
``apply_v3.run_apply`` on copy B. Items written, ratings written (sidecar bytes) and items skipped
must be identical; the only output differences allowed are those documented in 14.7.
"""
from __future__ import annotations
import argparse, contextlib, io, json, os, shutil, tempfile, unittest
from pathlib import Path

from photo_curator import cli
from photo_curator.apply_v3 import SKIP_BELOW_MIN, SKIP_RATING_0, run_apply
from photo_curator.core import GENERATED_DIR, OUTPUT_MARKER
from photo_curator.locking_v3 import LOCK_NAME

DOCUMENTED_NEW_LINES = (SKIP_RATING_0, SKIP_BELOW_MIN)          # 14.7 difference 1


def build(root: Path, version: int) -> Path:
    lib = root / 'library'
    work = root / 'out'
    (lib / GENERATED_DIR).mkdir(parents=True)
    (work / 'previews').mkdir(parents=True)
    (work / OUTPUT_MARKER).write_text('m', encoding='utf-8')
    (work / 'previews' / OUTPUT_MARKER).write_text('m', encoding='utf-8')
    for name in ('A.JPG', 'B.ARW', 'C.ARW', 'D.ARW', 'E.ARW', 'F.ARW', 'I.ARW', 'J.ARW', 'K.ARW', 'L.ARW', 'real-H.ARW'):
        (lib / name).write_bytes(b'raw ' + name.encode())
    (lib / 'notes.txt').write_bytes(b'x')
    (lib / GENERATED_DIR / 'x.JPG').write_bytes(b'x')
    os.symlink(lib / 'real-H.ARW', lib / 'H.ARW')
    (lib / 'I.ARW.xmp').write_bytes(b"<?xml version='1.0' encoding='utf-8'?>\n<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" "
                                    b"xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\"><rdf:RDF>"
                                    b"<rdf:Description rdf:about=\"\" /></rdf:RDF></x:xmpmeta>")
    items = [('A.JPG', 4), ('B.ARW', 0), ('C.ARW', 2), ('D.ARW', 6), ('E.ARW', '4'), ('F.ARW', True),
             ('G.ARW', 3), ('H.ARW', 5), ('notes.txt', 3), (f'{GENERATED_DIR}/x.JPG', 3), ('I.ARW', 3), ('J.ARW', -1)]
    results = []
    for name, rating in items:
        r = {'path': str(lib / name), 'rating': rating, 'decision': 'usable', 'confidence': 0.7, 'reasons': [], 'flags': []}
        if version == 2:
            r.update(original_path=str(lib / name), preview_path=str(work / 'previews' / f'{Path(name).stem}__x.jpg'),
                     session_id='s1')
        results.append(r)
    if version == 2:
        results.append({'path': str(lib / 'K.ARW'), 'original_path': str(lib / 'A.JPG'), 'rating': 3,
                        'preview_path': str(work / 'previews' / 'K__x.jpg'), 'session_id': 's1'})       # ambiguous
        results.append({'path': str(lib / 'L.ARW'), 'original_path': str(lib / 'L.ARW'), 'rating': 3,
                        'preview_path': str(lib / 'L.ARW'), 'session_id': 's1'})                         # = preview
    else:
        results.append({'path': str(lib / 'K.ARW'), 'rating': 3})                                        # v1: no fields
    results_path = work / 'ratings-review.json'
    results_path.write_text(json.dumps({'schema_version': version, 'results': results, 'errors': []}), encoding='utf-8')
    return results_path


def sidecars(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted((root / 'library').rglob('*.xmp'))}


def backups(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted((root / 'library').rglob('*.photo-curator.bak'))}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s14-eq-')
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))

    def pair(self, version: int):
        a, b = self.tmp / f'v{version}-today', self.tmp / f'v{version}-new'
        a.mkdir()
        b.mkdir()
        return build(a, version), build(b, version), a, b

    def run_both(self, version: int, **opts):
        ra, rb, a, b = self.pair(version)
        out = io.StringIO()
        ns = argparse.Namespace(results=str(ra), min_rating=opts.get('min_rating', 1),
                                include_existing=opts.get('include_existing', False), yes=True)
        with contextlib.redirect_stdout(out):
            cli.cmd_apply(ns)
        today = out.getvalue().splitlines()
        report = run_apply(rb, min_rating=opts.get('min_rating', 1),
                           include_existing=opts.get('include_existing', False), yes=True)
        return today, report, a, b

    @staticmethod
    def normalise(lines, root: Path):
        return [line.replace(str(root), '<ROOT>') for line in lines]


class EquivalenceTest(Base):
    def check(self, version: int, **opts):
        today, report, a, b = self.run_both(version, **opts)
        self.assertEqual(report.exit_code, 0)
        new = [line for line in report.lines if not line.startswith(DOCUMENTED_NEW_LINES)]
        self.assertEqual(self.normalise(new, b), self.normalise(today, a))           # same lines otherwise
        self.assertEqual(sidecars(a), sidecars(b))                                    # same items, same bytes
        self.assertTrue((b / 'out' / LOCK_NAME).exists())                            # 14.7 difference 3
        self.assertFalse((a / 'out' / LOCK_NAME).exists())
        return today, report, a, b

    def test_v1(self):
        self.check(1)

    def test_v2(self):
        self.check(2)

    def test_include_existing(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.setUp()
                _, report, a, b = self.check(version, include_existing=True)
                self.assertIn('library/I.ARW.xmp', sidecars(b))
                old, new = backups(a), backups(b)                                    # 14.7 difference 7: 13.2 backups
                self.assertEqual(list(old.values()), list(new.values()))
                self.assertRegex(next(iter(new)), r'^I\.ARW\.xmp\.\d{8}T\d{6}\.\d{6}Z\.photo-curator\.bak$')

    def test_min_rating(self):
        self.check(1, min_rating=3)
        self.setUp()
        self.check(2, min_rating=3)


class InterpretationTest(Base):
    """K-14a/b/c: the cases where 13.1 read literally would differ from today."""

    def test_k14a_missing_original_silent(self):
        _, report, _, _ = self.run_both(1)
        self.assertFalse([line for line in report.lines if 'G.ARW' in line])

    def test_k14b_symlinked_original_written(self):
        _, report, _, b = self.run_both(2)
        self.assertIn('library/H.ARW.xmp', sidecars(b))
        self.assertTrue(any(line.startswith('[OK] H.ARW: 5 stars') for line in report.lines))

    def test_k14c_ratings_as_today(self):
        _, report, a, b = self.run_both(1)
        self.assertNotIn('library/D.ARW.xmp', sidecars(b))                        # 6: silent skip
        self.assertNotIn('library/E.ARW.xmp', sidecars(b))                        # "4": silent skip
        self.assertIn(b'xmp:Rating="True"', sidecars(b)['library/F.ARW.xmp'])    # True: str(True), as today
        self.assertEqual(sidecars(a)['library/F.ARW.xmp'], sidecars(b)['library/F.ARW.xmp'])


class V33RegressionTest(Base):
    def test_v1_items_without_v2_fields(self):
        _, report, _, b = self.run_both(1)
        self.assertIn('library/K.ARW.xmp', sidecars(b))                           # v1 item with only path: written
        self.assertFalse([line for line in report.lines if 'inconsistent result' in line or 'ambiguous' in line])
        self.assertTrue(any(line.startswith('[SKIP not an original image]') and 'notes.txt' in line for line in report.lines))
        self.assertTrue(any(line.startswith('[SKIP not an original image]') and GENERATED_DIR in line for line in report.lines))

    def test_v2_ambiguous_and_preview_equal(self):
        _, report, _, _ = self.run_both(2)
        self.assertTrue(any(line == '[SKIP ambiguous original_path/path] A.JPG' for line in report.lines))
        self.assertTrue(any(line.startswith('[SKIP not an original image]') and 'L.ARW' in line for line in report.lines))


if __name__ == '__main__':
    unittest.main()
