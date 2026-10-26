import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from tools.archive_restore import restore, sha256


class FullArchiveRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.assets = self.root / 'assets'
        self.assets.mkdir()
        self.payload = b'synthetic research evidence'
        self.sha = hashlib.sha256(self.payload).hexdigest()
        with zipfile.ZipFile(self.assets / 'part.zip', 'w') as z:
            z.writestr('objects/' + self.sha, self.payload)
        (self.assets / 'vault.zip').write_bytes(b'synthetic vault')
        self.row = {'root': 'research', 'path': 'notes/evidence.txt', 'sha256': self.sha, 'bytes': len(self.payload)}
        self.destination = self.root / 'restored'

    def manifest(self, rows=None):
        (self.assets / 'catalogue.jsonl').write_text('\n'.join(json.dumps(r) for r in (rows or [self.row])))
        manifest = {'format': 'p4-full-research-release-v1', 'catalogue': 'catalogue.jsonl', 'vault': 'vault.zip', 'objects': {self.sha: {'asset': 'part.zip', 'member': 'objects/' + self.sha, 'bytes': len(self.payload)}}, 'assets': [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in self.assets.iterdir() if p.name != 'p4-manifest.json']}
        p = self.assets / 'p4-manifest.json'
        p.write_text(json.dumps(manifest))
        return sha256(p)

    def test_restore_and_refuse_overwrite(self):
        expected = self.manifest()
        result = restore(self.assets, self.destination, expected)
        self.assertEqual(result['files_restored'], 1)
        self.assertEqual((self.destination / 'sources/research/notes/evidence.txt').read_bytes(), self.payload)
        with self.assertRaises(ValueError): restore(self.assets, self.destination, expected)

    def test_tampered_asset(self):
        expected = self.manifest()
        (self.assets / 'part.zip').write_bytes(b'tampered')
        with self.assertRaises(ValueError): restore(self.assets, self.destination, expected)
        self.assertFalse(self.destination.exists())

    def test_manifest_pin(self):
        self.manifest()
        with self.assertRaises(ValueError): restore(self.assets, self.destination, '0' * 64)

    def test_traversal(self):
        for path in ['../escape', '/escape', 'notes/../../escape', 'notes\\escape']:
            with self.subTest(path=path):
                expected = self.manifest([{**self.row, 'path': path}])
                with self.assertRaises(ValueError): restore(self.assets, self.destination, expected)
                self.assertFalse(self.destination.exists())

    def test_duplicate_destinations(self):
        expected = self.manifest([self.row, self.row])
        with self.assertRaises(ValueError): restore(self.assets, self.destination, expected)

    def test_wrong_object_content(self):
        with zipfile.ZipFile(self.assets / 'part.zip', 'w') as z:
            z.writestr('objects/' + self.sha, b'x' * len(self.payload))
        expected = self.manifest()
        with self.assertRaises(ValueError): restore(self.assets, self.destination, expected)
        self.assertFalse((self.destination / 'sources').exists())

    def test_supplement_manifest(self):
        expected = self.manifest()
        (self.assets / 'p4-manifest.json').rename(self.assets / 'p4-supplement-manifest.json')
        result = restore(self.assets, self.destination, expected, 'p4-supplement-manifest.json')
        self.assertEqual(result['files_restored'], 1)
        self.assertTrue((self.destination / 'p4-supplement-manifest.json').exists())

    def test_manifest_path_escape(self):
        expected = self.manifest()
        with self.assertRaises(ValueError): restore(self.assets, self.destination, expected, '../p4-manifest.json')
