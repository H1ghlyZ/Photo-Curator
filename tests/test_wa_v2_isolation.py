"""W-A: isolation of the v2 commands (R5 section 5: W-A does not change v2 apply, review or
gallery) and CLI-1 (analyze has no path to the v2 pipeline)."""
from __future__ import annotations
import argparse, contextlib, inspect, io, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import cli, core
from tests.test_analyze_v3_e2e import Base as E2EBase


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


class AnalyzeHasNoV2PathTest(E2EBase):
    def test_v2_pipeline_never_called(self):
        boom = AssertionError('v2 pipeline called')
        with mock.patch.object(core, 'analyze_batch', side_effect=boom), \
             mock.patch.object(core, 'validate_batch_results', side_effect=boom), \
             mock.patch.object(cli, 'analyze_batch', side_effect=boom), \
             mock.patch.object(cli, 'validate_batch_results', side_effect=boom), \
             mock.patch.object(cli, 'write_review_html', side_effect=boom), \
             mock.patch.object(cli, '_legacy_cmd_analyze_v2', side_effect=boom):
            doc, _ = self.analyze()
        self.assertEqual(doc['schema_version'], 3)

    def test_cmd_analyze_delegates_only_to_analyze_v3(self):
        source = inspect.getsource(cli.cmd_analyze)
        self.assertIn('return run_analyze(a)', source)
        for v2 in ('analyze_batch', 'validate_batch_results', 'make_preview(', 'write_review_html', 'schema_version'):
            self.assertNotIn(v2, source)

    def test_no_schema_selector_flag_or_environment(self):
        parser_source = inspect.getsource(cli.main)
        for selector in ('--schema', '--v2', '--legacy', 'environ', 'getenv'):
            self.assertNotIn(selector, parser_source)
        self.assertNotIn('environ', inspect.getsource(cli.cmd_analyze))

    def test_legacy_function_unreachable(self):
        self.assertNotIn('_legacy_cmd_analyze_v2', inspect.getsource(cli.main))
        with mock.patch.object(sys, 'argv', ['photo-curator', '--help']), contextlib.redirect_stdout(io.StringIO()) as out, \
             self.assertRaises(SystemExit):
            cli.main()
        self.assertNotIn('legacy', out.getvalue())

    def test_help_no_longer_says_zero_disables(self):
        with mock.patch.object(sys, 'argv', ['photo-curator', 'analyze', '--help']), \
             contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main()
        self.assertNotIn('0 disables', out.getvalue())
        self.assertIn('(2-12)', out.getvalue())


class V2CommandsUnchangedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-wa-iso-')
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))

    def v2_folder(self) -> Path:
        work = self.tmp / 'v2'
        previews = work / 'previews'
        core.mark_output_dir(work)
        core.mark_output_dir(previews)
        (previews / 'A__0000000000.jpg').write_bytes(b'\xff\xd8 preview')
        return write_json(work / 'ratings-review.json', {
            'schema_version': 2, 'results': [{
                'path': '/lib/A.JPG', 'original_path': '/lib/A.JPG', 'preview_path': str(previews / 'A__0000000000.jpg'),
                'session_id': 's', 'rating': 4, 'decision': 'strong', 'confidence': 0.8, 'reasons': ['mock'],
                'flags': [], 'technical_score': 4, 'editorial_score': 4, 'best_in_burst': None}], 'errors': []})

    def test_v2_gallery_and_review_still_work(self):
        results = self.v2_folder()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_gallery(argparse.Namespace(results=str(results)))
            cli.cmd_review(argparse.Namespace(results=str(results)))
        self.assertTrue((results.parent / 'review.html').is_file())                 # D-2: v2 keeps review.html
        self.assertFalse((results.parent / 'ratings-review.html').exists())
        self.assertIn('To approve, edit ratings-review.json', out.getvalue())       # v2 review text unchanged

    def test_v1_gallery_still_works(self):
        results = write_json(self.tmp / 'v1' / 'ratings-review.json',
                             {'schema_version': 1, 'results': [{'path': '/lib/A.JPG', 'rating': 3, 'decision': 'usable',
                                                                 'confidence': 0.7, 'reasons': [], 'flags': []}],
                              'errors': []})
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_gallery(argparse.Namespace(results=str(results)))
        self.assertTrue((results.parent / 'review.html').is_file())

    def test_v3_refused_by_gallery_review_and_apply(self):
        """W-B (X-4): gallery still refuses v3 (D-1). review and apply now take the v3 path (CLI-4/5);
        an invalid minimal v3 file is refused there by V31/12.8, safely: nothing written, no XMP,
        listing unchanged apart from the permitted lock file (14.7 difference 3)."""
        from photo_curator.apply_v3 import ApplyRefused
        from photo_curator.locking_v3 import LOCK_NAME
        results = write_json(self.tmp / 'v3' / 'ratings-review.json', {'schema_version': 3, 'results': [], 'errors': []})
        before = sorted(os.listdir(results.parent))
        with self.assertRaises(ValueError) as ctx:                                  # D-1
            cli.cmd_gallery(argparse.Namespace(results=str(results)))
        self.assertIn('Unsupported results schema', str(ctx.exception))
        with self.assertRaises(cli.CommandRefusal) as ctx, contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_review(argparse.Namespace(results=str(results)))
        self.assertIn('rubric_version', ctx.exception.message)                     # V31 refused the file
        self.assertEqual(out.getvalue(), '')                                        # nothing printed
        with self.assertRaises(ApplyRefused) as ctx:
            cli.apply_command(argparse.Namespace(results=str(results), min_rating=1, include_existing=False, yes=True))
        self.assertIn('rubric_version', ctx.exception.message)
        after = sorted(os.listdir(results.parent))
        self.assertEqual([n for n in after if n != LOCK_NAME], before)               # only the lock file may appear
        self.assertEqual(results.read_text(encoding='utf-8'),
                         json.dumps({'schema_version': 3, 'results': [], 'errors': []}))
        self.assertFalse(list(self.tmp.rglob('*.xmp*')))

    def test_schema_versions_unchanged(self):
        self.assertEqual(core.SCHEMA_VERSIONS, {1, 2})

    def test_v2_gallery_marks_burst_winners_with_data_best(self):
        """v2 gallery on a v2 fixture with real burst data: data-best="1" exactly on the winners."""
        from html.parser import HTMLParser
        work = self.tmp / 'v2burst'
        previews = work / 'previews'
        core.mark_output_dir(work)
        core.mark_output_dir(previews)

        def item(name, group, sequence, best, rating=3):
            preview = previews / f'{Path(name).stem}__0000000000.jpg'
            preview.write_bytes(b'\xff\xd8 ' + name.encode())
            return {'path': f'/lib/{name}', 'original_path': f'/lib/{name}', 'preview_path': str(preview),
                    'session_id': 'DCIM/100', 'rating': rating, 'decision': 'usable', 'confidence': 0.7,
                    'technical_score': 3, 'editorial_score': 3, 'reasons': ['mock'], 'flags': [],
                    'burst_group': group, 'burst_sequence': sequence, 'best_in_burst': best}
        results = [
            item('A1.JPG', 'burst-A', 'burst-A', True, 4), item('A2.JPG', 'burst-A', 'burst-A', False),
            item('A3.JPG', 'burst-A', 'burst-A', False),
            item('L1.JPG', 'burst-L-p1', 'burst-L', True), item('L2.JPG', 'burst-L-p1', 'burst-L', False),
            item('L3.JPG', 'burst-L-p2', 'burst-L', False), item('L4.JPG', 'burst-L-p2', 'burst-L', True, 2),
            item('S1.JPG', None, None, None, 5), item('S2.JPG', None, None, None, 1),
        ]
        path = write_json(work / 'ratings-review.json', {'schema_version': 2, 'results': results, 'errors': []})
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_gallery(argparse.Namespace(results=str(path)))

        class Cards(HTMLParser):
            def __init__(self):
                super().__init__()
                self.cards = []

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == 'article' and a.get('class') == 'photo-card':
                    self.cards.append(a)
        page_text = (work / 'review.html').read_text(encoding='utf-8')
        parser = Cards()
        parser.feed(page_text)
        best_names = {c['data-name'] for c in parser.cards if c['data-best'] == '1'}
        self.assertEqual(len(parser.cards), 9)
        self.assertEqual(sum(c['data-best'] == '1' for c in parser.cards), 3)          # one per comparison group
        self.assertEqual(best_names, {'a1.jpg', 'l1.jpg', 'l4.jpg'})
        self.assertTrue(all(c['data-best'] == '0' for c in parser.cards if c['data-name'] in ('s1.jpg', 's2.jpg')))
        self.assertEqual(page_text.count('★ Best in burst'), 3)
        self.assertEqual(page_text.count('Sequence: burst-L'), 4)                        # split parts only
        self.assertNotIn('Sequence: burst-A', page_text)

    def test_v2_review_text_pinned(self):
        """The complete v2 ``review`` output, exactly as the existing implementation prints it."""
        results = [
            {'path': '/lib/B.JPG', 'original_path': '/lib/B.JPG', 'rating': 2, 'decision': 'weak', 'confidence': 0.35,
             'session_id': 'DCIM/100', 'best_in_burst': False, 'reasons': [], 'flags': []},
            {'path': '/lib/C.JPG', 'original_path': '/lib/C.JPG', 'rating': 4, 'decision': 'strong', 'confidence': 0.9,
             'reasons': ['sharp eyes on the speaker'], 'flags': []},
            {'path': '/lib/A.JPG', 'original_path': '/lib/A.JPG', 'rating': 4, 'decision': 'strong', 'confidence': 0.8,
             'session_id': 'DCIM/100', 'best_in_burst': True, 'reasons': ['peak gesture', 'clean background'],
             'flags': ['clutter', 'mergers']},
        ]
        path = write_json(self.tmp / 'v2review' / 'ratings-review.json',
                          {'schema_version': 2, 'results': results, 'errors': []})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_review(argparse.Namespace(results=str(path)))
        expected = (
            '★★★★☆  strong        conf=0.80  A.JPG  [DCIM/100]  ★best-in-burst\n'
            '    - peak gesture\n'
            '    - clean background\n'
            '    flags: clutter, mergers\n'
            '★★★★☆  strong        conf=0.90  C.JPG\n'
            '    - sharp eyes on the speaker\n'
            '★★☆☆☆  weak          conf=0.35  B.JPG  [DCIM/100]\n'
            '\n'
            '3 results. To approve, edit ratings-review.json or use --min-rating when applying.\n'
        )
        self.assertEqual(out.getvalue(), expected)


if __name__ == '__main__':
    unittest.main()
