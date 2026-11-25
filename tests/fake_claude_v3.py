"""Fake Claude for v3 tests (decision M-11: every v3 test uses this fake, never the real CLI).

``FakeClaudeV3`` is a drop-in for ``core.run``-style calls (``fake(cmd, timeout=...)``). It reads
the v3 prompt (inline after ``-p`` or from the prompt file named in it), finds the listed
originals and their burst groups, and returns a ``subprocess.CompletedProcess`` whose stdout is
a ``claude --output-format json`` envelope holding a ``photos`` array. Default answers are
valid v3 model results; tests override them per original path.

It never starts a process, never reads an image and never touches an original.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
from typing import Any, Callable

GOOD_TECH = 'The speaker in the centre is sharp, eyes in focus'
GOOD_EDIT = 'The speaker gestures with his left hand toward the audience'
_ORIGINAL = re.compile(r'^- Original: (.+)$')
_BURST = re.compile(r'^  burst_group: (.+)$')
_PROMPT_FILE = re.compile(r'Read the prompt file at (\S+)')


def evaluable_result(path: str, **overrides) -> dict:
    """A valid evaluable v3 model result outside a burst (T3/E3, proposed rating 3)."""
    result = {
        'path': path, 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': 3, 'editorial_score': 3,
        'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD_TECH]},
        'editorial': {'evidence': [GOOD_EDIT]}, 'decisive_evidence': [], 'proposed_rating': 3,
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': False, 'distinct_moment_evidence': [], 'burst_comparison': None, 'burst_rank': None,
        'best_in_burst': None, 'co_best': None, 'near_duplicate_of': None, 'rank_reason': None,
        'not_comparable_evidence': [], 'confidence': 0.9,
    }
    result.update(copy.deepcopy(overrides))
    return result


def unevaluable_result(path: str, reason: str = 'preview is corrupt and cannot be read') -> dict:
    """A valid unevaluable v3 model result (spec 6.4 exact values)."""
    return evaluable_result(
        path, evaluable=False, unevaluable_reason=reason, technical_score=None, editorial_score=None,
        technical={'motion': None, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []},
        editorial={'evidence': []}, proposed_rating=0, confidence=0.5)


def ranked_burst_results(paths: list[str]) -> list[dict]:
    """A valid ``ranked`` group: first frame wins, the rest rank 2..N."""
    out = []
    for index, path in enumerate(paths):
        out.append(evaluable_result(
            path, burst_comparison='ranked', burst_rank=index + 1, best_in_burst=index == 0, co_best=False,
            near_duplicate_of=None,
            rank_reason=f'Frame {index + 1}: the speaker on the left has the clearest expression in this group'))
    return out


def parse_listing(prompt: str) -> list[tuple[str, str | None]]:
    """``(original_path, burst_group)`` pairs from a v3 prompt listing."""
    frames, current = [], None
    for line in prompt.splitlines():
        m = _ORIGINAL.match(line)
        if m:
            current = m.group(1)
            frames.append([current, None])
            continue
        m = _BURST.match(line)
        if m and frames:
            frames[-1][1] = None if m.group(1) == 'null' else m.group(1)
    return [tuple(f) for f in frames]


class FakeClaudeV3:
    """Callable fake for the Claude CLI.

    - ``overrides``: {original_path: result dict | None}; None omits that original.
    - ``extra``: items appended to ``photos`` (for example a path outside the batch).
    - ``returncode`` / ``stdout``: force a failed call or raw output.
    - ``transform``: optional function applied to the final photos list.
    """

    def __init__(self, overrides: dict[str, Any] | None = None, *, extra: list | None = None,
                 returncode: int = 0, stdout: str | None = None,
                 transform: Callable[[list], list] | None = None):
        self.overrides = overrides or {}
        self.extra = extra or []
        self.returncode, self.raw_stdout, self.transform = returncode, stdout, transform
        self.calls: list[dict] = []

    def _prompt_text(self, cmd: list[str]) -> str:
        text = cmd[cmd.index('-p') + 1] if '-p' in cmd else ''
        m = _PROMPT_FILE.search(text)
        if m:
            with open(m.group(1), encoding='utf-8') as handle:
                return handle.read()
        return text

    def photos_for(self, prompt: str) -> list:
        frames = parse_listing(prompt)
        groups: dict[str, list[str]] = {}
        for path, group in frames:
            if group is not None:
                groups.setdefault(group, []).append(path)
        defaults = {}
        for paths in groups.values():
            for result in ranked_burst_results(paths):
                defaults[result['path']] = result
        photos = []
        for path, _group in frames:
            if path in self.overrides:
                if self.overrides[path] is not None:
                    photos.append(copy.deepcopy(self.overrides[path]))
                continue
            photos.append(defaults.get(path) or evaluable_result(path))
        photos.extend(copy.deepcopy(self.extra))
        return self.transform(photos) if self.transform else photos

    def __call__(self, cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
        prompt = self._prompt_text(list(cmd))
        photos = self.photos_for(prompt)
        self.calls.append({'cmd': list(cmd), 'timeout': timeout, 'prompt': prompt, 'photos': photos})
        if self.raw_stdout is not None:
            stdout = self.raw_stdout
        else:
            stdout = json.dumps({'type': 'result', 'result': json.dumps({'photos': photos})})
        return subprocess.CompletedProcess(list(cmd), self.returncode, stdout=stdout,
                                           stderr='' if self.returncode == 0 else 'fake failure')
