"""Archive safety tests; never imports the application or opens a database."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('archive_ml_results', Path(__file__).parents[1] / 'scripts/archive_ml_results.py')
archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archive)


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self):
        archive.GENERATED.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=archive.GENERATED)
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'daily-review-20261103-test'
        self.source.mkdir()
        self.original = self.source / 'quality-2026-11-03.json'
        self.original.write_text(json.dumps({'report_date': '2026-11-03', 'status': 'WARN'}), encoding='utf-8')

    def make_item(self):
        item, data = archive.bundle('2026-11-03', self.source)
        path = Path(self.temp.name) / item['name']
        path.write_bytes(data)
        item['path'] = str(path)
        return item, data

    def test_deterministic_bundle_and_file_readback(self):
        item, data = self.make_item()
        self.assertEqual(data, archive.bundle('2026-11-03', self.source)[1])
        self.assertEqual(archive.verify(item, data), 1)

    def test_unknown_file_blocks_entire_run(self):
        (self.source / '.env').write_text('DO_NOT_UPLOAD', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.make_item()

    def test_sensitive_fields_and_text_blocked(self):
        for content in (b'{"account_number":"123456"}', b'{"message":"Bearer secret"}',
                        b'{"message":"password=secret"}', b'{"email":"owner@example.com"}',
                        '{"계좌번호":"123456"}'.encode('utf-8')):
            with self.subTest(content=content), self.assertRaises(ValueError):
                archive.scan('report.json', content)

    def test_path_escape_blocked(self):
        with self.assertRaises(ValueError):
            archive.bundle('2026-11-03', archive.ROOT / 'scripts')

    def test_date_mismatch_blocked(self):
        with self.assertRaises(ValueError):
            archive.bundle('2026-11-04', self.source)

    def test_corruption_and_changed_source_blocked(self):
        item, data = self.make_item()
        with self.assertRaises(ValueError):
            archive.verify(item, data[:-1] + bytes([data[-1] ^ 1]))
        self.original.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            archive.verify(item, data)

    def test_repeated_upload_reads_existing_without_creating(self):
        item, data = self.make_item()
        class MemoryDrive:
            created = 0
            def matches(self, name):
                return [{'id': 'existing'}] if self.created else []
            def create(self, item, content):
                self.created += 1
                return {'id': 'existing'}
            def request(self, *args, **kwargs):
                return data
        drive = MemoryDrive()
        plan = {'archives': [item]}
        self.assertFalse(archive.upload(plan, drive)[0]['reused'])
        self.assertTrue(archive.upload(plan, drive)[0]['reused'])
        self.assertEqual(drive.created, 1)

    def test_upload_failure_retains_original_bytes(self):
        item, data = self.make_item()
        before = self.original.read_bytes()
        class FailedDrive:
            def matches(self, name):
                raise OSError('offline')
        with self.assertRaises(OSError):
            archive.upload({'archives': [item]}, FailedDrive())
        self.assertEqual(self.original.read_bytes(), before)
        self.assertEqual(Path(item['path']).read_bytes(), data)

    def test_remote_name_with_wrong_content_is_not_trusted(self):
        item, _ = self.make_item()
        class CorruptDrive:
            def matches(self, name):
                return [{'id': 'corrupt'}]
            def request(self, *args, **kwargs):
                return b'corrupt'
        with self.assertRaises(ValueError):
            archive.upload({'archives': [item]}, CorruptDrive())


if __name__ == '__main__':
    unittest.main()
