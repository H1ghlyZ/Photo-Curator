"""S7b: journalled archive of existing output (spec 12.7 --archive-existing, 12.9 steps 1-6;
spec 20 item 16b, library part).

Synthetic temporary work folders only. Nothing is wired into the CLI.
"""
from __future__ import annotations
import hashlib, json, os, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from photo_curator import archive_v3
from photo_curator.archive_v3 import (ARCHIVED_JOURNAL, ArchiveFailure, ArchiveRefusal, archive_existing,
                                      archive_timestamp, journal_problems)
from photo_curator import atomic_v3
from photo_curator.core import OUTPUT_MARKER
from photo_curator.locking_v3 import (JOURNAL_NAME, LOCK_NAME, ArchiveInProgressRefusal, acquire_lock,
                                      startup_v3)
from photo_curator.output_v3 import refuse_existing_output

NOW = datetime(2026, 11, 15, 10, 0, 0, tzinfo=timezone.utc)
TS = '20261115T100000Z'
FOLDER = f'archive/{TS}'
LISTED = ['previews', 'ratings-review.complete.json', 'ratings-review.csv', 'ratings-review.html',
          'ratings-review.json', 'ratings-review.json.20261115T090000.000000Z.photo-curator.bak',
          'ratings-review.json.tmp-1-dead']


def tree(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                out[rel] = 'link:' + os.readlink(full)
            elif os.path.isfile(full):
                out[rel] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
            else:
                out[rel] = 'dir'
    return out


def make_output(work: Path) -> None:
    (work / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
    results = {'schema_version': 3, 'results': [
        {'path': '/lib/A.ARW', 'original_path': '/lib/A.ARW', 'preview_path': str(work / 'previews/A__1.jpg'),
         'rating': 3, 'needs_review': True, 'original_sha256': 'a' * 64, 'approved': True, 'approved_rating': 3,
         'approved_at': '2026-11-15T07:12:03Z', 'approved_original_path': '/lib/A.ARW',
         'approved_original_sha256': 'a' * 64, 'approved_preview_path': str(work / 'previews/A__1.jpg'),
         'approved_preview_sha256': 'b' * 64}]}
    (work / 'ratings-review.json').write_text(json.dumps(results), encoding='utf-8')
    (work / 'ratings-review.csv').write_bytes(b'csv\n')
    (work / 'ratings-review.html').write_bytes(b'<html></html>\n')
    (work / 'ratings-review.complete.json').write_bytes(b'{"manifest": true}\n')
    (work / 'ratings-review.json.20261115T090000.000000Z.photo-curator.bak').write_bytes(b'{"old": 1}\n')
    (work / 'ratings-review.json.tmp-1-dead').write_bytes(b'leftover')
    (work / 'notes.txt').write_bytes(b'not on the refusal list')
    previews = work / 'previews'
    previews.mkdir()
    (previews / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
    (previews / 'A__1.jpg').write_bytes(b'\xff\xd8 A')
    (previews / 'orphan.jpg').write_bytes(b'\xff\xd8 orphan')


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s7b-arc-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        make_output(self.work)
        self.lock = acquire_lock(self.work, 'exclusive')
        self.addCleanup(self.lock.release)

    def archive(self, **kwargs):
        return archive_existing(self.work, self.lock, now=NOW, **kwargs)

    def fail_rename_at(self, position: int):
        """Patch os.rename so the call number ``position`` (1-based) fails."""
        real, calls = os.rename, []

        def rename(src, dst):
            calls.append((src, dst))
            if len(calls) == position:
                raise OSError(5, 'Input/output error')
            real(src, dst)
        return mock.patch.object(archive_v3.os, 'rename', side_effect=rename), calls


class TimestampTest(unittest.TestCase):
    def test_m16_format(self):
        self.assertEqual(archive_timestamp(NOW), TS)
        self.assertRegex(archive_timestamp(), r'^\d{8}T\d{6}Z$')


class SuccessTest(Base):
    def test_moves_exactly_the_listed_items_and_deletes_nothing(self):
        before = tree(self.work)
        result = self.archive()
        after = tree(self.work)
        self.assertEqual(result.archive_folder, FOLDER)
        self.assertEqual(sorted(result.moved), LISTED)
        for rel, digest in before.items():
            top = rel.split('/')[0]
            if top in LISTED:
                self.assertEqual(after[f'{FOLDER}/{rel}'], digest, rel)       # moved, byte-identical
                self.assertNotIn(rel, after)
            else:
                self.assertEqual(after[rel], digest, rel)                     # untouched
        self.assertTrue((self.work / FOLDER / OUTPUT_MARKER).is_file())       # marked as output
        self.assertFalse((self.work / JOURNAL_NAME).exists())
        journal = json.loads((self.work / FOLDER / ARCHIVED_JOURNAL).read_text(encoding='utf-8'))
        self.assertEqual(journal_problems(journal), [])
        self.assertEqual([e['source'] for e in journal['entries']], LISTED)
        preview_entry = journal['entries'][0]
        self.assertEqual(preview_entry['names'], sorted([OUTPUT_MARKER, 'A__1.jpg', 'orphan.jpg']))
        self.assertEqual(preview_entry['count'], 3)

    def test_orphans_move_with_previews_in_one_rename(self):
        real, calls = os.rename, []
        with mock.patch.object(archive_v3.os, 'rename', side_effect=lambda s, d: (calls.append((s, d)), real(s, d))):
            self.archive()
        preview_moves = [c for c in calls if os.path.basename(c[0]) == 'previews']
        self.assertEqual(preview_moves, [(str(self.work / 'previews'), str(self.work / FOLDER / 'previews'))])
        self.assertTrue((self.work / FOLDER / 'previews' / 'orphan.jpg').is_file())

    def test_approvals_summary_reported(self):
        result = self.archive()
        self.assertEqual(result.approvals_summary.passing, 1)
        self.assertIsNone(result.approvals_unknown_reason)

    def test_new_analysis_can_start_afterwards(self):
        self.archive()
        refuse_existing_output(self.work)                       # nothing on the 12.7 list remains
        self.lock.release()
        startup_v3(self.work, 'exclusive').lock.release()       # journal gone: not blocked

    def test_second_archive_uses_existing_archive_root(self):
        self.archive()
        (self.work / 'ratings-review.json').write_text('{"schema_version": 3, "results": []}', encoding='utf-8')
        later = archive_existing(self.work, self.lock, now=datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc))
        self.assertEqual(later.archive_folder, 'archive/20261115T110000Z')
        self.assertTrue((self.work / FOLDER / 'ratings-review.json').exists())


class RefusalTest(Base):
    def assert_refused_unchanged(self, *, lock=None, contains=''):
        before = tree(self.work)
        with self.assertRaises(ArchiveRefusal) as ctx:
            archive_existing(self.work, lock or self.lock, now=NOW)
        self.assertIn(contains, ctx.exception.message)
        self.assertEqual(tree(self.work), before)

    def test_requires_exclusive_lock_on_this_folder(self):
        self.lock.release()
        self.assert_refused_unchanged(lock=self.lock, contains='exclusive lock')
        shared = acquire_lock(self.work, 'shared')
        self.addCleanup(shared.release)
        self.assert_refused_unchanged(lock=shared, contains='exclusive lock')
        other_dir = tempfile.TemporaryDirectory(prefix='pc-s7b-other-')
        self.addCleanup(other_dir.cleanup)
        other = acquire_lock(other_dir.name, 'exclusive')
        self.addCleanup(other.release)
        self.assert_refused_unchanged(lock=other, contains='exclusive lock')

    def test_journal_present(self):
        (self.work / JOURNAL_NAME).write_bytes(b'{}')
        self.assert_refused_unchanged(contains='recover-archive')

    def test_nothing_to_archive(self):
        for name in LISTED:
            path = self.work / name
            if path.is_dir():
                for child in path.iterdir():
                    child.unlink()
                path.rmdir()
            else:
                path.unlink()
        self.assert_refused_unchanged(contains='nothing to archive')

    def test_name_collision(self):
        (self.work / FOLDER).mkdir(parents=True)
        self.assert_refused_unchanged(contains='already exists')

    def test_archive_root_is_a_symlink(self):
        elsewhere = tempfile.TemporaryDirectory(prefix='pc-s7b-elsewhere-')
        self.addCleanup(elsewhere.cleanup)
        os.symlink(elsewhere.name, self.work / 'archive')
        self.assert_refused_unchanged(contains='not a real directory')
        self.assertEqual(os.listdir(elsewhere.name), [])

    def test_listed_item_is_a_symlink(self):
        target = self.work / 'notes.txt'
        (self.work / 'ratings-review.csv').unlink()
        os.symlink(target, self.work / 'ratings-review.csv')
        self.assert_refused_unchanged(contains='not a regular file')

    def test_previews_is_a_symlink(self):
        real = self.work / 'real-previews'
        os.rename(self.work / 'previews', real)
        os.symlink(real, self.work / 'previews')
        self.assert_refused_unchanged(contains='previews')


class FailureTest(Base):
    def assert_blocked(self):
        self.lock.release()
        for mode in ('shared', 'exclusive'):
            with self.assertRaises(ArchiveInProgressRefusal):
                startup_v3(self.work, mode)
        startup_v3(self.work, 'shared', exempt_journal=True).lock.release()     # recover-archive may run

    def test_journal_write_fails_before_replacement(self):
        before = tree(self.work)
        with mock.patch.object(atomic_v3, '_replace', side_effect=OSError(5, 'x')), \
             self.assertRaises(ArchiveFailure) as ctx:
            self.archive()
        self.assertFalse(ctx.exception.journal_written)
        self.assertEqual(ctx.exception.done, ())
        self.assertEqual(tree(self.work), before)                # nothing moved, no journal

    def test_journal_durability_failure_blocks_folder(self):
        with mock.patch.object(atomic_v3, '_fsync_dir', side_effect=OSError(5, 'x')), \
             self.assertRaises(ArchiveFailure) as ctx:
            self.archive()
        self.assertEqual(ctx.exception.stage, 'journal')
        self.assertTrue((self.work / JOURNAL_NAME).exists())
        for name in LISTED:
            self.assertTrue((self.work / name).exists(), name)     # nothing moved
        self.assert_blocked()

    def test_archive_folder_creation_failure(self):
        with mock.patch.object(archive_v3, 'mark_output_dir', side_effect=OSError(28, 'No space left on device')), \
             self.assertRaises(ArchiveFailure) as ctx:
            self.archive()
        self.assertEqual(ctx.exception.stage, 'archive folder')
        self.assertTrue((self.work / JOURNAL_NAME).exists())
        self.assert_blocked()

    def test_rename_failure_at_each_position(self):
        for position in range(1, len(LISTED) + 1):
            with self.subTest(position=position):
                tmp = tempfile.TemporaryDirectory(prefix='pc-s7b-pos-')
                self.addCleanup(tmp.cleanup)
                work = Path(os.path.realpath(tmp.name))
                make_output(work)
                before = tree(work)
                lock = acquire_lock(work, 'exclusive')
                patcher, calls = self.fail_rename_at(position)
                with patcher, self.assertRaises(ArchiveFailure) as ctx:
                    archive_existing(work, lock, now=NOW)
                lock.release()
                error = ctx.exception
                self.assertEqual(error.stage, 'rename')
                self.assertEqual(len(error.done), position - 1)
                self.assertEqual(list(error.done) + list(error.pending), LISTED)
                self.assertEqual(len(calls), position)                       # no retry, no rollback
                self.assertTrue((work / JOURNAL_NAME).exists())
                after = tree(work)
                for rel, digest in before.items():                           # nothing deleted or changed
                    where = f'{FOLDER}/{rel}' if rel.split('/')[0] in error.done else rel
                    self.assertEqual(after.get(where), digest, rel)
                for mode in ('shared', 'exclusive'):
                    with self.assertRaises(ArchiveInProgressRefusal):
                        startup_v3(work, mode)
                self.assertTrue((work / 'previews').is_dir() != (work / FOLDER / 'previews').is_dir())

    def test_verification_failure_leaves_journal(self):
        with mock.patch.object(archive_v3, '_verify_all_done', return_value=['x: SHA-256 differs from journal']), \
             self.assertRaises(ArchiveFailure) as ctx:
            self.archive()
        self.assertEqual(ctx.exception.stage, 'verify')
        self.assertTrue((self.work / JOURNAL_NAME).exists())
        self.assert_blocked()


class JournalShapeTest(unittest.TestCase):
    def good(self):
        return {'archive_folder': FOLDER, 'entries': [
            {'source': 'previews', 'target': f'{FOLDER}/previews', 'kind': 'folder', 'names': ['a.jpg'], 'count': 1},
            {'source': 'ratings-review.json', 'target': f'{FOLDER}/ratings-review.json', 'kind': 'file',
             'sha256': 'a' * 64}]}

    def test_good(self):
        self.assertEqual(journal_problems(self.good()), [])

    def test_malformed(self):
        g = self.good
        cases = {
            'not object': [],
            'folder outside archive': dict(g(), archive_folder='../x/20261115T100000Z'),
            'folder bad ts': dict(g(), archive_folder='archive/today'),
            'no entries': dict(g(), entries=[]),
            'source with slash': dict(g(), entries=[dict(g()['entries'][1], source='../x', target=f'{FOLDER}/../x')]),
            'source is journal': dict(g(), entries=[dict(g()['entries'][1], source=JOURNAL_NAME,
                                                         target=f'{FOLDER}/{JOURNAL_NAME}')]),
            'source is lock': dict(g(), entries=[dict(g()['entries'][1], source=LOCK_NAME, target=f'{FOLDER}/{LOCK_NAME}')]),
            'target elsewhere': dict(g(), entries=[dict(g()['entries'][1], target='/etc/passwd')]),
            'bad hash': dict(g(), entries=[dict(g()['entries'][1], sha256='A' * 64)]),
            'bad kind': dict(g(), entries=[dict(g()['entries'][1], kind='link')]),
            'folder not previews': dict(g(), entries=[dict(g()['entries'][0], source='x', target=f'{FOLDER}/x')]),
            'names unsorted': dict(g(), entries=[dict(g()['entries'][0], names=['b', 'a'], count=2)]),
            'count wrong': dict(g(), entries=[dict(g()['entries'][0], count=2)]),
            'count bool': dict(g(), entries=[dict(g()['entries'][0], count=True)]),
            'duplicate source': dict(g(), entries=[g()['entries'][1], g()['entries'][1]]),
        }
        for label, journal in cases.items():
            with self.subTest(label=label):
                self.assertTrue(journal_problems(journal))


if __name__ == '__main__':
    unittest.main()
