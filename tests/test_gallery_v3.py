"""S10: v3 gallery (spec 14.6, 12.3; spec 20 item 18 labels, relative preview paths, archived
gallery and the same check after ``recover-archive --rollback``)."""
from __future__ import annotations
import hashlib, os, tempfile, unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from photo_curator import archive_v3, artifacts_v3, hashing_v3
from photo_curator.archive_v3 import ArchiveFailure, archive_existing, recover_rollback
from photo_curator.artifacts_v3 import NOT_VERIFIED_LABEL, csv_bytes, gallery_bytes, gallery_html
from photo_curator.document_v3 import document_bytes
from photo_curator.locking_v3 import acquire_lock
from photo_curator.output_v3 import write_analysis_outputs
from tests.test_document_v3 import STANDARD_GROUPS, Run, original, standard_overrides

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
FOLDER = 'archive/20261115T120000Z'


class Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('src', 'href') and value is not None:
                self.refs.append(value)


def refs(html_text: str) -> list[str]:
    parser = Refs()
    parser.feed(html_text)
    return parser.refs


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='pc-s10-gal-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(os.path.realpath(tmp.name))
        self.run_ = Run(self.work, STANDARD_GROUPS, standard_overrides())
        self.html = gallery_html(self.run_.doc)

    def write_outputs(self):
        doc = self.run_.doc
        write_analysis_outputs(self.work, results_bytes=document_bytes(doc), csv_bytes=csv_bytes(doc),
                               html_bytes=gallery_bytes(doc))

    def assert_refs_resolve(self, html_file: Path, forbidden_dir: Path | None = None):
        text = html_file.read_text(encoding='utf-8')
        found = refs(text)
        self.assertEqual(len(found), len(self.run_.doc['results']))
        for ref in found:
            target = (html_file.parent / ref).resolve()
            self.assertTrue(target.is_file(), ref)
            self.assertFalse(target.is_symlink())
            self.assertIn(target.name, {Path(r['preview_path']).name for r in self.run_.doc['results']})
            expected = b'\xff\xd8 synthetic preview %d' % int(target.name[3:8])     # Run's synthetic bytes
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), hashlib.sha256(expected).hexdigest())
            if forbidden_dir is not None:
                self.assertNotEqual(target.parent, forbidden_dir.resolve())


class LabelsTest(Base):
    def card(self, n: int) -> str:
        name = f'DSC{n:05d}.ARW'
        start = self.html.index(f'Preview of {name}')
        return self.html[start:self.html.index('</article>', start)]

    def test_review_item_labels(self):
        card = self.card(9)                                         # unevaluable: needs review
        self.assertIn('Approval state (at analysis): <b>pending</b>', card)
        self.assertIn('<li>unevaluable</li>', card)
        self.assertIn(f'Original: <code>{original(9)}</code>', card)
        preview = Path(self.run_.result(9)['preview_path'])
        self.assertIn(f'Preview SHA-256: <code>{hashlib.sha256(preview.read_bytes()).hexdigest()}</code>', card)
        self.assertIn(f'({NOT_VERIFIED_LABEL})', card)
        self.assertIn('Capture time: 2026-11-15T10:14:09', card)

    def test_not_required_item_has_no_preview_hash(self):
        plain = next(r for r in self.run_.doc['results'] if r['needs_review'] is False)
        card = self.card(int(Path(plain['original_path']).name[3:8]))
        self.assertIn('Approval state (at analysis): <b>not required</b>', card)
        self.assertNotIn('Preview SHA-256', card)

    def test_burst_status_labels(self):
        self.assertIn('<span class="burst-status">not comparable</span>', self.card(4))
        self.assertIn('<span class="burst-status">unvalidated</span>', self.card(6))
        self.assertNotIn('Best in burst', self.card(6))                   # winner of an invalid group
        self.assertIn('<span class="burst-status">validated</span>', self.card(2))
        self.assertIn('Best in burst', self.card(2))

    def test_every_result_has_one_card(self):
        self.assertEqual(self.html.count('<article class="card"'), len(self.run_.doc['results']))

    def test_text_is_escaped(self):
        doc = dict(self.run_.doc)
        doc['results'] = [dict(doc['results'][0], session_id='<script>x</script>')]
        self.assertNotIn('<script>x</script>', gallery_html(doc))


class RelativePathTest(Base):
    def test_every_reference_is_relative(self):
        found = refs(self.html)
        self.assertEqual(len(found), len(self.run_.doc['results']))
        for ref in found:
            self.assertTrue(ref.startswith('previews/'), ref)
            self.assertFalse(ref.startswith('/') or ref.lower().startswith('file:'), ref)
            self.assertNotIn('..', ref)
        self.assertNotIn('file://', self.html)
        self.assertNotIn(str(self.work), ''.join(found))

    def test_names_are_url_quoted(self):
        doc = dict(self.run_.doc)
        doc['results'] = [dict(doc['results'][0], preview_path=str(self.work / 'previews' / 'a b#c__1.jpg'))]
        self.assertEqual(refs(gallery_html(doc)), ['previews/a%20b%23c__1.jpg'])

    def test_gallery_never_hashes_or_opens_originals(self):
        opened = []
        real_open = os.open
        with mock.patch.object(hashing_v3, 'sha256_file', side_effect=AssertionError('original hashed')), \
             mock.patch.object(artifacts_v3.os, 'open', side_effect=lambda p, *a: (opened.append(p), real_open(p, *a))[1]):
            gallery_html(self.run_.doc)
        self.assertTrue(opened)
        self.assertTrue(all('/previews/' in str(p) for p in opened))


class ArchivedGalleryTest(Base):
    def test_archived_gallery_resolves_inside_the_archive(self):
        self.write_outputs()
        lock = acquire_lock(self.work, 'exclusive')
        try:
            archive_existing(self.work, lock, now=NOW)
        finally:
            lock.release()
        (self.work / 'previews').mkdir()                              # a new analysis' previews
        archived_html = self.work / FOLDER / 'ratings-review.html'
        self.assertTrue(archived_html.is_file())
        self.assert_refs_resolve(archived_html, forbidden_dir=self.work / 'previews')

    def test_after_rollback_gallery_resolves_again(self):
        self.write_outputs()
        real, calls = os.rename, []

        def rename(src, dst):
            calls.append(src)
            if len(calls) == 5:          # journal order: previews, manifest, csv, html, json -> fail at json
                raise OSError(5, 'Input/output error')
            real(src, dst)
        lock = acquire_lock(self.work, 'exclusive')
        try:
            with mock.patch.object(archive_v3.os, 'rename', side_effect=rename), self.assertRaises(ArchiveFailure):
                archive_existing(self.work, lock, now=NOW)
        finally:
            lock.release()
        self.assertTrue((self.work / FOLDER / 'ratings-review.html').exists())
        recover_rollback(self.work)
        self.assert_refs_resolve(self.work / 'ratings-review.html')


if __name__ == '__main__':
    unittest.main()
