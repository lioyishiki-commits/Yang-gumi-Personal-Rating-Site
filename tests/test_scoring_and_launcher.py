from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

import database as db
import scoring
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


class ScoringAndLauncherTest(unittest.TestCase):
    def test_score_item_cap_uses_group_cap_times_item_weight(self):
        config = scoring.default_score_config()
        self.assertEqual(scoring.score_item_cap("body", "score_story", config), 3.6)
        self.assertEqual(scoring.score_item_cap("body", "score_pacing", config), 0.45)
        self.assertEqual(scoring.score_item_cap("feeling", "score_personal", config), 0.28)
        self.assertEqual(scoring.score_item_cap("era", "score_influence", config), 0.18)

    def test_saved_settings_recalculate_existing_auto_scores_only(self):
        original_db_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
        original_scoring_paths = scoring.DATA_DIR, scoring.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
            scoring.DATA_DIR, scoring.SETTINGS_PATH = root, root / "scoring_settings.json"
            try:
                db.init_db()
                scoring.save_score_config(scoring.default_score_config())
                perfect_scores = {
                    field: 10.0
                    for field in list(scoring.score_weights("body")) + list(scoring.score_weights("feeling"))
                }
                auto_id = db.save_work({
                    "title": "历史自动评分",
                    "type": "动画",
                    "score_mode": "auto",
                    "score_total": scoring.calculate_total_score(perfect_scores, 0),
                    **perfect_scores,
                })
                manual_id = db.save_work({
                    "title": "历史手动评分",
                    "type": "动画",
                    "score_mode": "manual",
                    "score_total": 6.66,
                    **perfect_scores,
                })

                changed_config = scoring.default_score_config()
                changed_config["body"]["cap"] = 8.0
                scoring.save_score_config(changed_config)
                self.assertEqual(db.recalculate_auto_scores(), 1)
                self.assertEqual(db.get_work(auto_id)["score_total"], 8.7)
                self.assertEqual(db.get_work(manual_id)["score_total"], 6.66)
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_db_paths
                scoring.DATA_DIR, scoring.SETTINGS_PATH = original_scoring_paths

    def test_requested_weighted_score_and_special_gate(self):
        values = {
            "score_story": 8, "score_character": 9, "score_art": 7,
            "score_music": 8, "score_atmosphere": 9, "score_pacing": 8,
            "score_personal": 10, "rewatch_value": 8,
            "score_uniqueness": 9, "score_aftertaste": 7,
            "score_influence": 10, "score_originality": 5,
        }
        self.assertEqual(scoring.calculate_composite_score({**values, "bangumi_total_votes": 3000}), 7.77)
        self.assertEqual(scoring.calculate_composite_score({**values, "bangumi_total_votes": 3001}), 8.01)
        self.assertLessEqual(scoring.calculate_bonus_score(values), 0.7)
        self.assertLessEqual(scoring.calculate_special_score(values, 3001) or 0, 0.3)

    def test_manga_novel_and_game_use_their_approved_fixed_formulas(self):
        manga = scoring.score_config_for_type("漫画")
        self.assertEqual(manga["body"]["cap"], 8.5)
        self.assertEqual(manga["body"]["weights"], {
            "score_story": 0.40, "score_character": 0.20, "score_art": 0.20,
            "score_direction": 0.10, "score_pacing": 0.10,
        })
        self.assertEqual(manga["body"]["labels"]["score_art"], "画工")
        self.assertEqual(manga["body"]["labels"]["score_direction"], "演出 / 分镜")

        novel = scoring.score_config_for_type("轻小说")
        self.assertEqual(novel["body"]["weights"]["custom_writing"], 0.30)
        self.assertEqual(novel["body"]["labels"]["custom_writing"], "文笔")

        game = scoring.score_config_for_type("游戏")
        self.assertEqual(game["body"]["weights"]["custom_gameplay_design"], 0.20)
        self.assertEqual(game["body"]["labels"]["score_art"], "美术设计")
        self.assertEqual(game["feeling"]["labels"]["rewatch_value"], "重玩价值")
        self.assertEqual(scoring.score_config_for_type("动画")["feeling"]["labels"]["rewatch_value"], "重看价值")

        for work_type, threshold in (("漫画", 300), ("轻小说", 300), ("游戏", 1000)):
            with self.subTest(work_type=work_type):
                config = scoring.score_config_for_type(work_type)
                values = {
                    "type": work_type,
                    **{field: 10.0 for field in scoring.all_component_fields(config)},
                }
                self.assertFalse(scoring.should_show_special_scores(threshold - 1, work_type))
                self.assertTrue(scoring.should_show_special_scores(threshold, work_type))
                self.assertEqual(scoring.calculate_total_score(values, threshold), 10.0)
        self.assertFalse(scoring.should_show_special_scores(3000, "动画"))
        self.assertTrue(scoring.should_show_special_scores(3001, "动画"))

    def test_step12_requested_score_breakdown(self):
        values = {
            "score_story": 9.5, "score_character": 8.5, "score_art": 7.0,
            "score_music": 8.8, "score_atmosphere": 8.0, "score_pacing": 9.0,
            "score_personal": 9.0, "rewatch_value": 10.0,
            "score_uniqueness": 7.8, "score_aftertaste": 7.5,
            "score_influence": 9.0, "score_originality": 9.0,
            "bangumi_total_votes": 27027,
        }
        self.assertEqual(scoring.explain_score_breakdown(values), {
            "main_score": 7.8, "bonus_score": 0.61,
            "special_score": 0.27, "imbalance_gap": 2.5,
            "imbalance_penalty_cap": 0.0, "imbalance_penalty_score": None,
            "imbalance_penalty": None, "total_score": 8.68,
        })

    def test_empty_scores_and_explicit_zero(self):
        self.assertIsNone(scoring.calculate_composite_score({}))
        self.assertEqual(scoring.calculate_composite_score({"score_story": 0.0, "score_art": 10.0}), 3.0)

    def test_imbalance_penalty_tiers_ignore_public_score_and_votes(self):
        values = {
            "score_story": 10.0, "score_character": 6.4, "score_art": 9.0,
            "score_music": 9.0, "score_atmosphere": 9.0, "score_pacing": 9.0,
            "bangumi_score": 7.0, "score_imbalance_penalty": 10.0,
        }
        self.assertEqual(scoring.imbalance_penalty_cap(values), 2.0)
        self.assertEqual(scoring.calculate_imbalance_penalty(values), 2.0)
        self.assertEqual(scoring.calculate_composite_score(values), 6.74)

        mid_gap = {**values, "score_story": 9.0, "score_character": 6.4, "score_art": 8.8}
        self.assertEqual(scoring.imbalance_penalty_cap(mid_gap), 0.5)
        self.assertEqual(scoring.calculate_imbalance_penalty(mid_gap), 0.5)

        public_score_too_close = {**values, "bangumi_score": 10.0, "bangumi_total_votes": 1}
        self.assertEqual(scoring.imbalance_penalty_cap(public_score_too_close, 1), 2.0)
        self.assertEqual(scoring.calculate_imbalance_penalty(public_score_too_close, 1), 2.0)

        low_vote_work = {
            "score_story": 2.7, "score_character": 4.7, "score_art": 6.8,
            "score_music": 6.8, "score_atmosphere": 4.8, "score_pacing": 3.8,
            "score_personal": 2.5, "rewatch_value": 1.0,
            "score_uniqueness": 8.5, "score_aftertaste": 5.6,
            "score_influence": 10.0, "score_originality": 10.0,
            "bangumi_score": 5.0, "bangumi_total_votes": 1813,
            "score_imbalance_penalty": 10.0,
        }
        self.assertIsNone(scoring.calculate_special_score(low_vote_work, 1813))
        self.assertEqual(scoring.imbalance_penalty_cap(low_vote_work, 1813), 2.0)
        self.assertEqual(scoring.calculate_imbalance_penalty(low_vote_work, 1813), 2.0)

    def test_manual_total_is_detected_without_schema_change(self):
        values = {"score_story": 9.0, "score_character": 9.0, "score_total": 8.7}
        self.assertFalse(scoring.default_auto_score(values))
        self.assertTrue(scoring.default_auto_score({**values, "score_total": 8.1}))

    def test_scored_animation_is_saved_as_completed(self):
        original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
            try:
                db.init_db()
                work_id = db.save_work({
                    "title": "已评分动画", "type": "动画", "status": "想看", "score_total": 11.2,
                })
                saved = db.get_work(work_id)
                self.assertEqual(saved["status"], "已看")
                self.assertEqual(saved["score_total"], 10.0)
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_paths

    def test_dimension_update_recalculates_auto_total_but_preserves_manual_total(self):
        original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
            try:
                db.init_db()
                config = scoring.default_score_config()
                component_scores = {
                    field: 6.0
                    for field in scoring.all_component_fields(config)
                }
                auto_total = scoring.calculate_total_score(
                    component_scores,
                    5000,
                    config=config,
                )
                auto_id = db.save_work({
                    "title": "自动校准作品",
                    "type": "动画",
                    "score_mode": "auto",
                    "score_total": auto_total,
                    "bangumi_total_votes": 5000,
                    **component_scores,
                })
                manual_id = db.save_work({
                    "title": "手动校准作品",
                    "type": "游戏",
                    "score_mode": "manual",
                    "score_total": 7.7,
                    **component_scores,
                })

                updated_auto = db.update_work_component_score(
                    auto_id,
                    "score_story",
                    9.5,
                    config,
                )
                self.assertEqual(updated_auto["score_story"], 9.5)
                self.assertNotEqual(updated_auto["score_total"], auto_total)
                self.assertEqual(updated_auto["status"], "已看")

                updated_manual = db.update_work_component_score(
                    manual_id,
                    "score_story",
                    3.5,
                    config,
                )
                self.assertEqual(updated_manual["score_story"], 3.5)
                self.assertEqual(updated_manual["score_total"], 7.7)

                with self.assertRaisesRegex(ValueError, "启用的小项目"):
                    db.update_work_component_score(
                        manual_id,
                        "not_a_score",
                        5.0,
                        config,
                    )
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_paths

    def test_windows_launcher_and_fixed_port_are_present(self):
        batch = (ROOT / "启动 Yang-gumi.bat").read_text(encoding="utf-8")
        launcher = (ROOT / "start_yanggumi.py").read_text(encoding="utf-8")
        config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        self.assertIn("title Yang-gumi 本地评分库", batch)
        self.assertIn("python start_yanggumi.py", batch)
        self.assertIn("PORT = 8501", launcher)
        self.assertIn('"--server.headless", "true"', launcher)
        self.assertIn('port = 8501', config)
        self.assertIn('headless = true', config)

    def test_auto_and_manual_totals_save_to_score_total(self):
        original_paths = db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = root, root / "acgn.db", root / "exports"
            try:
                db.init_db()
                app = AppTest.from_file("app.py", default_timeout=30).run()
                next(button for button in app.button if button.key == "sidebar_nav_新增条目").click().run()
                next(item for item in app.text_input if item.label == "作品名 *").set_value("自动评分测试")
                requested = [9.5, 9.5, 8.0, 9.5, 10.0, 8.0, 8.5, 10.0, 9.0, 8.0, 8.5]
                visible_fields = list(scoring.score_weights("body")) + list(scoring.score_weights("feeling"))
                for field, value in zip(visible_fields, requested):
                    next(item for item in app.number_input if item.key == f"work_new_{field}").set_value(value)
                app.run()
                self.assertEqual(next(item for item in app.metric if item.label == "综合评分 · 自动计算").value, "8.88")
                next(button for button in app.button if button.key == "work_new_save").click().run()
                auto_saved = next(work for work in db.list_works() if work["title"] == "自动评分测试")
                self.assertEqual(auto_saved["score_total"], 8.88)

                app = AppTest.from_file("app.py", default_timeout=30).run()
                next(button for button in app.button if button.key == "sidebar_nav_新增条目").click().run()
                next(item for item in app.text_input if item.label == "作品名 *").set_value("手动评分测试")
                next(item for item in app.toggle if item.key == "work_new_auto_score").set_value(False).run()
                next(item for item in app.number_input if item.key == "work_new_score_story").set_value(9.5)
                next(item for item in app.number_input if item.key == "work_new_manual_total").set_value(8.7)
                app.run()
                next(button for button in app.button if button.key == "work_new_save").click().run()
                manual_saved = next(work for work in db.list_works() if work["title"] == "手动评分测试")
                self.assertEqual(manual_saved["score_total"], 8.7)
                self.assertEqual(manual_saved["score_story"], 9.5)
            finally:
                db.DATA_DIR, db.DB_PATH, db.EXPORT_DIR = original_paths


if __name__ == "__main__":
    unittest.main()
