from __future__ import annotations

from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest


class AllPagesUiTest(unittest.TestCase):
    def test_season_progress_sits_below_status_text(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        self.assertIn(".yg-season-progress{{position:absolute;bottom:5px", source)
        self.assertIn(".yg-season-live-stage{{position:relative;height:565px", source)

    PAGES = (
        "首页",
        "条目库",
        "新增条目",
        "Bangumi",
        "排行榜",
        "评分对比",
        "标签筛选",
        "评分设置",
        "数据管理",
    )

    def test_all_navigation_pages_render_without_exceptions(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                app = AppTest.from_file("app.py", default_timeout=30).run()
                navigation = next(
                    button
                    for button in app.button
                    if button.key == f"sidebar_nav_{page}"
                )
                navigation.click().run()

                self.assertEqual(list(app.exception), [])
                self.assertEqual(app.session_state["nav_page"], page)

    def test_app_has_page_recovery_guard(self):
        source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
        self.assertIn("def render_page_safely", source)
        self.assertIn("logs/app_errors.log", source)
        self.assertIn("render_page_safely(st.session_state.nav_page)", source)


if __name__ == "__main__":
    unittest.main()
