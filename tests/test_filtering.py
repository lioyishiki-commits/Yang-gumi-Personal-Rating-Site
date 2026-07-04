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
        self.assertEqual(flt.format_diff(diff), "+1.2")
        self.assertTrue(flt.diff_direction_matches(diff, "我高于 Bangumi"))
        self.assertTrue(flt.diff_abs_in_range(diff, "1.0 到 1.5"))

    def test_negative_difference_filters(self):
        diff = flt.calculate_score_diff({"score_total": 7.0, "bangumi_score": 8.2})
        self.assertEqual(diff, -1.2)
        self.assertEqual(flt.format_diff(diff), "-1.2")
        self.assertTrue(flt.diff_direction_matches(diff, "我低于 Bangumi"))
        self.assertTrue(flt.diff_abs_in_range(diff, "1.0 到 1.5"))

    def test_consistent_difference_filters(self):
        diff = flt.calculate_score_diff({"score_total": 8.1, "bangumi_score": 7.8})
        self.assertEqual(diff, 0.3)
        self.assertTrue(flt.diff_direction_matches(diff, "基本一致"))
        self.assertTrue(flt.diff_abs_in_range(diff, "0 到 0.5"))
        self.assertTrue(flt.diff_direction_matches(0.5, "基本一致"))
        self.assertTrue(flt.diff_direction_matches(-0.5, "基本一致"))

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


if __name__ == "__main__":
    unittest.main()
