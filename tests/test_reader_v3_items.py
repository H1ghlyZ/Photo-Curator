"""S11: per-item read-time checks V33-V38, V40, V42, V45 (spec 15.3, 12.1, 12.2, 14.1, 14.2;
spec 20 items 13 and 20). Each row is broken on its own in an otherwise complete v3 output."""
from __future__ import annotations
import json, os, unittest
from pathlib import Path
from unittest import mock

from photo_curator import hashing_v3, reader_v3
from photo_curator.core import OUTPUT_MARKER
from photo_curator.preview_checks_v3 import APPLY_SKIP_MESSAGE
from photo_curator.reader_v3 import (BINDING_MESSAGES, SKIP_AMBIGUOUS, SKIP_GENERATED, SKIP_INCONSISTENT,
                                     SKIP_MALFORMED, SKIP_NOT_ORIGINAL, read_results)
from tests.test_document_v3 import original
from tests.test_reader_v3_file import RESULTS, Base


class Helpers(Base):
    def edit(self, n: int, **changes) -> dict:
        k = self.index(n)                                   # remembered before original_path may change
        self.out.doc['results'][k].update(changes)
        self.out.rewrite()
        return self.out.doc['results'][k]

    def index(self, n: int) -> int:
        cache = self.__dict__.setdefault('_indexes', {})
        if n not in cache or cache[n][0] is not self.out:
            cache[n] = (self.out, next(k for k, r in enumerate(self.out.doc['results'])
                                       if r['original_path'] == original(n)))
        return cache[n][1]

    def assert_row(self, n: int, row: str, skip: str | None, *, approve_refused: bool = True):
        report = self.out.read()
        self.assertIsNone(report.refusal, report.refusal)
        item = report.items[self.index(n)]
        self.assertIn(row, item.findings, item.findings)
        self.assertEqual(item.skip_message, skip)
        if approve_refused:
            self.assertIn(row, item.approve_refusals)
        return item


class V33Test(Helpers):
    def test_ambiguous(self):
        self.edit(2, path=original(3))
        self.assert_row(2, 'V33', SKIP_AMBIGUOUS)

    def test_not_an_original_image(self):
        for change in ({'path': '/lib/x.txt', 'original_path': '/lib/x.txt'},
                       {'path': '/lib/.photo-curator/a.JPG', 'original_path': '/lib/.photo-curator/a.JPG'}):
            with self.subTest(change=change):
                self.setUp()
                self.edit(2, **change)
                self.assert_row(2, 'V33', SKIP_NOT_ORIGINAL)

    def test_equals_preview_path(self):
        r = self.out.result(2)
        self.edit(2, path=r['preview_path'], original_path=r['preview_path'])
        item = self.assert_row(2, 'V33', SKIP_NOT_ORIGINAL)

    def test_inside_generated_output(self):
        marked = self.work.parent / (self.work.name + '-marked')
        marked.mkdir()
        self.addCleanup(lambda: (os.unlink(marked / OUTPUT_MARKER), os.rmdir(marked)))
        (marked / OUTPUT_MARKER).write_text('m', encoding='utf-8')
        target = str(marked / 'A.JPG')
        self.edit(2, path=target, original_path=target)
        self.assert_row(2, 'V33', SKIP_GENERATED)

    def test_v1_v2_never_require_v2_v3_fields(self):
        (self.work / RESULTS).write_text(json.dumps({'schema_version': 1, 'results': [
            {'path': '/lib/A.JPG', 'rating': 3}, {'path': '/lib/b.txt', 'rating': 3}]}), encoding='utf-8')
        report = read_results(self.work / RESULTS)
        self.assertEqual(report.items[0].findings, {})
        self.assertEqual(report.items[1].skip_message, SKIP_NOT_ORIGINAL)


class V34Test(Helpers):
    def test_hand_edited_rating_decision_override(self):
        cases = {'rating': {'rating': 5}, 'decision': {'decision': 'strong'},
                 'override outside cells': {'moment_override': True}, 'rating type': {'rating': '3'}}
        for label, change in cases.items():
            with self.subTest(label=label):
                self.setUp()
                self.edit(2, **change)
                item = self.assert_row(2, 'V34', SKIP_INCONSISTENT)
                self.assertIn('inconsistent stored result', item.gallery_flags)

    def test_unevaluable_rating_not_zero(self):
        self.edit(9, rating=1, decision='reject')
        self.assert_row(9, 'V34', SKIP_INCONSISTENT)


class V35Test(Helpers):
    def test_routing_fields(self):
        cases = {'needs_review type': {'needs_review': 'yes'}, 'unknown code': {'review_reasons': ['nope']},
                 'duplicate code': {'review_reasons': ['score_gap', 'score_gap'], 'needs_review': True},
                 'needs_review mismatch': {'needs_review': True},
                 'low_confidence missing': {'confidence': 0.3},
                 'confidence null without field_invalid': {'confidence': None}}
        for label, change in cases.items():
            with self.subTest(label=label):
                self.setUp()
                self.edit(2, **change)
                self.assert_row(2, 'V35', SKIP_INCONSISTENT)


class V36Test(Helpers):
    def test_missing_field_and_wrong_types(self):
        cases = {'missing': None, 'path relative': {'path': 'DSC.ARW', 'original_path': 'DSC.ARW'},
                 'session': {'session_id': ''}, 'technical': {'technical': None},
                 'flags': {'flags': ['none']}, 'burst group mismatch': {'burst_sequence': None}}
        for label, change in cases.items():
            with self.subTest(label=label):
                self.setUp()
                if change is None:
                    del self.out.result(2)['preview_path']
                    self.out.rewrite()
                else:
                    self.edit(2, **change)
                self.assert_row(2, 'V36', SKIP_INCONSISTENT)

    def test_w_and_k_shapes_without_their_reason(self):
        cases = {'flags W without field_invalid': {'flags': None},
                 'technical.evidence K [] without evidence_missing':
                     {'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': []}},
                 'editorial W without evidence_missing': {'editorial': None},
                 'generic evidence without evidence_generic':
                     {'technical': {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': ['great photo']}},
                 'proposed_rating W without rating_rule_mismatch': {'proposed_rating': None}}
        for label, change in cases.items():
            with self.subTest(label=label):
                self.setUp()
                self.edit(8, **change)
                self.assert_row(8, 'V36', SKIP_INCONSISTENT)

    def test_w_shape_with_its_reason_is_valid(self):
        r = self.out.result(8)
        self.edit(8, flags=None, review_reasons=sorted(set(r['review_reasons']) | {'field_invalid'}), needs_review=True)
        report = self.out.read()
        self.assertNotIn('V36', report.items[self.index(8)].findings)

    def test_unevaluable_shape(self):
        self.edit(9, fatal_flaws=['preview unreadable'])
        self.assert_row(9, 'V36', SKIP_INCONSISTENT)


GOOD_EV = ['The speaker on the left raises a hand toward the audience']
TECH = {'motion': 'frozen', 'motion_evidence': [], 'recoverable_in_raw': [], 'evidence': GOOD_EV}


def tech(**over):
    return dict(TECH, **over)


# (label, frame, changes, reasons the stored shape requires). Frame 8: evaluable, outside a burst;
# 3: ranked group b1 (non-winner); 4: not_comparable group b2. Every row is tested both ways.
W_K_MATRIX = [
    ('unevaluable_reason K', 8, {'unevaluable_reason': 'leftover text'}, ['field_invalid']),
    ('capture_type W', 8, {'capture_type': None}, ['field_invalid']),
    ('motion_evidence W (required)', 8, {'technical': tech(motion='intentional_pan', motion_evidence=None)}, ['evidence_missing']),
    ('motion_evidence W (not required)', 8, {'technical': tech(motion_evidence=None)}, ['field_invalid']),
    ('motion_evidence K [] (required)', 8, {'technical': tech(motion='intentional_pan', motion_evidence=[])}, ['evidence_missing']),
    ('motion_evidence K generic (required)', 8, {'technical': tech(motion='intentional_pan', motion_evidence=['great photo'])}, ['evidence_generic']),
    ('recoverable_in_raw W', 8, {'technical': tech(recoverable_in_raw=None)}, ['field_invalid']),
    ('technical.evidence W', 8, {'technical': tech(evidence=None)}, ['evidence_missing']),
    ('technical.evidence K []', 8, {'technical': tech(evidence=[])}, ['evidence_missing']),
    ('technical.evidence K generic', 8, {'technical': tech(evidence=['great photo'])}, ['evidence_generic']),
    ('editorial W', 8, {'editorial': None}, ['evidence_missing']),
    ('editorial.evidence W', 8, {'editorial': {'evidence': None}}, ['evidence_missing']),
    ('editorial.evidence K []', 8, {'editorial': {'evidence': []}}, ['evidence_missing']),
    ('editorial.evidence K generic', 8, {'editorial': {'evidence': ['nice composition']}}, ['evidence_generic']),
    ('decisive W (required)', 8, {'proposed_moment_override': True, 'decisive_evidence': None}, ['evidence_missing']),
    ('decisive W (not required)', 8, {'decisive_evidence': None}, ['field_invalid']),
    ('decisive K [] (required)', 8, {'proposed_moment_override': True, 'decisive_evidence': []}, ['evidence_missing']),
    ('decisive K generic (required)', 8, {'proposed_moment_override': True, 'decisive_evidence': ['good exposure']}, ['evidence_generic']),
    ('proposed_rating W', 8, {'proposed_rating': None}, ['rating_rule_mismatch']),
    ('proposed_moment_override W', 8, {'proposed_moment_override': None}, ['field_invalid']),
    ('flags W', 8, {'flags': None}, ['field_invalid']),
    ('fatal_flaws W (evaluable)', 8, {'fatal_flaws': None}, ['field_invalid']),
    ('uncertain_checks W (evaluable)', 8, {'uncertain_checks': None}, ['field_invalid', 'motion_conflict']),
    ('distinct_moment W', 8, {'distinct_moment': None}, ['field_invalid']),
    ('distinct_moment_evidence W with claim', 8, {'distinct_moment': True, 'distinct_moment_evidence': None},
     ['evidence_missing', 'distinct_moment_invalid']),
    ('distinct_moment_evidence K [] with claim', 8, {'distinct_moment': True, 'distinct_moment_evidence': []},
     ['evidence_missing', 'distinct_moment_invalid']),
    ('distinct_moment_evidence K generic with claim', 8, {'distinct_moment': True, 'distinct_moment_evidence': ['great photo']},
     ['evidence_generic', 'distinct_moment_invalid']),
    ('distinct_moment_evidence K without claim', 8, {'distinct_moment_evidence': GOOD_EV}, ['field_invalid']),
    ('distinct_moment_evidence null without claim (D3-14)', 8, {'distinct_moment_evidence': None}, ['field_invalid']),
    ('not_comparable_evidence K outside a burst', 8, {'not_comparable_evidence': GOOD_EV}, ['field_invalid']),
    ('not_comparable_evidence null outside a burst (D3-14)', 8, {'not_comparable_evidence': None}, ['field_invalid']),
    ('burst_comparison W in a burst', 3, {'burst_comparison': None}, ['burst_comparison_invalid']),
    ('burst_rank W (ranked group)', 3, {'burst_rank': None}, ['burst_rank_invalid']),
    ('rank_reason W (ranked group)', 3, {'rank_reason': None}, ['evidence_missing']),
    ('rank_reason K generic (ranked group)', 3, {'rank_reason': 'great photo'}, ['evidence_generic']),
    ('not_comparable_evidence K (ranked group)', 3, {'not_comparable_evidence': GOOD_EV}, ['field_invalid']),
    ('not_comparable_evidence null (ranked group, D4-4)', 3, {'not_comparable_evidence': None}, ['field_invalid']),
    ('ranking field K (not_comparable group, failed C2)', 4, {'burst_rank': 1}, ['burst_comparison_invalid']),
    ('not_comparable_evidence W (not_comparable group)', 4, {'not_comparable_evidence': None},
     ['evidence_missing', 'burst_comparison_invalid']),
    ('not_comparable_evidence K [] (not_comparable group)', 4, {'not_comparable_evidence': []},
     ['evidence_missing', 'burst_comparison_invalid']),
    ('not_comparable_evidence K generic (not_comparable group)', 4, {'not_comparable_evidence': ['great photo']},
     ['evidence_generic', 'burst_comparison_invalid']),
]

# Unevaluable column (6.4 / 14.2): frame 9. Deviations that are never valid, and the
# "as valid, or W" fields whose W shape is valid together with its reason.
UNEVALUABLE_INVALID = [
    ('technical_score', {'technical_score': 3}), ('editorial_score', {'editorial_score': 3}),
    ('technical', {'technical': tech(motion=None)}),                       # evidence not []
    ('editorial', {'editorial': {'evidence': GOOD_EV}}),
    ('decisive_evidence', {'decisive_evidence': GOOD_EV}), ('distinct_moment_evidence', {'distinct_moment_evidence': GOOD_EV}),
    ('not_comparable_evidence', {'not_comparable_evidence': GOOD_EV}), ('proposed_moment_override', {'proposed_moment_override': True}),
    ('distinct_moment', {'distinct_moment': True}), ('burst_comparison', {'burst_comparison': 'ranked'}),
    ('burst_rank', {'burst_rank': 1}), ('best_in_burst', {'best_in_burst': False}), ('co_best', {'co_best': False}),
    ('near_duplicate_of', {'near_duplicate_of': '/library/X.ARW'}), ('rank_reason', {'rank_reason': 'x' * 25}),
    ('unevaluable_reason', {'unevaluable_reason': ''}), ('fatal_flaws', {'fatal_flaws': ['preview unreadable']}),
    ('capture_type', {'capture_type': 'nope'}), ('burst_ranking_status', {'burst_ranking_status': 'validated'}),
    ('documentation_suggestion', {'documentation_suggestion': 'retain'}), ('editorial_suggestion', {'editorial_suggestion': 'highlight'}),
]
UNEVALUABLE_W = [
    ('proposed_rating other value', {'proposed_rating': 2}, ['rating_rule_mismatch']),
    ('flags W', {'flags': None}, ['field_invalid']),
    ('uncertain_checks W (no motion_conflict when unevaluable)', {'uncertain_checks': None}, ['field_invalid']),
    ('capture_type null', {'capture_type': None}, []),
]


class V36MatrixTest(Helpers):
    def apply(self, n: int, changes: dict, add: list[str]) -> None:
        k = self.index(n)
        r = self.out.doc['results'][k]
        r.update(changes)
        if add:
            r['review_reasons'] = sorted(set(r['review_reasons']) | set(add))
            r['needs_review'] = True
        self.out.rewrite()

    def v36(self, n: int) -> list[str]:
        return self.out.read().items[self.index(n)].findings.get('V36', [])

    def test_every_w_k_shape_both_ways(self):
        for label, n, changes, required in W_K_MATRIX:
            with self.subTest(label=label, direction='without reason'):
                self.setUp()
                self.apply(n, changes, [])
                self.assertTrue(self.v36(n), f'{label}: shape accepted without {required}')
            with self.subTest(label=label, direction='with reason'):
                self.setUp()
                self.apply(n, changes, required)
                self.assertEqual(self.v36(n), [], f'{label}: shape refused although {required} present')

    def test_each_reason_is_required(self):
        """A shape needing two reasons fails with only one of them."""
        for label, n, changes, required in W_K_MATRIX:
            if len(required) < 2:
                continue
            for keep in required:
                with self.subTest(label=label, only=keep):
                    self.setUp()
                    self.apply(n, changes, [keep])
                    self.assertTrue(self.v36(n))

    def test_unevaluable_column(self):
        for label, changes in UNEVALUABLE_INVALID:
            with self.subTest(field=label):
                self.setUp()
                self.apply(9, changes, [])
                self.assertTrue(self.v36(9), f'unevaluable {label} accepted')
        for label, changes, required in UNEVALUABLE_W:
            with self.subTest(field=label, direction='with reason'):
                self.setUp()
                self.apply(9, changes, required)
                self.assertEqual(self.v36(9), [])
            if required:
                with self.subTest(field=label, direction='without reason'):
                    self.setUp()
                    self.apply(9, changes, [])
                    self.assertTrue(self.v36(9))

    def test_undetermined_mode_applies_no_mode_evidence_rule(self):
        """C1 (mixed modes, D4-1a): no not_comparable evidence rule is applied."""
        self.apply(4, {'burst_comparison': 'ranked', 'not_comparable_evidence': []}, [])
        self.assertFalse(any('not_comparable_evidence' in p for p in self.v36(5)))


class V37Test(Helpers):
    def test_suggestions_never_trusted(self):
        self.edit(8, documentation_suggestion='retain_low_rating', editorial_suggestion='hero_candidate', reasons=['x'])
        report = self.out.read()
        item = report.items[self.index(8)]
        self.assertTrue(any(w.startswith('V37: stored documentation_suggestion') for w in item.warnings))
        self.assertTrue(any(w.startswith('V37: stored editorial_suggestion') for w in item.warnings))
        self.assertIsNone(item.skip_message)                           # apply never uses suggestions
        self.assertEqual(item.recomputed_suggestions['documentation_suggestion'], 'retain')


class V38Test(Helpers):
    def test_each_12_2_rule_malformed(self):
        base = None
        cases = {'approved string': {'approved': 'true'}, 'rating bool': {'approved_rating': True},
                 'approved_at without Z': {'approved_at': '2026-11-15T07:12:03'},
                 'relative path': {'approved_preview_path': 'previews/a.jpg'},
                 'uppercase hash': {'approved_preview_sha256': 'A' * 64},
                 'hash differs from original_sha256': {'approved_original_sha256': 'c' * 64},
                 'false with non-null': {'approved': False}}
        for label, change in cases.items():
            with self.subTest(label=label):
                self.setUp()
                self.out.approve(1)
                self.edit(1, **change)
                report = self.out.read()
                item = report.items[self.index(1)]
                self.assertEqual((item.approval_state, item.approval_message), ('malformed', SKIP_MALFORMED))
                self.assertIn('V38', item.findings)
                self.assertIn('V38', item.approve_refusals)

    def test_valid_approval_and_binding_checks(self):
        self.out.approve(1)
        item = self.out.read().items[self.index(1)]
        self.assertEqual(item.approval_state, 'approved')
        r = self.out.result(1)
        for change, message in (({'approved_rating': 4 if r['rating'] != 4 else 2}, BINDING_MESSAGES[1]),
                                ({'approved_preview_path': r['preview_path'] + 'x.jpg'}, BINDING_MESSAGES[4]),
                                ({'approved_preview_sha256': 'e' * 64}, BINDING_MESSAGES[6])):
            with self.subTest(message=message):
                self.setUp()
                self.out.approve(1)
                self.edit(1, **change)
                item = self.out.read().items[self.index(1)]
                self.assertEqual((item.approval_state, item.approval_message), ('invalidated', message))

    def test_reader_never_hashes_originals(self):
        self.out.approve(1)
        with mock.patch.object(hashing_v3, 'sha256_file', side_effect=AssertionError('original hashed')):
            self.out.read()


class V40Test(Helpers):
    def test_invalid_capture_time_display_only(self):
        for value in ('2026-13-01T10:00:00', '2026-11-15 10:00:00', '2026-11-15T10:00:00.1234', '2026-11-15T10:00:00+15:00', 5):
            with self.subTest(value=value):
                self.setUp()
                self.edit(2, capture_time=value)
                report = self.out.read()
                item = report.items[self.index(2)]
                self.assertIn('V40', item.findings)
                self.assertIsNone(item.skip_message)
                self.assertTrue(any('untimed' in w for w in item.warnings))

    def test_valid_forms(self):
        for value in ('2026-11-15T10:00:00', '2026-11-15T10:00:00.25', '2026-11-15T10:00:00.250+07:00', None):
            with self.subTest(value=value):
                self.setUp()
                self.edit(2, capture_time=value)
                self.assertNotIn('V40', self.out.read().items[self.index(2)].findings)


class V42Test(Helpers):
    """Item 20 consequences through the reader: each PV failing alone on an approved item gives
    the apply skip message (binding check 5) and an approve refusal."""

    def break_pv(self, check: str):
        r = self.out.result(1)
        preview = Path(r['preview_path'])
        if check == 'PV1':
            moved = self.work / preview.name
            os.replace(preview, moved)
            self.edit(1, preview_path=str(moved), approved_preview_path=str(moved))
        elif check == 'PV2':
            os.unlink(self.work / 'previews' / OUTPUT_MARKER)
        elif check == 'PV3':
            os.replace(preview, preview.with_name('real.bin'))
            os.symlink(preview.with_name('real.bin'), preview)
        elif check == 'PV5':
            renamed = preview.with_name('DSC00001__0000000000.jpg')
            os.replace(preview, renamed)
            self.edit(1, preview_path=str(renamed), approved_preview_path=str(renamed))
        elif check == 'PV6':
            self.edit(1, original_path=r['preview_path'], path=r['preview_path'], approved_original_path=r['preview_path'])

    def test_each_pv_failure(self):
        for check in ('PV1', 'PV2', 'PV3', 'PV5', 'PV6'):
            with self.subTest(check=check):
                self.setUp()
                self.out.approve(1)
                self.break_pv(check)
                report = self.out.read()
                self.assertIsNone(report.refusal)
                item = report.items[self.index(1)]
                if check == 'PV6':                                     # V33 already skips it
                    self.assertEqual(item.skip_message, SKIP_NOT_ORIGINAL)
                    continue
                self.assertEqual(item.approval_message, APPLY_SKIP_MESSAGE)
                self.assertIn(check, item.findings['V42'][0])
                self.assertIn('V42', item.approve_refusals)


class V45Test(Helpers):
    def test_original_sha256(self):
        for value in (None, 'A' * 64, 'a' * 63, 12):
            with self.subTest(value=value):
                self.setUp()
                self.edit(2, original_sha256=value)
                self.assert_row(2, 'V45', SKIP_INCONSISTENT)


if __name__ == '__main__':
    unittest.main()
