"""S10: v3 CSV (spec 14.6; spec 20 item 18 "new CSV columns in the stated order; flags names")."""
from __future__ import annotations
import csv, io, os, tempfile, unittest
from pathlib import Path

from photo_curator import artifacts_v3, schema_v3
from photo_curator.artifacts_v3 import CSV_COLUMNS, NEW_CSV_COLUMNS, V2_CSV_COLUMNS, csv_bytes
from tests.test_document_v3 import STANDARD_GROUPS, Run, original, standard_overrides
from tests.fake_claude_v3 import evaluable_result

ROOT = Path(__file__).resolve().parent.parent


def rows(data: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(data.decode('utf-8'), newline='')))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s10-csv-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        overrides = standard_overrides()
        overrides[original(1)] = evaluable_result(original(1), flags=['clutter', 'mergers'])
        self.run_ = Run(self.work, STANDARD_GROUPS, overrides)
        self.data = csv_bytes(self.run_.doc)


class ColumnsTest(Base):
    def test_v2_columns_then_new_columns_in_14_6_order(self):
        header = next(csv.reader(io.StringIO(self.data.decode('utf-8'))))
        self.assertEqual(tuple(header), CSV_COLUMNS)
        self.assertEqual(NEW_CSV_COLUMNS, ('capture_time', 'needs_review', 'review_reasons', 'documentation_suggestion',
                                           'editorial_suggestion', 'moment_override', 'burst_comparison',
                                           'burst_ranking_status'))
        self.assertEqual(CSV_COLUMNS[:14], V2_CSV_COLUMNS)

    def test_v2_prefix_matches_the_v2_writer(self):
        cli_source = (ROOT / 'photo_curator' / 'cli.py').read_text(encoding='utf-8')
        self.assertIn('w.writerow([' + ','.join(f"'{c}'" for c in V2_CSV_COLUMNS) + '])', cli_source)


class RowsTest(Base):
    def test_one_row_per_result_matching_json(self):
        parsed = rows(self.data)
        self.assertEqual(len(parsed), len(self.run_.doc['results']))
        by_path = {r['original_path']: r for r in self.run_.doc['results']}
        for row in parsed:
            result = by_path[row['original_path']]
            for column in CSV_COLUMNS:
                value = result.get(column)
                expected = '' if value is None else ('; '.join(map(str, value)) if isinstance(value, list) else str(value))
                self.assertEqual(row[column], expected, (row['original_path'], column))

    def test_flags_use_the_section_3_3_names(self):
        self.assertIs(artifacts_v3.FLAGS, schema_v3.FLAGS)
        row = next(r for r in rows(self.data) if r['original_path'] == original(1))
        self.assertEqual(row['flags'], 'clutter; mergers')
        for r in rows(self.data):
            for flag in filter(None, r['flags'].split('; ')):
                self.assertIn(flag, schema_v3.FLAGS)

    def test_sorted_like_v2(self):
        parsed = rows(self.data)
        keys = [(-int(r['rating']), r['path']) for r in parsed]
        self.assertEqual(keys, sorted(keys))

    def test_no_approval_state_in_csv(self):
        self.assertFalse(any(c.startswith('approved') for c in CSV_COLUMNS))

    def test_unevaluable_row(self):
        row = next(r for r in rows(self.data) if r['original_path'] == original(9))
        self.assertEqual((row['rating'], row['decision'], row['technical_score'], row['needs_review']),
                         ('0', 'unreadable', '', 'True'))
        self.assertIn('unevaluable', row['review_reasons'].split('; '))


if __name__ == '__main__':
    unittest.main()
