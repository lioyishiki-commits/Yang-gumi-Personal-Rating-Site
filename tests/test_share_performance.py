from __future__ import annotations

import inspect
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import share_assets
import share_public
import ui_components


class ShareAssetPerformanceTests(unittest.TestCase):
    def test_optimized_asset_keeps_high_resolution_and_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            static_root = root / "static"
            cache_root = static_root / "share_assets"
            source = root / "poster.png"
            Image.effect_noise((1200, 1800), 80).convert("RGB").save(source)
            with mock.patch.object(share_assets, "STATIC_ROOT", static_root), mock.patch.object(
                share_assets, "CACHE_ROOT", cache_root
            ):
                first = share_assets.optimized_image_url(
                    str(source), bucket="covers", key="future-work", max_size=(720, 1080), quality=92
                )
                cached = static_root / first.removeprefix("/app/static/")
                first_mtime = cached.stat().st_mtime_ns
                second = share_assets.optimized_image_url(
                    str(source), bucket="covers", key="future-work", max_size=(720, 1080), quality=92
                )
            self.assertEqual(first, second)
            self.assertEqual(cached.stat().st_mtime_ns, first_mtime)
            with Image.open(cached) as image:
                self.assertEqual(image.size, (720, 1080))

    def test_source_change_gets_a_new_fingerprinted_url(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            static_root = root / "static"
            source = root / "poster.png"
            Image.new("RGB", (600, 900), "red").save(source)
            with mock.patch.object(share_assets, "STATIC_ROOT", static_root), mock.patch.object(
                share_assets, "CACHE_ROOT", static_root / "share_assets"
            ):
                first = share_assets.optimized_image_url(
                    str(source), bucket="covers", key="new-work", max_size=(280, 420)
                )
                time.sleep(0.01)
                Image.new("RGB", (600, 900), "blue").save(source)
                os.utime(source, None)
                second = share_assets.optimized_image_url(
                    str(source), bucket="covers", key="new-work", max_size=(280, 420)
                )
            self.assertNotEqual(first, second)

    def test_prewarmer_can_start_from_an_already_prepared_revision(self):
        revision = (1, 2, 3, 4)
        checks = 0

        def stop_requested() -> bool:
            nonlocal checks
            checks += 1
            return checks > 1

        with mock.patch.object(share_assets, "source_revision", return_value=revision), mock.patch.object(
            share_assets, "prepare_share_assets"
        ) as prepare:
            share_assets.run_prewarmer(stop_requested, interval=1, initial_revision=revision)
        prepare.assert_not_called()

    def test_share_reuses_normal_app_and_prewarms_assets_dynamically(self):
        environment = share_public.streamlit_environment("token")
        self.assertNotIn("YANGGUMI_SHARE_ASSETS", environment)
        self.assertNotIn("YANGGUMI_READ_ONLY", environment)
        source = Path(share_public.__file__).read_text(encoding="utf-8")
        self.assertIn("share_assets.prepare_share_assets()", source)
        self.assertIn("target=share_assets.run_prewarmer", source)
        command = share_public.streamlit_command(18649)
        self.assertIn(str(Path(share_public.__file__).parent / "app.py"), command)
        self.assertNotIn("app_public.py", " ".join(command))
        proxy = share_public.proxy_command(18650, 18649)
        self.assertIn(str(Path(share_public.__file__).parent / "share_proxy_server.py"), proxy)
        self.assertIn("--server.enableWebsocketCompression", command)
        self.assertIn("--server.fileWatcherType", command)

    def test_readonly_navigation_and_pager_do_not_force_second_reruns(self):
        pager_source = Path(share_public.__file__).with_name("app.py").read_text(encoding="utf-8")
        pager_source = pager_source[
            pager_source.index("def render_jump_pager("):pager_source.index("def score_interval(")
        ]
        self.assertIn("on_click=_set_page_state", pager_source)
        self.assertNotIn("st.rerun()", pager_source)
        self.assertNotIn("st.rerun()", inspect.getsource(ui_components.render_top_nav))
        self.assertNotIn("st.rerun()", inspect.getsource(ui_components.work_grid_card))

    def test_compat_static_route_has_long_lived_fingerprint_cache_headers(self):
        fast_server = Path(share_public.__file__).with_name("share_fast_server.py").read_text(encoding="utf-8")
        self.assertIn('"public, max-age=31536000, immutable"', fast_server)
        self.assertIn('"public, max-age=86400"', fast_server)
        proxy = Path(share_public.__file__).with_name("share_proxy_server.py").read_text(encoding="utf-8")
        self.assertIn('"public, max-age=31536000, immutable"', proxy)
        self.assertIn("gzip.compress", proxy)


if __name__ == "__main__":
    unittest.main()
