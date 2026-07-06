from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import frontend_compat


ROOT = Path(__file__).resolve().parents[1]


class FrontendCompatibilityTest(unittest.TestCase):
    def test_pinned_runtime_and_compatibility_asset_are_present(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("streamlit==1.58.0", requirements)
        self.assertTrue(frontend_compat.PATCH_BUNDLE.is_file())
        source = frontend_compat.PATCH_BUNDLE.read_text(encoding="utf-8")
        self.assertTrue(source.startswith("/* yanggumi-old-edge-compat-v2 */"))
        self.assertNotIn("}static{", source)

    def test_compatibility_frontend_is_installed_once_and_cache_busted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            static_root = Path(temp)
            js_root = static_root / "static" / "js"
            js_root.mkdir(parents=True)
            target = js_root / frontend_compat.BUNDLE_NAME
            target.write_text("stock frontend", encoding="utf-8")
            index = static_root / "index.html"
            index.write_text(
                f'<script type="module" src="./static/js/{frontend_compat.BUNDLE_NAME}"></script>',
                encoding="utf-8",
            )

            with (
                mock.patch.object(frontend_compat.metadata, "version", return_value="1.58.0"),
                mock.patch.object(frontend_compat, "streamlit_static_root", return_value=static_root),
            ):
                self.assertTrue(frontend_compat.ensure_streamlit_frontend_compatibility())
                self.assertFalse(frontend_compat.ensure_streamlit_frontend_compatibility())

            self.assertEqual(
                target.read_bytes(), frontend_compat.PATCH_BUNDLE.read_bytes()
            )
            self.assertIn(
                f"{frontend_compat.BUNDLE_NAME}?v={frontend_compat.CACHE_QUERY}",
                index.read_text(encoding="utf-8"),
            )

    def test_zoom_resistant_recent_card_layout_is_retained(self) -> None:
        source = (ROOT / "ui_components.py").read_text(encoding="utf-8")
        self.assertIn(
            "grid-template-columns:clamp(104px,7.1vw,136px) minmax(0,1fr) "
            "clamp(126px,8.85vw,170px)",
            source,
        )
        self.assertIn(".st-key-home_recent_grid .yg-score-row {{flex-wrap:nowrap", source)


if __name__ == "__main__":
    unittest.main()
