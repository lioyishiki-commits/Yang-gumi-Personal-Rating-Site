from __future__ import annotations

import os
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest


class InteractiveReadOnlyShareTest(unittest.TestCase):
    PAGES = (
        "首页", "条目库", "排行榜", "评分对比", "标签筛选", "评分设置",
    )
    PRIVATE_PAGES = ("新增条目", "Bangumi", "数据管理")

    def open_readonly_app(self) -> AppTest:
        environment = {
            "YANGGUMI_READ_ONLY": "1",
            "YANGGUMI_SHARE_TOKEN": "",
            "STREAMLIT_GLOBAL_DEVELOPMENT_MODE": "false",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            app = AppTest.from_file("app.py", default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        return app

    def test_readonly_navigation_exposes_only_the_six_approved_pages(self):
        app = self.open_readonly_app()
        navigation_keys = [
            button.key for button in app.button
            if button.key and button.key.startswith("sidebar_nav_")
        ]
        self.assertEqual(navigation_keys, [f"sidebar_nav_{page}" for page in self.PAGES])
        for page in self.PRIVATE_PAGES:
            self.assertNotIn(f"sidebar_nav_{page}", navigation_keys)

    def test_each_public_page_remains_interactive(self):
        app = self.open_readonly_app()
        for page in self.PAGES:
            with self.subTest(page=page):
                with mock.patch.dict(
                    os.environ,
                    {"YANGGUMI_READ_ONLY": "1", "YANGGUMI_SHARE_TOKEN": ""},
                    clear=False,
                ):
                    next(
                        button for button in app.button
                        if button.key == f"sidebar_nav_{page}"
                    ).click().run()
                self.assertEqual(list(app.exception), [])
                self.assertEqual(app.session_state["nav_page"], page)

    def test_scoring_settings_is_view_only(self):
        app = self.open_readonly_app()
        with mock.patch.dict(
            os.environ,
            {"YANGGUMI_READ_ONLY": "1", "YANGGUMI_SHARE_TOKEN": ""},
            clear=False,
        ):
            next(
                button for button in app.button
                if button.key == "sidebar_nav_评分设置"
            ).click().run()
        self.assertEqual(list(app.exception), [])
        labels = {button.label for button in app.button}
        self.assertNotIn("保存评分设置并重新计算", labels)
        self.assertNotIn("重置评分设置并重新计算", labels)
        downloads = {
            element.label for element in app.get("download_button")
        }
        self.assertIn("导出全部评分明细 XLSX", downloads)
        self.assertGreaterEqual(len(app.dataframe), 1)

    def test_compare_page_has_readonly_dimension_view_without_xlsx_download(self):
        app = self.open_readonly_app()
        with mock.patch.dict(
            os.environ,
            {"YANGGUMI_READ_ONLY": "1", "YANGGUMI_SHARE_TOKEN": ""},
            clear=False,
        ):
            next(
                button for button in app.button
                if button.key == "sidebar_nav_评分对比"
            ).click().run()
        self.assertEqual(list(app.exception), [])
        select_labels = {widget.label for widget in app.selectbox}
        self.assertTrue({"评分小项目", "分项排序", "分项每页"}.issubset(select_labels))
        self.assertNotIn("当前结果每页", select_labels)
        page_size = next(widget for widget in app.selectbox if widget.label == "分项每页")
        self.assertEqual(page_size.value, 24)
        downloads = {
            element.label for element in app.get("download_button")
        }
        self.assertNotIn("下载全部评分明细 XLSX", downloads)
        self.assertFalse(any(
            button.key and button.key.startswith("compare_dimension_apply_")
            for button in app.button
        ))


if __name__ == "__main__":
    unittest.main()
