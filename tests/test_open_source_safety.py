from __future__ import annotations

import unittest
from pathlib import Path

import daily_art
import ui_components


ROOT = Path(__file__).resolve().parents[1]


class OpenSourceSafetyTest(unittest.TestCase):
    def test_defaults_do_not_depend_on_the_original_computer(self) -> None:
        forbidden = ("E:\\图片", "C:\\Users\\Administrator", "192.168.79.118")
        source_files = [
            ROOT / "app.py",
            ROOT / "bangumi_client.py",
            ROOT / "daily_art.py",
            ROOT / "share_public.py",
            ROOT / "ui_components.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
        for value in forbidden:
            self.assertNotIn(value, combined)

        self.assertIn("Pictures", str(daily_art.DEFAULT_LOCAL_ROOTS["portrait"]))
        self.assertIn("Pictures", str(daily_art.DEFAULT_LOCAL_ROOTS["wallpaper"]))
        self.assertIn("Pictures", str(ui_components.USER_WALLPAPER_DIR))

    def test_private_runtime_state_is_ignored(self) -> None:
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for required in (
            "data/*",
            "backups/*",
            "exports/*",
            "static/daily_art/",
            ".streamlit/secrets.toml",
        ):
            self.assertIn(required, rules)

    def test_open_source_default_cover_is_present(self) -> None:
        self.assertTrue((ROOT / "covers" / "default.svg").is_file())


if __name__ == "__main__":
    unittest.main()
