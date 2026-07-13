from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import daily_art
import database as db


class PublicAndDailyArtTest(unittest.TestCase):
    def test_public_export_has_required_metadata_and_no_private_paths(self):
        payload = json.loads(db.export_json(True).decode("utf-8"))
        self.assertEqual(payload["export_meta"]["site_name"], "Yang-gumi")
        self.assertTrue(payload["export_meta"]["read_only"])
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("private_note", "resource_path", "cover_path", "raw_json", "E:\\\\"):
            self.assertNotIn(forbidden, serialized)
        for work in payload["works"]:
            self.assertIn("score_breakdown", work)
            self.assertIn("bangumi_tags", work)

    def test_live_public_app_opens_the_same_database_read_only(self):
        root = Path(__file__).parents[1]
        database_source = root.joinpath("database.py").read_text(encoding="utf-8")
        app_source = root.joinpath("app.py").read_text(encoding="utf-8")
        self.assertIn("mode=ro", database_source)
        self.assertIn("YANGGUMI_READ_ONLY", database_source)
        self.assertIn("YANGGUMI_READ_ONLY", app_source)
        self.assertIn('@st.fragment(run_every="10s")', app_source)
        self.assertIn("_watch_shared_database", app_source)
        self.assertIn("MAX(updated_at)", app_source)
        self.assertIn("您没有操作权限", app_source)
        self.assertIn('hidden_pages.add("新增条目")', app_source)

    def test_private_share_launcher_uses_token_and_live_read_only_app(self):
        root = Path(__file__).parents[1]
        source = root.joinpath("share_public.py").read_text(encoding="utf-8")
        self.assertIn("token_urlsafe", source)
        self.assertIn("app.py", source)
        self.assertIn("YANGGUMI_READ_ONLY", source)
        self.assertNotIn("export_json", source)
        self.assertIn("0.0.0.0", source)

    def test_read_only_batch_supports_owner_and_standalone_visitor(self):
        root = Path(__file__).parents[1]
        source = root.joinpath("启动只读分享.bat").read_text(encoding="utf-8")
        self.assertIn('if exist "%~dp0share_public.py" goto owner', source)
        self.assertIn("goto visitor", source)
        self.assertIn("$request.Proxy=$null", source)
        self.assertIn("--proxy-server=direct://", source)
        self.assertIn("192.168.81.1:8502", source)

    def test_private_tag_input_and_display_are_removed(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        self.assertNotIn('text_input("私人标签', source)
        self.assertNotIn('caption("私人标签', source)
        self.assertNotIn('["全部", "Bangumi", "私人标签"]', source)

    def test_manifest_skips_oversize_and_uses_cached_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            portrait = root / "portrait"; wallpaper = root / "wallpaper"
            portrait.mkdir(); wallpaper.mkdir()
            Image.new("RGB", (300, 500), "red").save(portrait / "ok.jpg")
            old = (daily_art.LOCAL_ROOTS, daily_art.MANIFEST_PATH, daily_art.ASSET_DIR, daily_art.MAX_FILE_SIZE)
            daily_art.MAX_FILE_SIZE = (portrait / "ok.jpg").stat().st_size + 128
            (portrait / "too-big.jpg").write_bytes(b"x" * (daily_art.MAX_FILE_SIZE + 1))
            daily_art.LOCAL_ROOTS = {"portrait": portrait, "wallpaper": wallpaper}
            daily_art.MANIFEST_PATH = root / "manifest.json"
            daily_art.ASSET_DIR = root / "assets"
            try:
                built = daily_art.rebuild_manifest()
                self.assertEqual(len(built["items"]), 1)
                loaded = daily_art.load_manifest()
                self.assertEqual(len(loaded["items"]), 1)
                self.assertEqual(loaded["items"][0]["type"], "portrait")
                self.assertIn("refresh_slot", built)
                self.assertEqual(built["scan_stats"]["portrait"]["files_checked"], 2)
                self.assertEqual(built["scan_stats"]["portrait"]["supported"], 2)
                self.assertEqual(built["scan_stats"]["portrait"]["accepted"], 1)
                self.assertEqual(built["scan_stats"]["portrait"]["oversized"], 1)
            finally:
                daily_art.LOCAL_ROOTS, daily_art.MANIFEST_PATH, daily_art.ASSET_DIR, daily_art.MAX_FILE_SIZE = old

    def test_manifest_finds_images_in_deeply_nested_new_computer_folders(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            portrait = root / "portrait"
            nested = portrait / "artist" / "series" / "chapter" / "selected"
            wallpaper = root / "wallpaper"
            nested.mkdir(parents=True)
            wallpaper.mkdir()
            Image.new("RGB", (300, 500), "purple").save(nested / "deep.jpg")
            old = (dict(daily_art.LOCAL_ROOTS), daily_art.MANIFEST_PATH, daily_art.ASSET_DIR)
            daily_art.LOCAL_ROOTS.clear()
            daily_art.LOCAL_ROOTS.update({"portrait": portrait, "wallpaper": wallpaper})
            daily_art.MANIFEST_PATH = root / "manifest.json"
            daily_art.ASSET_DIR = root / "assets"
            try:
                built = daily_art.rebuild_manifest("portrait")
                self.assertEqual(len(built["items"]), 1)
                self.assertEqual(Path(built["items"][0]["path"]).name, "deep.jpg")
                self.assertTrue((daily_art.ASSET_DIR / Path(built["items"][0]["asset"]).name).is_file())
            finally:
                daily_art.LOCAL_ROOTS.clear()
                daily_art.LOCAL_ROOTS.update(old[0])
                daily_art.MANIFEST_PATH, daily_art.ASSET_DIR = old[1], old[2]

    def test_selected_folder_accepts_supported_images_regardless_of_orientation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            portrait = root / "portrait"; wallpaper = root / "wallpaper"
            portrait.mkdir(); wallpaper.mkdir()
            Image.new("RGB", (900, 400), "orange").save(portrait / "wide-in-portrait.bmp")
            Image.new("RGB", (400, 900), "cyan").save(wallpaper / "tall-in-wallpaper.gif")
            old = (dict(daily_art.LOCAL_ROOTS), daily_art.MANIFEST_PATH, daily_art.ASSET_DIR)
            daily_art.LOCAL_ROOTS.clear()
            daily_art.LOCAL_ROOTS.update({"portrait": portrait, "wallpaper": wallpaper})
            daily_art.MANIFEST_PATH = root / "manifest.json"
            daily_art.ASSET_DIR = root / "assets"
            try:
                built = daily_art.rebuild_manifest()
                self.assertEqual({item["type"] for item in built["items"]}, {"portrait", "wallpaper"})
                sizes = {}
                for item in built["items"]:
                    with Image.open(daily_art.ASSET_DIR / Path(item["asset"]).name) as image:
                        sizes[item["type"]] = image.size
                self.assertEqual(sizes, {"portrait": (720, 1080), "wallpaper": (1280, 720)})
                self.assertEqual(built["scan_stats"]["portrait"]["accepted"], 1)
                self.assertEqual(built["scan_stats"]["wallpaper"]["accepted"], 1)
            finally:
                daily_art.LOCAL_ROOTS.clear()
                daily_art.LOCAL_ROOTS.update(old[0])
                daily_art.MANIFEST_PATH, daily_art.ASSET_DIR = old[1], old[2]

    def test_homepage_asset_crops_toward_detected_focus(self):
        source = Image.new("RGB", (400, 200), "blue")
        for x in range(200, 400):
            for y in range(200):
                source.putpixel((x, y), (255, 0, 0))
        cropped = daily_art._homepage_asset(source, "portrait", "80% 50%")
        self.assertEqual(cropped.size, (720, 1080))
        red, green, blue = cropped.getpixel((360, 540))
        self.assertGreater(red, 220)
        self.assertLess(green, 30)
        self.assertLess(blue, 30)

    def test_daily_art_folder_picker_buttons_are_exposed(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        self.assertIn('key="daily_art_choose_portrait_folder"', source)
        self.assertIn('key="daily_art_choose_wallpaper_folder"', source)
        self.assertNotIn('key="daily_art_source_selector"', source)

    def test_wallpaper_luck_badge_and_rocket_animation_are_present(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        self.assertIn('if active_kind == "wallpaper":', source)
        self.assertIn("运气爆棚", source)
        self.assertIn("yg-art-rocket-flight", source)
        self.assertIn("title_col, luck_col, source_col, button_col", source)

    def test_daily_art_source_folders_are_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            portrait = root / "portrait"; wallpaper = root / "wallpaper"
            portrait.mkdir(); wallpaper.mkdir()
            old = (daily_art.SETTINGS_PATH, dict(daily_art.LOCAL_ROOTS))
            daily_art.SETTINGS_PATH = root / "settings.json"
            try:
                daily_art.set_source_folder("portrait", portrait)
                daily_art.set_source_folder("wallpaper", wallpaper)
                loaded = daily_art.load_source_folders()
                self.assertEqual(loaded["portrait"], portrait.resolve())
                self.assertEqual(loaded["wallpaper"], wallpaper.resolve())
            finally:
                daily_art.SETTINGS_PATH = old[0]
                daily_art.LOCAL_ROOTS.clear()
                daily_art.LOCAL_ROOTS.update(old[1])

    def test_windows_folder_chooser_result_is_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = root / "selected"
            selected.mkdir()
            old = (daily_art.SETTINGS_PATH, dict(daily_art.LOCAL_ROOTS))
            daily_art.SETTINGS_PATH = root / "settings.json"
            completed = mock.Mock(returncode=0, stdout=str(selected), stderr="")
            try:
                with mock.patch.object(daily_art.os, "name", "nt"), mock.patch.object(
                    daily_art.subprocess, "run", return_value=completed
                ) as run:
                    result = daily_art.choose_source_folder("portrait")
                self.assertEqual(result, selected.resolve())
                self.assertEqual(daily_art.load_source_folders()["portrait"], selected.resolve())
                self.assertEqual(run.call_args.args[0][0], "powershell.exe")
                self.assertIn("-STA", run.call_args.args[0])
            finally:
                daily_art.SETTINGS_PATH = old[0]
                daily_art.LOCAL_ROOTS.clear()
                daily_art.LOCAL_ROOTS.update(old[1])

    def test_refreshing_one_source_preserves_the_other_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            portrait = root / "portrait"; wallpaper = root / "wallpaper"
            portrait.mkdir(); wallpaper.mkdir()
            Image.new("RGB", (300, 500), "red").save(portrait / "portrait.jpg")
            Image.new("RGB", (500, 300), "blue").save(wallpaper / "wallpaper.jpg")
            old = (dict(daily_art.LOCAL_ROOTS), daily_art.MANIFEST_PATH, daily_art.ASSET_DIR)
            daily_art.LOCAL_ROOTS.clear()
            daily_art.LOCAL_ROOTS.update({"portrait": portrait, "wallpaper": wallpaper})
            daily_art.MANIFEST_PATH = root / "manifest.json"
            daily_art.ASSET_DIR = root / "assets"
            try:
                daily_art.rebuild_manifest()
                wallpaper_assets = {
                    item["asset"] for item in daily_art.load_manifest()["items"] if item["type"] == "wallpaper"
                }
                Image.new("RGB", (320, 520), "green").save(portrait / "new.jpg")
                refreshed = daily_art.rebuild_manifest("portrait")
                self.assertEqual(
                    {item["asset"] for item in refreshed["items"] if item["type"] == "wallpaper"},
                    wallpaper_assets,
                )
                self.assertEqual(sum(item["type"] == "portrait" for item in refreshed["items"]), 2)
            finally:
                daily_art.LOCAL_ROOTS.clear()
                daily_art.LOCAL_ROOTS.update(old[0])
                daily_art.MANIFEST_PATH, daily_art.ASSET_DIR = old[1], old[2]


if __name__ == "__main__":
    unittest.main()
