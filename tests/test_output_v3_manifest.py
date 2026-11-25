"""S7a: analysis output writing, completion manifest and preview accounting (spec 12.8, V46;
spec 20 item 16a "Failure classes" and "Preview accounting").

Synthetic temporary work folders only; no Claude, no originals, no XMP.
"""
from __future__ import annotations
import hashlib, json, os, tempfile, unittest, uuid
from pathlib import Path
from unittest import mock

from photo_curator import atomic_v3, output_v3
from photo_curator.core import OUTPUT_MARKER
from photo_curator.output_v3 import (ARTIFACT_ORDER, CSV_NAME, HTML_NAME, MANIFEST_FIELDS, MANIFEST_NAME,
                                     RESULTS_NAME, OutputWriteError, check_completeness, count_previews,
                                     is_uuid_v4, manifest_problems, v31_problems, write_analysis_outputs)

RUN_ID = str(uuid.UUID(int=0x1234, version=4))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def results_doc(work: Path, *, previews=(), error_previews=(), extra_errors=(), run_id=RUN_ID) -> dict:
    results = [{'path': f'/lib/IMG_{i}.ARW', 'original_path': f'/lib/IMG_{i}.ARW',
                'preview_path': str(work / 'previews' / name), 'rating': 3}
               for i, name in enumerate(previews)]
    errors = [{'stage': 'result', 'error': 'rejected', 'original_path': f'/lib/E_{i}.ARW', 'session_id': 's1',
               'burst_group': None, 'burst_sequence': None, 'capture_time': None, 'batch_index': 0,
               'sent_to_model': True, 'result': None, 'preview_path': str(work / 'previews' / name)}
              for i, name in enumerate(error_previews)]
    return {'schema_version': 3, 'rubric_version': 'v3', 'evidence_rules_version': 'v3.0', 'run_id': run_id,
            'confidence_threshold': 0.6, 'max_burst_size': 12, 'burst_window_seconds': 1.0,
            'burst_scope': 'per_session', 'results': results, 'errors': errors + list(extra_errors)}


def encode(doc: dict) -> bytes:
    return (json.dumps(doc, indent=2) + '\n').encode('utf-8')


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s7a-out-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        self.previews = self.work / 'previews'
        self.previews.mkdir()
        (self.previews / OUTPUT_MARKER).write_text('marker', encoding='utf-8')
        (self.work / OUTPUT_MARKER).write_text('marker', encoding='utf-8')

    def add_previews(self, *names):
        for name in names:
            (self.previews / name).write_bytes(b'\xff\xd8 jpeg ' + name.encode())

    def write(self, doc: dict, **kwargs):
        return write_analysis_outputs(self.work, results_bytes=encode(doc), csv_bytes=b'a,b\n1,2\n',
                                      html_bytes=b'<html></html>\n', **kwargs)

    def present(self) -> list[str]:
        return [n for n in ARTIFACT_ORDER if (self.work / n).exists()]


class WriteOrderTest(Base):
    def test_four_artifacts_in_order_manifest_last(self):
        self.add_previews('a__1.jpg')
        order = []
        real = atomic_v3.atomic_write
        with mock.patch.object(output_v3, 'atomic_write', side_effect=lambda p, d, **k: (order.append(os.path.basename(p)), real(p, d, **k))[1]):
            result = self.write(results_doc(self.work, previews=['a__1.jpg']))
        self.assertEqual(order, list(ARTIFACT_ORDER))
        self.assertEqual(self.present(), list(ARTIFACT_ORDER))
        self.assertTrue(check_completeness(self.work).complete)
        self.assertEqual(result.manifest['referenced_preview_count'], 1)

    def test_manifest_fields_and_values(self):
        doc = results_doc(self.work)
        self.write(doc, created_at='2026-11-15T10:00:00.000000Z')
        manifest = json.loads((self.work / MANIFEST_NAME).read_text(encoding='utf-8'))
        self.assertEqual(tuple(manifest), MANIFEST_FIELDS)
        self.assertEqual(manifest['run_id'], RUN_ID)
        self.assertEqual(manifest['created_at'], '2026-11-15T10:00:00.000000Z')
        self.assertEqual(manifest['results_sha256_at_analysis'], sha(encode(doc)))
        self.assertEqual(manifest['csv_sha256'], sha(b'a,b\n1,2\n'))
        self.assertEqual(manifest['html_sha256'], sha(b'<html></html>\n'))
        self.assertEqual(manifest_problems(manifest), [])

    def test_default_created_at_is_utc_z(self):
        self.write(results_doc(self.work))
        manifest = json.loads((self.work / MANIFEST_NAME).read_text(encoding='utf-8'))
        self.assertRegex(manifest['created_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$')

    def test_invalid_results_refused_before_any_write(self):
        for bad in ({'schema_version': 2, 'results': [], 'errors': []},
                    dict(results_doc(self.work), run_id='not-a-uuid'),
                    dict(results_doc(self.work), max_burst_size=0)):
            with self.subTest(bad=bad.get('run_id'), v=bad.get('schema_version')):
                with self.assertRaises(ValueError):
                    self.write(bad)
                self.assertEqual(self.present(), [])


class FailureClassTest(Base):
    def fail_dir_fsync_after(self, artifact: str):
        real = atomic_v3._fsync_dir
        state = {'n': 0}
        index = ARTIFACT_ORDER.index(artifact)

        def fsync_dir(directory):
            if state['n'] == index:
                state['n'] += 1
                raise OSError(5, 'simulated folder fsync failure')
            state['n'] += 1
            real(directory)
        return mock.patch.object(atomic_v3, '_fsync_dir', side_effect=fsync_dir)

    def test_durability_failure_after_results_csv_gallery(self):
        for artifact in (RESULTS_NAME, CSV_NAME, HTML_NAME):
            with self.subTest(artifact=artifact):
                for name in ARTIFACT_ORDER:
                    (self.work / name).unlink(missing_ok=True)
                with self.fail_dir_fsync_after(artifact), self.assertRaises(OutputWriteError) as ctx:
                    self.write(results_doc(self.work))
                error = ctx.exception
                self.assertEqual(error.failure_class, 'durability')
                self.assertEqual(error.artifact, artifact)
                self.assertIn('not confirmed durable', error.message)
                upto = ARTIFACT_ORDER.index(artifact) + 1
                self.assertEqual(self.present(), list(ARTIFACT_ORDER[:upto]))   # no later artifact
                self.assertFalse((self.work / MANIFEST_NAME).exists())
                self.assertFalse(check_completeness(self.work).complete)

    def test_durability_failure_after_manifest(self):
        with self.fail_dir_fsync_after(MANIFEST_NAME), self.assertRaises(OutputWriteError) as ctx:
            self.write(results_doc(self.work))
        self.assertEqual(ctx.exception.failure_class, 'durability')
        self.assertEqual(ctx.exception.artifact, MANIFEST_NAME)
        self.assertIn('not claimed durable', ctx.exception.message)
        self.assertEqual(self.present(), list(ARTIFACT_ORDER))     # no rollback
        self.assertEqual(ctx.exception.written, ARTIFACT_ORDER)

    def test_before_replace_failure_stops_run(self):
        for artifact in ARTIFACT_ORDER:
            with self.subTest(artifact=artifact):
                for name in ARTIFACT_ORDER:
                    (self.work / name).unlink(missing_ok=True)
                real = atomic_v3._replace
                target = str(self.work / artifact)
                with mock.patch.object(atomic_v3, '_replace',
                                       side_effect=lambda s, d: (_ for _ in ()).throw(OSError(5, 'x')) if d == target else real(s, d)), \
                     self.assertRaises(OutputWriteError) as ctx:
                    self.write(results_doc(self.work))
                self.assertEqual(ctx.exception.failure_class, 'before_replace')
                self.assertEqual(ctx.exception.artifact, artifact)
                self.assertEqual(self.present(), list(ARTIFACT_ORDER[:ARTIFACT_ORDER.index(artifact)]))
                self.assertEqual([n for n in os.listdir(self.work) if '.tmp-' in n], [])
                self.assertFalse(check_completeness(self.work).complete)


class PreviewAccountingTest(Base):
    def counts_after_write(self, doc):
        self.write(doc)
        return json.loads((self.work / MANIFEST_NAME).read_text(encoding='utf-8'))

    def test_only_accepted_images(self):
        self.add_previews('a__1.jpg', 'b__2.jpg', 'c__3.jpg')
        m = self.counts_after_write(results_doc(self.work, previews=['a__1.jpg', 'b__2.jpg'],
                                                error_previews=['c__3.jpg']))
        self.assertEqual((m['referenced_preview_count'], m['orphan_preview_count']), (3, 0))

    def test_stage_original_rejection_preview_is_orphan(self):
        self.add_previews('a__1.jpg', 'orig__9.jpg')
        rejection = {'stage': 'original', 'error': 'content changed', 'original_path': '/lib/orig.ARW',
                     'session_id': None, 'burst_group': None, 'burst_sequence': None, 'capture_time': None,
                     'batch_index': None, 'sent_to_model': False, 'result': None, 'preview_path': None}
        m = self.counts_after_write(results_doc(self.work, previews=['a__1.jpg'], extra_errors=[rejection]))
        self.assertEqual((m['referenced_preview_count'], m['orphan_preview_count']), (1, 1))
        self.assertTrue(check_completeness(self.work).complete)
        self.assertTrue((self.previews / 'orig__9.jpg').exists())          # never deleted

    def test_extra_file_symlink_folder_are_orphans_marker_ignored(self):
        self.add_previews('a__1.jpg', 'extra.txt')
        os.symlink(self.previews / 'a__1.jpg', self.previews / 'link__2.jpg')
        (self.previews / 'sub').mkdir()
        doc = results_doc(self.work, previews=['a__1.jpg'])
        doc['results'].append(dict(doc['results'][0], preview_path=str(self.previews / 'link__2.jpg')))
        counts = count_previews(self.work, doc)
        self.assertEqual((counts.referenced, counts.orphan), (1, 3))
        self.assertEqual(counts.orphan_names, ('extra.txt', 'link__2.jpg', 'sub'))
        self.assertNotIn(OUTPUT_MARKER, counts.orphan_names)

    def test_uppercase_or_non_jpg_is_orphan_even_when_referenced(self):
        self.add_previews('a__1.JPG', 'b__2.png')
        counts = count_previews(self.work, results_doc(self.work, previews=['a__1.JPG', 'b__2.png']))
        self.assertEqual((counts.referenced, counts.orphan), (0, 2))

    def test_missing_file_adds_to_neither(self):
        self.add_previews('a__1.jpg')
        counts = count_previews(self.work, results_doc(self.work, previews=['a__1.jpg', 'gone__2.jpg']))
        self.assertEqual((counts.referenced, counts.orphan), (1, 0))

    def test_two_entries_same_file_counted_once(self):
        self.add_previews('a__1.jpg')
        counts = count_previews(self.work, results_doc(self.work, previews=['a__1.jpg'], error_previews=['a__1.jpg']))
        self.assertEqual((counts.referenced, counts.orphan), (1, 0))

    def test_invalid_preview_path_is_no_reference_with_warning(self):
        self.add_previews('a__1.jpg')
        doc = results_doc(self.work, previews=['a__1.jpg'])
        doc['results'][0]['preview_path'] = 42
        counts = count_previews(self.work, doc)
        self.assertEqual((counts.referenced, counts.orphan), (0, 1))
        self.assertTrue(counts.warnings)

    def test_counts_sum_to_entries_excluding_marker(self):
        self.add_previews('a__1.jpg', 'b__2.jpg', 'x.txt', 'C__3.JPG')
        (self.previews / 'dir').mkdir()
        counts = count_previews(self.work, results_doc(self.work, previews=['a__1.jpg', 'missing.jpg']))
        self.assertEqual(counts.listed, len(os.listdir(self.previews)) - 1)

    def test_missing_previews_folder_counts_zero_and_symlinked_folder_refused(self):
        other = Path(tempfile.mkdtemp(prefix='pc-s7a-other-'))
        self.addCleanup(lambda: (os.rmdir(other)))
        for name in os.listdir(self.previews):
            os.unlink(self.previews / name)
        self.previews.rmdir()
        counts = count_previews(self.work, results_doc(self.work))
        self.assertEqual((counts.referenced, counts.orphan), (0, 0))
        os.symlink(other, self.previews)
        with self.assertRaises(ValueError):
            count_previews(self.work, results_doc(self.work))

    def test_manifest_count_validation_v46(self):
        self.write(results_doc(self.work))
        good = json.loads((self.work / MANIFEST_NAME).read_text(encoding='utf-8'))
        cases = {'missing referenced': {k: v for k, v in good.items() if k != 'referenced_preview_count'},
                 'missing orphan': {k: v for k, v in good.items() if k != 'orphan_preview_count'},
                 'negative': dict(good, orphan_preview_count=-1),
                 'float': dict(good, referenced_preview_count=1.0),
                 'string': dict(good, referenced_preview_count='1'),
                 'bool': dict(good, orphan_preview_count=True)}
        for label, manifest in cases.items():
            with self.subTest(label=label):
                (self.work / MANIFEST_NAME).write_text(json.dumps(manifest), encoding='utf-8')
                result = check_completeness(self.work)
                self.assertFalse(result.complete)
                self.assertTrue(result.problems)

    def test_orphan_added_after_analysis_warning_only(self):
        self.add_previews('a__1.jpg')
        self.write(results_doc(self.work, previews=['a__1.jpg']))
        self.add_previews('later.jpg')
        before = sorted(os.listdir(self.previews))
        result = check_completeness(self.work)
        self.assertTrue(result.complete)
        self.assertTrue(any('orphan preview count differs' in w for w in result.warnings))
        self.assertEqual(sorted(os.listdir(self.previews)), before)          # nothing deleted


class ManifestShapeTest(unittest.TestCase):
    def good(self):
        return {'run_id': RUN_ID, 'created_at': '2026-11-15T10:00:00Z', 'results_sha256_at_analysis': 'a' * 64,
                'csv_sha256': 'b' * 64, 'html_sha256': 'c' * 64, 'referenced_preview_count': 0,
                'orphan_preview_count': 0}

    def test_good(self):
        self.assertEqual(manifest_problems(self.good()), [])

    def test_each_field_missing(self):
        for name in MANIFEST_FIELDS:
            with self.subTest(name=name):
                bad = self.good()
                del bad[name]
                self.assertEqual(manifest_problems(bad), [f'manifest field {name} missing'])

    def test_bad_values(self):
        for name, value in (('run_id', 'x'), ('created_at', '2026-11-15T10:00:00'), ('created_at', 'nonsenseZ'),
                            ('csv_sha256', 'A' * 64), ('html_sha256', 'c' * 63), ('results_sha256_at_analysis', None)):
            with self.subTest(name=name, value=value):
                self.assertTrue(manifest_problems(dict(self.good(), **{name: value})))
        self.assertTrue(manifest_problems([]))


class UuidV4Test(unittest.TestCase):
    """Spec 14.4: run_id is a version 4 UUID; no particular string representation is required."""

    def test_v4_in_any_representation_accepted(self):
        u = uuid.UUID(int=0x1234, version=4)
        for text in (str(u), str(u).upper(), u.hex, '{' + str(u) + '}', u.urn):
            with self.subTest(text=text):
                self.assertTrue(is_uuid_v4(text))

    def test_other_versions_and_variants_rejected(self):
        for value in (str(uuid.UUID(int=0x1234, version=1)), str(uuid.UUID(int=0x1234, version=3)),
                      str(uuid.UUID(int=0x1234, version=5)), str(uuid.UUID(int=0)),
                      '12345678-1234-4234-c234-123456789abc'):        # version nibble 4, variant not RFC 4122
            with self.subTest(value=value):
                self.assertFalse(is_uuid_v4(value))

    def test_non_uuid_rejected(self):
        for value in (None, 1234, '', 'not-a-uuid', 'g' * 32, str(uuid.UUID(int=0x1234, version=4))[:-1]):
            with self.subTest(value=value):
                self.assertFalse(is_uuid_v4(value))

    def test_v31_and_manifest_use_v4_rule(self):
        doc = results_doc(Path('/w'))
        self.assertEqual(v31_problems(doc), [])
        v1 = str(uuid.UUID(int=0x1234, version=1))
        self.assertIn('run_id missing or not a version 4 UUID', v31_problems(dict(doc, run_id=v1)))
        good = {'run_id': RUN_ID.upper(), 'created_at': '2026-11-15T10:00:00Z', 'results_sha256_at_analysis': 'a' * 64,
                'csv_sha256': 'b' * 64, 'html_sha256': 'c' * 64, 'referenced_preview_count': 0,
                'orphan_preview_count': 0}
        self.assertEqual(manifest_problems(good), [])
        self.assertEqual(manifest_problems(dict(good, run_id=v1)), ['manifest run_id is not a version 4 UUID'])


if __name__ == '__main__':
    unittest.main()
