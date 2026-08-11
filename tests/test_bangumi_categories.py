from __future__ import annotations

import unittest
import tempfile
import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import bangumi_client as bgm
import database as db
from streamlit.testing.v1 import AppTest


class BangumiCategoryTest(unittest.TestCase):
    def test_title_normalization_handles_requested_r18_aliases(self):
        self.assertEqual(
            bgm.normalize_title("BLACKSOULS2"),
            bgm.normalize_title("BLACKSOULSII"),
        )
        self.assertEqual(
            bgm.normalize_title("\u76d1\u72f1\u52c7\u8005"),
            bgm.normalize_title("\u76e3\u7344\u52c7\u8005"),
        )

    def test_archive_subject_without_votes_does_not_fake_a_zero_score(self):
        subject = bgm._archive_subject_dictionary({
            "id": 99,
            "type": 4,
            "name": "No votes",
            "platform": 4001,
            "score": 0,
            "score_details": {str(value): 0 for value in range(1, 11)},
            "nsfw": True,
        })
        self.assertIsNone(subject["rating"]["score"])
        self.assertEqual(subject["precision_source"], "")

    def test_archive_distribution_supplies_true_precision_for_api_hidden_r18_subject(self):
        counts = {"1": 44, "2": 16, "3": 6, "4": 10, "5": 21, "6": 46,
                  "7": 94, "8": 276, "9": 735, "10": 2347}
        record = {
            "id": 226254, "type": 4, "name": "ランス10", "name_cn": "兰斯10 决战",
            "platform": 4001, "summary": "公开简介", "nsfw": True,
            "score": 9.3, "score_details": counts, "rank": 2,
            "infobox": "{{Infobox Game|开发=アリスソフト|地区=日本}}",
        }
        subject = bgm._archive_subject_dictionary(record)
        normalized = bgm.normalize_subject(subject)
        self.assertEqual(normalized["bangumi_score"], 9.31)
        self.assertEqual(normalized["bangumi_total_votes"], 3595)
        self.assertEqual(normalized["bangumi_summary"], "公开简介")
        self.assertTrue(subject["_yanggumi_nsfw"])

    def test_all_four_r18_categories_pass_their_category_gate(self):
        records = {
            "动画": {"id": 1, "type": 2, "platform": 2},
            "漫画": {"id": 2, "type": 1, "platform": 1001},
            "小说": {"id": 3, "type": 1, "platform": 1002},
            "游戏": {"id": 4, "type": 4, "platform": 4001},
        }
        for category, base in records.items():
            record = {
                **base, "name": f"R18 {category}", "nsfw": True, "rank": 10,
                "score_details": {"8": 2, "9": 2},
                "infobox": "{{Infobox|地区=日本}}",
            }
            with self.subTest(category=category):
                subject = bgm._archive_subject_dictionary(record)
                self.assertTrue(bgm._ranking_category_matches(category, subject))

    def test_category_search_merges_archive_only_r18_result(self):
        record = {
            "id": 226254, "type": 4, "name": "ランス10", "name_cn": "兰斯10 决战",
            "platform": 4001, "summary": "公开简介", "nsfw": True,
            "score_details": {"9": 1, "10": 2}, "rank": 2,
            "infobox": "{{Infobox Game|别名={[Rance 10]}|地区=日本}}",
        }
        with patch.object(bgm, "search_subjects", return_value=[]), patch.object(
            bgm, "_request_web_page", return_value=""
        ), patch.object(
            bgm.bangumi_archive, "search_archive_subjects", return_value=[record]
        ):
            results = bgm.search_subjects_by_category("Rance 10", "游戏")
        self.assertEqual(results[0]["id"], 226254)
        self.assertEqual(results[0]["rating"]["score"], 9.67)
        self.assertEqual(results[0]["_relevance_level"], "strict_exact")

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
                        "r18_included": True,
                    }
                },
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path):
                self.assertEqual(bgm.ranking_cache_count("动画"), 2)
                self.assertTrue(bgm.ranking_cache_complete("动画"))

    def test_complete_quarter_cache_is_due_again_on_the_next_local_date(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            cache_path.write_text(json.dumps({
                "version": bgm.RANKING_CACHE_VERSION,
                "quarter": "2026-Q3",
                "refreshed_on": "2026-08-08",
                "categories": {
                    category: {"items": [], "complete": True, "r18_included": True}
                    for category in bgm.RANKING_CATEGORY_LABELS
                },
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path):
                self.assertTrue(bgm.ranking_cache_refresh_due(datetime(2026, 8, 9, 0, 0, 1)))

    def test_daily_ranking_cache_is_not_due_twice_on_the_same_date(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            cache_path.write_text(json.dumps({
                "version": bgm.RANKING_CACHE_VERSION,
                "quarter": "2026-Q3",
                "refreshed_on": "2026-08-09",
                "categories": {
                    category: {"items": [], "complete": True, "r18_included": True}
                    for category in bgm.RANKING_CATEGORY_LABELS
                },
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path):
                self.assertFalse(bgm.ranking_cache_refresh_due(datetime(2026, 8, 9, 23, 59, 59)))

    def test_daily_ranking_refresh_atomically_publishes_a_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            cache_path.write_text("old-cache", encoding="utf-8")
            api_subject = {"id": 901, "rank": 1}
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path), patch.object(
                bgm, "_request", return_value={"data": [api_subject], "total": 1}
            ), patch.object(
                bgm, "_ranking_category_matches", return_value=True
            ), patch.object(
                bgm, "_ranking_item_from_subject",
                return_value={"id": 901, "rank": 1, "title": "日更测试"},
            ), patch.object(
                bgm, "_merge_public_browser_ranking_rows",
                side_effect=lambda category, results, seen, **kwargs: (results, True, 0, 0),
            ):
                counts = bgm.refresh_ranking_cache(
                    datetime(2026, 8, 9, 0, 0, 1), categories=("动画",), max_workers=1,
                )
                published = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(counts, {"动画": 1})
            self.assertEqual(published["refreshed_on"], "2026-08-09")
            self.assertTrue(published["categories"]["动画"]["complete"])
            self.assertFalse(cache_path.with_name("ranking.refreshing.json").exists())

    def test_failed_daily_ranking_refresh_keeps_the_previous_cache_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            previous = json.dumps({
                "version": bgm.RANKING_CACHE_VERSION,
                "quarter": "2026-Q3",
                "updated_at": "2026-08-08T00:10:00",
                "categories": {"动画": {"items": [{"id": 1}], "complete": True}},
            }, ensure_ascii=False).encode("utf-8")
            cache_path.write_bytes(previous)
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path), patch.object(
                bgm, "_request", side_effect=bgm.BangumiError("offline")
            ):
                with self.assertRaises(bgm.BangumiError):
                    bgm.refresh_ranking_cache(
                        datetime(2026, 8, 9, 0, 0, 1), categories=("动画",), max_workers=1,
                    )
            self.assertEqual(cache_path.read_bytes(), previous)

    def test_public_analysis_reads_the_whole_cached_category_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"
            cache_path.write_text(json.dumps({
                "version": bgm.RANKING_CACHE_VERSION,
                "quarter": bgm.ranking_quarter_key(),
                "categories": {
                    "动画": {
                        "items": [
                            {"id": 2, "rank": 2, "score": 8.0},
                            {"id": 1, "rank": 1, "score": 9.0},
                        ],
                        "complete": True,
                    }
                },
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path), patch.object(
                bgm, "_request"
            ) as request:
                rows = bgm.cached_ranking_subjects("动画")
        request.assert_not_called()
        self.assertEqual([row["id"] for row in rows], [1, 2])

    def test_cached_ranking_subjects_reuses_memory_until_the_daily_file_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            cache_path = Path(temp) / "ranking.json"

            def write_cache(subject_id: int) -> None:
                cache_path.write_text(json.dumps({
                    "version": bgm.RANKING_CACHE_VERSION,
                    "quarter": bgm.ranking_quarter_key(),
                    "categories": {
                        "动画": {
                            "items": [{"id": subject_id, "rank": 1}],
                            "complete": True,
                        }
                    },
                }, ensure_ascii=False), encoding="utf-8")

            write_cache(1)
            bgm._ranking_subjects_cache.clear()
            with patch.object(bgm, "RANKING_CACHE_PATH", cache_path), patch.object(
                bgm, "_load_ranking_disk_cache", wraps=bgm._load_ranking_disk_cache,
            ) as load_cache:
                self.assertEqual(bgm.cached_ranking_subjects("动画")[0]["id"], 1)
                self.assertEqual(bgm.cached_ranking_subjects("动画")[0]["id"], 1)
                self.assertEqual(load_cache.call_count, 1)

                previous_mtime = cache_path.stat().st_mtime_ns
                write_cache(2)
                os.utime(cache_path, ns=(previous_mtime + 1_000_000, previous_mtime + 1_000_000))
                self.assertEqual(bgm.cached_ranking_subjects("动画")[0]["id"], 2)
                self.assertEqual(load_cache.call_count, 2)
            bgm._ranking_subjects_cache.clear()

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
                bgm, "RANKING_MAX_ITEMS", 10
            ), patch.object(
                bgm, "_request", side_effect=request
            ), patch.object(
                bgm, "_ranking_category_matches", return_value=True
            ), patch.object(
                bgm, "_ranking_item_from_subject",
                side_effect=lambda subject: {
                    "id": subject["id"], "rank": subject["rank"], "title": str(subject["id"])
                },
            ), patch.object(
                bgm, "_merge_public_browser_ranking_rows",
                side_effect=lambda category, results, seen, **kwargs: (results, True, 0, 0),
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
            ), patch.object(
                bgm, "_merge_public_browser_ranking_rows",
                side_effect=lambda category, results, seen, **kwargs: (results, True, 0, 0),
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

    def test_subject_search_uses_real_perspective_score_not_zero_padding(self):
        subject = {
            "id": 234, "name": "AIR", "rating": {"score": 8, "total": 13510},
        }
        with tempfile.TemporaryDirectory() as temp, patch.object(
            bgm, "RATING_PRECISION_CACHE_PATH", Path(temp) / "precision.json"
        ), patch.object(
            bgm, "_fetch_rating_perspective",
            return_value={
                "score": 7.95, "votes": 13510,
                "date": datetime.now().date().isoformat(), "fetched_at": datetime.now().isoformat(),
            },
        ):
            enriched = bgm.enrich_precise_subject_ratings([subject])
        self.assertEqual(enriched[0]["rating"]["score"], 7.95)
        self.assertEqual(enriched[0]["rating"]["total"], 13510)
        self.assertEqual(enriched[0]["precision_source"], "bangumi-rating-perspective")

    def test_precise_search_rating_is_carried_into_selected_detail(self):
        detail = {"id": 234, "rating": {"score": 8, "total": 13510, "rank": 265}}
        search_result = {
            "id": 234, "rating": {"score": 7.95, "total": 13510},
            "precision_source": "bangumi-rating-perspective", "precision_date": "2026-08-03",
        }
        merged = bgm.merge_precise_subject_rating(detail, search_result)
        self.assertEqual(merged["rating"]["score"], 7.95)
        self.assertEqual(merged["rating"]["rank"], 265)

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
            with self.subTest(category=category), patch.object(
                bgm, "_request", return_value={"data": []}
            ) as request, patch.object(
                bgm, "_request_web_page", return_value=""
            ), patch.object(
                bgm.bangumi_archive, "search_archive_subjects", return_value=[]
            ):
                bgm.search_subjects_by_category("测试", category)
                post_call = next(call for call in request.call_args_list if "json" in call.kwargs)
                self.assertEqual(post_call.kwargs["json"]["filter"]["type"], subject_types)

        with patch.object(bgm, "_request", return_value={"data": []}) as request, patch.object(
            bgm, "_request_web_page", return_value=""
        ), patch.object(
            bgm.bangumi_archive, "search_archive_subjects", return_value=[]
        ):
            bgm.search_subjects_by_category("测试", "全部")
            post_call = next(call for call in request.call_args_list if "json" in call.kwargs)
            self.assertEqual(post_call.kwargs["json"]["filter"]["type"], [1, 2, 4])

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
