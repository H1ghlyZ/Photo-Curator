"""S4: P1-P4 on planning data (spec 8.0; D4-8) and their stated limits.

P1-P4 check only the consistency of the planning data supplied. They do not verify
``detect_bursts``; a key missing from BOTH v2 maps looks like a non-burst frame and is not
detected (documented by ``LimitTest``). Pure functions; v2 planning is called with synthetic
capture times only (no ExifTool, no files).
"""
from __future__ import annotations
import unittest
from pathlib import Path

from photo_curator.bursts_v3 import (BurstPreconditionError, PlannedFrame, check_burst_preconditions_v3,
                                     validate_max_burst_size_v3)
from photo_curator.core import detect_bursts, plan_batches, session_for, split_bursts


def p(n, session='S'):
    return f'/lib/{session}/DSC{n:05d}.ARW'


def frame(n, group=None, seq='same', session='S'):
    return PlannedFrame(p(n, session), session, group, group if seq == 'same' else seq)


class Base(unittest.TestCase):
    def ok(self, planned, frames, batches, size=12):
        check_burst_preconditions_v3(planned, frames, batches, size)

    def violations(self, planned, frames, batches, size=12):
        with self.assertRaises(BurstPreconditionError) as cm:
            check_burst_preconditions_v3(planned, frames, batches, size)
        self.assertIsInstance(cm.exception, ValueError)
        return cm.exception.violations

    def assert_one(self, needle, planned, frames, batches, size=12):
        found = self.violations(planned, frames, batches, size)
        self.assertTrue(any(needle in v for v in found), found)
        return found


def simple():
    frames = [frame(1, 'g1'), frame(2, 'g1'), frame(3), frame(4, 'g2'), frame(5, 'g2')]
    return [f.original_path for f in frames], frames, [[p(1), p(2), p(3)], [p(4), p(5)]]


class ValidInputTest(Base):
    def test_valid_plan_passes(self):
        self.ok(*simple())

    def test_one_frame_group_is_allowed(self):                         # not forbidden by P1-P4
        planned, frames, batches = simple()
        self.ok(planned + [p(6)], frames + [frame(6, 'g3')], batches + [[p(6)]])


class MaxBurstSizeTest(Base):                                          # P3
    def test_range(self):
        for value in (2, 12):
            self.assertEqual(validate_max_burst_size_v3(value), value)
        for value in (0, 1, 13, -1, True, '3', 2.0, None):
            with self.subTest(value=value):
                with self.assertRaises(BurstPreconditionError):
                    validate_max_burst_size_v3(value)
                self.assert_one('P3: max_burst_size', *simple(), size=value)

    def test_group_over_limit(self):
        frames = [frame(n, 'big') for n in range(1, 4)]
        planned = [f.original_path for f in frames]
        self.assert_one('P3: group big has 3 frames', planned, frames, [planned], size=2)
        self.ok(planned, frames, [planned], size=3)


class StructureTest(Base):
    def test_planned_originals(self):
        planned, frames, batches = simple()
        self.assert_one('duplicate planned original', planned + [p(1)], frames, batches)
        self.assert_one('not an absolute path', planned + ['rel/x.ARW'], frames, batches)
        self.assert_one('planned_originals must be a list', set(planned), frames, batches)

    def test_frames_match_planned_originals(self):
        planned, frames, batches = simple()
        self.assert_one('has no planning row', planned, frames[1:], batches)            # missing row
        self.assert_one('not planned', planned[:-1], frames, [[p(1), p(2), p(3)], [p(4)]])
        self.assert_one('duplicate planning row', planned, frames + [frame(1, 'g1')], batches)
        self.assert_one('not a PlannedFrame', planned, frames + ['x'], batches)

    def test_field_types(self):
        planned, frames, batches = simple()
        bad = [PlannedFrame(p(1), '', 'g1', 'g1')] + frames[1:]
        self.assert_one('invalid session_id', planned, bad, batches)
        bad = [PlannedFrame(p(1), 'S', '', '')] + frames[1:]
        self.assert_one('invalid burst_group', planned, bad, batches)

    def test_batches(self):
        planned, frames, _ = simple()
        self.assert_one('is empty or not a list', planned, frames, [[p(1), p(2), p(3)], [p(4), p(5)], []])
        self.assert_one('not planned', planned, frames, [[p(1), p(2), p(3), '/lib/S/X.ARW'], [p(4), p(5)]])
        self.assert_one('scheduled more than once', planned, frames, [[p(1), p(2), p(3)], [p(4), p(5), p(3)]])
        self.assert_one('not scheduled in any batch', planned, frames, [[p(1), p(2), p(3)], [p(4)]])

    def test_all_violations_reported(self):
        planned, frames, batches = simple()
        found = self.violations(planned + [p(1)], frames[1:], [[p(2), p(3)], [p(4)]], size=1)
        self.assertGreaterEqual(len(found), 4)
        self.assertEqual(found, self.violations(planned + [p(1)], frames[1:], [[p(2), p(3)], [p(4)]], size=1))


class P1Test(Base):
    def test_group_across_sessions(self):
        frames = [PlannedFrame(p(1, 'S'), 'S', 'g1', 'g1'), PlannedFrame(p(2, 'T'), 'T', 'g1', 'g1')]
        planned = [f.original_path for f in frames]
        self.assert_one('P1: group g1 spans several sessions', planned, frames, [planned])

    def test_sequence_across_sessions(self):
        frames = [PlannedFrame(p(1, 'S'), 'S', 'g1', 'seq'), PlannedFrame(p(2, 'T'), 'T', 'g2', 'seq')]
        planned = [f.original_path for f in frames]
        self.assert_one('P1: sequence seq spans several sessions', planned, frames, [[planned[0]], [planned[1]]])


class P2Test(Base):
    def test_group_split_across_batches(self):
        planned, frames, _ = simple()
        self.assert_one('P2: group g1 is split across batches', planned, frames,
                        [[p(1), p(3)], [p(2), p(4), p(5)]])


class P4Test(Base):
    def test_group_and_sequence_must_agree(self):
        planned, frames, batches = simple()
        bad = [frame(1, 'g1', seq=None)] + frames[1:]
        self.assert_one('P4: burst_group and burst_sequence disagree', planned, bad, batches)
        bad = [frame(1, None, seq='seq')] + frames[1:]
        self.assert_one('P4: burst_group and burst_sequence disagree', planned, bad, batches)

    def test_group_spans_sequences(self):
        planned, frames, batches = simple()
        bad = [frame(1, 'g1', seq='s1'), frame(2, 'g1', seq='s2')] + frames[2:]
        self.assert_one('P4: group g1 spans several sequences', planned, bad, batches)

    def test_split_sequence_parts_are_valid(self):
        frames = [frame(1, 'seq-p1', seq='seq'), frame(2, 'seq-p1', seq='seq'), frame(3, 'seq-p2', seq='seq')]
        planned = [f.original_path for f in frames]
        self.ok(planned, frames, [[p(1), p(2)], [p(3)]], size=2)


class V2PlanningTest(Base):
    """Planning data produced by the real v2 functions, with synthetic capture times."""

    def v2_plan(self):
        root = Path('/lib')
        paths = [Path(p(n)) for n in range(1, 8)] + [Path(p(n, 'T')) for n in (1, 2)]
        times = {p(1): 0.0, p(2): 0.3, p(3): 0.6, p(4): 10.0, p(5): 20.0, p(6): 20.2, p(7): 40.0,
                 p(1, 'T'): 0.0, p(2, 'T'): 0.2}
        raw = detect_bursts(paths, 1.0, root, times)
        group_map, sequence_map = split_bursts(raw, times, 2)
        batches = [[str(x) for x in b] for b in plan_batches(paths, 4, root, times, group_map)]
        planned = [str(x) for x in paths]
        frames = [PlannedFrame(str(x), session_for(x, root), group_map.get(str(x)), sequence_map.get(str(x)))
                  for x in paths]
        return planned, frames, batches, group_map, sequence_map

    def test_correct_v2_plan_passes(self):
        planned, frames, batches, group_map, sequence_map = self.v2_plan()
        self.assertTrue(any(group_map[k] != sequence_map[k] for k in group_map))     # a split happened
        self.ok(planned, frames, batches, size=2)

    def test_corrupted_v2_plans_are_refused(self):
        planned, frames, batches, group_map, _ = self.v2_plan()
        member = sorted(group_map)[0]
        only_group = [PlannedFrame(f.original_path, f.session_id, None, f.burst_sequence)
                      if f.original_path == member else f for f in frames]
        self.assert_one('P4: burst_group and burst_sequence disagree', planned, only_group, batches, 2)
        only_seq = [PlannedFrame(f.original_path, f.session_id, f.burst_group, None)
                    if f.original_path == member else f for f in frames]
        self.assert_one('P4: burst_group and burst_sequence disagree', planned, only_seq, batches, 2)
        self.assert_one('has no planning row', planned, [f for f in frames if f.original_path != member],
                        batches, 2)
        self.assert_one('scheduled more than once', planned, frames, batches + [[member]], 2)
        moved = [[x for x in b if x != member] for b in batches] + [[member]]
        self.assert_one(f'P2: group {group_map[member]} is split across batches', planned, frames,
                        [b for b in moved if b], 2)
        self.assert_one('P3: max_burst_size', planned, frames, batches, 0)     # v2 allows 0; v3 does not


class LimitTest(Base):
    def test_key_missing_from_both_maps_is_not_detected(self):
        """Documents the stated limit: a burst member dropped from BOTH the group and the
        sequence map is indistinguishable from a non-burst frame, so P1-P4 accept it."""
        planned, frames, batches = simple()
        dropped = [frame(1, None)] + frames[1:]          # was g1 / g1
        self.ok(planned, dropped, batches)


if __name__ == '__main__':
    unittest.main()
