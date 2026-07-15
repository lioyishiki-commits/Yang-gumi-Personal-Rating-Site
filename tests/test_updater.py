import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_yanggumi as updater


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name, content in {
            "app.py": "VALUE = 'old'\n",
            "database.py": "VALUE = 'db'\n",
            "启动 Yang-gumi.bat": "@echo off\n",
            "update_yanggumi.py": "# updater\n",
            "VERSION": "1.0.0\n",
        }.items():
            (self.root / name).write_text(content, encoding="utf-8")
        data = self.root / "data"
        data.mkdir()
        (data / "acgn.db").write_bytes(b"private-db")
        self.patches = [
            patch.object(updater, "ROOT", self.root),
            patch.object(updater, "VERSION_FILE", self.root / "VERSION"),
            patch.object(updater, "STATE_FILE", self.root / ".yanggumi-update-state.json"),
            patch.object(updater, "RESTORE_ROOT", self.root / "backups" / "update_restore_points"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_delta_update_versions_backs_up_and_rolls_back(self):
        new_app = b"VALUE = 'new'\n"
        compare = {
            "status": "ahead",
            "commits": [{"commit": {"message": "fix: save ratings"}}],
            "files": [{
                "filename": "app.py", "status": "modified",
                "sha": blob_sha(new_app), "changes": 1,
            }],
        }
        with (
            patch.object(updater, "_tag_commit", return_value="base"),
            patch.object(updater, "_head_commit", return_value="head"),
            patch.object(updater, "_request_json", return_value=compare),
            patch.object(updater, "_download", return_value=new_app),
            patch.dict(os.environ, {"YANGGUMI_UPDATE_SKIP_PROMPT": "Y"}),
        ):
            self.assertEqual(updater.check_and_update(), 0)
        self.assertEqual((self.root / "app.py").read_bytes(), new_app)
        self.assertEqual((self.root / "VERSION").read_text(encoding="utf-8").strip(), "1.0.1")
        self.assertIn('"commit": "head"', updater.STATE_FILE.read_text(encoding="utf-8"))
        backups = list(updater.RESTORE_ROOT.glob("*/manifest.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0].parent / "data_snapshot" / "acgn.db").read_bytes(), b"private-db")
        (self.root / "data" / "acgn.db").write_bytes(b"changed-db")
        with patch.dict(os.environ, {"YANGGUMI_UPDATE_SKIP_PROMPT": "Y"}):
            self.assertEqual(updater.rollback_latest(), 0)
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "VALUE = 'old'\n")
        self.assertEqual((self.root / "VERSION").read_text(encoding="utf-8"), "1.0.0\n")
        self.assertEqual((self.root / "data" / "acgn.db").read_bytes(), b"private-db")

    def test_no_update_changes_nothing(self):
        with (
            patch.object(updater, "_tag_commit", return_value="same"),
            patch.object(updater, "_head_commit", return_value="same"),
        ):
            self.assertEqual(updater.check_and_update(), 0)
        self.assertFalse(updater.STATE_FILE.exists())
        self.assertEqual((self.root / "VERSION").read_text(encoding="utf-8"), "1.0.0\n")

    def test_semantic_version_classification(self):
        self.assertEqual(updater._classify({"commits": [{"commit": {"message": "fix: bug"}}], "files": []})[0], "patch")
        self.assertEqual(updater._classify({"commits": [{"commit": {"message": "feat: new page"}}], "files": []})[0], "minor")
        self.assertEqual(updater._classify({"commits": [{"commit": {"message": "BREAKING CHANGE"}}], "files": []})[0], "major")


if __name__ == "__main__":
    unittest.main()
