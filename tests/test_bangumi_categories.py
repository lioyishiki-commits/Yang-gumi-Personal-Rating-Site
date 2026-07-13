from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import bangumi_client as bgm
import database as db
from streamlit.testing.v1 import AppTest


class BangumiCategoryTest(unittest.TestCase):
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
        self.assertEqual(fields["year"], 2007)

        web = {"id": 4, "type": 2, "name": "WEBアニメ", "platform": "WEB", "images": {}, "rating": {}}
        special = {"id": 5, "type": 2, "name": "特別編", "platform": "SP", "images": {}, "rating": {}}
        self.assertEqual(bgm.infer_local_subtype(web, "动画"), "WEB")
        self.assertEqual(bgm.infer_local_subtype(special, "动画"), "SP")

        novel = {"id": 3, "type": 1, "name": "涼宮ハルヒ", "tags": [{"name": "轻小说"}], "images": {}, "rating": {}}
        fields = bgm.suggested_local_fields(novel, "凉宫春日", "轻小说")
        self.assertEqual((fields["type"], fields["subtype"]), ("轻小说", "轻小说"))

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

    def test_empty_ranking_cache_loads_automatically(self):
        original_cache_path = bgm.RANKING_CACHE_PATH
        original_memory_cache = dict(bgm._ranking_cache)
        with tempfile.TemporaryDirectory() as temp_dir:
            bgm.RANKING_CACHE_PATH = Path(temp_dir) / "ranking-cache.json"
            bgm._ranking_cache.clear()
            try:
                with patch.object(bgm, "ranked_browser_subject_window", return_value=[]) as fetch:
                    app = AppTest.from_file("app.py", default_timeout=30).run()
                    next(button for button in app.button if button.key == "sidebar_nav_Bangumi").click().run()
                self.assertEqual(list(app.exception), [])
                fetch.assert_called_once_with("动画", 0, 25)
                self.assertFalse(any(button.key == "bangumi_rank_first_load_动画" for button in app.button))
            finally:
                bgm.RANKING_CACHE_PATH = original_cache_path
                bgm._ranking_cache.clear()
                bgm._ranking_cache.update(original_memory_cache)

    def test_ranking_uses_official_subjects_api_and_persists_results(self):
        original_cache_path = bgm.RANKING_CACHE_PATH
        original_memory_cache = dict(bgm._ranking_cache)
        payload = {
            "total": 2,
            "data": [
                {
                    "id": 101, "type": 2, "name": "テストアニメ", "name_cn": "测试动画",
                    "date": "2026-01-01", "platform": "TV", "images": {"large": "https://img/101.jpg"},
                    "rating": {"rank": 1, "score": 9.1, "total": 1234}, "tags": [],
                },
                {
                    "id": 102, "type": 2, "name": "テストアニメ二", "name_cn": "测试动画二",
                    "date": "2026-01-02", "platform": "TV", "images": {},
                    "rating": {"rank": 2, "score": 9.0, "total": 1000}, "tags": [],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            bgm.RANKING_CACHE_PATH = Path(temp_dir) / "ranking-cache.json"
            bgm._ranking_cache.clear()
            try:
                with patch.object(bgm, "_request", return_value=payload) as request:
                    rows = bgm.ranked_browser_subjects("动画", 2)
                self.assertEqual([row["id"] for row in rows], [101, 102])
                request.assert_called_once_with(
                    "GET", "/subjects",
                    params={"type": 2, "sort": "rank", "limit": 50, "offset": 0},
                )
                disk = bgm._load_ranking_disk_cache(bgm.ranking_quarter_key())
                self.assertEqual(disk["version"], 7)
                self.assertEqual(disk["categories"]["动画"]["source"], "official-api")
                self.assertEqual(len(disk["categories"]["动画"]["items"]), 2)
            finally:
                bgm.RANKING_CACHE_PATH = original_cache_path
                bgm._ranking_cache.clear()
                bgm._ranking_cache.update(original_memory_cache)

    def test_ranking_window_jumps_directly_to_requested_offset(self):
        original_cache_path = bgm.RANKING_CACHE_PATH
        original_memory_cache = dict(bgm._ranking_window_cache)
        payload = {
            "total": 7200,
            "data": [
                {
                    "id": 901, "type": 2, "name": "テストアニメ", "name_cn": "测试动画",
                    "date": "2026-01-01", "platform": "TV", "images": {},
                    "rating": {"rank": 937, "score": 7.0, "total": 100}, "tags": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            bgm.RANKING_CACHE_PATH = Path(temp_dir) / "ranking-cache.json"
            bgm._ranking_window_cache.clear()
            try:
                with patch.object(bgm, "_request", return_value=payload) as request:
                    rows = bgm.ranked_browser_subject_window("动画", 936, 25)
                self.assertEqual(rows[0]["rank"], 937)
                request.assert_called_once_with(
                    "GET", "/subjects",
                    params={"type": 2, "sort": "rank", "limit": 50, "offset": 936},
                )
            finally:
                bgm.RANKING_CACHE_PATH = original_cache_path
                bgm._ranking_window_cache.clear()
                bgm._ranking_window_cache.update(original_memory_cache)

    def test_animation_ranking_requires_confirmed_japanese_origin(self):
        japanese = {
            "id": 700, "type": 2, "name": "NARUTO -ナルト- 疾風伝",
            "name_cn": "火影忍者疾风传", "tags": [], "images": {}, "rating": {},
        }
        foreign = [
            {"id": 697, "type": 2, "name": "Waltz with Bashir", "name_cn": "和巴什尔跳华尔兹", "tags": [{"name": "非日本動畫電影"}, {"name": "アニメ映画"}]},
            {"id": 701, "type": 2, "name": "Soul", "name_cn": "心灵奇旅", "tags": [{"name": "Pixar"}], "infobox": [{"key": "别名", "value": "ソウル"}]},
        ]
        self.assertTrue(bgm._ranking_category_matches("动画", japanese))
        for subject in foreign:
            with self.subTest(title=subject["name"]):
                self.assertNotEqual(bgm.japanese_source_status(subject), "confirmed")
                self.assertFalse(bgm._ranking_category_matches("动画", subject))

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
