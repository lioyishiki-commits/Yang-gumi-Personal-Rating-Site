from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest


class PageSmokeTest(unittest.TestCase):
    def open_page(self, page: str) -> AppTest:
        app = AppTest.from_file("app.py", default_timeout=30).run()
        next(button for button in app.button if button.key == f"sidebar_nav_{page}").click().run()
        self.assertEqual(list(app.exception), [])
        return app

    def test_library_exposes_all_filter_dimensions(self):
        app = self.open_page("条目库")
        labels = {widget.label for widget in app.selectbox}
        self.assertTrue({"类型", "子类型", "状态", "年份", "我的评分区间", "Bangumi 评分区间", "评分差方向", "评分差绝对值", "排序"}.issubset(labels))
        self.assertEqual(app.multiselect[0].label, "标签（多选为任意匹配）")

    def test_compare_exposes_all_filter_dimensions(self):
        app = self.open_page("评分对比")
        labels = {widget.label for widget in app.selectbox}
        self.assertTrue({
            "榜单", "类型", "状态", "评分小项目", "分项排序", "分项每页",
        }.issubset(labels))
        self.assertTrue({
            "我的评分区间", "Bangumi 评分区间", "差值方向",
            "差值绝对值", "排序", "当前结果每页",
        }.isdisjoint(labels))
        board = next(widget for widget in app.selectbox if widget.label == "榜单")
        self.assertIn("Bangumi 高分但我个人无感", board.options)
        self.assertIn("Bangumi 一般但我很喜欢", board.options)
        dimension = next(widget for widget in app.selectbox if widget.label == "评分小项目")
        self.assertIn("作品本体 · 剧情", dimension.options)
        page_size = next(widget for widget in app.selectbox if widget.label == "分项每页")
        self.assertEqual(page_size.value, 24)

    def test_unscored_inputs_are_empty_instead_of_zero(self):
        app = self.open_page("新增条目")
        score_fields = [item for item in app.number_input if item.label != "年份"]
        self.assertEqual(len(score_fields), 11)
        self.assertTrue(all(item.value is None for item in score_fields))


if __name__ == "__main__":
    unittest.main()
