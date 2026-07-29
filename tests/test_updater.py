import hashlib
import json
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

    def test_delta_update_uses_remote_version_and_program_only_rollback(self):
        new_app = b"VALUE = 'new'\n"
        compare = {
            "status": "ahead",
            "commits": [{"commit": {"message": "feat: this must not invent 1.1.0"}}],
            "files": [{
                "filename": "app.py", "status": "modified",
                "sha": blob_sha(new_app), "changes": 1,
            }],
        }
        with (
            patch.object(updater, "_tag_commit", return_value="base"),
            patch.object(updater, "_head_commit", return_value="head"),
            patch.object(updater, "_remote_version", return_value="1.0.1"),
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
        self.assertEqual((self.root / "data" / "acgn.db").read_bytes(), b"changed-db")

    def test_fresh_110_install_does_not_invent_120(self):
        (self.root / "VERSION").write_text("1.1.0\n", encoding="utf-8")
        with (
            patch.object(updater, "_head_commit", return_value="v110-head"),
            patch.object(updater, "_remote_version", return_value="1.1.0"),
            patch.object(updater, "_tag_commit") as tag_commit,
            patch.object(updater, "_request_json") as request_json,
        ):
            self.assertEqual(updater.check_and_update(), 0)
        tag_commit.assert_not_called()
        request_json.assert_not_called()
        self.assertEqual((self.root / "VERSION").read_text(encoding="utf-8"), "1.1.0\n")
        state = json.loads(updater.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["version"], "1.1.0")
        self.assertEqual(state["commit"], "v110-head")

    def test_retired_state_commit_falls_back_to_matching_official_tag(self):
        new_app = b"VALUE = 'official-update'\n"
        updater.STATE_FILE.write_text(json.dumps({
            "version": "1.0.0",
            "commit": "retired-test-commit",
            "level": "patch",
        }), encoding="utf-8")
        diverged = {"status": "diverged", "files": []}
        official_compare = {
            "status": "ahead",
            "commits": [{"commit": {"message": "fix: official update"}}],
            "files": [{
                "filename": "app.py", "status": "modified",
                "sha": blob_sha(new_app), "changes": 1,
            }],
        }
        with (
            patch.object(updater, "_head_commit", return_value="official-head"),
            patch.object(updater, "_remote_version", return_value="1.0.1"),
            patch.object(updater, "_tag_commit", return_value="official-v100") as tag_commit,
            patch.object(updater, "_request_json", side_effect=[diverged, official_compare]) as request_json,
            patch.object(updater, "_download", return_value=new_app),
            patch.dict(os.environ, {"YANGGUMI_UPDATE_SKIP_PROMPT": "Y"}),
        ):
            self.assertEqual(updater.check_and_update(), 0)
        tag_commit.assert_called_once_with("1.0.0")
        self.assertEqual(request_json.call_count, 2)
        self.assertEqual((self.root / "app.py").read_bytes(), new_app)
        self.assertEqual((self.root / "data" / "acgn.db").read_bytes(), b"private-db")
        state = json.loads(updater.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["version"], "1.0.1")
        self.assertEqual(state["commit"], "official-head")

    def test_user_data_paths_are_never_remote_update_targets(self):
        protected = (
            "data/acgn.db",
            "data/acgn.db-wal",
            "covers/my-poster.jpg",
            "backgrounds/mine.png",
            "backups/snapshot.db",
            "exports/ratings.xlsx",
            "public_data.json",
            ".streamlit/secrets.toml",
            "static/daily_art/today.jpg",
            "static/share_assets/private-cover.jpg",
            "anywhere/private.sqlite3",
        )
        for path in protected:
            self.assertTrue(updater._protected(path), path)
        self.assertFalse(updater._protected("app.py"))
        self.assertFalse(updater._protected(".streamlit/config.toml"))

    def test_semantic_version_classification(self):
        self.assertEqual(updater._classify({"commits": [{"commit": {"message": "fix: bug"}}], "files": []})[0], "patch")
        self.assertEqual(updater._classify({"commits": [{"commit": {"message": "feat: new page"}}], "files": []})[0], "minor")
        self.assertEqual(updater._classify({"commits": [{"commit": {"message": "BREAKING CHANGE"}}], "files": []})[0], "major")
        self.assertEqual(updater._version_level("1.1.0", "1.1.1")[0], "patch")
        self.assertEqual(updater._version_level("1.1.0", "1.2.0")[0], "minor")


if __name__ == "__main__":
    unittest.main()
