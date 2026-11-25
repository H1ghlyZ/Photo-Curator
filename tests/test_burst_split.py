"""Max-burst-size splitting tests. Offline: synthetic paths/timestamps only."""
from __future__ import annotations
import random, unittest
from pathlib import Path

from photo_curator.core import detect_bursts, split_bursts, plan_batches, session_for, _partition_sizes, validate_batch_results

ROOT = Path('/nonexistent-photo-curator-test-root')
S1, S2 = 'DCIM/100MSDCF', 'DCIM 2'


def img(session, name): return (ROOT / session / name).resolve()


def seq(session, prefix, n, start=100.0, step=0.1, first=0):
    paths = [img(session, f'{prefix}{first + i:05d}.ARW') for i in range(n)]
    return paths, {str(p): start + step * i for i, p in enumerate(paths)}


def groups_of(group_map):
    out = {}
    for p, g in group_map.items(): out.setdefault(g, []).append(p)
    return out


class Invariants:
    def check(self, paths, times, cap, batch_size=6, window=1.0):
        times_before = dict(times)
        raw = detect_bursts(paths, window, ROOT, times)
        gmap, smap = split_bursts(raw, times, cap)
        self.assertEqual(times, times_before)                     # timestamps not discarded/mutated
        self.assertEqual(set(gmap), set(raw)); self.assertEqual(smap, raw)
        for g, members in groups_of(gmap).items():
            self.assertLessEqual(len(members), cap, g)              # cap respected
            self.assertEqual(len({session_for(Path(p), ROOT) for p in members}), 1, g)
            self.assertEqual(len({smap[p] for p in members}), 1, g)  # a part belongs to one sequence
        # Parts of a sequence are contiguous, chronological slices.
        for s, members in groups_of(smap).items():
            ordered = sorted(members, key=lambda p: (times[p], p))
            labels = [gmap[p] for p in ordered]
            compressed = [l for i, l in enumerate(labels) if i == 0 or l != labels[i - 1]]
            self.assertEqual(len(compressed), len(set(compressed)), s)
        batches = plan_batches(paths, batch_size, ROOT, times, gmap)
        flat = [str(p) for b in batches for p in b]
        self.assertEqual(sorted(flat), sorted(map(str, paths)))     # exactly once
        self.assertEqual(len(flat), len(set(flat)))
        where = {}
        for i, b in enumerate(batches):
            self.assertEqual(len({session_for(p, ROOT) for p in b}), 1)
            for p in b:
                if str(p) in gmap: where.setdefault(gmap[str(p)], set()).add(i)
        for g, idx in where.items(): self.assertEqual(len(idx), 1, g)  # group never split
        return raw, gmap, smap, batches


class SplitTests(Invariants, unittest.TestCase):
    def test_smaller_than_cap_unchanged(self):
        paths, times = seq(S1, 'DSC', 5)
        raw, gmap, smap, _ = self.check(paths, times, 12)
        self.assertEqual(gmap, raw); self.assertEqual(len(set(gmap.values())), 1)

    def test_equal_to_cap_unchanged(self):
        paths, times = seq(S1, 'DSC', 12)
        raw, gmap, smap, _ = self.check(paths, times, 12)
        self.assertEqual(gmap, raw)

    def test_larger_than_cap_split(self):
        paths, times = seq(S1, 'DSC', 13)
        raw, gmap, smap, _ = self.check(paths, times, 12)
        parts = groups_of(gmap)
        self.assertEqual(sorted(map(len, parts.values())), [6, 7])  # fewest parts, balanced
        seq_id = next(iter(raw.values()))
        self.assertEqual(set(parts), {f'{seq_id}-p1', f'{seq_id}-p2'})
        self.assertEqual(set(smap.values()), {seq_id})

    def test_fifty_frame_continuous_burst(self):
        paths, times = seq(S1, 'DSC', 50, step=0.1)
        raw, gmap, smap, batches = self.check(paths, times, 12)
        self.assertEqual(len(set(raw.values())), 1)
        parts = groups_of(gmap)
        self.assertEqual(sorted(map(len, parts.values())), [10] * 5)
        # p1..p5 follow capture order.
        sid = next(iter(raw.values()))
        self.assertEqual([gmap[str(p)] for p in paths[::10]], [f'{sid}-p{k}' for k in range(1, 6)])
        self.assertEqual(len(batches), 5)

    def test_irregular_gap_is_used_as_cut(self):
        paths, times = seq(S1, 'DSC', 14, step=0.1)
        for p in paths[4:]: times[str(p)] += 0.5                      # 0.6 s gap after frame 4
        _, gmap, _, _ = self.check(paths, times, 12)
        self.assertEqual([len(g) for g in sorted(groups_of(gmap).values(), key=lambda m: times[m[0]])], [4, 10])

    def test_two_natural_gaps_in_thirty_frames(self):
        paths, times = seq(S1, 'DSC', 30, step=0.1)
        for p in paths[10:]: times[str(p)] += 0.4
        for p in paths[20:]: times[str(p)] += 0.4
        _, gmap, _, _ = self.check(paths, times, 12)
        ordered = sorted(groups_of(gmap).values(), key=lambda m: times[m[0]])
        self.assertEqual([m for m in ordered], [[str(p) for p in paths[i:i + 10]] for i in (0, 10, 20)])

    def test_largest_gap_unusable_falls_back_to_feasible_balanced_cut(self):
        paths, times = seq(S1, 'DSC', 20, step=0.1)
        for p in paths[2:]: times[str(p)] += 0.5                      # cut here would leave 18 > cap
        _, gmap, _, _ = self.check(paths, times, 12)
        self.assertEqual(sorted(map(len, groups_of(gmap).values())), [10, 10])

    def test_equal_gaps_partition_sizes(self):
        self.assertEqual(sorted(_partition_sizes([100] * 24, 12)), [8, 8, 9])  # 25 frames
        for n in range(2, 80):
            sizes = _partition_sizes([100] * (n - 1), 12)
            self.assertEqual(sum(sizes), n)
            self.assertEqual(len(sizes), -(-n // 12))
            self.assertLessEqual(max(sizes) - min(sizes), 1, (n, sizes))  # equal gaps -> balanced

    def test_overlapping_sessions(self):
        a, ta = seq(S1, 'DSC', 20, start=500.0, step=0.1)
        b, tb = seq(S2, 'GAT', 20, start=500.05, step=0.1)
        raw, gmap, smap, batches = self.check(a + b, {**ta, **tb}, 12)
        self.assertEqual(len(set(raw.values())), 2)
        self.assertEqual(len(set(gmap.values())), 4)

    def test_missing_timestamps(self):
        paths, times = seq(S1, 'DSC', 15)
        untimed = [img(S1, 'DSC09998.ARW'), img(S1, 'DSC09999.ARW'), img(S2, 'GAT00001.ARW')]
        raw, gmap, _, batches = self.check(paths + untimed, times, 12)
        for p in untimed: self.assertNotIn(str(p), gmap)
        self.assertEqual(len(gmap), 15)

    def test_cap_disabled(self):
        paths, times = seq(S1, 'DSC', 30)
        raw = detect_bursts(paths, 1.0, ROOT, times)
        gmap, smap = split_bursts(raw, times, 0)
        self.assertEqual(gmap, raw)
        with self.assertRaises(ValueError): split_bursts(raw, times, 1)

    def test_randomized_sessions_and_caps(self):
        rng = random.Random(1234)
        for trial in range(40):
            paths, times = [], {}
            for session, prefix in ((S1, 'DSC'), (S2, 'GAT')):
                t = 1000.0
                for i in range(rng.randint(0, 120)):
                    p = img(session, f'{prefix}{i:05d}.ARW'); paths.append(p)
                    t += rng.choice([0.05, 0.1, 0.1, 0.2, 0.4, 0.9, 1.5, 5.0])
                    if rng.random() > 0.05: times[str(p)] = round(t, 3)   # ~5% untimed
            self.check(paths, times, rng.choice([2, 3, 6, 12]), rng.choice([1, 4, 6, 12]), rng.choice([0.3, 1.0, 2.0]))


class ValidationSequenceTests(unittest.TestCase):
    def test_results_carry_group_and_sequence(self):
        paths, times = seq(S1, 'DSC', 14)
        gmap, smap = split_bursts(detect_bursts(paths, 1.0, ROOT, times), times, 12)
        pm = {str(p): ROOT / '.photo-curator' / 'previews' / f'{p.stem}.jpg' for p in paths}
        sm = {str(p): S1 for p in paths}
        photos = [{'path': str(p), 'rating': 3, 'burst_group': 'made-up'} for p in paths]
        results, errors = validate_batch_results(paths, photos, pm, gmap, sm, smap)
        self.assertEqual(errors, [])
        for r in results:
            self.assertEqual(r['burst_group'], gmap[r['original_path']])
            self.assertEqual(r['burst_sequence'], smap[r['original_path']])
            self.assertNotEqual(r['burst_group'], r['burst_sequence'])
            self.assertIs(r['best_in_burst'], False)


if __name__ == '__main__':
    unittest.main()
