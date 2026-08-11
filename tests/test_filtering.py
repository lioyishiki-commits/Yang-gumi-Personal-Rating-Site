# Yang-gumi release: 1.3.0
from __future__ import annotations

import unittest

import filtering as flt


class FilteringTest(unittest.TestCase):
    def test_required_score_boundaries(self):
        self.assertTrue(flt.score_in_range(8.7, "8.5 到 9.0"))
        self.assertTrue(flt.score_in_range(9.0, "9.0 分以上"))
        self.assertFalse(flt.score_in_range(9.0, "8.5 到 9.0"))
        self.assertTrue(flt.score_in_range(8.2, "8.0 到 8.5"))

    def test_positive_difference_filters(self):
        diff = flt.calculate_score_diff({"score_total": 8.7, "bangumi_score": 7.5})
        self.assertEqual(diff, 1.2)
        self.assertEqual(flt.format_diff(diff), "+1.20")
        self.assertTrue(flt.diff_direction_matches(diff, "我高于 Bangumi"))
        self.assertTrue(flt.diff_abs_in_range(diff, "1.0 到 1.5"))

    def test_negative_difference_filters(self):
        diff = flt.calculate_score_diff({"score_total": 7.0, "bangumi_score": 8.2})
        self.assertEqual(diff, -1.2)
        self.assertEqual(flt.format_diff(diff), "-1.20")
        self.assertTrue(flt.diff_direction_matches(diff, "我低于 Bangumi"))
        self.assertTrue(flt.diff_abs_in_range(diff, "1.0 到 1.5"))

    def test_consistent_difference_filters(self):
        diff = flt.calculate_score_diff({"score_total": 8.1, "bangumi_score": 7.8})
        self.assertEqual(diff, 0.3)
        self.assertTrue(flt.diff_direction_matches(diff, "基本一致"))
        self.assertTrue(flt.diff_abs_in_range(diff, "0 到 0.5"))
        self.assertTrue(flt.diff_direction_matches(0.5, "基本一致"))
        self.assertTrue(flt.diff_direction_matches(-0.5, "基本一致"))

    def test_compare_bias_groups_are_mutually_exclusive(self):
        cases = [
            (0.51, "偏高"),
            (-0.51, "偏低"),
            (0.5, "接近"),
            (-0.5, "接近"),
            (0.0, "接近"),
        ]
        for value, expected in cases:
            matches = [
                option
                for option in flt.COMPARE_BIAS_OPTIONS[1:]
                if flt.compare_bias_matches(value, option)
            ]
            self.assertEqual(matches, [expected])
        self.assertFalse(flt.compare_bias_matches(None, "偏高"))
        self.assertTrue(flt.compare_bias_matches(None, "全部"))

    def test_missing_scores_do_not_enter_ranges_or_differences(self):
        self.assertFalse(flt.score_in_range(None, "8.5 到 9.0"))
        self.assertIsNone(flt.calculate_score_diff({"score_total": 8.7, "bangumi_score": None}))
        self.assertFalse(flt.diff_direction_matches(None, "我高于 Bangumi"))
        self.assertFalse(flt.diff_abs_in_range(None, "1.0 到 1.5"))

    def test_zero_is_a_real_score(self):
        self.assertEqual(flt.calculate_score_diff({"score_total": 0.0, "bangumi_score": 0.0}), 0.0)

    def test_nulls_sort_last_in_both_directions(self):
        items = [{"id": 1, "score_total": None}, {"id": 2, "score_total": 8.2}, {"id": 3, "score_total": 9.1}]
        self.assertEqual([x["id"] for x in flt.sort_null_last(items, "score_total", True)], [3, 2, 1])
        self.assertEqual([x["id"] for x in flt.sort_null_last(items, "score_total", False)], [2, 3, 1])

    def test_year_and_any_tag_helpers(self):
        self.assertEqual(flt.derive_year({"year": None, "release_date": "2024-09-28"}), 2024)
        self.assertIsNone(flt.derive_year({"year": None, "release_date": ""}))
        item = {"tag_names": "治愈 · 奇幻"}
        self.assertTrue(flt.matches_any_tag(item, ["校园", "奇幻"]))
        self.assertFalse(flt.matches_any_tag(item, ["校园", "机战"]))

    def test_public_ranking_subject_fields_support_time_and_tag_analysis(self):
        item = {
            "id": 326,
            "score": 9.2,
            "subject": {
                "date": "2004-01-01",
                "tags": [{"name": "科幻"}, {"name": "神作"}],
            },
        }
        self.assertEqual(flt.derive_year_quarter(item), (2004, 1))
        self.assertEqual(flt.item_tags(item), {"科幻", "神作"})
        self.assertTrue(flt.matches_any_tag(item, ["治愈", "科幻"]))
        self.assertEqual(
            flt.release_period_scope([item], "单季度", year=2004, quarter=1),
            [item],
        )

    def test_year_quarter_labels_and_filters_are_consistent(self):
        summer = {"year": 2026, "release_date": "2026-07-03"}
        year_only = {"year": 2026, "release_date": ""}
        bangumi_fallback = {"year": None, "release_date": "", "bangumi_date": "2025-04-01"}
        unknown = {"year": None, "release_date": ""}
        self.assertEqual(flt.derive_year_quarter(summer), (2026, 3))
        self.assertEqual(flt.year_quarter_label(summer), "2026年 · Q3")
        self.assertEqual(flt.year_quarter_label(year_only), "2026年 · 季度未知")
        self.assertEqual(flt.derive_year_quarter(bangumi_fallback), (2025, 2))
        self.assertEqual(flt.year_quarter_label(unknown), "年份未知")
        self.assertTrue(flt.year_quarter_matches(summer, "2026年 · Q3"))
        self.assertFalse(flt.year_quarter_matches(summer, "2026年 · Q2"))
        self.assertEqual(
            flt.year_quarter_options([year_only, summer, bangumi_fallback, unknown]),
            ["2026年 · Q3", "2026年 · 季度未知", "2025年 · Q2", "年份未知"],
        )

    def test_release_period_average_scopes_do_not_guess_unknown_quarters(self):
        items = [
            {"id": 1, "year": 2026, "release_date": "2026-07-03", "score_total": 8.0},
            {"id": 2, "year": 2026, "release_date": "2026-09-30", "score_total": 9.0},
            {"id": 3, "year": 2026, "release_date": "", "score_total": 7.0},
            {"id": 4, "year": 2025, "release_date": "2025-04-01", "score_total": 6.0},
            {"id": 5, "year": 2024, "release_date": "2024-01-01", "score_total": 10.0},
            {"id": 6, "year": None, "release_date": "", "score_total": 5.0},
        ]
        self.assertEqual(flt.release_year_options(items), [2026, 2025, 2024])
        self.assertEqual(
            [item["id"] for item in flt.release_period_scope(items, "单季度", year=2026, quarter=3)],
            [1, 2],
        )
        self.assertEqual(
            [item["id"] for item in flt.release_period_scope(items, "单年", year=2026)],
            [1, 2, 3],
        )
        self.assertEqual(flt.PERIOD_AVERAGE_MODES, ["单季度", "单年", "年代范围"])
        self.assertEqual(flt.PERIOD_QUARTERS, [4, 3, 2, 1])
        self.assertEqual(
            [item["id"] for item in flt.release_period_scope(
                items, "年代范围", start_year=2024, end_year=2026,
            )],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [item["id"] for item in flt.release_period_scope(
                items, "年代范围", start_year=2026, end_year=2024,
            )],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            flt.release_period_scope_label("单季度", year=2026, quarter=3),
            "2026年 · Q3",
        )
        self.assertEqual(
            flt.release_period_scope_label(
                "年代范围", start_year=2026, end_year=2024,
            ),
            "2024—2026年",
        )


if __name__ == "__main__":
    unittest.main()
