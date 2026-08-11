from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import update_yanggumi as updater


class UpdateSnapshotRepairTests(unittest.TestCase):
    def test_snapshot_repairs_every_program_file_and_preserves_share_runtime_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "install"
            staging = Path(temp_dir) / "staging"
            root.mkdir()
            staging.mkdir()
            launcher = root / updater.MUTABLE_SHARE_LAUNCHER
            launcher.write_text(
                '@echo off\nset "YANGGUMI_PUBLIC_URL=https://example.test/?access=private"\n'
                'set "YANGGUMI_SHARE_LOCATOR=https://example.test/locator"\nOLD BODY\n',
                encoding="utf-8",
            )
            archive_buffer = io.BytesIO()
            with zipfile.ZipFile(archive_buffer, "w") as bundle:
                prefix = "Yang-gumi-Personal-Rating-Site-main/"
                bundle.writestr(prefix + "app.py", "NEW APP\n")
                bundle.writestr(
                    prefix + updater.MUTABLE_SHARE_LAUNCHER,
                    '@echo off\nset "YANGGUMI_PUBLIC_URL="\n'
                    'set "YANGGUMI_SHARE_LOCATOR="\nNEW BODY\n',
                )
                bundle.writestr(prefix + "data/acgn.db", b"private")
                bundle.writestr(prefix + "VERSION", "1.3.1\n")

            with mock.patch.object(updater, "ROOT", root), mock.patch.object(
                updater, "_download", return_value=archive_buffer.getvalue()
            ):
                applicable = updater._download_snapshot("head", staging)

            paths = {item["filename"] for item in applicable}
            self.assertEqual(paths, {"app.py", updater.MUTABLE_SHARE_LAUNCHER})
            self.assertEqual((staging / "app.py").read_text(encoding="utf-8"), "NEW APP\n")
            updated_launcher = (staging / updater.MUTABLE_SHARE_LAUNCHER).read_text(encoding="utf-8")
            self.assertIn('YANGGUMI_PUBLIC_URL=https://example.test/?access=private', updated_launcher)
            self.assertIn('YANGGUMI_SHARE_LOCATOR=https://example.test/locator', updated_launcher)
            self.assertIn("NEW BODY", updated_launcher)
            self.assertNotIn("OLD BODY", updated_launcher)
            self.assertFalse((staging / "data" / "acgn.db").exists())
            self.assertFalse((staging / "VERSION").exists())

    def test_removed_paths_keeps_only_unprotected_obsolete_program_files(self):
        changes = [
            {"filename": "obsolete.py", "status": "removed"},
            {"filename": "data/acgn.db", "status": "removed"},
            {"filename": "renamed.py", "previous_filename": "old_name.py", "status": "renamed"},
        ]
        removed = updater._removed_paths(changes, {"renamed.py"})
        self.assertEqual(
            removed,
            [
                {"filename": "obsolete.py", "previous_filename": None, "status": "removed"},
                {"filename": "old_name.py", "previous_filename": None, "status": "removed"},
            ],
        )

    def test_upgrade_repairs_stale_program_file_not_listed_by_compare(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "install"
            root.mkdir()
            (root / "VERSION").write_text("1.3.0\n", encoding="utf-8")
            (root / ".yanggumi-update-state.json").write_text(
                json.dumps({"version": "1.3.0", "commit": "old"}), encoding="utf-8"
            )
            (root / "app.py").write_text("VALUE = 'OLD APP'\n", encoding="utf-8")
            (root / "stale.py").write_text("VALUE = 'OLD STALE'\n", encoding="utf-8")
            data_dir = root / "data"
            data_dir.mkdir()
            database = data_dir / "acgn.db"
            database.write_bytes(b"PERSONAL DATA")

            def snapshot(_head: str, staging: Path):
                (staging / "app.py").write_text("VALUE = 'NEW APP'\n", encoding="utf-8")
                (staging / "stale.py").write_text("VALUE = 'NEW STALE'\n", encoding="utf-8")
                return [
                    {"filename": "app.py", "previous_filename": None, "status": "modified"},
                    {"filename": "stale.py", "previous_filename": None, "status": "modified"},
                ]

            compare = {"status": "ahead", "files": [{"filename": "VERSION", "status": "modified"}]}
            variables = {
                "ROOT": root,
                "VERSION_FILE": root / "VERSION",
                "STATE_FILE": root / ".yanggumi-update-state.json",
                "RESTORE_ROOT": root / "backups" / "update_restore_points",
                "REQUIRED_AFTER_UPDATE": ("app.py",),
            }
            with mock.patch.multiple(updater, **variables), mock.patch.object(
                updater, "_head_commit", return_value="new"
            ), mock.patch.object(updater, "_remote_version", return_value="1.3.1"), mock.patch.object(
                updater, "_request_json", return_value=compare
            ), mock.patch.object(updater, "_download_snapshot", side_effect=snapshot), mock.patch.object(
                updater, "_restart_running_site_if_requested"
            ), mock.patch.dict(os.environ, {"YANGGUMI_UPDATE_SKIP_PROMPT": "Y"}):
                result = updater.check_and_update(restart_running=False)

            self.assertEqual(result, 0)
            self.assertEqual((root / "stale.py").read_text(encoding="utf-8"), "VALUE = 'NEW STALE'\n")
            self.assertEqual(database.read_bytes(), b"PERSONAL DATA")
            state = json.loads((root / ".yanggumi-update-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["version"], "1.3.1")
            self.assertEqual(state["commit"], "new")


if __name__ == "__main__":
    unittest.main()
