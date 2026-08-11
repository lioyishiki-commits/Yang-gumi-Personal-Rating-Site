from __future__ import annotations

import gzip
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

from PIL import Image

import daily_art
import database as db
import share_fast_server
import share_public
import share_static_export


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
        self.assertIn("ContextVar", database_source)
        self.assertIn("set_read_only_mode", database_source)
        self.assertIn("YANGGUMI_READ_ONLY", database_source)
        self.assertIn("YANGGUMI_READ_ONLY", app_source)
        self.assertIn('@st.fragment(run_every="10s")', app_source)
        self.assertIn("_watch_shared_database", app_source)
        self.assertIn("IntersectionObserver", app_source)
        self.assertIn("MutationObserver", app_source)
        self.assertIn("data-yg-src", app_source)
        self.assertIn("if (!root || typeof root !== 'object') return;", app_source)
        self.assertIn("const observeRoot = doc.body || doc.documentElement;", app_source)
        self.assertLess(
            app_source.index("ranking_view = st.empty()", app_source.index("def render_bangumi_ranking_browser")),
            app_source.index("bgm.ranked_browser_subject_window", app_source.index("def render_bangumi_ranking_browser")),
        )
        self.assertIn("ranking_view.empty()", app_source)
        self.assertIn("MAX(updated_at)", app_source)
        self.assertIn("您没有操作权限", app_source)
        for page in ("首页", "条目库", "新增条目", "Bangumi", "排行榜", "评分对比", "标签筛选", "评分设置", "数据管理"):
            self.assertIn(f'"{page}"', app_source)
        self.assertIn("visible = list(READ_ONLY_NAV_PAGES) if READ_ONLY_MODE", app_source)

    def test_private_share_runs_the_existing_readonly_ui_on_loopback(self):
        root = Path(__file__).parents[1]
        control_source = root.joinpath("share_control.py").read_text(encoding="utf-8")
        share_source = root.joinpath("share_public.py").read_text(encoding="utf-8")
        self.assertIn("INTERACTIVE_SHARE_SCRIPT", control_source)
        self.assertIn('"--managed"', control_source)
        self.assertIn("streamlit_server_ready(port=MAIN_APP_PORT)", share_source)
        command = share_public.streamlit_command(18647)
        self.assertIn(str(root / "app.py"), command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("--server.enableWebsocketCompression", command)
        self.assertIn("--server.fileWatcherType", command)
        self.assertNotIn("share_fast_server.py", " ".join(command))
        proxy = share_public.proxy_command(18648, 18647)
        self.assertIn(str(root / "share_proxy_server.py"), proxy)

    def test_homepage_only_preloads_visible_and_adjacent_season_posters(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        warm_start = source.index("function warmPosterCache()")
        warm_end = source.index("function signature(item)", warm_start)
        warm_source = source[warm_start:warm_end]
        self.assertIn("displaySlots()", warm_source)
        self.assertIn("current - 3", warm_source)
        self.assertIn("current + 3", warm_source)
        self.assertNotIn("items.forEach", warm_source)
        self.assertNotIn("link.rel = 'preload'", warm_source)

    def test_homepage_visible_season_posters_retry_and_use_remote_fallback(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        loader_start = source.index("function loadPoster(card, item)")
        loader_end = source.index("function animationClass", loader_start)
        loader_source = source[loader_start:loader_end]
        self.assertIn("new Set([item.image, item.remote_image].filter(Boolean))", loader_source)
        self.assertIn("poster.replaceChildren(img)", loader_source)
        self.assertIn("img.onload = () => settle(true)", loader_source)
        self.assertIn("img.onerror = () => settle(false)", loader_source)
        self.assertIn("window.setTimeout(() => settle(false), 6000)", loader_source)
        self.assertIn("retryUrl(source)", loader_source)
        self.assertNotIn("cloneNode(false)", loader_source)

    def test_main_launcher_enables_compression_and_disables_file_watching(self):
        source = Path(__file__).parents[1].joinpath("start_yanggumi.py").read_text(encoding="utf-8")
        self.assertIn('"--server.enableWebsocketCompression", "true"', source)
        self.assertIn('"--server.fileWatcherType", "none"', source)

    def test_modern_share_frontend_is_downleveled_for_old_edge(self):
        root = Path(__file__).parents[1]
        bundles = list(
            root.joinpath("tools", "streamlit_modern_compat", "streamlit", "static", "static", "js").glob("index.*.js")
        )
        self.assertEqual(len(bundles), 1)
        source = bundles[0].read_text(encoding="utf-8")
        self.assertNotIn("}static{", source)
        self.assertTrue(source.startswith(share_public.LEGACY_FRONTEND_MARKER))

    def test_legacy_frontend_polyfills_are_applied_once_without_duplicate_module_url(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            static_root = runtime / "streamlit" / "static"
            js_root = static_root / "static" / "js"
            js_root.mkdir(parents=True)
            bundle = js_root / "index.test.js"
            bundle.write_text(
                'Object.hasOwn(value, key);this.websocket=new WebSocket(n,["streamlit",...t]);',
                encoding="utf-8",
            )
            index = static_root / "index.html"
            index.write_text(
                '<script type="module" src="./static/js/index.test.js"></script>',
                encoding="utf-8",
            )

            self.assertTrue(share_public.ensure_legacy_frontend_compatibility(runtime))
            self.assertFalse(share_public.ensure_legacy_frontend_compatibility(runtime))
            patched = bundle.read_text(encoding="utf-8")
            self.assertTrue(patched.startswith(share_public.LEGACY_FRONTEND_MARKER))
            self.assertIn("Object.prototype.hasOwnProperty.call", patched)
            self.assertIn("constructor.prototype.at", patched)
            self.assertIn("String.prototype.replaceAll", patched)
            self.assertIn("AbortSignal.timeout", patched)
            self.assertIn("wormhole", patched)
            self.assertNotIn(share_public.STREAMLIT_WEBSOCKET_CONSTRUCTOR, patched)
            self.assertIn('src="./static/js/index.test.js"', index.read_text(encoding="utf-8"))
            self.assertNotIn("?v=", index.read_text(encoding="utf-8"))

    def test_public_share_prefers_project_compatibility_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            runtime.joinpath("streamlit").mkdir()
            with mock.patch.object(share_public, "COMPAT_RUNTIME", runtime):
                env = share_public.streamlit_environment("test-token")
            self.assertNotIn("YANGGUMI_SHARE_TOKEN", env)
            self.assertNotIn("YANGGUMI_READ_ONLY", env)
            self.assertEqual(env["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"], "false")
            self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(runtime))

    def test_interactive_share_launcher_is_ascii_and_uses_readonly_app(self):
        root = Path(__file__).parents[1]
        content = root.joinpath("启动只读分享.bat").read_bytes()
        source = content.decode("ascii")
        self.assertIn("share_public.py", source)
        self.assertIn("SHIKISHARE_DRY_RUN", source)
        self.assertIn("goto visitor", source)
        self.assertIn("YANGGUMI_PUBLIC_URL", source)
        self.assertIn("runlocal\\.eu", source)
        self.assertNotIn("chcp", source.lower())
        self.assertNotIn("share_static_export.py", source)
        self.assertNotIn("ShiKiShare.exe", source)
        self.assertNotIn("0.0.0.0", source)
        self.assertFalse(content.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_vmware_launcher_url_keeps_token_and_port(self):
        source = "http://192.168.79.118:8502/?access=secret-token"
        self.assertEqual(
            share_public.replace_url_host(source, "192.168.81.1"),
            "http://192.168.81.1:8502/?access=secret-token",
        )

    def test_static_share_export_is_privacy_safe_and_self_contained(self):
        public_work = {
            "id": 900001, "title": "公开样本", "original_title": "Public Sample",
            "type": "动画", "subtype": "TV", "status": "已看", "year": 2025,
            "score_total": 8.2, "bangumi_score": 7.8, "short_review": "公开短评",
            "bangumi_summary": "公开简介", "bangumi_tags": ["测试"],
            "updated_at": "2026-08-11T00:00:00",
        }
        for score_field in db.SCORE_FIELDS:
            public_work.setdefault(score_field, None)
        exported = {
            "export_meta": {"read_only": True, "exported_at": ""},
            "works": [dict(public_work)],
        }
        private_work = {**public_work, "cover_path": "", "cover_url": "", "bangumi_image_url": ""}
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            db, "export_json", return_value=json.dumps(exported, ensure_ascii=False).encode("utf-8")
        ), mock.patch.object(
            db, "list_works", return_value=[private_work]
        ), mock.patch.object(
            db, "list_seasonal_anime", return_value=[]
        ), mock.patch.object(
            daily_art, "load_manifest", return_value={
                "items": [
                    {"asset": f"daily_art/sample-{index}.webp", "type": "portrait", "key": str(index)}
                    for index in range(3)
                ]
            }
        ):
            destination = share_static_export.build_public_site(Path(temp) / "site")
            self.assertEqual(
                {path.name for path in destination.iterdir()},
                {"index.html", "snapshot.json"},
            )
            payload = json.loads(destination.joinpath("snapshot.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["export_meta"]["read_only"])
            self.assertTrue(payload["works"])
            public_work = payload["works"][0]
            for unused in (
                "score_breakdown", "custom_scores_json", "created_at",
                "release_date", "bangumi_total_votes", "bangumi_rank", "score_imbalance_penalty",
            ):
                self.assertNotIn(unused, public_work)
            self.assertIn("bangumi_summary", public_work)
            for score_field in payload["score_labels"]:
                self.assertIn(score_field, public_work)
            serialized = json.dumps(payload, ensure_ascii=False)
            for forbidden in ("private_note", "resource_path", "cover_path", "raw_json", "E:\\\\"):
                self.assertNotIn(forbidden, serialized)
            html = destination.joinpath("index.html").read_text(encoding="utf-8")
            self.assertIn('data-page="条目库"', html)
            self.assertIn("function pager(total)", html)
            self.assertIn("const revisionKey=value=>JSON.stringify(value??null)", html)
            self.assertIn("revisionKey(v.revision)!==revisionKey(data.revision)", html)
            self.assertNotIn("v.revision!==data.revision", html)
            self.assertEqual(html.count('rel="preload" as="image"'), 3)
            self.assertIn("warmAfterVisible(filteredWorks().slice(0,pageSize).map(image))", html)
            self.assertIn('name="referrer" content="no-referrer"', html)
            self.assertNotIn('src="./app.js"', html)
            self.assertNotIn('href="./app.css"', html)

    def test_fast_share_server_exchanges_query_token_for_secure_session_cookie(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "site"
            site.mkdir()
            site.joinpath("index.html").write_text("<!doctype html><h1>Yang-gumi</h1>", encoding="utf-8")
            site.joinpath("snapshot.json").write_text('{"revision":[1,2,3,4]}', encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), share_fast_server.ShareHandler)
            server.daemon_threads = True
            server.share_token = "test-access-token"
            server.access_cookie = "opaque-session-cookie"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with mock.patch.object(share_fast_server, "SITE_ROOT", site), mock.patch.object(
                share_fast_server, "rebuild_if_needed", return_value=(1, 2, 3, 4)
            ):
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    for protected in ("/revision.json", "/snapshot.json"):
                        with self.assertRaises(HTTPError) as blocked:
                            urlopen(base + protected, timeout=3)
                        self.assertEqual(blocked.exception.code, 403)

                    first = urlopen(
                        Request(
                            base + "/?access=test-access-token",
                            headers={"Host": "share.example", "X-Forwarded-Proto": "https"},
                        ),
                        timeout=3,
                    )
                    cookie = first.headers["Set-Cookie"]
                    self.assertIn("yanggumi_share_session=opaque-session-cookie", cookie)
                    self.assertIn("HttpOnly", cookie)
                    self.assertIn("SameSite=Strict", cookie)
                    self.assertIn("Secure", cookie)
                    cookie_pair = cookie.split(";", 1)[0]

                    revision = urlopen(
                        Request(base + "/revision.json", headers={"Cookie": cookie_pair}), timeout=3
                    )
                    self.assertEqual(json.loads(revision.read()), {"revision": [1, 2, 3, 4]})
                    snapshot = urlopen(
                        Request(base + "/snapshot.json", headers={"Cookie": cookie_pair}), timeout=3
                    )
                    self.assertEqual(json.loads(snapshot.read()), {"revision": [1, 2, 3, 4]})

                    compressed = urlopen(
                        Request(
                            base + "/?access=test-access-token",
                            headers={"Accept-Encoding": "gzip"},
                        ),
                        timeout=3,
                    )
                    self.assertEqual(compressed.headers["Content-Encoding"], "gzip")
                    self.assertIn(b"Yang-gumi", gzip.decompress(compressed.read()))
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)

    def test_private_tag_input_and_display_are_removed(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        self.assertNotIn('text_input("私人标签', source)
        self.assertNotIn('caption("私人标签', source)
        self.assertNotIn('["全部", "Bangumi", "私人标签"]', source)

    def test_recent_cards_keep_their_tracks_when_browser_zoom_changes(self):
        source = Path(__file__).parents[1].joinpath("ui_components.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "grid-template-columns:clamp(104px,7.1vw,136px) minmax(0,1fr) "
            "clamp(126px,8.85vw,170px)",
            source,
        )
        self.assertIn(".st-key-home_recent_grid .yg-score-row {{flex-wrap:nowrap", source)
        self.assertIn("width:clamp(88px,6vw,112px)!important", source)

    def test_manifest_skips_oversize_and_uses_cached_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            portrait = root / "portrait"; wallpaper = root / "wallpaper"
            portrait.mkdir(); wallpaper.mkdir()
            Image.new("RGB", (300, 500), "red").save(portrait / "ok.jpg")
            (portrait / "too-big.jpg").write_bytes(b"x" * (daily_art.MAX_FILE_SIZE + 1))
            old = (daily_art.LOCAL_ROOTS, daily_art.MANIFEST_PATH, daily_art.ASSET_DIR)
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
            finally:
                daily_art.LOCAL_ROOTS, daily_art.MANIFEST_PATH, daily_art.ASSET_DIR = old

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
                with mock.patch.object(daily_art.subprocess, "run", return_value=completed) as run:
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
