from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
import ui_components as components
import ui_settings as settings


class Upload:
    name = "soft-background.png"
    def getbuffer(self):
        return memoryview(b"small-local-image")


class UiSystemTest(unittest.TestCase):
    def test_corrupt_settings_recovers_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ui_settings.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.object(settings, "SETTINGS_PATH", path), patch.object(settings, "DATA_DIR", path.parent):
                loaded = settings.load_settings()
            self.assertEqual(loaded["home"]["background_mode"], "none")
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["global"]["enable_motion"])

    def test_local_upload_and_custom_url_render_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(settings, "BACKGROUNDS_DIR", Path(temp_dir)):
                saved = settings.save_uploaded_background(Upload(), "home")
                self.assertTrue(Path(saved).exists())
                self.assertEqual(Path(saved).read_bytes(), b"small-local-image")
        page = {"background_enabled": True, "background_mode": "custom_url", "background_url": "https://example.com/bg.jpg", "overlay_opacity": .8, "blur": 2, "brightness": .9, "fixed": True}
        with patch.object(components.st, "markdown") as markdown:
            components.render_page_background(page, [])
        markdown.assert_not_called()
        broken = {**page, "background_mode": "custom_image", "background_path": "Z:/missing.jpg"}
        with patch.object(components.st, "markdown") as markdown:
            components.render_page_background(broken, [])
        markdown.assert_not_called()

    def test_watched_anime_posters_are_prioritized_and_limited(self) -> None:
        works = [
            {"id": 1, "title": "游戏", "type": "游戏", "status": "已看", "bangumi_image_url": "https://img/1.jpg", "score_total": 10},
            {"id": 2, "title": "动画 A", "type": "动画", "status": "已看", "bangumi_image_url": "https://img/2.jpg", "score_total": 8},
            {"id": 3, "title": "动画 B", "type": "动画", "status": "想重看", "bangumi_image_url": "https://img/3.jpg", "score_total": 9},
        ]
        pool = components.poster_pool(works, 2)
        self.assertEqual([item["id"] for item in pool], [3, 2])

    def test_motion_can_be_fully_disabled(self) -> None:
        config = copy.deepcopy(settings.DEFAULT_SETTINGS)
        config["global"].update({"enable_motion": False, "animation_strength": "off"})
        with patch.object(components.st, "markdown") as markdown:
            components.inject_css(config)
        css = markdown.call_args.args[0]
        self.assertIn("animation:none!important", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_top_fifty_posters_keep_original_color(self) -> None:
        config = copy.deepcopy(settings.DEFAULT_SETTINGS)
        with patch.object(components.st, "markdown") as markdown:
            components.inject_css(config)
        css = markdown.call_args.args[0]
        selector = '[class*="st-key-ranking_fifty_card_"] [data-testid="stImage"] img'
        rule = next(rule for rule in css.split("}") if selector in rule)
        self.assertIn("filter:none", rule)
        self.assertNotIn("saturate(", rule)

    def test_old_edge_native_form_controls_are_forced_dark(self) -> None:
        config = copy.deepcopy(settings.DEFAULT_SETTINGS)
        with patch.object(components.st, "markdown") as markdown:
            components.inject_css(config)
        css = markdown.call_args.args[0]
        self.assertIn("color-scheme:dark!important", css)
        self.assertIn('[data-testid="stNumberInput"] button', css)
        self.assertIn('input:not([type="checkbox"]):not([type="radio"])', css)
        self.assertIn('[data-testid="stExpander"] summary', css)
        self.assertIn('[data-testid="stFileUploaderDropzone"]', css)
        self.assertIn('[data-testid="stBaseButton-segmented_control"]', css)
        self.assertIn('[data-testid="stBaseButton-segmented_controlActive"]', css)
        segmented_rule = next(
            rule for rule in css.split("}")
            if '[data-testid="stBaseButton-segmented_control"]' in rule and ":hover" not in rule
        )
        self.assertIn("background:#0e1117!important", segmented_rule)
        self.assertIn("-webkit-text-fill-color:#e7e7e9!important", segmented_rule)

    def test_background_renderer_is_fully_disabled(self) -> None:
        page = {
            "background_enabled": True, "background_mode": "auto_poster_blur",
            "overlay_opacity": .8, "blur": 2, "brightness": .9, "fixed": True,
        }
        posters = [
            {"_poster_src": "https://img/one.jpg", "score_total": 9, "finish_date": "2026-01-01"},
            {"_poster_src": "https://img/two.jpg", "score_total": 8, "finish_date": "2025-01-01"},
        ]
        with patch.object(components, "_local_gallery_sources", return_value=[]), patch.object(components.st, "markdown") as markdown:
            components.render_page_background(page, posters)
        markdown.assert_not_called()

    def test_background_display_profiles_and_off_mode(self) -> None:
        self.assertEqual(components.BACKGROUND_MODE, "off")
        self.assertEqual(set(components.BACKGROUND_PROFILES), {"soft", "contain", "corner", "off"})
        self.assertLessEqual(components.background_profile("soft")["opacity"], .22)
        self.assertEqual(components.background_profile("soft")["opacity"], .10)
        self.assertEqual(components.background_profile("contain")["size"], "min(80vw, 1440px) auto")
        self.assertEqual(components.background_profile("corner")["position"], "right bottom")
        page = {"background_enabled": True, "background_mode": "custom_url", "background_url": "https://example.com/bg.jpg"}
        with patch.object(components, "BACKGROUND_MODE", "off"), patch.object(components.st, "markdown") as markdown:
            components.render_page_background(page, [])
        markdown.assert_not_called()

    def test_season_windows_compare_the_same_quarter_only(self) -> None:
        works = [
            {"title": "当季", "type": "动画", "status": "在看", "release_date": "2026-04-10"},
            {"title": "五年前同季", "type": "动画", "status": "已看", "release_date": "2021-06-01"},
            {"title": "五年前异季", "type": "动画", "status": "已看", "release_date": "2021-01-01"},
            {"title": "未观看", "type": "动画", "status": "想看", "release_date": "2016-05-01"},
        ]
        groups = components.seasonal_anime_groups(works, date(2026, 6, 19))
        self.assertEqual([group["year"] for group in groups], [2026, 2021, 2016, 2006])
        self.assertTrue(all(group["season_code"] == "Q2" for group in groups))
        self.assertEqual([work["title"] for work in groups[1]["works"]], ["五年前同季"])
        self.assertEqual([work["title"] for work in groups[2]["works"]], ["未观看"])

    def test_season_memory_prefers_local_seasonal_poster(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            poster = root / "static" / "seasonal_posters" / "2026_Q3" / "569116.jpg"
            poster.parent.mkdir(parents=True)
            poster.write_bytes(b"poster")
            work = {"bangumi_id": 569116, "bangumi_image_url": "https://img.test/remote.jpg"}
            with patch.object(components, "ROOT", root):
                source = components._season_memory_cover_src(work, 2026, "Q3")
        self.assertEqual(source, "/app/static/seasonal_posters/2026_Q3/569116.jpg")

    def test_season_memory_transitions_use_the_same_real_time(self) -> None:
        seven_css, _ = components._season_memory_animation(1, 7, "seven")
        two_css, _ = components._season_memory_animation(1, 2, "two")
        seven_start = 1 / 7 * 100
        two_start = 1 / 2 * 100
        seven_before = seven_start - components.SEASON_MEMORY_TRANSITION_HALF_SECONDS / 70 * 100
        two_before = two_start - components.SEASON_MEMORY_TRANSITION_HALF_SECONDS / 20 * 100
        self.assertIn(f"{seven_before:.3f}%", seven_css)
        self.assertIn(f"{two_before:.3f}%", two_css)
        self.assertAlmostEqual((seven_start - seven_before) / 100 * 70, 0.3)
        self.assertAlmostEqual((two_start - two_before) / 100 * 20, 0.3)

    def test_first_season_memory_is_visible_immediately(self) -> None:
        css, _ = components._season_memory_animation(0, 7, "first")
        self.assertIn("@keyframes first{0%,", css)
        self.assertIn("{opacity:1;transform:translateY(0);pointer-events:auto}", css)

    def test_season_memory_refresh_restarts_all_windows_together(self) -> None:
        groups = [
            {
                "year": 2026 - years_ago,
                "years_ago": years_ago,
                "season_code": "Q3",
                "season_month_label": "7月番",
                "works": [
                    {"title": f"作品 {years_ago}-1", "type": "动画"},
                    {"title": f"作品 {years_ago}-2", "type": "动画"},
                ],
            }
            for years_ago in (0, 5, 10, 20)
        ]
        with (
            patch.object(components, "seasonal_anime_groups", return_value=groups),
            patch.object(components.secrets, "token_hex", return_value="sharedrun"),
            patch.object(components, "_season_memory_cover_src", return_value="/poster.jpg"),
            patch.object(components.st, "markdown") as markdown,
        ):
            components.render_season_time_windows([])
        rendered = markdown.call_args.args[0]
        for group_index in range(4):
            self.assertIn(f"yg-season-sharedrun-{group_index}-0", rendered)

    def test_real_time_season_boundaries(self) -> None:
        cases = [
            (datetime(2026, 1, 1, 0, 0, 0), "Q1", "1月番"),
            (datetime(2026, 3, 31, 23, 59, 59), "Q1", "1月番"),
            (datetime(2026, 4, 1, 0, 0, 0), "Q2", "4月番"),
            (datetime(2026, 6, 30, 23, 59, 59), "Q2", "4月番"),
            (datetime(2026, 7, 1, 0, 0, 0), "Q3", "7月番"),
            (datetime(2026, 9, 30, 23, 59, 59), "Q3", "7月番"),
            (datetime(2026, 10, 1, 0, 0, 0), "Q4", "10月番"),
            (datetime(2026, 12, 31, 23, 59, 59), "Q4", "10月番"),
            (datetime(2027, 1, 1, 0, 0, 0), "Q1", "1月番"),
        ]
        for moment, code, label in cases:
            season = components.get_current_season_by_real_time(moment)
            self.assertEqual((season["season_code"], season["season_month_label"]), (code, label))

    def test_wallpaper_sampler_skips_portrait_and_outputs_16_by_10(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gallery = root / "wallpapers"; cache = root / "cache"
            gallery.mkdir()
            Image.new("RGB", (400, 900), "red").save(gallery / "portrait.jpg")
            Image.new("RGB", (1600, 900), "blue").save(gallery / "landscape.jpg")
            with patch.object(components, "USER_WALLPAPER_DIR", gallery), patch.object(components, "RUNTIME_GALLERY_DIR", cache), patch.object(components.st, "session_state", {}):
                sources = components._local_gallery_sources(1)
            self.assertEqual(len(sources), 1)
            generated = next(cache.glob("*.jpg"))
            with Image.open(generated) as image:
                self.assertEqual(image.size, (1440, 900))

    def test_ui_settings_are_not_in_public_export(self) -> None:
        exported = db.export_json(public=True).decode("utf-8")
        self.assertNotIn("ui_settings", exported)
        self.assertNotIn("background_path", exported)


if __name__ == "__main__":
    unittest.main(verbosity=2)
