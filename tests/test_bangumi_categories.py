from __future__ import annotations

import unittest
import tempfile
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import bangumi_client as bgm
import database as db
from streamlit.testing.v1 import AppTest


class BangumiCategoryTest(unittest.TestCase):
    def test_japanese_animation_adaptations_are_not_rejected_by_source_country_tags(self):
        korean_source = {
            "id": 1,
            "type": 2,
            "name": "神之塔 -Tower of God- 2nd Season",
            "tags": [
                {"name": "韩国原作改编"},
                {"name": "韩国"},
                {"name": "日本"},
                {"name": "日本动画"},
            ],
        }
        western_ip = {
            "id": 2,
            "type": 2,
            "name": "異世界スーサイド・スクワッド",
            "tags": [{"name": "欧美"}, {"name": "日本"}, {"name": "日本动画"}],
        }
        self.assertEqual(bgm.japanese_source_status(korean_source), "confirmed")
        self.assertEqual(bgm.japanese_source_status(western_ip), "confirmed")

    def test_explicit_non_japanese_animation_tags_stay_excluded(self):
        for subject in (
            {"type": 2, "name": "国产作品", "tags": [{"name": "国产"}, {"name": "日本"}]},
            {"type": 2, "name": "US Animation", "tags": [{"name": "美国动画"}]},
            {"type": 2, "name": "非日本作品", "tags": [{"name": "非日本动画"}]},
        ):
            with self.subTest(subject=subject["name"]):
                self.assertEqual(bgm.japanese_source_status(subject), "excluded")

    def test_previous_ranking_cache_is_reused_for_incremental_rule_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            cache_path.write_text(json.dumps({
                "version": bgm.RANKING_CACHE_VERSION - 1,
                "quarter": bgm.ranking_quarter_key(),
                "categories": {
                    "动画": {
                        "items": [{"id": 1, "rank": None}, {"id": 2, "rank": 2}],
                        "loaded_offset": 100,
                        "complete": True,
                    }
                },
            }), encoding="utf-8")
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path):
                loaded = bgm._load_ranking_disk_cache()
        category = loaded["categories"]["动画"]
        self.assertEqual(loaded["version"], bgm.RANKING_CACHE_VERSION)
        self.assertEqual([item["id"] for item in category["items"]], [2, 1])
        self.assertEqual(category["loaded_offset"], 100)
        self.assertTrue(category["complete"])

    def test_complete_cache_does_not_require_nominal_7200_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            cache_path.write_text(json.dumps({
                "version": bgm.RANKING_CACHE_VERSION,
                "quarter": bgm.ranking_quarter_key(),
                "categories": {
                    "动画": {
                        "items": [{"id": 1, "rank": 1}, {"id": 2, "rank": 2}],
                        "complete": True,
                    }
                },
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path):
                self.assertEqual(bgm.ranking_cache_count("动画"), 2)
                self.assertTrue(bgm.ranking_cache_complete("动画"))

    def test_ranking_page_capacity_grows_beyond_the_current_300_page_reserve(self):
        self.assertEqual(bgm.ranking_browser_capacity(7136), 7200)
        self.assertEqual(bgm.ranking_browser_capacity(7201), 7201)
        self.assertEqual(bgm.ranking_browser_capacity(7380), 7380)

    def test_parallel_prewarm_builds_every_ranking_page_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            subjects = [{"id": value, "rank": value} for value in range(1, 7)]

            def request(_method, _path, **kwargs):
                offset = int(kwargs["params"]["offset"])
                return {
                    "data": subjects if offset == 0 else [],
                    "total": len(subjects),
                }

            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path), patch.object(
                bgm, "RANKING_MAX_ITEMS", 6
            ), patch.object(
                bgm, "_request", side_effect=request
            ), patch.object(
                bgm, "_ranking_category_matches", return_value=True
            ), patch.object(
                bgm, "_ranking_item_from_subject",
                side_effect=lambda subject: {
                    "id": subject["id"], "rank": subject["rank"], "title": str(subject["id"])
                },
            ):
                bgm._ranking_cache.clear()
                bgm._ranking_window_cache.clear()
                count = bgm._prewarm_ranking_capacity("动画", max_workers=1)
                cached = json.loads(cache_path.read_text(encoding="utf-8"))

        rows = cached["categories"]["动画"]["items"]
        self.assertEqual(count, 6)
        self.assertEqual([row["id"] for row in rows], [1, 2, 3, 4, 5, 6])
        self.assertEqual(len({row["id"] for row in rows}), 6)
        self.assertTrue(cached["categories"]["动画"]["complete"])

    def test_filtered_ranking_pages_are_contiguous_and_reuse_full_api_batches(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            subjects = [{"id": value, "rank": value} for value in range(1, 7)]
            offsets: list[int] = []

            def request(_method, _path, **kwargs):
                offset = int(kwargs["params"]["offset"])
                offsets.append(offset)
                return {
                    "data": subjects if offset == 0 else [],
                    "total": len(subjects),
                }

            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path), patch.object(
                bgm, "_request", side_effect=request
            ), patch.object(
                bgm, "_ranking_category_matches", return_value=True
            ), patch.object(
                bgm, "_ranking_item_from_subject",
                side_effect=lambda subject: {
                    "id": subject["id"], "rank": subject["rank"], "title": str(subject["id"])
                },
            ):
                bgm._ranking_cache.clear()
                bgm._ranking_window_cache.clear()
                first = bgm.ranked_browser_subject_window("动画", 0, 4)
                second = bgm.ranked_browser_subject_window("动画", 3, 4)

        self.assertEqual([item["id"] for item in first[:3]], [1, 2, 3])
        self.assertEqual([item["id"] for item in second[:3]], [4, 5, 6])
        self.assertEqual(set(item["id"] for item in first[:3]) & set(item["id"] for item in second[:3]), set())
        self.assertEqual(offsets, [0])

    def test_cached_precise_scores_never_start_network_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "precision.json"
            cache_path.write_text(json.dumps({
                "version": bgm.RATING_PRECISION_CACHE_VERSION,
                "items": {
                    "265": {
                        "score": 8.33,
                        "votes": 8690,
                        "date": datetime.now().date().isoformat(),
                    }
                },
            }), encoding="utf-8")
            with patch.object(bgm, "RATING_PRECISION_CACHE_PATH", cache_path), patch.object(
                bgm, "_fetch_rating_perspective"
            ) as fetch:
                rows = bgm.enrich_precise_anime_ratings(
                    [{"id": 265, "score": 8.3, "votes": 8600}],
                    allow_network=False,
                )
        fetch.assert_not_called()
        self.assertEqual(rows[0]["score"], 8.33)
        self.assertEqual(rows[0]["votes"], 8690)

    def test_public_character_endpoint_keeps_voice_actors(self):
        payload = [{"name": "角色", "actors": [{"name": "声优"}]}]
        with patch.object(bgm, "_request", return_value=payload) as request:
            self.assertEqual(bgm.get_subject_characters(123), payload)
        request.assert_called_once_with("GET", "/subjects/123/characters")

    def test_category_search_maps_to_public_subject_types(self):
        expected = {
            "动画": [2], "漫画": [1], "轻小说": [1],
            "游戏": [4], "其他": [1, 2, 4],
        }
        for category, subject_types in expected.items():
            with self.subTest(category=category), patch.object(bgm, "_request", return_value={"data": []}) as request:
                bgm.search_subjects_by_category("测试", category)
                self.assertEqual(request.call_args.kwargs["json"]["filter"]["type"], subject_types)

        with patch.object(bgm, "_request", return_value={"data": []}) as request:
            bgm.search_subjects_by_category("测试", "全部")
            self.assertEqual(request.call_args.kwargs["json"]["filter"]["type"], [1, 2, 4])

    def test_book_results_keep_requested_category_when_ambiguous(self):
        subject = {"id": 1, "type": 1, "name": "テスト", "tags": [], "images": {}, "rating": {}}
        self.assertEqual(bgm.infer_local_category(subject, "漫画"), "漫画")
        self.assertEqual(bgm.infer_local_category(subject, "轻小说"), "轻小说")

    def test_binding_suggests_editable_local_type_and_subtype(self):
        anime = {"id": 2, "type": 2, "name_cn": "空之境界", "name": "空の境界", "date": "2007-12-01", "tags": [{"name": "剧场版"}], "images": {}, "rating": {}}
        fields = bgm.suggested_local_fields(anime, "空之境界", "动画")
        self.assertEqual(fields["type"], "动画")
        self.assertEqual(fields["subtype"], "剧场版")
        self.assertEqual(fields["status"], "已看")
        self.assertEqual(fields["year"], 2007)

        web = {"id": 4, "type": 2, "name": "WEBアニメ", "platform": "WEB", "images": {}, "rating": {}}
        special = {"id": 5, "type": 2, "name": "特別編", "platform": "SP", "images": {}, "rating": {}}
        self.assertEqual(bgm.infer_local_subtype(web, "动画"), "WEB")
        self.assertEqual(bgm.infer_local_subtype(special, "动画"), "SP")

        novel = {"id": 3, "type": 1, "name": "涼宮ハルヒ", "tags": [{"name": "轻小说"}], "images": {}, "rating": {}}
        fields = bgm.suggested_local_fields(novel, "凉宫春日", "轻小说")
        self.assertEqual((fields["type"], fields["subtype"]), ("轻小说", "轻小说"))

    def test_existing_bangumi_draft_reuses_record_and_preserves_local_fields(self):
        original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
            try:
                db.init_db()
                work_id = db.save_work({
                    "title": "旧标题", "type": "动画", "status": "在看", "bangumi_id": 77,
                    "private_note": "保留笔记", "short_review": "保留短评",
                }, [("私人标签", "其他")])
                draft = db.merge_existing_bangumi_draft({
                    "title": "新标题", "type": "动画", "status": "已看", "bangumi_id": 77,
                })
                self.assertEqual(draft["_existing_work_id"], work_id)
                self.assertEqual(draft["private_note"], "保留笔记")
                self.assertEqual(draft["short_review"], "保留短评")
                self.assertEqual(draft["status"], "已看")
                self.assertEqual(draft["tags"][0]["name"], "私人标签")
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_paths

    def test_add_and_match_pages_expose_category_search(self):
        original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
            try:
                db.init_db()
                work_id = db.save_work({"title": "凉宫春日", "type": "轻小说", "subtype": "轻小说"})

                add = AppTest.from_file("app.py", default_timeout=30).run()
                next(button for button in add.button if button.key == "sidebar_nav_新增条目").click().run()
                add_category = next(radio for radio in add.radio if radio.key == "add_search_category")
                self.assertEqual(add_category.options, list(bgm.CATEGORY_LABELS))
                self.assertEqual(add_category.value, "动画")
                add_category.set_value("动画").run()
                with patch.object(bgm, "search_subjects_by_category", return_value=[{"id": 1, "type": 2, "name": "空の境界", "images": {}, "rating": {}}]) as search:
                    next(item for item in add.text_input if item.key == "add_query").set_value("  空之境界  ").run()
                self.assertEqual(search.call_args.args[:2], ("空之境界", "动画"))

                match = AppTest.from_file("app.py", default_timeout=30).run()
                match.session_state["match_work_id"] = work_id
                next(button for button in match.button if button.key == "sidebar_nav_Bangumi").click().run()
                match_category = next(radio for radio in match.radio if radio.key == f"match_search_category_{work_id}")
                self.assertEqual(match_category.value, "轻小说")
                with patch.object(bgm, "search_subjects_by_category", return_value=[{"id": 2, "type": 1, "name": "涼宮ハルヒ", "images": {}, "rating": {}}]) as search:
                    next(item for item in match.text_input if item.key == f"match_query_{work_id}").set_value("凉宫春日的忧郁").run()
                self.assertEqual(search.call_args.args[:2], ("凉宫春日的忧郁", "轻小说"))
                self.assertEqual(list(match.exception), [])
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_paths

    def test_rematching_does_not_overwrite_personal_scores_or_reviews(self):
        original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
            try:
                db.init_db()
                work_id = db.save_work({
                    "title": "旧标题", "type": "动画", "subtype": "TV",
                    "score_total": 9.4, "short_review": "我的短评", "long_review": "我的长评",
                })
                fields = bgm.binding_fields({
                    "id": 265, "type": 2, "name_cn": "新世纪福音战士",
                    "name": "新世紀エヴァンゲリオン", "images": {}, "rating": {},
                }, "旧标题", "")
                db.update_bangumi(work_id, fields)
                saved = db.get_work(work_id)
                self.assertEqual(saved["score_total"], 9.4)
                self.assertEqual(saved["short_review"], "我的短评")
                self.assertEqual(saved["long_review"], "我的长评")
                self.assertEqual(saved["type"], "动画")
                self.assertEqual(saved["subtype"], "TV")
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_paths


if __name__ == "__main__":
    unittest.main()
