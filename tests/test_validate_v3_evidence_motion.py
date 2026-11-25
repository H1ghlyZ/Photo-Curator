"""S3: specificity check (D3-1..D3-4, D3-9), evidence states per field (V9, V11, V12a/b/c,
V19), motion consistency (V17, section 10.1), distinct-moment claims and review-code triggers.

Pure validator only; synthetic values, no files, no tools.
"""
from __future__ import annotations
import unittest

from photo_curator.schema_v3 import REVIEW_CODES
from photo_curator.validate_v3 import Accepted, FrameContext, is_specific, validate_frame_v3

ORIG = '/library/DCIM/100MSDCF/DSC00001.ARW'
GOOD = 'The speaker raises his left hand at the podium'
GOOD2 = 'Eyes of the speaker meet the audience on the left'
GENERIC = 'Great photo, nice composition!'
CTX = FrameContext(original_path=ORIG, preview_path='/out/previews/DSC00001__abcdef0123.jpg',
                   session_id='DCIM/100MSDCF', burst_group=None, burst_sequence=None,
                   capture_time=None, original_sha256='c' * 64, batch_index=0)


def evaluable(t=3, e=3, motion='frozen', **over):
    raw = {
        'path': ORIG, 'evaluable': True, 'unevaluable_reason': None, 'capture_type': 'candid',
        'technical_score': t, 'editorial_score': e,
        'technical': {'motion': motion, 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]},
        'editorial': {'evidence': [GOOD2]}, 'decisive_evidence': [], 'proposed_rating': 3,
        'proposed_moment_override': False, 'flags': [], 'fatal_flaws': [], 'uncertain_checks': [],
        'distinct_moment': False, 'distinct_moment_evidence': [], 'burst_comparison': None,
        'burst_rank': None, 'best_in_burst': None, 'co_best': None, 'near_duplicate_of': None,
        'rank_reason': None, 'not_comparable_evidence': [], 'confidence': 0.9,
    }
    raw.update(over)
    return raw


def tech(**over):
    base = {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': [GOOD]}
    base.update(over)
    return base


class Base(unittest.TestCase):
    def ok(self, raw) -> Accepted:
        out = validate_frame_v3(raw, CTX)
        self.assertIsInstance(out, Accepted, getattr(out, 'entry', None))
        self.assertTrue(out.review_reasons <= REVIEW_CODES)
        return out


class SpecificityTest(unittest.TestCase):                                 # plan 4.1 table
    CASES = [
        ('The speaker raises his left hand', True),
        ('SPEAKER at the lectern, sharply focused', True),
        ("The speaker's gesture is sharp and clear", True),
        ('Left-hand side shows the podium clearly', True),
        ('Eyes are closed on the main subject here', True),
        ('GREAT PHOTO — speaker in focus, crisp', True),
        ('Great photo, nice composition!', False),
        ('Good exposure; nice composition; great photo.', False),
        ('Great photo great photo great photo', False),
        ('speaker', False),
        ('   speaker smiles   ', False),
        ('Leftover chairs clutter the room scene', True),                  # v3.1: chairs, room
        ('Leftover tables clutter the scene now', False),                  # whole tokens; 'tables' not listed
        ('Leftover chairs clutter the background', True),
        ('Handshake between two officials at podium', True),              # v3.1: officials, podium
        ('Handshake between two delegates outdoors', False),               # 'handshake' is not 'hand'
        ('Both hands raised above the crowd now', True),                  # D3-9 superseded by M-27: 'hands' listed
        ('Ｓｐｅａｋｅｒ at the podium, sharp focus', True),   # D3-2 kept for fullwidth; v3.1 'podium' matches
        ('Ｓｐｅａｋｅｒ ｐｏｄｉｕｍ, sharp focus here', False),  # D3-2: fullwidth tokens never match
        ('Nice composition overall, crowd visible', True),                # v3.1: crowd
        ('Nice composition overall, very pleasing', False),
    ]

    def test_table(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertIs(is_specific(text), expected)

    def test_non_strings_and_blank(self):
        for value in (None, 5, ['speaker on the left side'], '', '                         '):
            self.assertIs(is_specific(value), False)

    def test_length_counted_after_strip(self):
        self.assertIs(is_specific('speaker on the left!'), True)          # exactly 20
        self.assertIs(is_specific('  speaker on the lef  '), False)       # 19 after strip


class EvidenceRulesV31Test(unittest.TestCase):                            # M-27 (S16); not calibrated
    def test_new_keywords_make_an_entry_specific(self):
        for text in ('The bride laughs beside the groom outside', 'Microphone stand blocks the presenter',
                     'Banner at the top edge is cut in half', 'The moderator turns toward the panelists',
                     'Children run across the foreground grass'):
            with self.subTest(text=text):
                self.assertIs(is_specific(text), True)

    def test_singular_and_plural_listed_explicitly_no_stemming(self):
        self.assertIs(is_specific('One hand rests on the wooden rail'), True)
        self.assertIs(is_specific('Both hands rest on the wooden rail'), True)
        self.assertIs(is_specific('A single musician tunes up near exit'), True)
        self.assertIs(is_specific('Two musicians tune up near the exit'), False)    # plural not listed
        self.assertIs(is_specific('Two singers warm up near the exit now'), False)  # plural not listed

    def test_technical_terms_without_a_subject_stay_generic(self):
        for text in ('Sharp focus and good exposure throughout', 'Highlights are clipped and shadows noisy',
                     'Motion blur and noise across the frame'):
            with self.subTest(text=text):
                self.assertIs(is_specific(text), False)

    def test_new_generic_phrases(self):
        for text in ('Nice shot, great moment, well composed', 'Good lighting and good composition overall',
                     'Beautiful photo. Great image. Good shot.'):
            with self.subTest(text=text):
                self.assertIs(is_specific(text), False)
        self.assertIs(is_specific('Great shot of the speaker at night'), True)  # a keyword still counts


class TechnicalEditorialEvidenceTest(Base):                               # V11
    def test_states(self):
        cases = [
            ('missing', None, 'evidence_missing'),
            ('wrong type', 'speaker on the left side', 'evidence_missing'),
            ('non-string entry', [GOOD, 3], 'evidence_missing'),
            ('empty', [], 'evidence_missing'),
            ('generic', [GENERIC], 'evidence_generic'),
            ('one bad entry', [GOOD, 'speaker'], 'evidence_generic'),
            ('blank entry', [GOOD, '   '], 'evidence_generic'),           # D3-4
            ('empty string entry', [''], 'evidence_generic'),
        ]
        for label, value, code in cases:
            with self.subTest(field='technical', label=label):
                t = tech() if label == 'missing' else tech(evidence=value)
                if label == 'missing':
                    del t['evidence']
                out = self.ok(evaluable(technical=t))
                self.assertIn(code, out.review_reasons)
                stored = out.fields['technical']['evidence']
                self.assertEqual(stored, None if label in ('missing', 'wrong type', 'non-string entry') else value)
            with self.subTest(field='editorial', label=label):
                ed = {} if label == 'missing' else {'evidence': value}
                out = self.ok(evaluable(editorial=ed))
                self.assertIn(code, out.review_reasons)

    def test_editorial_not_object(self):
        for value in (None, [GOOD2], 'text'):
            with self.subTest(value=value):
                out = self.ok(evaluable(editorial=value))
                self.assertIsNone(out.fields['editorial'])
                self.assertIn('evidence_missing', out.review_reasons)
        raw = evaluable(); del raw['editorial']
        out = self.ok(raw)
        self.assertIsNone(out.fields['editorial'])

    def test_valid_evidence_no_codes(self):
        out = self.ok(evaluable())
        self.assertEqual(out.review_reasons, frozenset())
        self.assertEqual(out.fields['technical']['evidence'], [GOOD])
        self.assertEqual(out.fields['editorial'], {'evidence': [GOOD2]})


class MotionEvidenceTest(Base):                                           # V9
    def test_required_for_intentional(self):
        for motion in ('intentional_pan', 'intentional_drag'):
            with self.subTest(motion=motion):
                out = self.ok(evaluable(motion=motion, technical=tech(motion=motion, motion_evidence=[])))
                self.assertIn('evidence_missing', out.review_reasons)
                out = self.ok(evaluable(motion=motion, technical=tech(motion=motion, motion_evidence=[GENERIC])))
                self.assertIn('evidence_generic', out.review_reasons)
                self.assertEqual(out.fields['technical']['motion_evidence'], [GENERIC])
                out = self.ok(evaluable(motion=motion, technical=tech(motion=motion, motion_evidence='x')))
                self.assertIn('evidence_missing', out.review_reasons)
                self.assertIsNone(out.fields['technical']['motion_evidence'])
                out = self.ok(evaluable(motion=motion, technical=tech(motion=motion, motion_evidence=[GOOD])))
                self.assertEqual(out.review_reasons, frozenset())

    def test_not_required_otherwise(self):
        out = self.ok(evaluable(technical=tech(motion_evidence=[GENERIC])))
        self.assertEqual(out.review_reasons, frozenset())
        self.assertEqual(out.fields['technical']['motion_evidence'], [GENERIC])
        out = self.ok(evaluable(technical=tech(motion_evidence=None)))
        self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
        self.assertIsNone(out.fields['technical']['motion_evidence'])


class DecisiveEvidenceTest(Base):                                         # V12a, V12b, V12c
    def test_v12b_final_rating_1_4_5(self):
        for t, e, rating in ((1, 3, 1), (4, 4, 4), (5, 5, 5)):
            with self.subTest(rating=rating):
                out = self.ok(evaluable(t, e, proposed_rating=rating, decisive_evidence=[]))
                self.assertIn('evidence_missing', out.review_reasons)
                self.assertNotIn('override_invalid', out.review_reasons)
                out = self.ok(evaluable(t, e, proposed_rating=rating, decisive_evidence=[GENERIC]))
                self.assertIn('evidence_generic', out.review_reasons)
                raw = evaluable(t, e, proposed_rating=rating); del raw['decisive_evidence']
                out = self.ok(raw)
                self.assertIn('evidence_missing', out.review_reasons)
                self.assertIsNone(out.fields['decisive_evidence'])

    def test_model_proposed_3_matrix_4_without_evidence(self):            # 9.2 example
        out = self.ok(evaluable(4, 4, proposed_rating=3, decisive_evidence=[]))
        self.assertEqual(out.review_reasons, frozenset({'evidence_missing', 'rating_rule_mismatch'}))

    def test_v12c_type_only(self):
        out = self.ok(evaluable(3, 3, decisive_evidence=[GENERIC]))
        self.assertEqual(out.review_reasons, frozenset())
        self.assertEqual(out.fields['decisive_evidence'], [GENERIC])
        for value in (None, 'text', [1]):
            with self.subTest(value=value):
                out = self.ok(evaluable(3, 3, decisive_evidence=value))
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
                self.assertIsNone(out.fields['decisive_evidence'])
        raw = evaluable(3, 3); del raw['decisive_evidence']
        self.assertEqual(self.ok(raw).review_reasons, frozenset({'field_invalid'}))

    def test_v12a_wrong_type_withheld(self):
        out = self.ok(evaluable(2, 4, proposed_moment_override=True, decisive_evidence='because'))
        self.assertIsNone(out.fields['decisive_evidence'])
        self.assertTrue({'override_invalid', 'evidence_missing'} <= out.review_reasons)
        self.assertNotIn('field_invalid', out.review_reasons)


class MotionConsistencyTest(Base):                                         # V17, 10.1
    DEFINITE = ('frozen', 'camera_shake', 'unintended_subject_blur', 'intentional_pan', 'intentional_drag')

    def run_case(self, motion, checks, flags=()):
        ev = [GOOD] if motion.startswith('intentional') else []
        return self.ok(evaluable(motion=motion, technical=tech(motion=motion, motion_evidence=ev),
                                 uncertain_checks=checks, flags=list(flags)))

    def test_first_table(self):
        out = self.run_case('uncertain', ['motion_intent'])
        self.assertEqual(out.review_reasons, frozenset({'motion_uncertain', 'critical_defect_uncertain'}))
        out = self.run_case('uncertain', [])
        self.assertEqual(out.review_reasons, frozenset({'motion_conflict', 'motion_uncertain'}))
        for motion in self.DEFINITE:
            with self.subTest(motion=motion):
                out = self.run_case(motion, ['motion_intent'])
                self.assertEqual(out.review_reasons, frozenset({'motion_conflict', 'critical_defect_uncertain'}))
                self.assertEqual(out.fields['technical']['motion'], motion)      # kept
                self.assertEqual(out.fields['uncertain_checks'], ['motion_intent'])
                self.assertEqual(self.run_case(motion, []).review_reasons, frozenset())

    def test_null_uncertain_checks(self):
        out = self.run_case('frozen', None)
        self.assertEqual(out.review_reasons, frozenset({'motion_conflict', 'field_invalid'}))

    def test_subject_blur_table(self):
        for motion in ('camera_shake', 'unintended_subject_blur'):
            self.assertEqual(self.run_case(motion, [], ['subject_blur']).review_reasons, frozenset())
        for motion in ('intentional_pan', 'intentional_drag', 'frozen'):
            with self.subTest(motion=motion):
                out = self.run_case(motion, [], ['subject_blur'])
                self.assertEqual(out.review_reasons, frozenset({'motion_conflict'}))
                self.assertEqual(out.fields['flags'], ['subject_blur'])
        out = self.run_case('uncertain', ['motion_intent'], ['subject_blur'])
        self.assertIn('motion_conflict', out.review_reasons)

    def test_subject_blur_skipped_when_flags_invalid(self):
        out = self.ok(evaluable(flags=['subject_blur', 'none']))
        self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))


class DistinctMomentTest(Base):                                            # V18, V19, 7.1
    def test_valid_claim(self):
        out = self.ok(evaluable(3, 3, distinct_moment=True, distinct_moment_evidence=[GOOD]))
        self.assertIs(out.distinct_moment_claim_valid, True)
        self.assertEqual(out.review_reasons, frozenset())

    def test_claim_failures(self):
        cases = [
            ([], {'evidence_missing', 'distinct_moment_invalid'}, []),
            ([GENERIC], {'evidence_generic', 'distinct_moment_invalid'}, [GENERIC]),
            ('text', {'evidence_missing', 'distinct_moment_invalid'}, None),
        ]
        for value, codes, stored in cases:
            with self.subTest(value=value):
                out = self.ok(evaluable(distinct_moment=True, distinct_moment_evidence=value))
                self.assertEqual(out.review_reasons, frozenset(codes))
                self.assertEqual(out.fields['distinct_moment_evidence'], stored)
                self.assertIs(out.distinct_moment_claim_valid, False)
        raw = evaluable(distinct_moment=True); del raw['distinct_moment_evidence']
        out = self.ok(raw)
        self.assertEqual(out.review_reasons, frozenset({'evidence_missing', 'distinct_moment_invalid'}))

    def test_e1_alone(self):
        out = self.ok(evaluable(3, 1, proposed_rating=1, decisive_evidence=[GOOD],
                                distinct_moment=True, distinct_moment_evidence=[GOOD]))
        self.assertEqual(out.review_reasons, frozenset({'distinct_moment_invalid'}))
        self.assertIs(out.distinct_moment_claim_valid, False)

    def test_without_claim(self):
        # field_invalid also comes from V18 when distinct_moment is null; codes are a set.
        for claim in (False, None):
            for value in ([GOOD], [GENERIC], [3]):                          # non-empty list: K
                with self.subTest(claim=claim, value=value):
                    out = self.ok(evaluable(distinct_moment=claim, distinct_moment_evidence=value))
                    self.assertEqual(out.fields['distinct_moment_evidence'], value)
                    self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
                    self.assertIs(out.distinct_moment_claim_valid, False)
        out = self.ok(evaluable(distinct_moment=False, distinct_moment_evidence=[]))
        self.assertEqual(out.fields['distinct_moment_evidence'], [])
        self.assertEqual(out.review_reasons, frozenset())
        out = self.ok(evaluable(distinct_moment=None, distinct_moment_evidence=[]))
        self.assertEqual(out.fields['distinct_moment_evidence'], [])
        self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))  # from V18 only

    def test_without_claim_missing_or_not_list_is_null(self):               # D3-14
        for claim in (False, None):
            for value in (None, 'text', 0, {'a': 1}, True):
                with self.subTest(claim=claim, value=value):
                    out = self.ok(evaluable(distinct_moment=claim, distinct_moment_evidence=value))
                    self.assertIsNone(out.fields['distinct_moment_evidence'])
                    self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
                    self.assertIs(out.distinct_moment_claim_valid, False)
            with self.subTest(claim=claim, value='missing'):
                raw = evaluable(distinct_moment=claim); del raw['distinct_moment_evidence']
                out = self.ok(raw)
                self.assertIsNone(out.fields['distinct_moment_evidence'])
                self.assertEqual(out.review_reasons, frozenset({'field_invalid'}))
                self.assertIs(out.distinct_moment_claim_valid, False)


class ReviewCodeTriggerTest(Base):
    def test_clean_result_has_no_codes(self):
        self.assertEqual(self.ok(evaluable()).review_reasons, frozenset())

    def test_codes_are_a_frozenset_of_known_codes(self):
        out = self.ok(evaluable(4, 1, proposed_rating=None, confidence=0.1, uncertain_checks=None,
                                decisive_evidence=[], flags='bad', distinct_moment=True))
        self.assertIsInstance(out.review_reasons, frozenset)
        self.assertTrue(out.review_reasons <= REVIEW_CODES)
        self.assertTrue({'score_gap', 'rating_rule_mismatch', 'low_confidence', 'motion_conflict',
                         'field_invalid', 'evidence_missing', 'distinct_moment_invalid'} <= out.review_reasons)


if __name__ == '__main__':
    unittest.main()
