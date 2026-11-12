import hashlib
import importlib.util
from pathlib import Path
import tempfile
import tomllib
import unittest


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/prepare_release.py'
        spec = importlib.util.spec_from_file_location('prepare_release', script)
        self.release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.release)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'pyproject.toml').write_text('[project]\nversion="1.2.3"\n')
        (self.root / 'agentops.example.toml').write_text(
            'repo = "example/upstream"\nimage = "headroom-agentops-worker:local"\npublish = false\n')
        self.dist = self.root / 'dist'
        self.dist.mkdir()
        for name in ('headroom_agentops-1.2.3-py3-none-any.whl', 'headroom_agentops-1.2.3.tar.gz'):
            (self.dist / name).write_bytes(b'synthetic release artifact')

    def test_release_assets_match_version_destination_and_checksums(self):
        self.release.prepare_release(self.root, self.dist, 'new-owner/new-repo', 'v1.2.3')
        config = tomllib.loads((self.dist / 'agentops.example.toml').read_text())
        self.assertEqual(config['image'], 'ghcr.io/new-owner/new-repo/worker:v1.2.3')
        self.assertEqual(config['repo'], 'OWNER/REPO')
        self.assertFalse(config['publish'])
        guide = (self.dist / 'INSTALL.md').read_text()
        self.assertIn('https://github.com/new-owner/new-repo/releases/download/v1.2.3/', guide)
        self.assertIn('docker pull ghcr.io/new-owner/new-repo/worker:v1.2.3', guide)
        self.assertIn('uv tool install', guide)
        lines = (self.dist / 'SHA256SUMS').read_text().splitlines()
        self.assertEqual(len(lines), 4)
        for line in lines:
            digest, name = line.split('  ', 1)
            self.assertEqual(digest, hashlib.sha256((self.dist / name).read_bytes()).hexdigest())

    def test_wrong_tag_cannot_produce_mislabeled_release_assets(self):
        with self.assertRaisesRegex(ValueError, 'version'):
            self.release.prepare_release(self.root, self.dist, 'owner/repo', 'v9.9.9')
        self.assertFalse((self.dist / 'INSTALL.md').exists())

    def test_missing_package_prevents_release_assets(self):
        (self.dist / 'headroom_agentops-1.2.3-py3-none-any.whl').unlink()
        with self.assertRaisesRegex(ValueError, 'wheel'):
            self.release.prepare_release(self.root, self.dist, 'owner/repo')

    def test_repository_cannot_inject_asset_urls_or_commands(self):
        with self.assertRaisesRegex(ValueError, 'Repository'):
            self.release.prepare_release(self.root, self.dist, 'owner/repo;echo injected')
