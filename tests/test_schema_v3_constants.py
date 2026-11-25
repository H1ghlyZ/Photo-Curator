"""S1: schema v3 constants, enumerations, decision table, field inventory and evidence pin.

Pure data checks against spec 5g3a. No files, no network, no external tools.
"""
from __future__ import annotations
import unittest

from photo_curator import schema_v3 as s


class FileLevelConstantsTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(s.SCHEMA_VERSION_V3, 3)
        self.assertEqual(s.RUBRIC_VERSION, 'v3')
        self.assertEqual(s.RATING_STYLE_V3, 'strict_v3')
        self.assertEqual(s.BURST_SCOPE, 'per_session')
        self.assertEqual(s.CONFIDENCE_THRESHOLD, 0.6)
        self.assertEqual(s.MAX_BURST_SIZE_RANGE, (2, 12))
        self.assertEqual(s.EVIDENCE_RULES_VERSION, 'v3.1')                # M-27 (S16)

    def test_v3_not_enabled_in_core(self):                       # decision A-1
        from photo_curator.core import SCHEMA_VERSIONS
        self.assertEqual(SCHEMA_VERSIONS, {1, 2})


class EnumerationTest(unittest.TestCase):
    def test_flags_frozen_twelve(self):                           # spec 3.3, U7
        self.assertEqual(s.FLAGS, {
            'missed_focus', 'subject_blur', 'closed_eyes', 'awkward_expression',
            'unrecoverable_exposure', 'heavy_noise', 'clutter', 'mergers',
            'obstruction', 'subject_cut_off', 'cut_at_joint', 'duplicate'})
        self.assertEqual(len(s.FLAGS), 12)
        for old in ('none', 'poor_exposure', 'subject_clipped', 'blur', 'Missed_Focus'):
            self.assertNotIn(old, s.FLAGS)
        self.assertIsInstance(s.FLAGS, frozenset)

    def test_uncertain_checks(self):                              # spec 10
        self.assertEqual(s.UNCERTAIN_CHECKS, {'focus', 'eyes', 'motion_intent', 'exposure',
                                              'expression', 'subject_identification'})

    def test_motion_classes(self):                                # spec 3.2
        self.assertEqual(s.MOTION_CLASSES, {'frozen', 'intentional_pan', 'intentional_drag',
                                            'camera_shake', 'unintended_subject_blur', 'uncertain'})

    def test_capture_types(self):                                 # spec 5
        self.assertEqual(s.CAPTURE_TYPES, {'candid', 'posed_portrait', 'group', 'detail', 'venue'})

    def test_decisions(self):                                     # spec 6.5
        self.assertEqual(s.DECISIONS, {0: 'unreadable', 1: 'reject', 2: 'weak', 3: 'usable',
                                       4: 'strong', 5: 'exceptional'})

    def test_suggestions_and_states(self):                        # spec 7.1, 7.2, 12.1
        self.assertEqual(s.DOCUMENTATION_SUGGESTIONS,
                         {'retain', 'retain_low_rating', 'not_suggested', 'unrated'})
        self.assertEqual(s.EDITORIAL_SUGGESTIONS,
                         {'hero_candidate', 'highlight', 'pending_approval', 'not_suggested'})
        self.assertEqual(s.APPROVAL_STATES,
                         {'malformed', 'not_required', 'pending', 'invalidated', 'approved'})

    def test_burst_enums(self):                                   # spec 8.1, 8.3
        self.assertEqual(s.BURST_COMPARISON, {'ranked', 'not_comparable'})
        self.assertEqual(s.BURST_RANKING_STATUS, {'validated', 'not_comparable', 'invalid'})
        self.assertNotIn('not_applicable', s.BURST_RANKING_STATUS)

    def test_review_codes(self):                                  # spec 11
        self.assertEqual(s.REVIEW_CODES, {
            'low_confidence', 'rating_5', 'moment_override', 'override_invalid',
            'rating_rule_mismatch', 'unevaluable', 'motion_uncertain', 'motion_conflict',
            'critical_defect_uncertain', 'evidence_missing', 'evidence_generic', 'score_gap',
            'co_best_tie', 'weak_burst_best', 'burst_winner_invalid', 'burst_rank_invalid',
            'near_duplicate_invalid', 'burst_frame_missing', 'burst_comparison_invalid',
            'burst_ranking_invalid', 'burst_not_comparable', 'documentation_keep_low_rating',
            'distinct_moment_highlight', 'distinct_moment_invalid', 'field_invalid'})
        self.assertEqual(len(s.REVIEW_CODES), 25)


class DecisionTableTest(unittest.TestCase):
    SPEC_6_2 = {   # rows T1..T5, columns E1..E5, base values as printed in spec 6.2
        1: [1, 1, 1, 1, 1],
        2: [1, 2, 2, 2, 2],
        3: [1, 2, 3, 3, 3],
        4: [1, 2, 3, 4, 5],
        5: [1, 2, 3, 4, 5],
    }

    def test_all_25_cells(self):
        self.assertEqual(len(s.BASE_MATRIX), 25)
        for t, row in self.SPEC_6_2.items():
            for e, expected in enumerate(row, 1):
                with self.subTest(t=t, e=e):
                    self.assertEqual(s.BASE_MATRIX[(t, e)], expected)

    def test_matrix_follows_spec_6_1_rules(self):
        def rule(t, e):
            if t == 1 or e == 1: return 1
            if e == 5 and t >= 4: return 5
            if e >= 4 and t >= 4: return 4
            if e >= 3 and t >= 3: return 3
            return 2
        for (t, e), value in s.BASE_MATRIX.items():
            with self.subTest(t=t, e=e): self.assertEqual(value, rule(t, e))

    def test_override_cells(self):                                # spec 6.3
        self.assertEqual(s.OVERRIDE_CELLS, {(2, 4): 3, (2, 5): 3, (3, 5): 4})
        for cell, capped in s.OVERRIDE_CELLS.items():
            self.assertEqual(capped, s.BASE_MATRIX[cell] + 1, cell)
        for t in range(1, 6):
            self.assertNotIn((1, t), s.OVERRIDE_CELLS)            # T = 1 never overridable


class FieldInventoryTest(unittest.TestCase):
    SPEC_14_1 = {
        'C': {'path', 'original_path', 'preview_path', 'session_id', 'capture_time',
              'original_sha256', 'moment_override', 'rating', 'decision', 'burst_group',
              'burst_sequence', 'burst_ranking_status', 'documentation_suggestion',
              'editorial_suggestion', 'needs_review', 'review_reasons', 'reasons'},
        'M': {'evaluable', 'unevaluable_reason', 'capture_type', 'technical_score',
              'editorial_score', 'technical', 'editorial', 'decisive_evidence',
              'proposed_rating', 'proposed_moment_override', 'flags', 'fatal_flaws',
              'uncertain_checks', 'distinct_moment', 'distinct_moment_evidence',
              'burst_comparison', 'burst_rank', 'best_in_burst', 'co_best',
              'near_duplicate_of', 'rank_reason', 'not_comparable_evidence', 'confidence'},
        'H': {'approved', 'approved_rating', 'approved_at', 'approved_original_path',
              'approved_original_sha256', 'approved_preview_path', 'approved_preview_sha256'},
    }

    def test_every_field_and_source(self):
        expected = {name: src for src, names in self.SPEC_14_1.items() for name in names}
        self.assertEqual(s.RESULT_FIELDS, expected)
        self.assertEqual(s.CODE_FIELDS, self.SPEC_14_1['C'])
        self.assertEqual(s.MODEL_FIELDS, self.SPEC_14_1['M'])
        self.assertEqual(s.HUMAN_FIELDS, self.SPEC_14_1['H'])
        self.assertFalse(s.CODE_FIELDS & s.MODEL_FIELDS or s.CODE_FIELDS & s.HUMAN_FIELDS
                         or s.MODEL_FIELDS & s.HUMAN_FIELDS)

    def test_nested_and_approval_fields(self):
        self.assertEqual(s.TECHNICAL_SUBFIELDS, ('motion', 'motion_evidence', 'recoverable_in_raw', 'evidence'))
        self.assertEqual(s.EDITORIAL_SUBFIELDS, ('evidence',))
        self.assertEqual(len(s.APPROVAL_FIELDS), 7)
        self.assertEqual(set(s.APPROVAL_FIELDS), self.SPEC_14_1['H'])
        self.assertEqual(s.APPROVAL_FIELDS[0], 'approved')


V30_GENERIC = ('great photo', 'nice composition', 'good exposure')            # spec 9.3 examples (A-2)
V30_KEYWORDS = ('speaker', 'eyes', 'left', 'background', 'hand')

# M-27 (S16): evidence rules v3.1, approved list; not calibrated (S17 / M-26).
V31_GENERIC = V30_GENERIC + (
    'nice shot', 'good shot', 'great shot', 'nice photo', 'good photo', 'beautiful photo',
    'great composition', 'good composition', 'well composed', 'good lighting', 'nice lighting',
    'great moment', 'nice moment', 'great image', 'good image')
V31_KEYWORDS = (
    'speaker', 'speakers', 'presenter', 'moderator', 'panelist', 'panelists', 'host', 'guest', 'guests',
    'audience', 'crowd', 'attendee', 'attendees', 'participant', 'participants', 'person', 'people',
    'man', 'woman', 'men', 'women', 'child', 'children', 'couple', 'bride', 'groom', 'group', 'team',
    'performer', 'performers', 'singer', 'musician', 'official', 'officials',
    'eyes', 'eye', 'face', 'faces', 'hand', 'hands', 'arm', 'arms', 'mouth', 'smile', 'expression',
    'gesture', 'head',
    'left', 'right', 'center', 'centre', 'top', 'bottom', 'foreground', 'background', 'corner', 'edge',
    'horizon', 'sky',
    'stage', 'podium', 'lectern', 'microphone', 'screen', 'banner', 'table', 'chair', 'chairs', 'venue',
    'room', 'hall', 'door', 'window', 'logo', 'sign')


class EvidenceRulesPinTest(unittest.TestCase):
    def test_rules_are_exactly_the_approved_v31_lists(self):     # M-27
        self.assertEqual(s.EVIDENCE_MIN_LENGTH, 20)
        self.assertEqual(len(s.GENERIC_PHRASES), len(set(s.GENERIC_PHRASES)))     # no duplicates
        self.assertEqual(len(s.CONCRETE_KEYWORDS), len(set(s.CONCRETE_KEYWORDS)))
        self.assertEqual(set(s.GENERIC_PHRASES), set(V31_GENERIC))
        self.assertEqual(set(s.CONCRETE_KEYWORDS), set(V31_KEYWORDS))

    def test_v31_is_a_superset_of_the_spec_examples(self):
        self.assertTrue(set(V30_GENERIC) <= set(s.GENERIC_PHRASES))
        self.assertTrue(set(V30_KEYWORDS) <= set(s.CONCRETE_KEYWORDS))

    def test_no_technical_only_terms_are_keywords(self):         # subjects or frame parts only
        for term in ('focus', 'exposure', 'highlights', 'shadows', 'sharp', 'blur', 'noise'):
            self.assertNotIn(term, s.CONCRETE_KEYWORDS)

    def test_canonical_form(self):                               # v3.0 bytes kept as history
        self.assertEqual(
            s.evidence_rules_canonical(20, V30_GENERIC, V30_KEYWORDS),
            b'{"concrete_keywords":["background","eyes","hand","left","speaker"],'
            b'"generic_phrases":["good exposure","great photo","nice composition"],"min_length":20}')
        self.assertTrue(s.evidence_rules_canonical().startswith(b'{"concrete_keywords":["arm","arms","attendee",'))

    def test_digest_matches_pin_for_current_version(self):
        self.assertIn(s.EVIDENCE_RULES_VERSION, s.EVIDENCE_RULES_PINS)
        self.assertEqual(s.evidence_rules_digest(), s.EVIDENCE_RULES_PINS[s.EVIDENCE_RULES_VERSION])
        self.assertEqual(s.EVIDENCE_RULES_PINS['v3.1'],
                         'b66f76715a784e56b9b5df7ae06bdd0ad4519ec35689a201a45b8a5c7fcd0df3')

    def test_v30_pin_kept_for_history(self):
        self.assertEqual(s.EVIDENCE_RULES_PINS['v3.0'],
                         '08d30b381400dfa7850a020bc0b53d2477d9e536c13edff0ee15faecbf49401f')
        self.assertEqual(s.evidence_rules_digest(20, V30_GENERIC, V30_KEYWORDS), s.EVIDENCE_RULES_PINS['v3.0'])
        self.assertNotEqual(s.EVIDENCE_RULES_PINS['v3.0'], s.EVIDENCE_RULES_PINS['v3.1'])

    def test_any_rule_change_changes_the_digest(self):
        pinned = s.EVIDENCE_RULES_PINS[s.EVIDENCE_RULES_VERSION]
        self.assertNotEqual(s.evidence_rules_digest(min_length=21), pinned)
        self.assertNotEqual(s.evidence_rules_digest(generic_phrases=list(s.GENERIC_PHRASES) + ['lovely shot']), pinned)
        self.assertNotEqual(s.evidence_rules_digest(concrete_keywords=list(s.CONCRETE_KEYWORDS)[:-1]), pinned)

    def test_order_does_not_change_the_digest(self):
        self.assertEqual(s.evidence_rules_digest(generic_phrases=list(reversed(s.GENERIC_PHRASES)),
                                                 concrete_keywords=list(reversed(s.CONCRETE_KEYWORDS))),
                         s.evidence_rules_digest())


if __name__ == '__main__':
    unittest.main()
