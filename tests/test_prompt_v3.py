"""S9: strict v3 prompt and response format (spec 20 items 17 and 5), against the fake Claude only."""
from __future__ import annotations
import json, re, unittest

from photo_curator import prompt_v3, schema_v3, validate_v3
from photo_curator.prompt_v3 import (FLAG_MEANINGS, RESPONSE_FIELDS, STRICT_PROMPT_V3, PromptFrame,
                                     ResponseFormatError, build_prompt, parse_claude_output)
from photo_curator.schema_v3 import (BURST_COMPARISON, CAPTURE_TYPES, EVIDENCE_MIN_LENGTH, FLAGS,
                                     MODEL_FIELDS, MOTION_CLASSES, UNCERTAIN_CHECKS)
from photo_curator.validate_v3 import Accepted, FrameContext, validate_batch_v3
from tests.fake_claude_v3 import FakeClaudeV3, parse_listing, unevaluable_result

LIB = '/library/DCIM/100MSDCF'
SHA = 'c' * 64


def frame(n, group=None, sequence=None):
    return PromptFrame(f'{LIB}/DSC{n:05d}.ARW', f'/out/previews/DSC{n:05d}__{n:010d}.jpg', 'DCIM/100MSDCF',
                       group, sequence)


def context(f: PromptFrame, batch_index=0):
    return FrameContext(original_path=f.original_path, preview_path=f.preview_path, session_id=f.session_id,
                        burst_group=f.burst_group, burst_sequence=f.burst_sequence or f.burst_group,
                        capture_time=None, original_sha256=SHA, batch_index=batch_index)


class VocabularyTest(unittest.TestCase):
    """Item 5: one flag constant shared by prompt and validator; every vocabulary from schema_v3."""

    def test_flag_meanings_cover_exactly_the_frozen_flags(self):
        self.assertEqual(set(FLAG_MEANINGS), set(FLAGS))
        self.assertEqual(len(FLAGS), 12)

    def test_prompt_and_validator_share_the_constant(self):
        self.assertIs(prompt_v3.FLAGS, schema_v3.FLAGS)
        self.assertIs(validate_v3.FLAGS, schema_v3.FLAGS)

    def test_every_flag_listed_with_its_meaning(self):
        for name in FLAGS:
            with self.subTest(flag=name):
                self.assertIn(f'- "{name}": {FLAG_MEANINGS[name]}', STRICT_PROMPT_V3)
        block = STRICT_PROMPT_V3.split('FLAGS ("flags")', 1)[1].split('FATAL FLAWS', 1)[0]
        listed = re.findall(r'^- "([a-z_]+)": ', block, flags=re.M)
        self.assertEqual(sorted(listed), sorted(FLAGS))

    def test_v2_flags_declared_invalid(self):
        self.assertIn('"none" and "poor_exposure" are not valid flags', STRICT_PROMPT_V3)

    def test_vocabularies_from_schema(self):
        for values in (MOTION_CLASSES, UNCERTAIN_CHECKS, CAPTURE_TYPES, BURST_COMPARISON):
            for value in values:
                with self.subTest(value=value):
                    self.assertIn(f'"{value}"', STRICT_PROMPT_V3)
        self.assertIn(f'at least {EVIDENCE_MIN_LENGTH} characters', STRICT_PROMPT_V3)


class DefinitionsTest(unittest.TestCase):
    """Item 17: the definitions the prompt must carry."""

    def assert_in_prompt(self, *phrases):
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, STRICT_PROMPT_V3)

    def test_subject_cut_off_capture_time_only(self):
        self.assert_in_prompt('outside the frame edge AT CAPTURE',
                              'Not for crops caused by straightening, tilt correction',
                              'It is NOT "subject_cut_off".')

    def test_subject_blur_unintended_only(self):
        self.assert_in_prompt('unintended blur on a key subject', 'Never for intentional motion blur',
                              'never with "frozen"', 'Intentional motion blur is never a fault.')

    def test_residual_noise_after_raw_noise_reduction(self):
        self.assert_in_prompt('judge only the RESIDUAL noise after the applicable RAW noise reduction',
                              'it can lower T at most to 3', 'T at most 2; flag "heavy_noise"')

    def test_raw_recoverable_rules(self):
        self.assert_in_prompt('judged AFTER the corrections RAW development allows',
                              'Record them in "technical.recoverable_in_raw"; they lower neither T nor E.',
                              'must not also be the reason for a lower T or E',
                              'Do not assume every flaw in the preview is also in the RAW data.')

    def test_fatal_flaws_and_unevaluable(self):
        self.assert_in_prompt('For an unevaluable image it must be exactly [].',
                              'non-empty "unevaluable_reason"',
                              '"decisive_evidence", "distinct_moment_evidence", "not_comparable_evidence" and\n"fatal_flaws" []',
                              '"proposed_rating" 0', 'A readable but difficult image is never unevaluable')

    def test_burst_comparison_and_not_comparable_evidence(self):
        self.assert_in_prompt('all evaluable frames of one\ngroup use the same value',
                              '"not_comparable_evidence" saying what makes this frame unrelated',
                              'Never pick an arbitrary winner.', 'never across sessions',
                              'Burst comparison never changes T or E.')

    def test_motion_intent_rules(self):
        self.assert_in_prompt('use "uncertain" AND put\n"motion_intent" in "uncertain_checks"',
                              'needs visible evidence\nof intent in "technical.motion_evidence"')

    def test_no_invention(self):
        self.assert_in_prompt('NEVER INVENT.', 'never make an observation up')

    R4_CLARIFICATION = (
        'The "duplicate" flag and "near_duplicate_of" are judged independently. "duplicate" identifies a '
        'near-identical frame and indicates redundancy only; it may apply to any frame, regardless of burst '
        'membership or ranking. "near_duplicate_of" identifies a better-ranked frame in the same ranked '
        'comparison group, subject to B5 validation. A non-null "near_duplicate_of" makes the frame ineligible '
        'for highlights, as specified in section 7.2. Neither field changes T, E, or the rating.')

    def test_duplicate_and_near_duplicate_clarification(self):                 # R-4
        self.assertEqual(STRICT_PROMPT_V3.count(self.R4_CLARIFICATION), 1)
        bursts = STRICT_PROMPT_V3.split('\nBURSTS.', 1)[1].split('\nUNEVALUABLE IMAGES', 1)[0]
        self.assertIn('Burst comparison never changes T or E.\n' + self.R4_CLARIFICATION + '\n', bursts)
        self.assertIn(self.R4_CLARIFICATION, build_prompt([frame(1, 'g1'), frame(2, 'g1')]))


class ResponseFieldsTest(unittest.TestCase):
    def test_exactly_the_model_fields_plus_path(self):
        self.assertEqual(set(RESPONSE_FIELDS) - {'path'}, set(MODEL_FIELDS))
        self.assertEqual(len(RESPONSE_FIELDS), len(set(RESPONSE_FIELDS)))

    def test_code_fields_never_requested(self):
        keys_line = json.dumps(list(RESPONSE_FIELDS))
        self.assertIn(keys_line, STRICT_PROMPT_V3)
        for code_field in schema_v3.CODE_FIELDS | schema_v3.HUMAN_FIELDS:
            if code_field != 'path':
                self.assertNotIn(f'"{code_field}"', keys_line)
        self.assertIn('Do NOT return\n"rating", "decision"', STRICT_PROMPT_V3)


class BuildPromptTest(unittest.TestCase):
    def test_listing(self):
        frames = [frame(1), frame(2, 'b1', 'b1'), frame(3, 'b1-p1', 'b1')]
        prompt = build_prompt(frames)
        self.assertTrue(prompt.startswith(STRICT_PROMPT_V3))
        for f in frames:
            self.assertIn(f'- Original: {f.original_path}\n  Preview to inspect: {f.preview_path}', prompt)
        self.assertIn('  burst_group: null', prompt)
        self.assertEqual(prompt.count('part of longer sequence: b1'), 1)       # only for the split part
        self.assertEqual(parse_listing(prompt), [(frames[0].original_path, None), (frames[1].original_path, 'b1'),
                                                  (frames[2].original_path, 'b1-p1')])

    def test_rejects_empty_or_duplicate(self):
        with self.assertRaises(ValueError):
            build_prompt([])
        with self.assertRaises(ValueError):
            build_prompt([frame(1), frame(1)])


class ParseOutputTest(unittest.TestCase):
    def test_envelope_with_json_result(self):
        stdout = json.dumps({'result': json.dumps({'photos': [{'path': 'x'}]})})
        self.assertEqual(parse_claude_output(stdout), [{'path': 'x'}])

    def test_result_wrapped_in_prose_or_fence(self):
        stdout = json.dumps({'result': 'Here you go:\n```json\n{"photos": []}\n```'})
        self.assertEqual(parse_claude_output(stdout), [])

    def test_failures(self):
        for stdout in ('not json', json.dumps({'result': 'no json here'}),
                       json.dumps({'result': json.dumps({'items': []})}),
                       json.dumps({'result': json.dumps({'photos': {}})})):
            with self.subTest(stdout=stdout[:30]), self.assertRaises(ResponseFormatError):
                parse_claude_output(stdout)


class FakeRoundTripTest(unittest.TestCase):
    """The fake's answers pass the S3-S4 validator unchanged; nothing real is executed."""

    def run_fake(self, frames, fake):
        prompt = build_prompt(frames)
        completed = fake(['claude', '-p', prompt, '--output-format', 'json'], timeout=60)
        self.assertEqual(completed.returncode, 0)
        photos = parse_claude_output(completed.stdout)
        return validate_batch_v3([f.original_path for f in frames], photos, {f.original_path: context(f) for f in frames})

    def test_default_answers_validate(self):
        frames = [frame(1), frame(2, 'b1'), frame(3, 'b1')]
        fake = FakeClaudeV3()
        accepted, errors = self.run_fake(frames, fake)
        self.assertEqual(errors, [])
        self.assertEqual(set(accepted), {f.original_path for f in frames})
        self.assertTrue(all(isinstance(a, Accepted) for a in accepted.values()))
        self.assertEqual(accepted[frames[1].original_path].burst_raw['burst_comparison'], 'ranked')
        self.assertEqual(len(fake.calls), 1)

    def test_unevaluable_answer_gives_rating_0(self):
        frames = [frame(1)]
        accepted, errors = self.run_fake(frames, FakeClaudeV3({frames[0].original_path: unevaluable_result(frames[0].original_path)}))
        self.assertEqual(errors, [])
        self.assertEqual(accepted[frames[0].original_path].rating, 0)

    def test_omitted_and_extra_answers_become_errors(self):
        frames = [frame(1), frame(2)]
        fake = FakeClaudeV3({frames[1].original_path: None}, extra=[{'path': '/elsewhere/X.ARW'}])
        accepted, errors = self.run_fake(frames, fake)
        self.assertEqual(set(accepted), {frames[0].original_path})
        self.assertEqual(len(errors), 2)

    def test_prompt_file_form(self):
        import os, tempfile
        frames = [frame(1)]
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as handle:
            handle.write(build_prompt(frames))
        self.addCleanup(os.unlink, handle.name)
        fake = FakeClaudeV3()
        completed = fake(['claude', '-p', f'Read the prompt file at {handle.name} and follow it.'])
        self.assertEqual([p['path'] for p in parse_claude_output(completed.stdout)], [frames[0].original_path])

    def test_failed_call(self):
        completed = FakeClaudeV3(returncode=1)(['claude', '-p', build_prompt([frame(1)])])
        self.assertEqual(completed.returncode, 1)


if __name__ == '__main__':
    unittest.main()
