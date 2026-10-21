"""Tests for scripts/scan_secrets.py. Fake secrets are built at runtime so this
file itself never matches the scanner's patterns."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import scan_secrets  # noqa: E402


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan_text(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return scan_secrets.scan([path])

    def test_kaggle_json_key_found(self):
        fake = '{"username": "someone", "key": "' + "0123456789abcdef" * 2 + '"}'
        self.assertTrue(self.scan_text("creds.txt", fake))

    def test_kaggle_access_token_found(self):
        self.assertTrue(self.scan_text("notes.md", "token: " + "KGAT_" + "a1" * 12))

    def test_env_assignment_found(self):
        self.assertTrue(self.scan_text("config.txt", "KAGGLE_" + "KEY=" + "x" * 32))

    def test_forbidden_file_name_found(self):
        self.assertTrue(self.scan_text("kaggle.json", "{}"))

    def test_clean_files_pass(self):
        self.assertEqual(self.scan_text("agent.yaml", "name: swe_agent\nKAGGLE_KEY=\n"), [])
        self.assertEqual(scan_secrets.scan([REPO_ROOT / ".env.example"]), [])


if __name__ == "__main__":
    unittest.main()
