"""F-1: the Claude call reads its prompt from a private folder inside the output folder and gets
``--add-dir`` for exactly that folder and ``<work>/previews`` (K-F1); whitespace in the output
path is refused (K-F3). Regression for OT-1, where Claude could not read a prompt in /tmp under
``permissions.blockReadsOutsideWorkingDirectories``. Fakes only; temporary folders only."""
from __future__ import annotations
import io, json, os, re, shutil, stat, subprocess, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from photo_curator import analyze_v3, core
from photo_curator.analyze_v3 import PROMPT_DIR_PREFIX, PROMPT_FILE_NAME, call_claude
from photo_curator.prompt_v3 import PromptFrame, ResponseFormatError, build_prompt, parse_claude_output
from tests.fake_claude_v3 import FakeClaudeV3
from tests.test_analyze_v3_e2e import CLAUDE, Base as E2EBase, make_image

PROMPT_RE = re.compile(r'Read the prompt file at (\S+) and follow its instructions exactly\.')


def add_dirs(cmd):
    i = cmd.index('--add-dir')
    return cmd[i + 1:]


class BlockReadsOutsideWorkingDirectories:
    """Simulates the Claude setting: Read is allowed only inside the cwd and the --add-dir
    folders. A denied prompt read gives the OT-1 answer (text without JSON)."""

    def __init__(self, inner=None):
        self.inner = inner or FakeClaudeV3()
        self.denied: list[str] = []
        self.calls: list[list[str]] = []

    def allowed(self, cmd, path):
        roots = [os.getcwd()] + (add_dirs(cmd) if '--add-dir' in cmd else [])
        real = os.path.realpath(path)
        return any(os.path.commonpath([real, os.path.realpath(r)]) == os.path.realpath(r) for r in roots)

    def __call__(self, cmd, timeout=None):
        self.calls.append(list(cmd))
        prompt = PROMPT_RE.search(cmd[cmd.index('-p') + 1]).group(1)
        if not self.allowed(cmd, prompt):
            self.denied.append(prompt)
            text = f"I couldn't read `{prompt}`: blockReadsOutsideWorkingDirectories."
            return subprocess.CompletedProcess(cmd, 0, json.dumps({'type': 'result', 'result': text}), '')
        listed = re.findall(r'Preview to inspect: (\S+)', Path(prompt).read_text(encoding='utf-8'))
        blocked = [p for p in listed if not self.allowed(cmd, p)]
        if blocked:
            self.denied.extend(blocked)
            return subprocess.CompletedProcess(cmd, 0, json.dumps({'type': 'result', 'result': 'no access'}), '')
        return self.inner(cmd, timeout)


class UnitBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-f1-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.work = self.root / 'out'
        (self.work / 'previews').mkdir(parents=True)
        self.source = self.root / 'library'
        self.source.mkdir()
        self.frames = [PromptFrame(str(self.source / f'DSC{n:05d}.ARW'),
                                   str(self.work / 'previews' / f'DSC{n:05d}__{n:010d}.jpg'), 'DCIM', None)
                       for n in (1, 2)]
        for f in self.frames:
            Path(f.preview_path).write_bytes(b'jpeg')
        self.seen = {}

    def call(self, run):
        with mock.patch.object(core.shutil, 'which', return_value=CLAUDE), \
             mock.patch.object(core, 'run', side_effect=run), \
             mock.patch.object(shutil, 'rmtree', side_effect=AssertionError('rmtree must never be used')):
            return call_claude(self.frames, 'sonnet', self.work)

    def recording(self, answer=None):
        fake = FakeClaudeV3()

        def run(cmd, timeout=None):
            prompt = PROMPT_RE.search(cmd[cmd.index('-p') + 1]).group(1)
            self.seen.update(cmd=list(cmd), timeout=timeout, prompt=prompt,
                             dir_mode=stat.S_IMODE(os.stat(os.path.dirname(prompt)).st_mode),
                             file_mode=stat.S_IMODE(os.stat(prompt).st_mode),
                             text=Path(prompt).read_text(encoding='utf-8'))
            return answer(cmd) if answer else fake(cmd, timeout)
        return run

    def prompt_dirs(self):
        return sorted(n for n in os.listdir(self.work) if n.startswith(PROMPT_DIR_PREFIX))


class CommandTest(UnitBase):
    def test_add_dir_exactly_prompt_folder_and_previews_and_last(self):
        photos = self.call(self.recording())
        self.assertEqual(len(photos), 2)
        cmd = self.seen['cmd']
        self.assertEqual(cmd.count('--add-dir'), 1)
        prompt_dir = os.path.dirname(self.seen['prompt'])
        self.assertEqual(add_dirs(cmd), [prompt_dir, str(self.work / 'previews')])   # last; two values
        for opened in add_dirs(cmd):
            self.assertNotIn(opened, (tempfile.gettempdir(), str(Path.home()), str(self.source), str(self.work)))

    def test_unchanged_tools_model_turns_timeout_and_instruction(self):
        self.call(self.recording())
        cmd = self.seen['cmd']
        self.assertEqual(cmd[cmd.index('--allowedTools') + 1], 'Read')
        self.assertEqual(cmd[cmd.index('--disallowedTools') + 1], 'Bash,Edit,Write')
        self.assertEqual(cmd[cmd.index('--model') + 1], 'sonnet')
        self.assertEqual(cmd[cmd.index('--output-format') + 1], 'json')
        self.assertEqual(cmd[cmd.index('--max-turns') + 1], '24')
        self.assertEqual(self.seen['timeout'], 360)
        self.assertTrue(PROMPT_RE.fullmatch(cmd[cmd.index('-p') + 1]))
        self.assertEqual(cmd[:2], [CLAUDE, '-p'])

    def test_prompt_location_content_and_permissions(self):
        self.call(self.recording())
        prompt = Path(self.seen['prompt'])
        self.assertEqual(prompt.name, PROMPT_FILE_NAME)
        self.assertEqual(prompt.parent.parent, self.work)                         # inside the output
        self.assertTrue(prompt.parent.name.startswith(PROMPT_DIR_PREFIX))
        self.assertNotEqual(prompt.parent, self.work / 'previews')
        self.assertEqual(self.seen['text'], build_prompt(self.frames))
        self.assertEqual((self.seen['dir_mode'], self.seen['file_mode']), (0o700, 0o600))

    def test_claude_missing_creates_nothing(self):
        with mock.patch.object(core.shutil, 'which', return_value=None), self.assertRaises(RuntimeError):
            call_claude(self.frames, 'sonnet', self.work)
        self.assertEqual(self.prompt_dirs(), [])


class CleanupTest(UnitBase):
    def assertCleaned(self):
        self.assertEqual(self.prompt_dirs(), [])
        self.assertEqual(sorted(os.listdir(self.work / 'previews')), sorted(Path(f.preview_path).name for f in self.frames))

    def test_success(self):
        self.call(self.recording())
        self.assertCleaned()

    def test_non_zero_exit(self):
        with self.assertRaises(RuntimeError):
            self.call(self.recording(lambda cmd: subprocess.CompletedProcess(cmd, 1, '', 'boom')))
        self.assertCleaned()

    def test_unparseable_answer(self):
        with self.assertRaises(ResponseFormatError):
            self.call(self.recording(lambda cmd: subprocess.CompletedProcess(cmd, 0, '{"result": "no json"}', '')))
        self.assertCleaned()

    def test_exception_from_run(self):
        for error in (OSError(5, 'I/O error'), subprocess.TimeoutExpired(['claude'], 360), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                def run(cmd, timeout=None, error=error):
                    raise error
                with self.assertRaises(type(error)):
                    self.call(run)
                self.assertCleaned()

    def test_only_unlink_and_rmdir(self):
        calls = []
        real_unlink, real_rmdir = os.unlink, os.rmdir
        with mock.patch.object(os, 'unlink', side_effect=lambda p, *a, **k: (calls.append(('unlink', p)), real_unlink(p, *a, **k))[1]), \
             mock.patch.object(os, 'rmdir', side_effect=lambda p, *a, **k: (calls.append(('rmdir', p)), real_rmdir(p, *a, **k))[1]):
            self.call(self.recording())
        prompt = self.seen['prompt']
        self.assertEqual(calls, [('unlink', prompt), ('rmdir', os.path.dirname(prompt))])

    def test_folder_that_cannot_be_removed_is_reported_and_kept(self):
        def run(cmd, timeout=None):
            prompt = PROMPT_RE.search(cmd[cmd.index('-p') + 1]).group(1)
            Path(os.path.dirname(prompt), 'unexpected.txt').write_text('x', encoding='utf-8')
            return FakeClaudeV3()(cmd, timeout)
        out = io.StringIO()
        with redirect_stdout(out):
            self.call(run)
        [kept] = self.prompt_dirs()
        self.assertEqual(os.listdir(self.work / kept), ['unexpected.txt'])         # never deleted recursively
        self.assertIn('[LEFTOVER]', out.getvalue())


class BlockReadsTest(UnitBase):
    def test_new_layout_is_readable_under_the_setting(self):
        sim = BlockReadsOutsideWorkingDirectories()
        photos = self.call(sim)
        self.assertEqual(sim.denied, [])
        self.assertEqual(len(photos), 2)

    def test_old_layout_reproduces_ot1(self):
        """The pre-F-1 command (prompt in a separate temporary folder, no --add-dir) is denied."""
        sim = BlockReadsOutsideWorkingDirectories()
        outside = tempfile.mkdtemp(prefix='pc-f1-old-')
        self.addCleanup(os.rmdir, outside)
        prompt = os.path.join(outside, 'tmpold.txt')
        Path(prompt).write_text(build_prompt(self.frames), encoding='utf-8')
        self.addCleanup(os.unlink, prompt)
        cmd = [CLAUDE, '-p', f'Read the prompt file at {prompt} and follow its instructions exactly.',
               '--model', 'sonnet', '--output-format', 'json', '--max-turns', '24',
               '--allowedTools', 'Read', '--disallowedTools', 'Bash,Edit,Write']
        with self.assertRaises(ResponseFormatError) as ctx:
            parse_claude_output(sim(cmd).stdout)
        self.assertIn('no JSON object', str(ctx.exception))
        self.assertEqual(sim.denied, [prompt])

    def test_source_and_rest_of_output_stay_closed(self):
        sim = BlockReadsOutsideWorkingDirectories()
        self.call(self.recording())
        cmd = self.seen['cmd']
        self.assertFalse(sim.allowed(cmd, self.frames[0].original_path))                # source photo
        self.assertFalse(sim.allowed(cmd, str(self.work / 'ratings-review.json')))      # rest of the output
        self.assertFalse(sim.allowed(cmd, str(self.work / 'archive' / 'old' / 'ratings-review.json')))
        self.assertTrue(sim.allowed(cmd, self.frames[0].preview_path))


class PipelineTest(E2EBase):
    """Full analyze with fakes and the permission simulation in front of the fake Claude."""

    def test_complete_run_under_the_setting(self):
        sim = BlockReadsOutsideWorkingDirectories(self.claude)
        self.claude = sim
        doc, _ = self.analyze()
        self.assertEqual(sim.denied, [])
        self.assertTrue(doc['results'])
        self.assertFalse([e for e in doc['errors'] if e['stage'] == 'batch'])
        self.assertFalse([n for n in os.listdir(self.work) if n.startswith(PROMPT_DIR_PREFIX)])
        manifest = json.loads((self.work / 'ratings-review.complete.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['orphan_preview_count'], 0)                     # prompt never a preview
        self.assertEqual(manifest['referenced_preview_count'], len(doc['results']))
        for cmd in sim.calls:
            self.assertEqual(add_dirs(cmd)[1], str(self.work / 'previews'))
            self.assertTrue(os.path.basename(add_dirs(cmd)[0]).startswith(PROMPT_DIR_PREFIX))

    def refused(self, **over) -> str:
        with self.assertRaises(SystemExit) as ctx:                                 # message travels in .code
            self.analyze(**over)
        return str(ctx.exception.code)

    def test_output_path_with_whitespace_refused_before_writing(self):
        spaced = self.tmp / 'out folder'
        self.assertIn('contains whitespace', self.refused(output=str(spaced)))
        code, _, _ = self.main('analyze', str(self.root), '--output', str(spaced))
        self.assertEqual(code, 1)                                                  # exit status through the CLI
        self.assertFalse(spaced.exists())
        self.assertEqual(self.claude.calls, [])

    def test_default_output_inside_a_spaced_source_refused(self):
        spaced_root = self.tmp / 'my library'
        make_image(spaced_root / 'DSC09999.JPG', 0)
        self.assertIn('contains whitespace', self.refused(folder=str(spaced_root), output=None))
        self.assertFalse((spaced_root / '.photo-curator').exists())
        self.assertEqual(sorted(os.listdir(spaced_root)), ['DSC09999.JPG'])


if __name__ == '__main__':
    unittest.main()
