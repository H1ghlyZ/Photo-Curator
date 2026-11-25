"""Capture time parsing for Photo Curator (spec 5g3a section 14.3; implementation step S2).

Pure functions, no I/O. The caller supplies the raw ExifTool values of
``DateTimeOriginal``, ``SubSecTimeOriginal`` and ``OffsetTimeOriginal`` from one read.

Rules (spec 14.3 and approved decisions B-2 to B-5):
- ``DateTimeOriginal`` must be exactly ``YYYY:MM:DD HH:MM:SS`` (surrounding whitespace
  trimmed) and a real calendar date and time. Anything else, including placeholder
  zeros, blanks or any extra suffix, makes the whole capture time unparseable (None).
- ``SubSecTimeOriginal`` must be one or more ASCII digits; it is kept as written,
  truncated (never rounded) to its first 3 digits and never padded. Invalid sub-second
  text is discarded while the date and time are kept.
- ``OffsetTimeOriginal`` is copied exactly when it matches ``±HH:MM`` with no trimming,
  hours 00-14, minutes 00-59, and minutes 00 when hours are 14. Otherwise the time is
  timezone-unknown (no offset). The offset is never applied.
- No fallback source is ever used (no file mtime, no other tag, no system timezone).
- Burst detection uses ``wall_clock_seconds``: the wall-clock fields as numbers, with the
  truncated sub-seconds, ignoring the offset and independent of the computer's timezone.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_DATETIME_RE = re.compile(r'[0-9]{4}:[0-9]{2}:[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}')   # ASCII digits only
_DATETIME_FORMAT = '%Y:%m:%d %H:%M:%S'
_SUBSEC_RE = re.compile(r'[0-9]+')
_OFFSET_RE = re.compile(r'[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00)')
SUBSEC_MAX_DIGITS = 3


@dataclass(frozen=True)
class CaptureTime:
    """A parsed capture time. ``date_time`` is naive wall-clock time (never tz-aware)."""
    date_time: datetime
    subsec: str            # '' or 1-3 ASCII digits as written, already truncated
    offset: str | None     # '+HH:MM' / '-HH:MM' copied exactly, or None (timezone-unknown)


def is_valid_offset(value: Any) -> bool:
    """True only for an exact ``±HH:MM`` string with no surrounding whitespace, hours
    00-14, minutes 00-59, and minutes 00 when hours are 14 (decision B-2)."""
    return isinstance(value, str) and _OFFSET_RE.fullmatch(value) is not None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _DATETIME_RE.fullmatch(text):
        return None
    try:
        return datetime.strptime(text, _DATETIME_FORMAT)
    except ValueError:            # placeholder zeros, impossible dates or times
        return None


def _parse_subsec(value: Any) -> str:
    if isinstance(value, bool):
        return ''
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return ''
    text = value.strip()
    if not _SUBSEC_RE.fullmatch(text):
        return ''
    return text[:SUBSEC_MAX_DIGITS]


def parse_capture(datetime_original: Any, subsec: Any = None, offset: Any = None) -> CaptureTime | None:
    """Parse raw ExifTool values into a CaptureTime, or None if the date and time
    are missing or unparseable."""
    dt = _parse_datetime(datetime_original)
    if dt is None:
        return None
    return CaptureTime(date_time=dt, subsec=_parse_subsec(subsec),
                       offset=offset if is_valid_offset(offset) else None)


def format_capture_time(record: CaptureTime) -> str:
    """The stored form: ``YYYY-MM-DDTHH:MM:SS[.fff][±HH:MM]`` (spec 14.3)."""
    text = record.date_time.strftime('%Y-%m-%dT%H:%M:%S')
    if record.subsec:
        text += '.' + record.subsec
    if record.offset is not None:
        text += record.offset
    return text


def wall_clock_seconds(record: CaptureTime) -> float:
    """Seconds for ordering and burst windows: the wall-clock fields treated as numbers
    (``calendar.timegm`` never consults the system timezone), plus the truncated
    sub-seconds. The offset is ignored."""
    seconds = float(calendar.timegm(record.date_time.timetuple()))
    if record.subsec:
        seconds += int(record.subsec) / (10 ** len(record.subsec))
    return seconds
