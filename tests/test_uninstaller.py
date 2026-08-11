from __future__ import annotations

import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import uninstall_yanggumi as uninstaller


class UninstallerTest(unittest.TestCase):
    def _database(self, path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE works (id INTEGER PRIMARY KEY, title TEXT)")
            connection.execute("INSERT INTO works(title) VALUES ('保留的数据')")
            connection.commit()
        finally:
            connection.close()

    def test_database_is_saved_outside_installation_and_stays_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "install" / "data" / "acgn.db"
            destination = root / "saved"
            source.parent.mkdir(parents=True)
            self._database(source)

            saved = uninstaller.backup_database_to(destination, source)

            self.assertTrue(saved.is_file())
            connection = sqlite3.connect(saved)
            try:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT title FROM works").fetchone()[0], "保留的数据")
            finally:
                connection.close()

    def test_cleanup_script_is_guarded_by_project_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Yang-gumi"
            root.mkdir()
            for marker in uninstaller.APP_MARKERS:
                (root / marker).write_text("marker", encoding="utf-8")
            validated = uninstaller.validate_install_root(root)
            script = uninstaller.cleanup_script(validated, 1234)
            self.assertIn("Wait-Process -Id 1234", script)
            self.assertIn("app.py", script)
            self.assertIn("start_yanggumi.py", script)
            self.assertIn("Get-CimInstance Win32_Process", script)
            self.assertIn("Stop-Process", script)
            self.assertIn("Remove-Item -LiteralPath $root -Recurse -Force", script)

    def test_cleanup_script_removes_a_marked_temporary_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sandbox = Path(temp)
            root = sandbox / "Yang-gumi-test-install"
            root.mkdir()
            for marker in uninstaller.APP_MARKERS:
                (root / marker).write_text("marker", encoding="utf-8")
            script_path = sandbox / "cleanup.ps1"
            script_path.write_text(
                uninstaller.cleanup_script(uninstaller.validate_install_root(root), 999999),
                encoding="utf-8-sig",
            )

            completed = subprocess.run(
                [
                    "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(script_path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(root.exists())
            self.assertFalse(script_path.exists())

    def test_windows_batch_uses_installed_virtual_environment(self) -> None:
        root = Path(__file__).parents[1]
        batch = root.joinpath("卸载 Yang-gumi.bat").read_text(encoding="utf-8")
        self.assertIn('.venv\\Scripts\\pythonw.exe', batch)
        self.assertIn("uninstall_yanggumi.py", batch)


if __name__ == "__main__":
    unittest.main()
