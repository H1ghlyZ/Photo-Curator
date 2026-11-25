"""Batch scheduling and result-validation tests.

Fully offline: synthetic paths that do not exist, mocked ExifTool, no Claude calls,
no image reads, no metadata or sidecar writes.
"""
from __future__ import annotations
import json, subprocess, unittest
from pathlib import Path
from unittest import mock

from photo_curator import core
from photo_curator.core import detect_bursts, plan_batches, session_for, validate_batch_results

ROOT = Path('/nonexistent-photo-curator-test-root')
S1, S2 = 'DCIM/100MSDCF', 'DCIM 2'


def img(session: str, name: str) -> Path:
    return (ROOT / session / name).resolve()


def flat(batches): return [str(p) for b in batches for p in b]


class SchedulingTests(unittest.TestCase):
    def assert_invariants(self, batches, paths, burst_map, batch_size):
        # Every original exactly once.
        self.assertEqual(sorted(flat(batches)), sorted(map(str, paths)))
        self.assertEqual(len(flat(batches)), len(set(flat(batches))))
        for b in batches:
            # Never mix sessions.
            self.assertEqual(len({session_for(p, ROOT) for p in b}), 1, b)
            # Only a single whole burst may exceed batch_size.
            if len(b) > batch_size:
                self.assertEqual(len({burst_map.get(str(p)) for p in b}), 1, b)
        # Never split a burst group.
        where = {}
        for i, b in enumerate(batches):
            for p in b:
                gid = burst_map.get(str(p))
                if gid: where.setdefault(gid, set()).add(i)
        for gid, idx in where.items(): self.assertEqual(len(idx), 1, gid)

    def test_burst_crossing_normal_batch_boundary(self):
        # 4 singles 10 s apart, then a 4-frame burst at 0.5 s spacing.
        paths = [img(S1, f'DSC{i:05d}.ARW') for i in range(8)]
        times = {str(p): 10.0 * i for i, p in enumerate(paths[:4])}
        times.update({str(p): 100.0 + 0.5 * i for i, p in enumerate(paths[4:])})
        bursts = detect_bursts(paths, 2.0, ROOT, times)
        self.assertEqual(len(set(bursts.values())), 1)
        self.assertEqual(len(bursts), 4)
        # Naive slicing by 6 would split the burst (frames 4,5 | 6,7).
        batches = plan_batches(list(reversed(paths)), 6, ROOT, times, bursts)
        self.assert_invariants(batches, paths, bursts, 6)
        self.assertEqual(batches, [paths[:4], paths[4:]])  # chronological, burst intact

    def test_non_burst_photos_fill_remaining_capacity(self):
        paths = [img(S1, f'DSC{i:05d}.ARW') for i in range(6)]
        times = {str(paths[0]): 0.0, str(paths[1]): 0.5, str(paths[2]): 1.0,   # burst of 3
                 str(paths[3]): 20.0, str(paths[4]): 40.0, str(paths[5]): 60.0}
        bursts = detect_bursts(paths, 2.0, ROOT, times)
        batches = plan_batches(paths, 4, ROOT, times, bursts)
        self.assert_invariants(batches, paths, bursts, 4)
        self.assertEqual(batches, [paths[:4], paths[4:]])

    def test_two_sessions_with_overlapping_timestamps(self):
        a = [img(S1, f'DSC{i:05d}.ARW') for i in range(3)]
        b = [img(S2, f'GAT{i:05d}.ARW') for i in range(3)]
        # Interleaved identical/near-identical clocks across sessions.
        times = {str(p): 1000.0 + 0.3 * i for i, p in enumerate(a)}
        times.update({str(p): 1000.1 + 0.3 * i for i, p in enumerate(b)})
        bursts = detect_bursts(a + b, 2.0, ROOT, times)
        self.assertEqual(len(set(bursts.values())), 2)
        for gid in set(bursts.values()):
            self.assertEqual(len({session_for(Path(k), ROOT) for k, g in bursts.items() if g == gid}), 1)
        batches = plan_batches(b + a, 12, ROOT, times, bursts)  # capacity would allow mixing
        self.assert_invariants(batches, a + b, bursts, 12)
        self.assertEqual(len(batches), 2)

    def test_burst_larger_than_batch_size(self):
        before = img(S1, 'DSC00000.ARW')
        burst = [img(S1, f'DSC{i:05d}.ARW') for i in range(1, 10)]   # 9 frames
        after = img(S1, 'DSC00010.ARW')
        paths = [before, *burst, after]
        times = {str(before): 0.0, str(after): 500.0}
        times.update({str(p): 100.0 + 0.2 * i for i, p in enumerate(burst)})
        bursts = detect_bursts(paths, 2.0, ROOT, times)
        batches = plan_batches(paths, 4, ROOT, times, bursts)
        self.assert_invariants(batches, paths, bursts, 4)
        self.assertEqual(batches, [[before], burst, [after]])

    def test_missing_exif_timestamps(self):
        timed = [img(S1, f'DSC{i:05d}.ARW') for i in range(3)]
        untimed = [img(S1, 'DSC00100.ARW'), img(S1, 'DSC00050.ARW')]
        paths = untimed + timed
        fake_rows = [{'SourceFile': str(p), 'DateTimeOriginal': '2026:11:14 10:00:00',
                      'SubSecTimeOriginal': f'{i * 3}'} for i, p in enumerate(timed)]
        fake_rows += [{'SourceFile': str(untimed[0])},                              # no tag
                      {'SourceFile': str(untimed[1]), 'DateTimeOriginal': '0000:00:00 00:00:00'}]  # junk
        proc = subprocess.CompletedProcess([], 1, stdout=json.dumps(fake_rows), stderr='')
        with mock.patch.object(core.shutil, 'which', return_value='/usr/bin/exiftool'), \
             mock.patch.object(core, 'run', return_value=proc):
            times = core.session_capture_times(paths, ROOT)
        self.assertEqual(set(times), set(map(str, timed)))           # untimed left out, others kept
        self.assertAlmostEqual(times[str(timed[1])] - times[str(timed[0])], 0.3)
        bursts = detect_bursts(paths, 2.0, ROOT, times)
        self.assertNotIn(str(untimed[0]), bursts); self.assertNotIn(str(untimed[1]), bursts)
        self.assertEqual(len(bursts), 3)
        batches = plan_batches(paths, 2, ROOT, times, bursts)
        self.assert_invariants(batches, paths, bursts, 2)
        # Timed burst first (oversized, whole), then untimed images by path.
        self.assertEqual(batches, [timed, sorted(untimed, key=str)])

    def test_no_exiftool_still_schedules_everything(self):
        paths = [img(S1, f'DSC{i:05d}.ARW') for i in range(7)] + [img(S2, 'GAT00001.ARW')]
        with mock.patch.object(core.shutil, 'which', return_value=None):
            times = core.session_capture_times(paths, ROOT)
        self.assertEqual(times, {})
        batches = plan_batches(paths, 3, ROOT, times, detect_bursts(paths, 2.0, ROOT, times))
        self.assert_invariants(batches, paths, {}, 3)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.batch = [img(S1, f'DSC{i:05d}.ARW') for i in range(4)]
        self.pm = {str(p): ROOT / '.photo-curator' / 'previews' / f'{p.stem}__abc.jpg' for p in self.batch}
        self.sm = {str(p): S1 for p in self.batch}
        self.bm = {str(self.batch[0]): 'burst-A', str(self.batch[1]): 'burst-A'}

    def test_duplicate_and_invalid_results_are_reported_not_dropped(self):
        b = self.batch
        photos = [
            {'path': str(b[0]), 'rating': 3, 'best_in_burst': True, 'burst_group': 'claude-made-up'},
            {'path': str(b[0]), 'rating': 5},                         # duplicate
            {'path': str(b[1]), 'rating': 9},                         # invalid rating
            {'path': str(b[2]), 'rating': True},                      # bool is not a rating
            {'path': '/elsewhere/OTHER.ARW', 'rating': 4},            # outside batch
            'not-an-object',
            {'path': str(self.pm[str(b[3])]), 'rating': 2, 'technical_score': 'x'},  # preview path
        ]
        results, errors = validate_batch_results(b, photos, self.pm, self.bm, self.sm)
        by = {r['original_path']: r for r in results}
        self.assertEqual(set(by), {str(b[0]), str(b[3])})
        self.assertEqual(by[str(b[0])]['rating'], 3)                  # first kept
        self.assertEqual(by[str(b[0])]['burst_group'], 'burst-A')     # ours, not Claude's
        self.assertTrue(by[str(b[3])]['path_corrected_from_preview'])
        self.assertEqual(by[str(b[3])]['technical_score'], 0)
        for r in results:
            self.assertEqual(r['path'], r['original_path'])
            self.assertEqual(r['preview_path'], str(self.pm[r['original_path']]))
            self.assertEqual(r['session_id'], S1)
        msgs = [e['error'] for e in errors]
        self.assertEqual(sum('Duplicate' in m for m in msgs), 1)
        self.assertEqual(sum('Invalid rating' in m for m in msgs), 2)
        self.assertEqual(sum('outside this batch' in m for m in msgs), 1)
        self.assertEqual(sum('non-object' in m for m in msgs), 1)
        # Every batch image is accounted for in results or errors.
        accounted = set(by) | {e.get('original_path') for e in errors}
        self.assertTrue(set(map(str, b)) <= accounted)

    def test_missing_results_are_reported(self):
        results, errors = validate_batch_results(self.batch, [{'path': str(self.batch[2]), 'rating': 3}],
                                                 self.pm, self.bm, self.sm)
        self.assertEqual(len(results), 1)
        missing = {e['original_path'] for e in errors if 'no result' in e['error']}
        self.assertEqual(missing, {str(self.batch[i]) for i in (0, 1, 3)})
        for e in errors:
            self.assertEqual(e['preview_path'], str(self.pm[e['original_path']]))
            self.assertEqual(e['session_id'], S1)


if __name__ == '__main__':
    unittest.main()
