from __future__ import annotations

import unittest
import re
from pathlib import Path

import daily_art
import ui_components


ROOT = Path(__file__).resolve().parents[1]


class OpenSourceSafetyTest(unittest.TestCase):
    def test_defaults_do_not_depend_on_the_original_computer(self) -> None:
        source_files = [
            ROOT / "app.py",
            ROOT / "bangumi_client.py",
            ROOT / "daily_art.py",
            ROOT / "share_public.py",
            ROOT / "ui_components.py",
            ROOT / "frontend_compat.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
        self.assertIsNone(re.search(r"[A-Z]:\\Users\\[^\\\"']+", combined))
        self.assertIsNone(re.search(r"192\.168\.\d{1,3}\.\d{1,3}", combined))
        self.assertNotIn("RCJbRue_", combined)

        self.assertIn("Pictures", str(daily_art.DEFAULT_LOCAL_ROOTS["portrait"]))
        self.assertIn("Pictures", str(daily_art.DEFAULT_LOCAL_ROOTS["wallpaper"]))
        self.assertIn("Pictures", str(ui_components.USER_WALLPAPER_DIR))

        compatibility_bundle = ROOT / "compat" / "streamlit-1.58.0" / "index.dkY5s53S.js"
        compatibility_source = compatibility_bundle.read_text(encoding="utf-8")
        self.assertNotIn("C:\\Users\\", compatibility_source)
        self.assertNotIn("192.168.", compatibility_source)

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
