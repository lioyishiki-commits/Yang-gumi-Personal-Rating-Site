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
        self.original_refresh_paths = (
            seasonal.SEASONAL_SOURCE_PATH,
            seasonal.MISSING_COVER_REFRESH_PATH,
            seasonal.RATING_PRECISION_REFRESH_PATH,
            seasonal.SAVED_PUBLIC_REFRESH_PATH,
            seasonal.PUBLIC_DATA_REFRESH_PATH,
        )
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
        seasonal.SEASONAL_SOURCE_PATH = root / "seasonal_title_sources.json"
        seasonal.MISSING_COVER_REFRESH_PATH = root / "missing_cover_refresh.json"
        seasonal.RATING_PRECISION_REFRESH_PATH = root / "rating_precision_refresh.json"
        seasonal.SAVED_PUBLIC_REFRESH_PATH = root / "saved_bangumi_refresh.json"
        seasonal.PUBLIC_DATA_REFRESH_PATH = root / "public_data_refresh.json"
        db.init_db()

    def tearDown(self):
        db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = self.original_paths
        (
            seasonal.SEASONAL_SOURCE_PATH,
            seasonal.MISSING_COVER_REFRESH_PATH,
            seasonal.RATING_PRECISION_REFRESH_PATH,
            seasonal.SAVED_PUBLIC_REFRESH_PATH,
            seasonal.PUBLIC_DATA_REFRESH_PATH,
        ) = self.original_refresh_paths
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

    def test_homepage_rejects_short_tv_and_non_tv(self):
        short = subject(105)
        short["date"] = "2026-07-05"
        short["infobox"] = [{"key": "每话时长", "value": "约 5 分钟"}]
        cached = {**seasonal._candidate(short), "season_year": 2026, "season_code": "Q3"}
        self.assertTrue(seasonal.is_short_episode_anime(cached))
        self.assertFalse(seasonal.is_homepage_seasonal_anime(cached))
        movie = {**short, "id": 106, "platform": "剧场版", "name_cn": "测试动画 剧场版"}
        cached_movie = {**seasonal._candidate(movie), "season_year": 2026, "season_code": "Q3"}
        self.assertFalse(seasonal.is_tv_seasonal_anime(cached_movie))
        self.assertFalse(seasonal.is_homepage_seasonal_anime(cached_movie))

    def test_seasonal_cache_uses_real_two_decimal_perspective_score(self):
        item = seasonal._candidate(subject(701))
        db.upsert_seasonal_anime([item], 2026, "Q2", "4月番")
        (db.DATA_DIR / "bangumi_rating_precision.json").write_text(json.dumps({
            "version": 1,
            "items": {
                "701": {
                    "score": 8.17,
                    "votes": 4876,
                    "date": datetime.now().date().isoformat(),
                },
            },
        }, ensure_ascii=False), encoding="utf-8")

        cached = db.list_seasonal_anime(2026, "Q2", include_unconfirmed=True)[0]

        self.assertEqual(cached["bangumi_score"], 8.17)
        self.assertEqual(cached["bangumi_total_votes"], 4876)
        self.assertEqual(cached["precision_source"], "bangumi-rating-perspective")

    def test_bgm_calendar_parser_reads_subject_ids_and_weekdays(self):
        page = """
        <li class="week "><dl><dt class="Sun"><div><h3>星期日</h3></div></dt><dd class="Sun">
        <ul class="coverList"><li style="background:url('//lain.bgm.tv/a.jpg')"><div class="info">
        <p><a href="/subject/501" class="nav">日曜动画</a></p>
        <p><a href="/subject/501" class="nav"><small><em>Sunday Anime</em></small></a></p>
        </div></li></ul></dd></dl></li>
        <li class="week Sat"><dl><dt class="Sat"><div><h3>星期六</h3></div></dt><dd class="Sat">
        <ul class="coverList"><li style="background:url('https://lain.bgm.tv/b.jpg')"><div class="info">
        <p><a href="/subject/502" class="nav">土曜动画</a></p>
        <p><a href="/subject/502" class="nav"><small><em>Saturday Anime</em></small></a></p>
        </div></li></ul></dd></dl></li>
        """
        rows = seasonal.parse_bgm_calendar_entries(page)
        self.assertEqual([row["bangumi_id"] for row in rows], [501, 502])
        self.assertEqual([row["broadcast_day"] for row in rows], [6, 5])
        self.assertEqual(rows[0]["poster_url"], "https://lain.bgm.tv/a.jpg")

    def test_calendar_resolution_uses_subject_id_and_rejects_movies(self):
        tv = subject(701)
        tv["date"] = "2026-07-04"
        movie = {**subject(702), "platform": "剧场版", "date": "2026-08-01"}
        entries = [
            {"bangumi_id": 701, "title": "TV", "broadcast_day": 5, "broadcast_day_label": "周六",
             "broadcast_time": "", "broadcast_sort": 8639, "poster_url": ""},
            {"bangumi_id": 702, "title": "Movie", "broadcast_day": 5, "broadcast_day_label": "周六",
             "broadcast_time": "", "broadcast_sort": 8639, "poster_url": ""},
        ]
        rows, failures = seasonal.resolve_bgm_calendar_entries(
            entries, seasonal.current_season(datetime(2026, 7, 1)), {701: tv, 702: movie},
        )
        self.assertEqual([row["bangumi_id"] for row in rows], [701])
        self.assertEqual(failures, [702])
        raw = json.loads(rows[0]["raw_json"])
        self.assertEqual(raw["_yanggumi_season_source"], "bgm_calendar")

    def test_calendar_subject_ids_are_reused_for_yuc_aliases(self):
        calendar = [{
            "bangumi_id": 801, "title": "无职转生 第三季", "original_title": "無職転生Ⅲ",
        }]
        matches = seasonal.calendar_matches_for_titles(calendar, {
            "无职转生 第3期": ["無職転生Ⅲ"], "另一部动画": ["Another Anime"],
        })
        self.assertEqual(matches, {"无职转生 第3期": 801})

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
        response = MagicMock(text=page, url="https://yuc.wiki/202607/", status_code=200, content=page.encode())
        response.raise_for_status.return_value = None
        with patch("seasonal_service.requests.get", return_value=response):
            rows = seasonal.fetch_yuc_season_entries(seasonal.current_season(datetime(2026, 7, 1)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Re:从零开始的异世界生活 第4期 P2")
        self.assertIn("Re:从零开始的异世界生活 第4期 Part.2 夺还篇", rows[0]["aliases"])
        self.assertIn("Re:ゼロから始める異世界生活 4th season 奪還編", rows[0]["aliases"])
        self.assertEqual((rows[0]["broadcast_day"], rows[0]["broadcast_time"]), (2, "21:00"))
        self.assertEqual(rows[0]["yuc_reference_count"], 2)
        self.assertEqual(rows[0]["yuc_schedule_count"], 1)
        self.assertEqual(rows[0]["yuc_source_url"], "https://yuc.wiki/202607/")
        self.assertEqual(rows[0]["yuc_http_status"], 200)

    def test_yuc_fetch_falls_back_to_http_when_https_certificate_fails(self):
        page = """
        <table><tr><td class="date2">周一 (月)</td></tr></table>
        <div style="float:left"><div class="div_date"><p>21:00~</p><img src="same.jpg"></div>
        <div><table><tr><td class="date_title_">当季作品</td></tr></table></div></div>
        <p class="intro">details</p>
        <div style="float:left"><img data-src="same.jpg"></div>
        <div><table><tr><td><p class="title_cn_r1">当季作品</p></td></tr></table></div>
        """
        response = MagicMock(text=page, url="http://yuc.wiki/202607/", status_code=200, content=page.encode())
        response.raise_for_status.return_value = None
        with patch(
            "seasonal_service.requests.get",
            side_effect=[seasonal.requests.exceptions.SSLError("expired"), response],
        ) as request:
            rows = seasonal.fetch_yuc_season_entries(seasonal.current_season(datetime(2026, 7, 1)))
        self.assertEqual([item["title"] for item in rows], ["当季作品"])
        self.assertEqual(request.call_args_list[0].args[0], "https://yuc.wiki/202607/")
        self.assertEqual(request.call_args_list[1].args[0], "http://yuc.wiki/202607/")

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
        watching = db.get_work(work_id)
        self.assertEqual(watching["score_total"], None)
        self.assertEqual(watching["status"], "在看")
        db.save_work({**db.get_work(work_id), "status": "已看", "score_total": 8.88,
                      "short_review": "保留短评", "start_date": "2026-04-03", "finish_date": "2026-06-20"}, work_id=work_id)
        _, should_edit = seasonal.set_candidate_status(cache["id"], "弃置")
        saved = db.get_work(work_id)
        self.assertTrue(should_edit)
        self.assertEqual(saved["status"], "弃置")
        self.assertEqual(saved["score_total"], 8.88)
        self.assertEqual(saved["short_review"], "保留短评")
        self.assertEqual(saved["start_date"], "2026-04-03")
        self.assertEqual(saved["finish_date"], "2026-06-20")
        _, should_edit_again = seasonal.set_candidate_status(cache["id"], "弃置")
        self.assertFalse(should_edit_again)
        seasonal.set_candidate_status(cache["id"], "已看")
        self.assertEqual(db.get_work(work_id)["status"], "已看")

    def test_sync_meta_prevents_implicit_repeat_decision(self):
        db.mark_seasonal_sync(2026, "Q2", "success")
        meta = db.seasonal_cache_meta(2026, "Q2")
        self.assertEqual(meta["status"], "success")
        self.assertIsNotNone(meta["last_sync"])

    def test_daily_gate_refreshes_only_once_for_the_local_date(self):
        now = datetime(2026, 7, 2, 0, 0, 1)
        manifest = {"seasons": {"2026-Q3": {"cache_revision": seasonal.SEASONAL_CACHE_REVISION}}}
        with patch("seasonal_service._load_source_manifest", return_value=manifest), patch(
            "seasonal_service.db.seasonal_cache_meta", return_value={"last_sync": "2026-07-02T00:00:00"}
        ), patch(
            "seasonal_service.refresh_current_season"
        ) as refresh:
            changed, season, count = seasonal.refresh_current_season_if_due(now)
        self.assertFalse(changed)
        self.assertEqual((season["season_code"], count), ("Q3", 0))
        refresh.assert_not_called()

    def test_daily_gate_rebuilds_same_day_cache_from_an_older_algorithm(self):
        now = datetime(2026, 7, 2, 14, 53, 0)
        expected = seasonal.current_season(now)
        with patch("seasonal_service._load_source_manifest", return_value={"seasons": {}}), patch(
            "seasonal_service.db.seasonal_cache_meta", return_value={"last_sync": "2026-07-02T00:00:00"}
        ), patch("seasonal_service.refresh_current_season", return_value=(expected, 70)) as refresh:
            changed, season, count = seasonal.refresh_current_season_if_due(now)
        self.assertTrue(changed)
        self.assertEqual((season["season_code"], count), ("Q3", 70))
        refresh.assert_called_once_with(now)

    def test_startup_catchup_runs_in_a_background_thread(self):
        original_started = seasonal._scheduler_started
        seasonal._scheduler_started = False
        try:
            with patch("seasonal_service.threading.Thread") as thread:
                seasonal.start_midnight_refresh_scheduler()
            targets = [call.kwargs.get("target") for call in thread.call_args_list]
            self.assertIn(seasonal._public_data_catchup, targets)
            self.assertIn(seasonal._midnight_refresh_scheduler, targets)
            self.assertEqual(thread.return_value.start.call_count, 2)
        finally:
            seasonal._scheduler_started = original_started

    def test_background_season_catchup_does_not_escape_network_errors(self):
        with patch("seasonal_service.refresh_current_season_if_due", side_effect=RuntimeError("offline")):
            seasonal._current_season_catchup()

    def test_saved_public_refresh_preserves_every_personal_field_and_manual_tag(self):
        item = seasonal._candidate(subject())
        db.upsert_seasonal_anime([item], 2026, "Q2", "4月番")
        cache = db.list_seasonal_anime(2026, "Q2")[0]
        work_id, _ = seasonal.set_candidate_status(cache["id"], "在看")
        current = db.get_work(work_id)
        db.save_work({
            **current,
            "status": "已看",
            "score_total": 8.88,
            "short_review": "保留短评",
            "cover_path": "private-art.webp",
            "start_date": "2026-04-03",
            "finish_date": "2026-06-20",
        }, tags=[("私人标签", "自定义")], work_id=work_id)
        updated_subject = subject()
        updated_subject["rating"] = {"score": 8.2, "rank": 90, "total": 5000}
        updated_subject["summary"] = "新的公开简介"
        with patch("seasonal_service.bgm.get_subject", return_value=updated_subject):
            count = seasonal.refresh_saved_bangumi_public_fields(datetime(2026, 8, 9), max_workers=1)
        saved = db.get_work(work_id)
        self.assertEqual(count, 1)
        self.assertEqual(saved["status"], "已看")
        self.assertEqual(saved["score_total"], 8.88)
        self.assertEqual(saved["short_review"], "保留短评")
        self.assertEqual(saved["cover_path"], "private-art.webp")
        self.assertEqual(saved["start_date"], "2026-04-03")
        self.assertEqual(saved["finish_date"], "2026-06-20")
        self.assertIn("私人标签", [tag["name"] for tag in saved["tags"]])
        self.assertEqual(saved["bangumi_summary"], "新的公开简介")

    def test_unified_daily_refresh_runs_every_public_component_once(self):
        with patch("seasonal_service._season_refresh_result", return_value={"changed": True, "count": 70}) as season, patch(
            "seasonal_service._archive_refresh_result", return_value={"changed": True, "count": 100}
        ) as archive, patch(
            "seasonal_service._saved_public_refresh_result", return_value={"changed": True, "count": 2}
        ) as saved, patch(
            "seasonal_service.refresh_missing_anime_covers_if_due", return_value=(True, 3)
        ) as covers, patch(
            "seasonal_service._ranking_refresh_result", return_value={"changed": True, "count": 10}
        ) as ranking, patch(
            "seasonal_service.refresh_precise_anime_ratings_if_due", return_value=(True, 4)
        ) as precise:
            changed, status = seasonal.refresh_public_data_if_due(datetime(2026, 8, 9, 0, 0, 1))
        self.assertTrue(changed)
        self.assertEqual(status["status"], "success")
        self.assertEqual(status["scheduled_for"], "2026-08-09T00:00:00")
        self.assertEqual(status["personal_data_policy"], "excluded")
        for mocked in (season, archive, saved, covers, ranking, precise):
            mocked.assert_called_once()

    def test_one_failed_public_component_does_not_block_the_remaining_refreshes(self):
        with patch("seasonal_service._season_refresh_result", side_effect=RuntimeError("calendar offline")), patch(
            "seasonal_service._archive_refresh_result", return_value={"changed": False, "count": 100}
        ) as archive, patch(
            "seasonal_service._saved_public_refresh_result", return_value={"changed": True, "count": 2}
        ) as saved, patch(
            "seasonal_service.refresh_missing_anime_covers_if_due", return_value=(False, 0)
        ) as covers, patch(
            "seasonal_service._ranking_refresh_result", return_value={"changed": True, "count": 10}
        ) as ranking, patch(
            "seasonal_service.refresh_precise_anime_ratings_if_due", return_value=(True, 4)
        ) as precise:
            changed, status = seasonal.refresh_public_data_if_due(datetime(2026, 8, 9, 0, 0, 1))
        self.assertTrue(changed)
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["components"]["current_season"]["status"], "error")
        for mocked in (archive, saved, covers, ranking, precise):
            mocked.assert_called_once()

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
            "seasonal_service.fetch_bgm_calendar_entries", side_effect=RuntimeError("verification")
        ), patch(
            "seasonal_service.fetch_yuc_season_entries", side_effect=RuntimeError("offline")
        ), patch("seasonal_service.bgm.get_subject", side_effect=RuntimeError("offline")), patch(
            "seasonal_service.preload_seasonal_posters", return_value=0
        ):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                seasonal.refresh_current_season(datetime(2026, 7, 2))
        rows = db.list_seasonal_anime(2026, "Q3", include_unconfirmed=True)
        self.assertEqual([row["bangumi_id"] for row in rows], [808])

    def test_refresh_with_yuc_schedule_hides_unscheduled_official_and_calendar_candidates(self):
        season = seasonal.current_season(datetime(2026, 7, 2))
        official = subject(901)
        official["date"] = "2026-07-03"
        calendar_only = subject(903)
        calendar_only["date"] = "2026-07-04"
        scheduled = subject(902)
        scheduled["date"] = "2026-04-06"
        scheduled = seasonal._mark_season_subject(
            scheduled, "半年连载动画", "yuc", broadcast={
                "day": 0, "day_label": "周一", "time": "23:00", "sort": 1020, "note": "(全24话)",
            },
        )
        entry = {
            "titles": [], "matches": {"半年连载动画": 902}, "yuc_titles": ["半年连载动画"],
            "yuc_reference_count": 1,
            "calendar_entries": [{"bangumi_id": 903, "title": "日历额外动画"}],
            "yuc_broadcasts": {"半年连载动画": {
                "day": 0, "day_label": "周一", "time": "23:00", "sort": 1020, "note": "(全24话)",
            }},
        }
        payload = {"version": 1, "seasons": {"2026-Q3": entry}}
        seasonal.SEASONAL_SOURCE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with patch("seasonal_service.fetch_seasonal_candidates", return_value=[seasonal._candidate(official)]), patch(
            "seasonal_service._season_source_entry", return_value=(payload, entry, "")
        ), patch("seasonal_service._update_yuc_source_entry", return_value=""), patch(
            "seasonal_service.resolve_bgm_calendar_entries",
            return_value=([seasonal._candidate(calendar_only)], []),
        ), patch(
            "seasonal_service.match_kisssub_titles",
            return_value=([seasonal._candidate(scheduled)], {"半年连载动画": 902}, []),
        ), patch("seasonal_service.preload_seasonal_posters", return_value=0):
            refreshed, count = seasonal.refresh_current_season(datetime(2026, 7, 2))
        self.assertEqual((refreshed["season_code"], count), ("Q3", 1))
        rows = db.list_seasonal_anime(2026, "Q3", include_unconfirmed=True)
        self.assertEqual([row["bangumi_id"] for row in rows], [902])

    def test_refresh_rejects_count_more_than_five_away_from_yuc_reference(self):
        entry = {
            "matches": {},
            "yuc_titles": [f"季度动画 {index}" for index in range(10)],
            "yuc_reference_count": 1,
            "calendar_entries": [],
            "yuc_aliases": {},
            "yuc_broadcasts": {},
        }
        payload = {"version": 1, "seasons": {"2026-Q3": entry}}
        seeded = []
        matches = {}
        for index, title in enumerate(entry["yuc_titles"], start=1000):
            item = subject(index)
            item["date"] = "2026-07-03"
            seeded.append(seasonal._candidate(seasonal._mark_season_subject(item, title, "yuc")))
            matches[title] = index
        with patch("seasonal_service.fetch_seasonal_candidates", return_value=[]), patch(
            "seasonal_service._season_source_entry", return_value=(payload, entry, "")
        ), patch("seasonal_service._update_yuc_source_entry", return_value=""), patch(
            "seasonal_service.resolve_bgm_calendar_entries", return_value=([], [])
        ), patch(
            "seasonal_service.match_kisssub_titles", return_value=(seeded, matches, [])
        ), patch("seasonal_service.preload_seasonal_posters", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "超过允许的 5 部"):
                seasonal.refresh_current_season(datetime(2026, 7, 2))
        self.assertEqual(db.list_seasonal_anime(2026, "Q3", include_unconfirmed=True), [])

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
