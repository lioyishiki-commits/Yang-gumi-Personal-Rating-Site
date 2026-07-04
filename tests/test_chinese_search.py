"""Live regression test for UTF-8 Bangumi search and local SQLite search."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bangumi_client as bgm
import database as db


CASES = {
    "新世纪福音战士": "福音",
    "空之境界": "境界",
    "葬送的芙莉莲": "芙莉莲",
    "孤独摇滚": "孤独",
    "命运石之门": "命运",
}


def choose_relevant(results: list[dict], keyword: str) -> dict:
    exact = [item for item in results if (item.get("name_cn") or "").strip() == keyword]
    contained = [item for item in results if keyword in (item.get("name_cn") or "")]
    return (exact or contained or results)[0]


class ChineseSearchTest(unittest.TestCase):
    def test_title_normalization_and_relevance_levels(self) -> None:
        self.assertEqual(bgm.normalize_title(" 水星领航员　第3季！ "), "水星领航员season3")
        self.assertEqual(bgm.normalize_title("ARIA: The ORIGINATION"), "ariatheorigination")
        exact = bgm.score_title_relevance("CLANNAD", {"name": "CLANNAD"})
        related = bgm.score_title_relevance("水星领航员 第三季", {"name_cn": "水星领航员 The ORIGINATION"})
        unrelated = bgm.score_title_relevance("银魂", {"name_cn": "葬送的芙莉莲"})
        self.assertEqual(exact["level"], "strict_exact")
        self.assertEqual(related["level"], "series_related")
        self.assertEqual(unrelated["level"], "irrelevant")

    def test_rank_search_results_hides_foreign_and_unrelated_items(self) -> None:
        subjects = [
            {"id": 1, "type": 2, "name_cn": "银魂", "name": "銀魂", "tags": [{"name": "日本动画"}]},
            {"id": 2, "type": 2, "name_cn": "银魂 第二季", "name": "銀魂'", "tags": [{"name": "日本"}]},
            {"id": 3, "type": 2, "name_cn": "无关动画", "name": "OTHER"},
            {"id": 4, "type": 2, "name_cn": "银魂中国版", "name": "银魂", "tags": [{"name": "国产动画"}]},
            {"id": 5, "type": 6, "name_cn": "银魂真人剧", "name": "銀魂"},
        ]
        ranked = bgm.rank_search_results("银魂", subjects)
        self.assertEqual([item["id"] for item in ranked], [1, 2])
        self.assertEqual(ranked[0]["_relevance_level"], "strict_exact")

    def test_japanese_movie_is_not_rejected_by_chinese_release_note(self) -> None:
        movie = {
            "id": 242, "type": 2, "name_cn": "穿越时空的少女", "name": "時をかける少女",
            "platform": "剧场版", "tags": [{"name": "日本"}],
            "infobox": [{"key": "其他上映日期", "value": "2025年1月11日（中国大陆）"}],
        }
        with patch.object(bgm, "_request", return_value={"data": [movie]}):
            results = bgm.search_subjects_by_category("穿越时空的少女", "动画")
        self.assertEqual([item["id"] for item in results], [242])
        self.assertEqual(results[0]["_source_status"], "confirmed")

    def test_fallback_keeps_exact_utf8_query_first(self) -> None:
        with patch.object(bgm, "_request") as request:
            request.side_effect = [{"data": []}, {"data": [{"id": 1, "name": "空の境界"}]}]
            results = bgm.search_subjects("  空 之 境界  ")
        self.assertTrue(results)
        self.assertEqual(request.call_args_list[0].kwargs["json"]["keyword"], "空 之 境界")
        self.assertEqual(request.call_args_list[1].kwargs["json"]["keyword"], "空之境界")

    def test_local_search_covers_every_requested_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
            db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = temp, temp / "acgn.db", temp / "exports"
            try:
                db.init_db()
                work_id = db.save_work({
                    "title": "新世纪福音战士", "original_title": "新世紀エヴァンゲリオン",
                    "bangumi_name_cn": "新世纪福音战士", "bangumi_name": "Neon Genesis Evangelion",
                    "short_review": "意识流短评", "long_review": "关于使徒的长评",
                    "favorite_characters": "绫波丽", "favorite_quote": "不能逃避",
                }, [("末世氛围", "氛围")])
                for term in ["福音", "エヴァ", "世纪", "EVANGELION", "意识流", "使徒", "绫波", "逃避", "末世"]:
                    self.assertIn(work_id, db.search_work_ids(term), term)
                fields = bgm.binding_fields({
                    "id": 42, "name_cn": "空之境界", "name": "空の境界", "type": 2,
                    "images": {}, "rating": {},
                }, "旧标题", "旧原名")
                db.update_bangumi(work_id, fields)
                rebound = db.get_work(work_id)
                self.assertEqual(rebound["title"], "空之境界")
                self.assertEqual(rebound["original_title"], "空の境界")
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_paths

    def test_live_bangumi_binding_and_local_partial_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
            db.DATA_DIR = temp
            db.DB_PATH = temp / "acgn.db"
            db.EXPORT_DIR = temp / "exports"
            try:
                db.init_db()
                saved: dict[str, int] = {}
                for keyword in CASES:
                    try:
                        results = bgm.search_subjects(keyword, limit=10)
                    except bgm.BangumiError as exc:
                        self.skipTest(f"Bangumi 当前不可用：{exc}")
                    self.assertTrue(results, keyword)
                    self.assertTrue(any(item.get("name_cn") for item in results), keyword)
                    self.assertTrue(any((item.get("images") or {}).get("common") or (item.get("images") or {}).get("large") for item in results), keyword)

                    selected = choose_relevant(results, keyword)
                    try:
                        detail = bgm.get_subject(selected["id"])
                    except bgm.BangumiError as exc:
                        self.skipTest(f"Bangumi 当前不可用：{exc}")
                    fields = bgm.suggested_local_fields(detail, keyword)
                    self.assertEqual(fields["title"], fields["bangumi_name_cn"] or fields["bangumi_name"])
                    self.assertEqual(fields["original_title"], fields["bangumi_name"] or keyword)
                    self.assertTrue(fields["title"])
                    saved[keyword] = db.save_work(fields)

                for keyword, partial in CASES.items():
                    self.assertIn(saved[keyword], db.search_work_ids(keyword))
                    self.assertIn(saved[keyword], db.search_work_ids(partial))

                sample = db.get_work(saved["葬送的芙莉莲"])
                self.assertEqual(sample["title"], sample["bangumi_name_cn"])
                self.assertEqual(sample["original_title"], sample["bangumi_name"])
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_paths


if __name__ == "__main__":
    unittest.main(verbosity=2)
