from __future__ import annotations

import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

import bangumi_client as bgm
import database as db
import filtering as flt


class PageSmokeTest(unittest.TestCase):
    @staticmethod
    def period_sample_works() -> list[dict[str, object]]:
        return [
            {
                "id": 900001, "title": "季度样本一", "original_title": "Sample A",
                "type": "动画", "status": "已看", "year": 2024,
                "bangumi_id": 900001,
                "release_date": "2024-01-10", "score_total": 8.2, "bangumi_score": 7.8,
            },
            {
                "id": 900002, "title": "季度样本二", "original_title": "Sample B",
                "type": "动画", "status": "已看", "year": 2025,
                "bangumi_id": 900002,
                "release_date": "2025-04-10", "score_total": 7.4, "bangumi_score": 7.0,
            },
        ]

    def open_page(self, page: str) -> AppTest:
        app = AppTest.from_file("app.py", default_timeout=30).run()
        next(button for button in app.button if button.key == f"sidebar_nav_{page}").click().run()
        self.assertEqual(list(app.exception), [])
        return app

    def test_library_exposes_all_filter_dimensions(self):
        app = self.open_page("条目库")
        labels = {widget.label for widget in app.selectbox}
        self.assertTrue({"类型", "子类型", "状态", "年份 / 季度", "我的评分区间", "Bangumi 评分区间", "评分差方向", "评分差绝对值", "排序"}.issubset(labels))
        self.assertEqual(app.multiselect[0].label, "标签（可搜索、多选）")
        self.assertEqual(app.multiselect[0].placeholder, "输入标签名搜索，可连续多选")

    def test_library_migrates_an_old_integer_year_filter_safely(self):
        app = AppTest.from_file("app.py", default_timeout=30).run()
        app.session_state["lib_year"] = 2022
        next(button for button in app.button if button.key == "sidebar_nav_条目库").click().run()
        year_quarter = next(widget for widget in app.selectbox if widget.label == "年份 / 季度")
        self.assertEqual(year_quarter.value, "全部")
        self.assertEqual(list(app.exception), [])

    def test_compare_exposes_all_filter_dimensions(self):
        app = self.open_page("评分对比")
        labels = {widget.label for widget in app.selectbox}
        self.assertTrue({
            "类型", "状态", "评分小项目", "分项排序", "分项每页",
        }.issubset(labels))
        self.assertNotIn("榜单", labels)
        self.assertTrue({
            "我的评分区间", "Bangumi 评分区间", "差值方向",
            "差值绝对值", "排序", "当前结果每页",
        }.isdisjoint(labels))
        dimension = next(widget for widget in app.selectbox if widget.label == "评分小项目")
        self.assertIn("作品本体 · 剧情", dimension.options)
        page_size = next(widget for widget in app.selectbox if widget.label == "分项每页")
        self.assertEqual(page_size.value, 24)
        bias_filter = next(
            widget for widget in app.segmented_control if widget.label == "评分差筛选"
        )
        self.assertEqual(bias_filter.options[0], "全部")
        self.assertTrue(bias_filter.options[1].startswith("偏高 "))
        self.assertTrue(bias_filter.options[2].startswith("偏低 "))
        self.assertTrue(bias_filter.options[3].startswith("接近 "))
        self.assertEqual(bias_filter.value, "全部")

    def test_period_average_uses_a_continuous_year_range_on_both_pages(self):
        for page in ("条目库", "评分对比"):
            with self.subTest(page=page):
                with patch.object(db, "list_works", return_value=self.period_sample_works()):
                    st.cache_data.clear()
                    app = self.open_page(page)
                    prefix = "library" if page == "条目库" else "compare"
                    next(
                        widget for widget in app.toggle
                        if widget.key == f"{prefix}_period_average_enabled"
                    ).set_value(True).run()
                    mode = next(
                        widget for widget in app.segmented_control if widget.label == "统计范围"
                    )
                    self.assertEqual(mode.options, ["单季度", "单年", "年代范围"])
                    mode.set_value("年代范围").run()
                    labels = {widget.label for widget in app.selectbox}
                    self.assertTrue({"起始年份", "结束年份"}.issubset(labels))
                    self.assertFalse(any(
                        widget.label == "选择要合并统计的年份" for widget in app.multiselect
                    ))
                    self.assertTrue(any(
                        "含首尾" in str(element.value) for element in app.caption
                    ))
                    self.assertEqual(list(app.exception), [])

    def test_period_average_filters_the_downstream_results_on_both_pages(self):
        works = self.period_sample_works()
        years = flt.release_year_options(works)
        empty_period = next(
            (year, quarter)
            for year in years
            for quarter in flt.PERIOD_QUARTERS
            if not flt.release_period_scope(
                works, "单季度", year=year, quarter=quarter,
            )
        )
        year, quarter = empty_period

        with patch.object(db, "list_works", return_value=works):
            st.cache_data.clear()
            library = self.open_page("条目库")
            next(
                widget for widget in library.toggle
                if widget.key == "library_period_average_enabled"
            ).set_value(True).run()
            next(widget for widget in library.selectbox if widget.key == "library_period_year").set_value(year).run()
            quarter_widget = next(
                widget for widget in library.selectbox if widget.key == "library_period_quarter"
            )
            self.assertEqual(quarter_widget.options, ["Q4", "Q3", "Q2", "Q1"])
            quarter_widget.set_value(quarter).run()
            self.assertTrue(any(
                "当前查询结果：0 /" in str(element.value)
                for element in library.caption
            ))

            st.cache_data.clear()
            compare = self.open_page("评分对比")
            next(
                widget for widget in compare.toggle
                if widget.key == "compare_period_average_enabled"
            ).set_value(True).run()
            next(widget for widget in compare.selectbox if widget.key == "compare_period_year").set_value(year).run()
            next(
                widget for widget in compare.selectbox if widget.key == "compare_period_quarter"
            ).set_value(quarter).run()
            self.assertTrue(any(
                "共 0 部作品" in str(element.value)
                for element in compare.caption
            ))
            self.assertEqual(list(library.exception), [])
            self.assertEqual(list(compare.exception), [])

    def test_bangumi_public_scores_support_searchable_tags_and_all_time_scopes(self):
        rows = [
            {
                "id": 1, "title": "治愈一", "original_title": "A", "rank": 1,
                "score": 9.0, "votes": 100, "image": "",
                "subject": {"date": "2026-01-05", "tags": [{"name": "治愈"}]},
            },
            {
                "id": 2, "title": "治愈二", "original_title": "B", "rank": 2,
                "score": 7.0, "votes": 80, "image": "",
                "subject": {
                    "date": "2026-10-05", "tags": [{"name": "治愈"}, {"name": "奇幻"}],
                },
            },
            {
                "id": 3, "title": "科幻一", "original_title": "C", "rank": 3,
                "score": 6.0, "votes": 60, "image": "",
                "subject": {"date": "2024-07-05", "tags": [{"name": "科幻"}]},
            },
        ]
        with patch.object(bgm, "cached_ranking_subjects", return_value=rows), patch.object(
            bgm, "ranking_cache_complete", return_value=True
        ), patch.object(
            bgm, "ranked_browser_subject_window", return_value=rows
        ), patch.object(
            bgm, "enrich_precise_anime_ratings", side_effect=lambda values, **_: values
        ):
            app = self.open_page("Bangumi")
            next(
                widget for widget in app.toggle
                if widget.key == "bangumi_public_analysis_enabled"
            ).set_value(True).run()
            tags = next(widget for widget in app.multiselect if widget.key == "bangumi_public_tags")
            self.assertEqual(tags.label, "标签搜索（可多选）")
            self.assertIn("输入标签名称搜索", tags.placeholder)
            self.assertEqual(tags.options, ["治愈", "奇幻", "科幻"])
            mode = next(
                widget for widget in app.segmented_control
                if widget.key == "bangumi_public_period_mode"
            )
            self.assertEqual(mode.options, ["全部时间", "单季度", "单年", "年代范围"])
            self.assertEqual(mode.value, "全部时间")

            tags.set_value(["治愈"]).run()
            html_lines = [str(element.value) for element in app.markdown]
            self.assertTrue(any("标签均分" in line and "8.00" in line for line in html_lines))
            self.assertTrue(any("筛选结果" in line and ">2<" in line for line in html_lines))

            mode = next(
                widget for widget in app.segmented_control
                if widget.key == "bangumi_public_period_mode"
            )
            mode.set_value("单季度").run()
            quarter = next(
                widget for widget in app.selectbox
                if widget.key == "bangumi_public_period_quarter"
            )
            self.assertEqual(quarter.options, ["Q4", "Q3", "Q2", "Q1"])
            quarter.set_value(1).run()
            html_lines = [str(element.value) for element in app.markdown]
            self.assertTrue(any("2026年 · Q1" in line and "9.00" in line for line in html_lines))

            next(
                widget for widget in app.segmented_control
                if widget.key == "bangumi_public_period_mode"
            ).set_value("年代范围").run()
            self.assertTrue({"起始年份", "结束年份"}.issubset({
                widget.label for widget in app.selectbox
            }))
            self.assertFalse(any("我的均分" in line for line in html_lines))
            self.assertEqual(list(app.exception), [])

    def test_search_category_controls_include_the_durable_pin_action(self):
        app = self.open_page("新增条目")
        categories = [widget for widget in app.radio if widget.label == "搜索分类"]
        self.assertEqual(len(categories), 1)
        self.assertTrue(any(
            button.key == "search_default_pin_add_search_category"
            for button in app.button
        ))

    def test_add_search_result_score_keeps_two_decimal_places(self):
        app = self.open_page("新增条目")
        subject = {
            "id": 1, "type": 2, "name_cn": "悠哉日常大王", "name": "のんのんびより",
            "date": "2013-10-07", "images": {},
            "rating": {"score": 8.2, "total": 11447, "rank": 228}, "tags": [],
        }
        precise_subject = {
            **subject,
            "rating": {**subject["rating"], "score": 8.27},
            "precision_source": "bangumi-rating-perspective",
        }
        with patch.object(bgm, "search_subjects_by_category", return_value=[subject]), patch.object(
            bgm, "enrich_precise_subject_ratings", return_value=[precise_subject]
        ):
            next(item for item in app.text_input if item.key == "add_query").set_value("悠哉日常大王").run()
        result_lines = [str(element.value) for element in app.markdown if "首播日期" in str(element.value)]
        self.assertTrue(any("评分 8.27（Bangumi 评分透视）" in line for line in result_lines))

    def test_add_search_never_pads_rounded_api_score_as_fake_precision(self):
        subject = {
            "id": 234, "type": 2, "name_cn": "AIR", "name": "AIR",
            "date": "2005-01-06", "images": {},
            "rating": {"score": 8, "total": 13510, "rank": 265}, "tags": [],
        }
        app = self.open_page("新增条目")
        with patch.object(bgm, "search_subjects_by_category", return_value=[subject]), patch.object(
            bgm, "enrich_precise_subject_ratings", return_value=[subject]
        ):
            next(item for item in app.text_input if item.key == "add_query").set_value("AIR").run()
        result_lines = [str(element.value) for element in app.markdown if "首播日期" in str(element.value)]
        self.assertTrue(any("评分 —（尚未取得可计算两位小数的公开评分分布）" in line for line in result_lines))
        self.assertFalse(any("评分 8.00" in line for line in result_lines))

    def test_unscored_inputs_are_empty_instead_of_zero(self):
        app = self.open_page("新增条目")
        score_fields = [item for item in app.number_input if item.label != "年份"]
        self.assertEqual(len(score_fields), 11)
        self.assertTrue(all(item.value is None for item in score_fields))


if __name__ == "__main__":
    unittest.main()
