"""S2: capture_time parsing, the single ExifTool read and timezone-independent bursts.

Offline: synthetic values, mocked ExifTool (core.run / shutil.which), synthetic paths
that don't exist. No image reads, no metadata writes, no file mtime.
"""
from __future__ import annotations
import json, os, subprocess, time, unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from photo_curator import core
from photo_curator.capture_time import (CaptureTime, format_capture_time, is_valid_offset,
                                        parse_capture, wall_clock_seconds)

ROOT = Path('/nonexistent-photo-curator-s2-root')
S1, S2 = 'DCIM/100MSDCF', 'DCIM 2'


def img(session: str, name: str) -> Path:
    return (ROOT / session / name).resolve()


def fmt(*args):
    record = parse_capture(*args)
    return None if record is None else format_capture_time(record)


class ParseFormatTest(unittest.TestCase):
    def test_with_and_without_offset(self):
        self.assertEqual(fmt('2026:11:15 10:14:03', '250', '+07:00'), '2026-11-15T10:14:03.250+07:00')
        self.assertEqual(fmt('2026:11:15 10:14:03', '250', None), '2026-11-15T10:14:03.250')
        self.assertEqual(fmt('2026:11:15 10:14:03'), '2026-11-15T10:14:03')

    def test_surrounding_whitespace_trimmed_on_datetime(self):
        self.assertEqual(fmt('  2026:11:15 10:14:03 '), '2026-11-15T10:14:03')

    def test_record_is_naive_wall_clock(self):
        r = parse_capture('2026:11:15 10:14:03', '5', '+07:00')
        self.assertIsInstance(r, CaptureTime)
        self.assertIsNone(r.date_time.tzinfo)
        self.assertEqual((r.subsec, r.offset), ('5', '+07:00'))


class SubsecTest(unittest.TestCase):                                  # decisions B-3, B-4
    def test_digits_kept_truncated_never_rounded_never_padded(self):
        cases = {'5': '.5', '05': '.05', '005': '.005', '000': '.000', '123': '.123',
                 '123456': '.123', '999999': '.999', ' 25 ': '.25'}
        for raw, suffix in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(fmt('2026:11:15 10:14:03', raw), '2026-11-15T10:14:03' + suffix)

    def test_exiftool_numbers(self):
        self.assertEqual(fmt('2026:11:15 10:14:03', 5), '2026-11-15T10:14:03.5')
        self.assertEqual(fmt('2026:11:15 10:14:03', 123), '2026-11-15T10:14:03.123')
        self.assertEqual(fmt('2026:11:15 10:14:03', 1234567), '2026-11-15T10:14:03.123')

    def test_invalid_subsec_discarded_datetime_kept(self):
        for raw in (None, '', '   ', '12a', 'a12', '1.5', '-5', -5, 1.5, True, [], {}):
            with self.subTest(raw=raw):
                self.assertEqual(fmt('2026:11:15 10:14:03', raw), '2026-11-15T10:14:03')


class OffsetTest(unittest.TestCase):                                  # decision B-2 (corrected)
    VALID = ('+00:00', '-00:00', '+07:00', '-05:00', '+05:45', '+05:30', '-09:30',
             '+13:59', '-13:59', '+14:00', '-14:00', '+09:00')
    INVALID = ('+14:01', '+14:59', '-14:01', '+15:00', '+24:00', '+07:60', '+7:00', '+0700',
               '07:00', 'Z', '', ' +07:00', '+07:00 ', '+07:00\n', '+07', '+07:00:00', '±07:00',
               None, 7, 7.0, True)

    def test_valid_offsets_copied_exactly(self):
        for off in self.VALID:
            with self.subTest(off=off):
                self.assertTrue(is_valid_offset(off))
                self.assertEqual(fmt('2026:11:15 10:14:03', None, off), '2026-11-15T10:14:03' + off)

    def test_fourteen_hour_boundary(self):
        self.assertTrue(is_valid_offset('+14:00'))
        self.assertFalse(is_valid_offset('+14:01'))
        self.assertFalse(is_valid_offset('+14:59'))
        self.assertTrue(is_valid_offset('-14:00'))
        self.assertFalse(is_valid_offset('-14:01'))

    def test_invalid_offsets_give_timezone_unknown_not_null(self):
        for off in self.INVALID:
            with self.subTest(off=off):
                self.assertFalse(is_valid_offset(off))
                r = parse_capture('2026:11:15 10:14:03', '250', off)
                self.assertIsNotNone(r)
                self.assertIsNone(r.offset)
                self.assertEqual(format_capture_time(r), '2026-11-15T10:14:03.250')

    def test_offset_never_applied(self):
        a = parse_capture('2026:11:15 10:14:03', '', '+07:00')
        b = parse_capture('2026:11:15 10:14:03', '', '-05:00')
        self.assertEqual(wall_clock_seconds(a), wall_clock_seconds(b))
        self.assertEqual(a.date_time, b.date_time)


class NullTest(unittest.TestCase):                                    # decision B-5
    def test_unparseable_datetime_gives_none(self):
        for raw in (None, '', '   ', '0000:00:00 00:00:00', '    :  :     :  :  ',
                    '2026:02:30 10:00:00', '2026:13:01 10:00:00', '2026:11:15 24:00:00',
                    '2026:11:15 10:60:00', '2026-11-15 10:14:03', '2026:11:15T10:14:03',
                    '2026:11:15 10:14:03+07:00', '2026:11:15 10:14:03.250', '2026:11:15 10:14',
                    '2026:11:15', 20261115, 1.0, True, [], {}):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_capture(raw, '250', '+07:00'))

    def test_non_ascii_digits_rejected(self):                       # O-1
        arabic_indic = '٢٠٢٦:١١:١٥ ١٠:١٤:٠٣'                        # U+0660-0669
        fullwidth = '２０２６:１１:１５ １０:１４:０３'                  # U+FF10-FF19
        mixed = '2026:11:15 10:14:0٣'
        for raw in (arabic_indic, fullwidth, mixed):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_capture(raw, '250', '+07:00'))

    def test_parser_never_uses_file_metadata(self):
        with mock.patch.object(os, 'stat', side_effect=AssertionError('stat used')), \
             mock.patch.object(os.path, 'getmtime', side_effect=AssertionError('mtime used')):
            self.assertIsNone(parse_capture(None))
            self.assertIsNotNone(parse_capture('2026:11:15 10:14:03'))


class WallClockTest(unittest.TestCase):
    def test_truncated_subsec_used(self):
        # Truncation is checked exactly on the stored record (spec 14.3: at most 3 digits,
        # truncated, never rounded). The seconds difference is checked to 6 places because
        # values near 1.8e9 carry only ~2e-7 s of float resolution; an untruncated value
        # (0.123999) or a rounded one (0.124) would still fail by a wide margin.
        base = wall_clock_seconds(parse_capture('2026:11:15 10:14:03'))
        for raw, stored, fraction in (('123999', '123', 0.123), ('5', '5', 0.5), ('005', '005', 0.005)):
            with self.subTest(raw=raw):
                record = parse_capture('2026:11:15 10:14:03', raw)
                self.assertEqual(record.subsec, stored)
                self.assertAlmostEqual(wall_clock_seconds(record) - base, fraction, places=6)

    def test_epoch_reference(self):
        self.assertEqual(wall_clock_seconds(parse_capture('1970:01:01 00:00:00')), 0.0)


class ExifToolReadTest(unittest.TestCase):
    def read(self, rows, paths, which='/usr/bin/exiftool', stdout=None):
        proc = subprocess.CompletedProcess([], 1, stdout=json.dumps(rows) if stdout is None else stdout, stderr='')
        with mock.patch.object(core.shutil, 'which', return_value=which), \
             mock.patch.object(core, 'run', return_value=proc) as run:
            records = core.capture_records(paths)
            times = core.capture_times(paths)
        return records, times, run

    def test_single_call_with_three_tags(self):
        p = [img(S1, 'DSC00001.ARW')]
        rows = [{'SourceFile': str(p[0]), 'DateTimeOriginal': '2026:11:15 10:14:03',
                 'SubSecTimeOriginal': '250', 'OffsetTimeOriginal': '+07:00'}]
        records, times, run = self.read(rows, p)
        self.assertEqual(run.call_count, 2)                       # one per function call above
        for call in run.call_args_list:
            cmd = call.args[0]
            self.assertIn('-DateTimeOriginal', cmd); self.assertIn('-SubSecTimeOriginal', cmd)
            self.assertIn('-OffsetTimeOriginal', cmd)
            self.assertNotIn('-OffsetTime', cmd); self.assertNotIn('-OffsetTimeDigitized', cmd)
        self.assertEqual(format_capture_time(records[str(p[0])]), '2026-11-15T10:14:03.250+07:00')
        self.assertEqual(times[str(p[0])], wall_clock_seconds(records[str(p[0])]))

    def test_capture_records_reads_exiftool_once(self):
        p = [img(S1, f'DSC{i:05d}.ARW') for i in range(3)]
        proc = subprocess.CompletedProcess([], 0, stdout='[]', stderr='')
        with mock.patch.object(core.shutil, 'which', return_value='/usr/bin/exiftool'), \
             mock.patch.object(core, 'run', return_value=proc) as run:
            core.capture_records(p)
        self.assertEqual(run.call_count, 1)

    def test_untimed_and_broken_rows_absent(self):
        p = [img(S1, f'DSC{i:05d}.ARW') for i in range(5)]
        rows = [{'SourceFile': str(p[0]), 'DateTimeOriginal': '2026:11:15 10:14:03'},
                {'SourceFile': str(p[1])},                                            # no tag
                {'SourceFile': str(p[2]), 'DateTimeOriginal': '0000:00:00 00:00:00'},  # placeholder
                {'SourceFile': str(p[3]), 'DateTimeOriginal': '2026:11:15 10:14:03Z'}, # suffix
                'not-a-row', {'DateTimeOriginal': '2026:11:15 10:14:03'}]             # no SourceFile
        records, times, _ = self.read(rows, p)
        self.assertEqual(set(records), {str(p[0])})
        self.assertEqual(set(times), {str(p[0])})

    def test_unrequested_rows_ignored(self):                        # O-3
        requested = [img(S1, 'DSC00001.ARW')]
        other = img(S1, 'DSC09999.ARW')
        rows = [{'SourceFile': str(requested[0]), 'DateTimeOriginal': '2026:11:15 10:14:03'},
                {'SourceFile': str(other), 'DateTimeOriginal': '2026:11:15 10:14:04'}]
        records, times, _ = self.read(rows, requested)
        self.assertEqual(set(records), {str(requested[0])})
        self.assertEqual(set(times), {str(requested[0])})
        self.assertNotIn(str(other), records)

    def test_every_timed_path_has_matching_record(self):
        p = [img(S1, f'DSC{i:05d}.ARW') for i in range(3)]
        rows = [{'SourceFile': str(x), 'DateTimeOriginal': '2026:11:15 10:14:0' + str(i),
                 'SubSecTimeOriginal': str(i * 3)} for i, x in enumerate(p)]
        records, times, _ = self.read(rows, p)
        self.assertEqual(set(records), set(times))
        for k in times: self.assertEqual(times[k], wall_clock_seconds(records[k]))

    def test_exiftool_absent_or_bad_output(self):
        p = [img(S1, 'DSC00001.ARW')]
        self.assertEqual(self.read([], p, which=None)[:2], ({}, {}))
        self.assertEqual(self.read(None, p, stdout='not json')[:2], ({}, {}))
        self.assertEqual(self.read(None, p, stdout='{"a": 1}')[:2], ({}, {}))
        self.assertEqual(self.read(None, p, stdout='')[:2], ({}, {}))

    def test_never_uses_file_mtime(self):
        p = [img(S1, 'DSC00001.ARW')]
        with mock.patch.object(os.path, 'getmtime', side_effect=AssertionError('mtime used')):
            records, times, _ = self.read([{'SourceFile': str(p[0])}], p)
        self.assertEqual((records, times), ({}, {}))


class TimezoneIndependenceTest(unittest.TestCase):
    ZONES = ('America/New_York', 'Asia/Tokyo')

    def setUp(self):
        self._old_tz = os.environ.get('TZ')
        self.addCleanup(self._restore_tz)

    def _restore_tz(self):
        if self._old_tz is None: os.environ.pop('TZ', None)
        else: os.environ['TZ'] = self._old_tz
        time.tzset()

    def _set_tz(self, zone):
        os.environ['TZ'] = zone; time.tzset()

    def times_for(self, entries):
        """entries: [(path, DateTimeOriginal, SubSec, Offset)] -> capture_times via mocked exiftool."""
        rows = [{'SourceFile': str(p), 'DateTimeOriginal': d, 'SubSecTimeOriginal': s, 'OffsetTimeOriginal': o}
                for p, d, s, o in entries]
        proc = subprocess.CompletedProcess([], 0, stdout=json.dumps(rows), stderr='')
        with mock.patch.object(core.shutil, 'which', return_value='/usr/bin/exiftool'), \
             mock.patch.object(core, 'run', return_value=proc):
            return core.capture_times([e[0] for e in entries])

    def test_values_identical_under_two_timezones(self):
        entries = [(img(S1, 'DSC00001.ARW'), '2026:03:08 01:59:59', '800', None),
                   (img(S1, 'DSC00002.ARW'), '2026:11:01 01:30:00', '000', '-04:00'),
                   (img(S2, 'GAT00001.ARW'), '2026:07:01 12:00:00', '5', '+09:00')]
        results = []
        for zone in self.ZONES:
            self._set_tz(zone); results.append(self.times_for(entries))
        self.assertEqual(results[0], results[1])

    def test_spring_forward_gap_not_grouped(self):
        a, b = img(S1, 'DSC00010.ARW'), img(S1, 'DSC00011.ARW')
        entries = [(a, '2026:03:08 01:59:59', '800', None), (b, '2026:03:08 03:00:00', '000', None)]
        for zone in self.ZONES:
            with self.subTest(zone=zone):
                self._set_tz(zone)
                times = self.times_for(entries)
                self.assertAlmostEqual(times[str(b)] - times[str(a)], 3600.2)
                self.assertEqual(core.detect_bursts([a, b], 1.0, ROOT, times), {})

    def test_fall_back_repeat_grouped_by_wall_clock(self):
        a, b = img(S1, 'DSC00020.ARW'), img(S1, 'DSC00021.ARW')
        entries = [(a, '2026:11:01 01:30:00', '000', None), (b, '2026:11:01 01:30:00', '500', None)]
        for zone in self.ZONES:
            with self.subTest(zone=zone):
                self._set_tz(zone)
                times = self.times_for(entries)
                bursts = core.detect_bursts([a, b], 1.0, ROOT, times)
                self.assertEqual(set(bursts), {str(a), str(b)})
                self.assertEqual(len(set(bursts.values())), 1)

    def test_offsets_ignored_for_grouping(self):
        a, b = img(S1, 'DSC00030.ARW'), img(S1, 'DSC00031.ARW')
        entries = [(a, '2026:11:15 10:14:03', '000', '+07:00'), (b, '2026:11:15 10:14:03', '400', '-05:00')]
        for zone in self.ZONES:
            with self.subTest(zone=zone):
                self._set_tz(zone)
                times = self.times_for(entries)
                self.assertEqual(len(set(core.detect_bursts([a, b], 1.0, ROOT, times).values())), 1)

    def test_untimed_never_grouped(self):
        a, b, c = img(S1, 'DSC00040.ARW'), img(S1, 'DSC00041.ARW'), img(S1, 'DSC00042.ARW')
        entries = [(a, '2026:11:15 10:14:03', '000', None), (b, '2026:11:15 10:14:03', '200', None),
                   (c, '0000:00:00 00:00:00', '100', None)]
        for zone in self.ZONES:
            with self.subTest(zone=zone):
                self._set_tz(zone)
                times = self.times_for(entries)
                self.assertNotIn(str(c), times)
                bursts = core.detect_bursts([a, b, c], 1.0, ROOT, times)
                self.assertNotIn(str(c), bursts)
                self.assertEqual(set(bursts), {str(a), str(b)})


if __name__ == '__main__':
    unittest.main()
