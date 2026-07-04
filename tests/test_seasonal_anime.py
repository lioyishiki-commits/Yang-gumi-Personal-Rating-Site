from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import database as db
import seasonal_service as seasonal
from streamlit.testing.v1 import AppTest


def subject(subject_id: int = 101) -> dict:
    return {
        "id": subject_id, "type": 2, "name": "テストアニメ", "name_cn": "测试动画",
        "date": "2026-04-03", "platform": "TV", "summary": "日本动画测试",
        "images": {"large": "https://example.test/poster.jpg"},
        "rating": {"score": 8.1, "rank": 100, "total": 4567},
        "tags": [{"name": "日本", "count": 100}, {"name": "TV", "count": 80}],
    }


class SeasonalAnimeTest(unittest.TestCase):
    def setUp(self):
        self.original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
        self.original_source_path = seasonal.SEASONAL_SOURCE_PATH
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
        seasonal.SEASONAL_SOURCE_PATH = root / "seasonal_title_sources.json"
        db.init_db()

    def tearDown(self):
        db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = self.original_paths
        seasonal.SEASONAL_SOURCE_PATH = self.original_source_path
        self.temp.cleanup()

    def test_quarter_boundaries(self):
        expected = {
            datetime(2026, 1, 1, 0, 0, 0): ("Q1", 1),
            datetime(2026, 4, 1, 0, 0, 0): ("Q2", 4),
            datetime(2026, 7, 1, 0, 0, 0): ("Q3", 7),
            datetime(2026, 10, 1, 0, 0, 0): ("Q4", 10),
        }
        for value, result in expected.items():
            current = seasonal.current_season(value)
            self.assertEqual((current["season_code"], current["start_month"]), result)

    def test_homepage_rejects_seasonal_anime_below_one_hundred_votes(self):
        self.assertTrue(seasonal.is_homepage_seasonal_anime(subject()))
        low = subject(102)
        low["rating"]["total"] = 99
        self.assertFalse(seasonal.is_homepage_seasonal_anime(low))

    def test_homepage_accepts_confirmed_kisssub_seed_before_vote_threshold(self):
        low = subject(102)
        low["rating"]["total"] = 0
        low["_yanggumi_season_source"] = "kisssub"
        self.assertTrue(seasonal.is_homepage_seasonal_anime(low))

    def test_homepage_accepts_confirmed_yuc_seed_before_vote_threshold(self):
        low = subject(103)
        low["rating"]["total"] = 0
        low["_yanggumi_season_source"] = "yuc"
        self.assertTrue(seasonal.is_homepage_seasonal_anime(low))

    def test_homepage_rejects_cached_old_title_misidentified_as_current_season(self):
        old = subject(104)
        old["date"] = "2018-10-04"
        old["_yanggumi_season_source"] = "yuc"
        cached = {
            **seasonal._candidate(old),
            "season_year": 2026,
            "season_code": "Q3",
        }
        self.assertFalse(seasonal.is_homepage_seasonal_anime(cached))

    def test_homepage_rejects_episode_runtime_below_twelve_minutes(self):
        short = subject(105)
        short["date"] = "2026-07-05"
        short["infobox"] = [{"key": "每话时长", "value": "约 5 分钟"}]
        cached = {**seasonal._candidate(short), "season_year": 2026, "season_code": "Q3"}
        self.assertTrue(seasonal.is_short_episode_anime(cached))
        self.assertFalse(seasonal.is_homepage_seasonal_anime(cached))
        short["infobox"][0]["value"] = "12分钟"
        cached = {**seasonal._candidate(short), "season_year": 2026, "season_code": "Q3"}
        self.assertFalse(seasonal.is_short_episode_anime(cached))

    def test_kisssub_parser_keeps_only_titles_between_quarter_markers(self):
        page = """
        <table><tr><th>星期一</th><td><a>旧番</a><span>7月新番→</span>
        <a>新番甲</a><a>新番乙 第2季</a><span>←7月新番</span><a>续播番</a></td></tr></table>
        """
        self.assertEqual(seasonal.parse_kisssub_season_titles(page, 7), ["新番甲", "新番乙 第2季"])
        with self.assertRaises(RuntimeError):
            seasonal.parse_kisssub_season_titles('<form id="visitor-test-form">captcha</form>', 7)

    def test_kisssub_match_marks_source_and_remembers_bangumi_id(self):
        result = subject(333)
        result["name_cn"] = "新番甲"
        result["name"] = "新番甲"
        result["date"] = "2026-07-03"
        season = seasonal.current_season(datetime(2026, 7, 1))
        with patch("seasonal_service.bgm.search_subjects", return_value=[result]), patch(
            "seasonal_service.bgm.get_subject", return_value=result
        ):
            rows, matches, failures = seasonal.match_kisssub_titles(["新番甲"], season)
        self.assertEqual(failures, [])
        self.assertEqual(matches, {"新番甲": 333})
        self.assertEqual(json.loads(rows[0]["raw_json"])["_yanggumi_season_source"], "kisssub")

    def test_yuc_parser_reads_detailed_title_and_poster_pairs(self):
        page = """
        <table><tr><td class="date_title_">紧凑日程标题</td></tr></table>
        <div style="float:left"><img data-src="https://img.test/poster.jpg" width="180px"></div>
        <div><table><tr><td><p class="title_cn_r1"> 当季作品 第2期 </p></td></tr></table></div>
        """
        self.assertEqual(
            seasonal.parse_yuc_season_entries(page),
            [{"title": "当季作品 第2期", "original_title": "", "poster_url": "https://img.test/poster.jpg"}],
        )
        season = seasonal.current_season(datetime(2026, 7, 1))
        self.assertEqual(seasonal.yuc_season_url(season), "https://yuc.wiki/202607/")

    def test_yuc_parser_marks_instant_anime_from_compact_schedule(self):
        page = """
        <table><tr><td class="date2">周一 (月)</td></tr></table>
        <div style="float:left"><div class="div_date"><p>21:00~</p><p>(泡面)</p><img src="small.jpg"></div>
        <div><table><tr><td class="date_title_">短篇动画</td></tr></table></div></div>
        <p class="intro">details</p>
        """
        self.assertEqual(seasonal.parse_yuc_short_titles(page), ["短篇动画"])

    def test_yuc_schedule_uses_six_am_broadcast_day_boundary(self):
        page = """
        <table><tr><td class="date2">周二 (火)</td></tr></table>
        <div style="float:left"><div class="div_date"><p>01:30~</p><p class="imgep">(全24话)</p><img src="late.jpg"></div>
        <div><table><tr><td class="date_title_">深夜动画</td></tr></table></div></div>
        <p class="intro">details</p>
        """
        rows = seasonal.parse_yuc_schedule_entries(page)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["broadcast_day"], rows[0]["broadcast_day_label"]), (0, "周一"))
        self.assertEqual(rows[0]["broadcast_time"], "25:30")
        self.assertEqual(rows[0]["broadcast_note"], "(全24话)")

    def test_yuc_fetch_keeps_weekly_schedule_and_merges_detail_alias(self):
        page = """
        <table><tr><td class="date2">周三 (水)</td></tr></table>
        <div style="float:left"><div class="div_date"><p>21:00~</p><img src="same.jpg"></div>
        <div><table><tr><td class="date_title_">Re:从零开始的异世界生活 第4期 P2</td></tr></table></div></div>
        <p class="intro">details</p>
        <div style="float:left"><img data-src="same.jpg"></div>
        <div><table><tr><td><p class="title_cn_r1">Re:从零开始的异世界生活 第4期 Part.2 夺还篇</p>
        <p class="title_jp_r2">Re:ゼロから始める異世界生活 4th season 奪還編</p></td></tr></table></div>
        <div style="float:left"><img data-src="only-detail.jpg"></div>
        <div><table><tr><td><p class="title_cn_r1">没有周播排期的作品</p></td></tr></table></div>
        """
        response = MagicMock(text=page)
        response.raise_for_status.return_value = None
        with patch("seasonal_service.requests.get", return_value=response):
            rows = seasonal.fetch_yuc_season_entries(seasonal.current_season(datetime(2026, 7, 1)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Re:从零开始的异世界生活 第4期 P2")
        self.assertIn("Re:从零开始的异世界生活 第4期 Part.2 夺还篇", rows[0]["aliases"])
        self.assertIn("Re:ゼロから始める異世界生活 4th season 奪還編", rows[0]["aliases"])
        self.assertEqual((rows[0]["broadcast_day"], rows[0]["broadcast_time"]), (2, "21:00"))

    def test_current_quarter_translation_difference_can_match_safely(self):
        translated = subject(335)
        translated["name_cn"] = "描绘直至生命尽头"
        translated["name"] = "これ描いて死ね"
        translated["date"] = "2026-07-03"
        season = seasonal.current_season(datetime(2026, 7, 1))
        broadcast = {"day": 4, "day_label": "周五", "time": "21:30", "sort": 6690, "note": "7/3~"}
        with patch("seasonal_service.bgm.search_subjects", return_value=[translated]), patch(
            "seasonal_service.bgm.get_subject", return_value=translated
        ):
            rows, matches, failures = seasonal.match_kisssub_titles(
                ["画完这个再去死"], season, source_names={"画完这个再去死": "yuc"},
                broadcasts={"画完这个再去死": broadcast},
                aliases={"画完这个再去死": ["これ描いて死ね"]},
            )
        self.assertEqual(failures, [])
        self.assertEqual(matches, {"画完这个再去死": 335})
        self.assertEqual(json.loads(rows[0]["raw_json"])["_yanggumi_broadcast_time"], "21:30")

    def test_curated_match_records_yuc_poster_fallback(self):
        result = subject(334)
        result["name_cn"] = "当季作品"
        result["name"] = "当季作品"
        result["date"] = "2026-07-04"
        season = seasonal.current_season(datetime(2026, 7, 1))
        with patch("seasonal_service.bgm.search_subjects", return_value=[result]), patch(
            "seasonal_service.bgm.get_subject", return_value=result
        ):
            rows, matches, failures = seasonal.match_kisssub_titles(
                ["当季作品"], season, source_names={"当季作品": "yuc"},
                poster_urls={"当季作品": "https://img.test/yuc.jpg"},
            )
        raw = json.loads(rows[0]["raw_json"])
        self.assertEqual((failures, matches), ([], {"当季作品": 334}))
        self.assertEqual(raw["_yanggumi_season_source"], "yuc")
        self.assertEqual(raw["_yanggumi_yuc_poster"], "https://img.test/yuc.jpg")

    def test_curated_match_replaces_known_old_series_entry_with_current_subject(self):
        old = subject(140001)
        old["name_cn"] = "从零开始的异世界生活"
        old["date"] = "2016-04-03"
        current = subject(640001)
        current["name_cn"] = "从零开始的异世界生活 第四季"
        current["name"] = current["name_cn"]
        current["date"] = "2026-07-08"
        season = seasonal.current_season(datetime(2026, 7, 1))
        with patch("seasonal_service.bgm.get_subject", side_effect=[old, current]), patch(
            "seasonal_service.bgm.search_subjects", return_value=[old, current]
        ):
            rows, matches, failures = seasonal.match_kisssub_titles(
                ["从零开始的异世界生活 第四季"], season,
                known_matches={"从零开始的异世界生活 第四季": 140001},
                source_names={"从零开始的异世界生活 第四季": "yuc"},
            )
        self.assertEqual(failures, [])
        self.assertEqual(matches["从零开始的异世界生活 第四季"], 640001)
        self.assertEqual(rows[0]["bangumi_id"], 640001)

    def test_yuc_schedule_keeps_half_year_anime_from_previous_quarter(self):
        continuing = subject(640002)
        continuing["name_cn"] = "半年连载动画"
        continuing["name"] = continuing["name_cn"]
        continuing["date"] = "2026-04-06"
        season = seasonal.current_season(datetime(2026, 7, 1))
        broadcast = {"day": 0, "day_label": "周一", "time": "23:00", "sort": 1020, "note": "(全24话)"}
        with patch("seasonal_service.bgm.search_subjects", return_value=[continuing]), patch(
            "seasonal_service.bgm.get_subject", return_value=continuing
        ):
            rows, _, failures = seasonal.match_kisssub_titles(
                ["半年连载动画"], season, source_names={"半年连载动画": "yuc"},
                broadcasts={"半年连载动画": broadcast},
            )
        self.assertEqual(failures, [])
        raw = json.loads(rows[0]["raw_json"])
        self.assertEqual(raw["_yanggumi_broadcast_day"], 0)
        self.assertEqual(raw["_yanggumi_broadcast_time"], "23:00")

    def test_yuc_weekly_schedule_rejects_movie_match(self):
        movie = subject(640003)
        movie["name_cn"] = "名侦探系列 剧场版"
        movie["name"] = movie["name_cn"]
        movie["date"] = "2026-09-18"
        movie["platform"] = "剧场版"
        season = seasonal.current_season(datetime(2026, 7, 1))
        broadcast = {"day": 6, "day_label": "周日", "time": "07:30", "sort": 8730, "note": "(年番)"}
        with patch("seasonal_service.bgm.search_subjects", return_value=[movie]):
            rows, matches, failures = seasonal.match_kisssub_titles(
                ["名侦探系列"], season, source_names={"名侦探系列": "yuc"},
                broadcasts={"名侦探系列": broadcast},
            )
        self.assertEqual((rows, matches, failures), ([], {}, ["名侦探系列"]))

    def test_official_candidate_fetch_deduplicates_and_keeps_animation_only(self):
        payloads = [
            {"data": [subject(101), {**subject(102), "type": 1}], "total": 2},
            {"data": [subject(101)], "total": 1},
            {"data": [], "total": 0},
        ]
        with patch("seasonal_service.bgm.list_subjects", side_effect=payloads) as request:
            rows = seasonal.fetch_seasonal_candidates(seasonal.current_season(datetime(2026, 4, 1)))
        self.assertEqual([row["bangumi_id"] for row in rows], [101])
        self.assertEqual(request.call_count, 3)

    def test_cache_and_status_actions_preserve_personal_fields_and_dates(self):
        item = seasonal._candidate(subject())
        db.upsert_seasonal_anime([item], 2026, "Q2", "4月番")
        cache = db.list_seasonal_anime(2026, "Q2")[0]
        work_id, should_edit = seasonal.set_candidate_status(cache["id"], "在看")
        self.assertFalse(should_edit)
        self.assertEqual(db.get_work(work_id)["score_total"], None)
        db.save_work({**db.get_work(work_id), "status": "已看", "score_total": 8.88,
                      "short_review": "保留短评", "start_date": "2026-04-03", "finish_date": "2026-06-20"}, work_id=work_id)
        _, should_edit = seasonal.set_candidate_status(cache["id"], "弃置")
        saved = db.get_work(work_id)
        self.assertTrue(should_edit)
        self.assertEqual(saved["score_total"], 8.88)
        self.assertEqual(saved["short_review"], "保留短评")
        self.assertEqual(saved["start_date"], "2026-04-03")
        self.assertEqual(saved["finish_date"], "2026-06-20")
        _, should_edit_again = seasonal.set_candidate_status(cache["id"], "弃置")
        self.assertFalse(should_edit_again)

    def test_sync_meta_prevents_implicit_repeat_decision(self):
        db.mark_seasonal_sync(2026, "Q2", "success")
        meta = db.seasonal_cache_meta(2026, "Q2")
        self.assertEqual(meta["status"], "success")
        self.assertIsNotNone(meta["last_sync"])

    def test_daily_gate_refreshes_only_once_for_the_local_date(self):
        now = datetime(2026, 7, 2, 0, 0, 1)
        with patch("seasonal_service.db.seasonal_cache_meta", return_value={"last_sync": "2026-07-02T00:00:00"}), patch(
            "seasonal_service.refresh_current_season"
        ) as refresh:
            changed, season, count = seasonal.refresh_current_season_if_due(now)
        self.assertFalse(changed)
        self.assertEqual((season["season_code"], count), ("Q3", 0))
        refresh.assert_not_called()

    def test_quarter_opening_refreshes_even_after_previous_quarter_sync(self):
        now = datetime(2026, 7, 1, 0, 0, 1)
        expected = seasonal.current_season(now)
        with patch(
            "seasonal_service.db.seasonal_cache_meta", return_value={"last_sync": "2026-06-30T23:59:59"}
        ), patch("seasonal_service.refresh_current_season", return_value=(expected, 70)) as refresh:
            changed, season, count = seasonal.refresh_current_season_if_due(now)
        self.assertTrue(changed)
        self.assertEqual((season["season_code"], count), ("Q3", 70))
        refresh.assert_called_once_with(now)

    def test_refresh_keeps_existing_carousel_when_all_sources_temporarily_fail(self):
        season = seasonal.current_season(datetime(2026, 7, 2))
        cached = subject(808)
        cached["date"] = "2026-07-01"
        db.upsert_seasonal_anime([seasonal._candidate(cached)], 2026, "Q3", "7月番")
        with patch("seasonal_service.fetch_seasonal_candidates", side_effect=RuntimeError("offline")), patch(
            "seasonal_service.fetch_kisssub_season_titles", side_effect=RuntimeError("verification")
        ), patch(
            "seasonal_service.fetch_yuc_season_entries", side_effect=RuntimeError("offline")
        ), patch("seasonal_service.bgm.get_subject", side_effect=RuntimeError("offline")), patch(
            "seasonal_service.preload_seasonal_posters", return_value=0
        ):
            refreshed, count = seasonal.refresh_current_season(datetime(2026, 7, 2))
        self.assertEqual((refreshed["season_code"], count), ("Q3", 1))
        rows = db.list_seasonal_anime(2026, "Q3", include_unconfirmed=True)
        self.assertEqual([row["bangumi_id"] for row in rows], [808])

    def test_refresh_with_yuc_schedule_hides_unscheduled_official_candidates(self):
        season = seasonal.current_season(datetime(2026, 7, 2))
        official = subject(901)
        official["date"] = "2026-07-03"
        scheduled = subject(902)
        scheduled["date"] = "2026-04-06"
        scheduled = seasonal._mark_season_subject(
            scheduled, "半年连载动画", "yuc", broadcast={
                "day": 0, "day_label": "周一", "time": "23:00", "sort": 1020, "note": "(全24话)",
            },
        )
        entry = {
            "titles": [], "matches": {"半年连载动画": 902}, "yuc_titles": ["半年连载动画"],
            "yuc_broadcasts": {"半年连载动画": {
                "day": 0, "day_label": "周一", "time": "23:00", "sort": 1020, "note": "(全24话)",
            }},
        }
        payload = {"version": 1, "seasons": {"2026-Q3": entry}}
        seasonal.SEASONAL_SOURCE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with patch("seasonal_service.fetch_seasonal_candidates", return_value=[seasonal._candidate(official)]), patch(
            "seasonal_service._season_source_entry", return_value=(payload, entry, "")
        ), patch("seasonal_service._update_yuc_source_entry", return_value=""), patch(
            "seasonal_service.match_kisssub_titles",
            return_value=([seasonal._candidate(scheduled)], {"半年连载动画": 902}, []),
        ), patch("seasonal_service.preload_seasonal_posters", return_value=0):
            refreshed, count = seasonal.refresh_current_season(datetime(2026, 7, 2))
        self.assertEqual((refreshed["season_code"], count), ("Q3", 1))
        rows = db.list_seasonal_anime(2026, "Q3", include_unconfirmed=True)
        self.assertEqual([row["bangumi_id"] for row in rows], [902])

    def test_form_hides_watch_dates_but_keeps_release_date_and_year(self):
        app = AppTest.from_file("app.py", default_timeout=30).run()
        next(button for button in app.button if button.key == "sidebar_nav_新增条目").click().run()
        labels = {item.label for item in app.text_input}
        self.assertNotIn("开始日期", labels)
        self.assertNotIn("完成日期", labels)
        self.assertTrue("首播日期" in labels or "发售日期" in labels)
        self.assertIn("年份", {item.label for item in app.number_input})


if __name__ == "__main__":
    unittest.main()
