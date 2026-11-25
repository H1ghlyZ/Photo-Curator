"""W-B: production CLI wiring CLI-4..CLI-9 and COMMAND_LOCK_MODES (R5 lock matrix, 12.6).

Commands run through ``cli.main`` on synthetic folders; every external tool is a fake and every
sidecar is written inside a temporary library only.
"""
from __future__ import annotations
import contextlib, hashlib, io, json, os, re, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

from photo_curator import cli, locking_v3
from photo_curator.artifacts_v3 import review_lines
from photo_curator.reader_v3 import read_results
from tests.test_apply_v3_order import ApplyFixture

EXPECTED_MODES = {
    'scan': None, 'analyze': ('exclusive', False), 'apply': ('exclusive', False),
    'approve --show': ('shared', False), 'approve --rating': ('exclusive', False), 'approve --revoke': ('exclusive', False),
    'review': ('shared', False), 'gallery': ('exclusive', False), 'coverage': ('shared', False),
    'recover-archive': ('shared', True), 'recover-archive --complete': ('exclusive', True),
    'recover-archive --rollback': ('exclusive', True),
}


def run_main(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(sys, 'argv', ['photo-curator', *argv]), contextlib.redirect_stdout(out), \
         contextlib.redirect_stderr(err):
        try:
            cli.main()
        except SystemExit as exit_:
            code = exit_.code if isinstance(exit_.code, int) else 1
            if isinstance(exit_.code, str):                       # as the interpreter prints it
                err.write(exit_.code + '\n')
    return code, out.getvalue(), err.getvalue()


def subcommands() -> list[str]:
    _, out, _ = run_main('--help')
    return re.search(r'\{([a-z,-]+)\}', out).group(1).split(',')


class LockMatrixTest(unittest.TestCase):
    def test_table_matches_r5(self):
        self.assertEqual(cli.COMMAND_LOCK_MODES, EXPECTED_MODES)

    def test_every_subcommand_has_a_lock_entry(self):
        names = subcommands()
        self.assertEqual(set(names), {'scan', 'analyze', 'gallery', 'review', 'coverage', 'apply', 'approve', 'recover-archive'})
        for name in names:
            with self.subTest(command=name):
                self.assertTrue(any(key == name or key.startswith(name + ' ') for key in cli.COMMAND_LOCK_MODES))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-wb-cli-')
        self.addCleanup(tmp.cleanup)
        self.fx = ApplyFixture(Path(os.path.realpath(tmp.name)))
        self.modes: list[str] = []
        real = locking_v3.acquire_lock
        patcher = mock.patch.object(locking_v3, 'acquire_lock', side_effect=lambda w, m: (self.modes.append(m), real(w, m))[1])
        patcher.start()
        self.addCleanup(patcher.stop)

    def sha(self, n: int) -> str:
        r = self.fx.doc()['results'][self.fx.index(n)]
        return hashlib.sha256(Path(r['preview_path']).read_bytes()).hexdigest()


class CommandModesTest(Base):
    def test_each_command_takes_its_declared_mode(self):
        cases = [
            (('review', str(self.fx.results)), 'review'),
            (('apply', str(self.fx.results), '--yes'), 'apply'),
            (('approve', str(self.fx.results), str(self.fx.paths[1]), '--show'), 'approve --show'),
            (('approve', str(self.fx.results), str(self.fx.paths[1]), '--rating', '3', '--preview-sha256', self.sha(1)),
             'approve --rating'),
            (('approve', str(self.fx.results), str(self.fx.paths[1]), '--revoke'), 'approve --revoke'),
            (('recover-archive', str(self.fx.work)), 'recover-archive'),
        ]
        for argv, key in cases:
            with self.subTest(command=key):
                self.modes.clear()
                code, out, err = run_main(*argv)
                self.assertEqual(code, 0, err)
                self.assertEqual(self.modes, [EXPECTED_MODES[key][0]])

    def test_gallery_exclusive_and_refuses_v3(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported results schema'):        # D-1, as before W-B
            run_main('gallery', str(self.fx.results))
        self.assertEqual(self.modes, ['exclusive'])
        self.assertFalse((self.fx.work / 'review.html').exists())

    def test_scan_takes_no_lock(self):
        code, out, _ = run_main('scan', str(self.fx.library))
        self.assertEqual((code, self.modes), (0, []))


class ApplyWiringTest(Base):
    def test_v3_apply_through_the_cli(self):
        code, out, err = run_main('apply', str(self.fx.results), '--yes')
        self.assertEqual(code, 0, err)
        self.assertIn('[OK] DSC00008.ARW', out)
        self.assertTrue((self.fx.library / 'DSC00008.ARW.xmp').exists())
        self.assertIn('[SKIP needs review: not approved] DSC00001.ARW', out)

    def test_apply_requires_yes_through_the_cli(self):
        code, _, err = run_main('apply', str(self.fx.results))
        self.assertEqual(code, 1)
        self.assertIn('Safety stop', err)
        self.assertEqual(self.fx.sidecars(), [])

    def test_apply_stop_exits_1(self):
        with mock.patch('photo_curator.apply_v3._sha_file', return_value='0' * 64):     # check 13 fails
            code, out, _ = run_main('apply', str(self.fx.results), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('apply stopped', out)

    def test_apply_command_is_wired_and_v2_reference_is_not(self):
        import inspect
        self.assertIn('func=apply_command', inspect.getsource(cli.main))
        self.assertNotIn('func=cmd_apply)', inspect.getsource(cli.main))


class ReviewWiringTest(Base):
    def test_v3_review_prints_review_lines_and_writes_no_html(self):
        before = sorted(os.listdir(self.fx.work))
        code, out, err = run_main('review', str(self.fx.results))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, '\n'.join(review_lines(read_results(self.fx.results).doc)) + '\n')
        after = sorted(n for n in os.listdir(self.fx.work) if n != 'ratings-review.json.lock')
        self.assertEqual(after, [n for n in before if n != 'ratings-review.json.lock'])
        self.assertFalse((self.fx.work / 'review.html').exists())

    def test_v3_review_refuses_incomplete_output(self):
        os.unlink(self.fx.work / 'ratings-review.complete.json')
        code, out, err = run_main('review', str(self.fx.results))
        self.assertEqual(code, 1)
        self.assertIn('analysis output incomplete', err)


class ApproveWiringTest(Base):
    def test_show_approve_revoke(self):
        code, out, _ = run_main('approve', str(self.fx.results), str(self.fx.paths[1]), '--show')
        self.assertEqual(code, 0)
        self.assertIn('approval state: pending', out)
        code, out, err = run_main('approve', str(self.fx.results), str(self.fx.paths[1]), '--rating', '3',
                                  '--preview-sha256', self.sha(1))
        self.assertEqual(code, 0, err)
        self.assertIn('(pending -> approved)', out)
        code, out, _ = run_main('approve', str(self.fx.results), str(self.fx.paths[1]), '--revoke')
        self.assertEqual(code, 0)
        self.assertIn('(approved -> pending)', out)

    def test_refusals_exit_1(self):
        code, _, err = run_main('approve', str(self.fx.results), str(self.fx.paths[1]), '--rating', '5',
                                '--preview-sha256', self.sha(1))
        self.assertEqual(code, 1)
        self.assertIn('differs from the item rating', err)
        code, _, err = run_main('approve', str(self.fx.results), str(self.fx.paths[1]), '--rating', '3')
        self.assertEqual(code, 1)
        self.assertIn('--preview-sha256', err)

    def test_modes_are_exclusive_in_argparse(self):
        code, _, _ = run_main('approve', str(self.fx.results), str(self.fx.paths[1]), '--show', '--revoke')
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
