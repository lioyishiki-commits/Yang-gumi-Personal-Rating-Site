# Yang-gumi release: 1.3.0
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

import database as db


class DataManagementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_backup_dir = db.BACKUP_DIR
        root = Path(self.temp.name)
        db.DB_PATH = root / "yanggumi.db"
        db.BACKUP_DIR = root / "backups"
        db.init_db()

    def tearDown(self) -> None:
        db.DB_PATH = self.old_db_path
        db.BACKUP_DIR = self.old_backup_dir
        self.temp.cleanup()

    def _add(self, title: str) -> int:
        return db.save_work({
            "title": title,
            "type": "动画",
            "status": "已看",
            "score_total": 8.25,
            "score_mode": "manual",
            "private_note": "不应公开",
            "resource_path": r"E:\private\media",
        })

    def test_backup_restore_exports_and_health_check(self) -> None:
        self._add("测试作品一")
        backup = db.backup_database()
        self.assertTrue(backup.exists())
        self.assertRegex(backup.name, r"yanggumi_backup_\d{8}_\d{6}_\d{6}\.db")

        csv_zip = db.export_csv()
        with zipfile.ZipFile(io.BytesIO(csv_zip)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"works.csv", "tags.csv", "work_tags.csv", "seasonal_anime_cache.csv"},
            )
            self.assertTrue(archive.read("works.csv").startswith(b"\xef\xbb\xbf"))

        full = json.loads(db.export_json(False).decode("utf-8"))
        self.assertEqual(set(full), {"export_meta", "works", "tags", "work_tags", "seasonal_anime_cache"})
        public = json.loads(db.export_json(True).decode("utf-8"))
        self.assertNotIn("private_note", public["works"][0])
        self.assertNotIn("resource_path", public["works"][0])

        self._add("测试作品二")
        self.assertEqual(db.table_counts()["works"], 2)
        db.restore_backup(backup.name)
        self.assertEqual(db.table_counts()["works"], 1)
        checks = db.health_check()
        self.assertEqual(len(checks), 16)
        self.assertTrue(all(item["ok"] for item in checks))

    def test_recent_precise_bangumi_cache_is_used_across_saved_work_views(self) -> None:
        old_data_dir = db.DATA_DIR
        try:
            db.DATA_DIR = Path(self.temp.name)
            db.DATA_DIR.joinpath("bangumi_rating_precision.json").write_text(
                json.dumps({
                    "version": 1,
                    "items": {
                        "265": {
                            "score": 8.69,
                            "votes": 33952,
                            "date": date.today().isoformat(),
                        }
                    },
                }),
                encoding="utf-8",
            )
            work_id = db.save_work({
                "title": "精确评分动画",
                "type": "动画",
                "score_mode": "auto",
                "score_total": 8.14,
                "bangumi_id": 265,
                "bangumi_score": 8.3,
                "bangumi_total_votes": 33000,
            })
            work = db.get_work(work_id)
            self.assertEqual(work["bangumi_score"], 8.69)
            self.assertEqual(work["bangumi_total_votes"], 33952)
            self.assertEqual(work["score_diff"], -0.55)
            listed = next(item for item in db.list_works() if item["id"] == work_id)
            self.assertEqual(listed["bangumi_score"], 8.69)
            self.assertEqual(listed["score_diff"], -0.55)
        finally:
            db.DATA_DIR = old_data_dir


if __name__ == "__main__":
    unittest.main()
