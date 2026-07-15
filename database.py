"""SQLite persistence and export helpers for Yang-gumi."""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "acgn.db"
EXPORT_DIR = ROOT / "exports"
BACKUP_DIR = ROOT / "backups"
READ_ONLY_MODE = os.getenv("YANGGUMI_READ_ONLY", "0") == "1"

SCORE_FIELDS = [
    "score_total", "score_story", "score_character", "score_art", "score_music",
    "score_direction", "score_atmosphere", "score_aftertaste", "score_uniqueness",
    "score_pacing", "score_personal", "rewatch_value", "score_influence",
    "score_originality", "score_imbalance_penalty",
]

WORK_COLUMNS = [
    "title", "original_title", "type", "subtype", "status", "start_date", "finish_date",
    "release_date", "year", *SCORE_FIELDS, "short_review", "long_review", "private_note",
    "favorite_characters", "favorite_episode", "favorite_quote", "cover_path", "cover_url",
    "resource_path", "bangumi_id", "bangumi_url", "bangumi_name", "bangumi_name_cn",
    "bangumi_type", "bangumi_score", "bangumi_rank", "bangumi_total_votes", "bangumi_date",
    "bangumi_summary", "bangumi_image_url", "bangumi_tags_json", "bangumi_rating_json",
    "bangumi_last_sync", "bangumi_raw_json", "score_mode", "custom_scores_json",
]


@contextmanager
def connect():
    if READ_ONLY_MODE:
        if not DB_PATH.exists():
            raise FileNotFoundError(f"找不到共享数据库：{DB_PATH}")
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        if not READ_ONLY_MODE:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _bangumi_tag_names(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    ranked: list[tuple[int, int, str]] = []
    for index, item in enumerate(values or []):
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            count = int(item.get("count") or 0)
        else:
            name, count = str(item).strip(), 0
        if name:
            ranked.append((-count, index, name))
    return list(dict.fromkeys(item[2] for item in sorted(ranked)))


def _sync_bangumi_tags(conn: sqlite3.Connection, work_id: int, raw_tags: Any) -> None:
    """Refresh only Bangumi-owned tag links while preserving every manual tag."""
    conn.execute("DELETE FROM work_tags WHERE work_id=? AND source='Bangumi'", (work_id,))
    for name in _bangumi_tag_names(raw_tags):
        conn.execute(
            "INSERT INTO tags(name, category) VALUES(?, 'Bangumi') ON CONFLICT(name) DO NOTHING",
            (name,),
        )
        tag_id = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO work_tags(work_id, tag_id, source) VALUES(?, ?, 'Bangumi')",
            (work_id, tag_id),
        )


def init_db() -> None:
    if READ_ONLY_MODE:
        if not DB_PATH.exists():
            raise FileNotFoundError(f"找不到共享数据库：{DB_PATH}")
        return
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "covers").mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, original_title TEXT, type TEXT, subtype TEXT, status TEXT,
            start_date TEXT, finish_date TEXT, release_date TEXT, year INTEGER,
            score_total REAL, score_story REAL, score_character REAL, score_art REAL,
            score_music REAL, score_direction REAL, score_atmosphere REAL, score_aftertaste REAL,
            score_uniqueness REAL, score_pacing REAL, score_personal REAL, rewatch_value REAL,
            score_influence REAL, score_originality REAL, score_imbalance_penalty REAL,
            short_review TEXT, long_review TEXT, private_note TEXT,
            favorite_characters TEXT, favorite_episode TEXT, favorite_quote TEXT,
            cover_path TEXT, cover_url TEXT, resource_path TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            bangumi_id INTEGER, bangumi_url TEXT, bangumi_name TEXT, bangumi_name_cn TEXT,
            bangumi_type INTEGER, bangumi_score REAL, bangumi_rank INTEGER,
            bangumi_total_votes INTEGER, bangumi_date TEXT, bangumi_summary TEXT,
            bangumi_image_url TEXT, bangumi_tags_json TEXT, bangumi_rating_json TEXT,
            bangumi_last_sync TEXT, bangumi_raw_json TEXT, score_mode TEXT,
            custom_scores_json TEXT,
            CHECK(score_total IS NULL OR score_total BETWEEN 0.0 AND 10.0),
            CHECK(score_story IS NULL OR score_story BETWEEN 0.0 AND 10.0),
            CHECK(score_character IS NULL OR score_character BETWEEN 0.0 AND 10.0),
            CHECK(score_art IS NULL OR score_art BETWEEN 0.0 AND 10.0),
            CHECK(score_music IS NULL OR score_music BETWEEN 0.0 AND 10.0),
            CHECK(score_direction IS NULL OR score_direction BETWEEN 0.0 AND 10.0),
            CHECK(score_atmosphere IS NULL OR score_atmosphere BETWEEN 0.0 AND 10.0),
            CHECK(score_aftertaste IS NULL OR score_aftertaste BETWEEN 0.0 AND 10.0),
            CHECK(score_uniqueness IS NULL OR score_uniqueness BETWEEN 0.0 AND 10.0),
            CHECK(score_personal IS NULL OR score_personal BETWEEN 0.0 AND 10.0),
            CHECK(rewatch_value IS NULL OR rewatch_value BETWEEN 0.0 AND 10.0)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_works_bangumi_id
            ON works(bangumi_id) WHERE bangumi_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL DEFAULT '其他'
        );
        CREATE TABLE IF NOT EXISTS work_tags (
            work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            source TEXT NOT NULL DEFAULT 'manual',
            PRIMARY KEY(work_id, tag_id)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            note_type TEXT, title TEXT, content TEXT, episode TEXT, timestamp TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bangumi_cache (
            bangumi_id INTEGER PRIMARY KEY,
            raw_subject_json TEXT NOT NULL,
            cached_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seasonal_anime_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bangumi_id INTEGER NOT NULL,
            season_year INTEGER NOT NULL,
            season_code TEXT NOT NULL,
            season_month_label TEXT NOT NULL,
            title TEXT NOT NULL,
            original_title TEXT,
            name_cn TEXT,
            name TEXT,
            release_date TEXT,
            air_date TEXT,
            image_url TEXT,
            bangumi_score REAL,
            bangumi_rank INTEGER,
            bangumi_total_votes INTEGER,
            summary TEXT,
            tags_json TEXT,
            raw_json TEXT NOT NULL,
            source_status TEXT NOT NULL DEFAULT 'unconfirmed',
            local_work_id INTEGER REFERENCES works(id) ON DELETE SET NULL,
            local_status TEXT,
            is_added_to_library INTEGER NOT NULL DEFAULT 0,
            is_hidden INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_sync TEXT NOT NULL,
            UNIQUE(bangumi_id, season_year, season_code)
        );
        CREATE TABLE IF NOT EXISTS seasonal_cache_meta (
            season_year INTEGER NOT NULL,
            season_code TEXT NOT NULL,
            last_attempt TEXT NOT NULL,
            last_sync TEXT,
            status TEXT NOT NULL,
            error TEXT,
            PRIMARY KEY(season_year, season_code)
        );
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(works)")}
        migrations = {
            "score_direction": "REAL", "score_pacing": "REAL", "score_influence": "REAL", "score_originality": "REAL",
            "score_imbalance_penalty": "REAL",
            "bangumi_raw_json": "TEXT", "score_mode": "TEXT", "custom_scores_json": "TEXT",
        }
        for field, sql_type in migrations.items():
            if field not in columns:
                conn.execute(f"ALTER TABLE works ADD COLUMN {field} {sql_type}")
        if "score_innovation" in columns:
            conn.execute(
                "UPDATE works SET score_originality=score_innovation "
                "WHERE score_originality IS NULL AND score_innovation IS NOT NULL"
            )
        work_tag_columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_tags)")}
        if "source" not in work_tag_columns:
            conn.execute("ALTER TABLE work_tags ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
            conn.execute(
                "UPDATE work_tags SET source='Bangumi' WHERE tag_id IN "
                "(SELECT id FROM tags WHERE category='Bangumi')"
            )
        conn.execute(
            "UPDATE works SET status = '已看' "
            "WHERE type = '动画' AND score_total IS NOT NULL AND COALESCE(status, '') <> '已看'"
        )
        import scoring as scoring_rules
        for row in conn.execute("SELECT * FROM works WHERE score_mode IS NULL"):
            item = dict(row)
            mode = "auto" if scoring_rules.default_auto_score(item) else "manual"
            conn.execute("UPDATE works SET score_mode=? WHERE id=?", (mode, int(row["id"])))
        for row in conn.execute("SELECT id, bangumi_tags_json FROM works WHERE bangumi_tags_json IS NOT NULL"):
            _sync_bangumi_tags(conn, int(row["id"]), row["bangumi_tags_json"])


def _clean_work(data: dict[str, Any]) -> dict[str, Any]:
    cleaned = {key: data.get(key) for key in WORK_COLUMNS}
    for field in SCORE_FIELDS + ["bangumi_score"]:
        value = cleaned.get(field)
        precision = 2 if field == "score_total" else 1
        if value in (None, ""):
            cleaned[field] = None
        else:
            upper = 10.0
            cleaned[field] = round(max(0.0, min(upper, float(value))), precision)
    custom_scores = cleaned.get("custom_scores_json")
    if isinstance(custom_scores, dict):
        custom_scores = json.dumps(custom_scores, ensure_ascii=False, sort_keys=True)
    if custom_scores:
        try:
            raw = json.loads(custom_scores) if isinstance(custom_scores, str) else {}
            clean_custom = {}
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if not str(key).startswith("custom_"):
                        continue
                    if value in (None, ""):
                        continue
                    clean_custom[str(key)] = round(max(0.0, min(10.0, float(value))), 1)
            cleaned["custom_scores_json"] = json.dumps(clean_custom, ensure_ascii=False, sort_keys=True) if clean_custom else None
        except (TypeError, ValueError, json.JSONDecodeError):
            cleaned["custom_scores_json"] = None
    else:
        cleaned["custom_scores_json"] = None
    if cleaned.get("type") == "动画" and cleaned.get("score_total") is not None:
        cleaned["status"] = "已看"
    for field in ["year", "bangumi_id", "bangumi_type", "bangumi_rank", "bangumi_total_votes"]:
        value = cleaned.get(field)
        cleaned[field] = None if value in (None, "", 0) else int(value)
    return cleaned


def save_work(data: dict[str, Any], tags: Iterable[tuple[str, str]] = (), work_id: int | None = None) -> int:
    values = _clean_work(data)
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        if work_id:
            assignments = ", ".join(f"{key} = ?" for key in WORK_COLUMNS)
            conn.execute(f"UPDATE works SET {assignments}, updated_at = ? WHERE id = ?",
                         [values[k] for k in WORK_COLUMNS] + [now, work_id])
        else:
            columns = ", ".join(WORK_COLUMNS + ["created_at", "updated_at"])
            placeholders = ", ".join("?" for _ in WORK_COLUMNS + ["created_at", "updated_at"])
            cur = conn.execute(f"INSERT INTO works ({columns}) VALUES ({placeholders})",
                               [values[k] for k in WORK_COLUMNS] + [now, now])
            work_id = int(cur.lastrowid)
        conn.execute("DELETE FROM work_tags WHERE work_id = ? AND source='manual'", (work_id,))
        for name, category in tags:
            name = name.strip()
            if not name:
                continue
            conn.execute("INSERT INTO tags(name, category) VALUES(?, ?) ON CONFLICT(name) DO NOTHING", (name, category or "其他"))
            tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO work_tags(work_id, tag_id, source) VALUES(?, ?, 'manual')", (work_id, tag_id))
            conn.execute("UPDATE work_tags SET source='manual' WHERE work_id=? AND tag_id=?", (work_id, tag_id))
        _sync_bangumi_tags(conn, int(work_id), values.get("bangumi_tags_json"))
    return int(work_id)


def get_work(work_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = [dict(r) for r in conn.execute(
            "SELECT t.id, t.name, t.category, wt.source FROM tags t JOIN work_tags wt ON wt.tag_id=t.id WHERE wt.work_id=? ORDER BY wt.source,t.category,t.name", (work_id,)
        )]
        item["tag_names"] = " · ".join(tag["name"] for tag in item["tags"])
        return item


def list_works() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("""
            SELECT w.*, GROUP_CONCAT(CASE WHEN t.category='Bangumi' THEN t.name END, ' · ') AS tag_names,
                   CASE WHEN w.score_total IS NOT NULL AND w.bangumi_score IS NOT NULL
                        THEN ROUND(w.score_total-w.bangumi_score, 1) END AS score_diff
            FROM works w LEFT JOIN work_tags wt ON wt.work_id=w.id
            LEFT JOIN tags t ON t.id=wt.tag_id GROUP BY w.id
        """).fetchall()
        return [dict(row) for row in rows]


def search_work_ids(query: str) -> set[int]:
    """UTF-8-safe partial search across local, Bangumi and personal text fields."""
    query = (query or "").strip()
    if not query:
        return {work["id"] for work in list_works()}
    pattern = f"%{query}%"
    searchable = [
        "w.title", "w.original_title", "w.bangumi_name_cn", "w.bangumi_name",
        "w.short_review", "w.long_review", "w.favorite_characters", "w.favorite_quote",
        "t.name",
    ]
    where = " OR ".join(f"COALESCE({field}, '') LIKE ? COLLATE NOCASE" for field in searchable)
    with connect() as conn:
        rows = conn.execute(f"""
            SELECT DISTINCT w.id FROM works w
            LEFT JOIN work_tags wt ON wt.work_id=w.id
            LEFT JOIN tags t ON t.id=wt.tag_id
            WHERE {where}
        """, [pattern] * len(searchable)).fetchall()
        return {int(row[0]) for row in rows}


def delete_work(work_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE seasonal_anime_cache SET local_work_id=NULL,local_status=NULL,is_added_to_library=0,updated_at=? WHERE local_work_id=?",
            (datetime.now().isoformat(timespec="seconds"), int(work_id)),
        )
        conn.execute("DELETE FROM works WHERE id = ?", (work_id,))


def all_tags() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("""
            SELECT t.*, COUNT(wt.work_id) AS work_count,
                   AVG(w.score_total) AS my_average, AVG(w.bangumi_score) AS bangumi_average,
                   AVG(CASE WHEN w.score_total IS NOT NULL AND w.bangumi_score IS NOT NULL
                       THEN w.score_total-w.bangumi_score END) AS average_diff
            FROM tags t LEFT JOIN work_tags wt ON wt.tag_id=t.id
            LEFT JOIN works w ON w.id=wt.work_id
            GROUP BY t.id
            ORDER BY CASE WHEN t.category='Bangumi' THEN 0 ELSE 1 END, work_count DESC, t.name
        """).fetchall()
        return [dict(row) for row in rows]


def cache_subject(subject_id: int, subject: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute("""INSERT INTO bangumi_cache(bangumi_id,raw_subject_json,cached_at)
            VALUES(?,?,?) ON CONFLICT(bangumi_id) DO UPDATE SET raw_subject_json=excluded.raw_subject_json,cached_at=excluded.cached_at""",
            (subject_id, json.dumps(subject, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))


def get_cached_subject(subject_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT raw_subject_json FROM bangumi_cache WHERE bangumi_id=?", (subject_id,)).fetchone()
        return json.loads(row[0]) if row else None


def update_bangumi(work_id: int, fields: dict[str, Any], include_local_titles: bool = True) -> None:
    allowed = [key for key in fields if key.startswith("bangumi_")]
    if include_local_titles:
        allowed.extend(key for key in ("title", "original_title") if key in fields)
    if not allowed:
        return
    with connect() as conn:
        conn.execute(f"UPDATE works SET {', '.join(f'{k}=?' for k in allowed)}, updated_at=? WHERE id=?",
                     [fields[k] for k in allowed] + [datetime.now().isoformat(timespec="seconds"), work_id])
        if "bangumi_tags_json" in fields:
            _sync_bangumi_tags(conn, work_id, fields.get("bangumi_tags_json"))


def adopt_bangumi_identity(work_id: int, fields: dict[str, Any]) -> None:
    """Adopt Bangumi title/date fields only after explicit user confirmation."""
    allowed = [key for key in ("title", "original_title", "release_date", "year") if fields.get(key) not in (None, "")]
    if not allowed:
        return
    with connect() as conn:
        conn.execute(
            f"UPDATE works SET {', '.join(f'{key}=?' for key in allowed)}, updated_at=? WHERE id=?",
            [fields[key] for key in allowed] + [datetime.now().isoformat(timespec="seconds"), int(work_id)],
        )


def get_work_by_bangumi_id(bangumi_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT id FROM works WHERE bangumi_id=?", (int(bangumi_id),)).fetchone()
    return get_work(int(row[0])) if row else None


def merge_existing_bangumi_draft(fields: dict[str, Any]) -> dict[str, Any]:
    """Reuse an existing Bangumi-bound record without losing its local fields."""
    bangumi_id = fields.get("bangumi_id")
    existing = get_work_by_bangumi_id(int(bangumi_id)) if bangumi_id else None
    if not existing:
        return dict(fields)
    return {**existing, **fields, "tags": existing.get("tags", []), "_existing_work_id": int(existing["id"])}


def update_work_status(work_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE works SET status=?, updated_at=? WHERE id=?",
            (status, datetime.now().isoformat(timespec="seconds"), int(work_id)),
        )


def upsert_seasonal_anime(items: Iterable[dict[str, Any]], year: int, season_code: str, month_label: str) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    count = 0
    with connect() as conn:
        # Keep historical rows for audit/links, but hide candidates no longer returned by
        # the current official quarter query instead of deleting them.
        conn.execute(
            "UPDATE seasonal_anime_cache SET is_hidden=1,updated_at=? WHERE season_year=? AND season_code=?",
            (now, int(year), season_code),
        )
        for item in items:
            bangumi_id = int(item["bangumi_id"])
            existing_work = conn.execute("SELECT id, status FROM works WHERE bangumi_id=?", (bangumi_id,)).fetchone()
            conn.execute("""
                INSERT INTO seasonal_anime_cache(
                    bangumi_id,season_year,season_code,season_month_label,title,original_title,
                    name_cn,name,release_date,air_date,image_url,bangumi_score,bangumi_rank,
                    bangumi_total_votes,summary,tags_json,raw_json,source_status,local_work_id,
                    local_status,is_added_to_library,created_at,updated_at,last_sync
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(bangumi_id,season_year,season_code) DO UPDATE SET
                    season_month_label=excluded.season_month_label,title=excluded.title,
                    original_title=excluded.original_title,name_cn=excluded.name_cn,name=excluded.name,
                    release_date=excluded.release_date,air_date=excluded.air_date,image_url=excluded.image_url,
                    bangumi_score=excluded.bangumi_score,bangumi_rank=excluded.bangumi_rank,
                    bangumi_total_votes=excluded.bangumi_total_votes,summary=excluded.summary,
                    tags_json=excluded.tags_json,raw_json=excluded.raw_json,
                    source_status=excluded.source_status,updated_at=excluded.updated_at,last_sync=excluded.last_sync,
                    is_hidden=0,
                    local_work_id=COALESCE(excluded.local_work_id,seasonal_anime_cache.local_work_id),
                    local_status=COALESCE(excluded.local_status,seasonal_anime_cache.local_status),
                    is_added_to_library=MAX(excluded.is_added_to_library,seasonal_anime_cache.is_added_to_library)
            """, (
                bangumi_id, int(year), season_code, month_label, item.get("title") or item.get("name") or "未命名动画",
                item.get("original_title"), item.get("name_cn"), item.get("name"), item.get("release_date"),
                item.get("air_date"), item.get("image_url"), item.get("bangumi_score"), item.get("bangumi_rank"),
                item.get("bangumi_total_votes"), item.get("summary"), item.get("tags_json"),
                item.get("raw_json") or "{}", item.get("source_status") or "unconfirmed",
                int(existing_work["id"]) if existing_work else None,
                existing_work["status"] if existing_work else None, 1 if existing_work else 0, now, now, now,
            ))
            count += 1
    return count


def list_seasonal_anime(year: int, season_code: str, include_unconfirmed: bool = False) -> list[dict[str, Any]]:
    source_clause = "" if include_unconfirmed else "AND s.source_status='confirmed'"
    with connect() as conn:
        rows = conn.execute(f"""
            SELECT s.*, COALESCE(w.status,s.local_status) AS effective_status,
                   w.score_total AS local_score, w.short_review AS local_short_review
            FROM seasonal_anime_cache s
            LEFT JOIN works w ON w.id=s.local_work_id OR (w.bangumi_id=s.bangumi_id AND s.local_work_id IS NULL)
            WHERE s.season_year=? AND s.season_code=? AND s.is_hidden=0 {source_clause}
            GROUP BY s.id
            ORDER BY CASE COALESCE(w.status,s.local_status)
                WHEN '在看' THEN 0 WHEN '重看中' THEN 0 WHEN '想看' THEN 1
                WHEN '弃置' THEN 3 WHEN '已看' THEN 4 ELSE 2 END,
                COALESCE(s.air_date,s.release_date,'9999-99-99'), s.bangumi_total_votes DESC
        """, (int(year), season_code)).fetchall()
        return [dict(row) for row in rows]


def get_seasonal_anime(cache_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM seasonal_anime_cache WHERE id=?", (int(cache_id),)).fetchone()
        return dict(row) if row else None


def link_seasonal_work(cache_id: int, work_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute("""
            UPDATE seasonal_anime_cache SET local_work_id=?,local_status=?,is_added_to_library=1,updated_at=?
            WHERE id=?
        """, (int(work_id), status, datetime.now().isoformat(timespec="seconds"), int(cache_id)))


def update_seasonal_source(cache_id: int, source_status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE seasonal_anime_cache SET source_status=?,updated_at=? WHERE id=?",
            (source_status, datetime.now().isoformat(timespec="seconds"), int(cache_id)),
        )


def seasonal_cache_meta(year: int, season_code: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM seasonal_cache_meta WHERE season_year=? AND season_code=?",
            (int(year), season_code),
        ).fetchone()
        return dict(row) if row else None


def mark_seasonal_sync(year: int, season_code: str, status: str, error: str = "") -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("""
            INSERT INTO seasonal_cache_meta(season_year,season_code,last_attempt,last_sync,status,error)
            VALUES(?,?,?,?,?,?) ON CONFLICT(season_year,season_code) DO UPDATE SET
                last_attempt=excluded.last_attempt,last_sync=excluded.last_sync,
                status=excluded.status,error=excluded.error
        """, (int(year), season_code, now, now if status == "success" else None, status, error or None))


def unbind_bangumi(work_id: int) -> None:
    fields = [c for c in WORK_COLUMNS if c.startswith("bangumi_")]
    with connect() as conn:
        conn.execute(f"UPDATE works SET {', '.join(f'{f}=NULL' for f in fields)}, updated_at=? WHERE id=?",
                     (datetime.now().isoformat(timespec="seconds"), work_id))
        _sync_bangumi_tags(conn, work_id, None)


def backup_database() -> Path:
    if not DB_PATH.exists():
        raise FileNotFoundError("当前数据库不存在。")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"yanggumi_backup_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    dest = sqlite3.connect(target)
    try:
        with connect() as source:
            source.backup(dest)
    finally:
        dest.close()
    return target


def _validate_database(path: Path) -> None:
    required = {"works", "tags", "work_tags", "seasonal_anime_cache"}
    conn = sqlite3.connect(path)
    try:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(present):
            raise ValueError("备份文件缺少 Yang-gumi 必需的数据表。")
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError("备份数据库完整性检查失败。")
    finally:
        conn.close()


def restore_database(uploaded_bytes: bytes) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = DB_PATH.parent / f"restore-check-{datetime.now():%Y%m%d%H%M%S%f}.db"
    temp.write_bytes(uploaded_bytes)
    try:
        _validate_database(temp)
        backup_database()
        temp.replace(DB_PATH)
    finally:
        temp.unlink(missing_ok=True)


def list_backups() -> list[Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(BACKUP_DIR.glob("yanggumi_backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def restore_backup(name: str) -> Path:
    candidate = (BACKUP_DIR / Path(name).name).resolve()
    if candidate.parent != BACKUP_DIR.resolve() or candidate.suffix.lower() != ".db" or not candidate.exists():
        raise ValueError("只能恢复 backups 文件夹内存在的 .db 备份。")
    _validate_database(candidate)
    safety = backup_database()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = DB_PATH.parent / f"restore-selected-{datetime.now():%Y%m%d%H%M%S%f}.db"
    shutil.copy2(candidate, temp)
    try:
        _validate_database(temp)
        temp.replace(DB_PATH)
    finally:
        temp.unlink(missing_ok=True)
    return safety


def table_counts() -> dict[str, int]:
    names = ("works", "tags", "work_tags", "seasonal_anime_cache")
    with connect() as conn:
        return {name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in names}


def _table_rows(name: str) -> list[dict[str, Any]]:
    if name not in {"works", "tags", "work_tags", "seasonal_anime_cache"}:
        raise ValueError("不支持的数据表")
    with connect() as conn:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {name}")]


def public_rows() -> list[dict[str, Any]]:
    import scoring
    score_config = scoring.load_score_config()

    def group_scores(work: dict[str, Any], group_key: str) -> dict[str, Any]:
        custom = {}
        try:
            custom = json.loads(work.get("custom_scores_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            custom = {}
        result = {}
        for field in scoring.score_weights(group_key, score_config):
            result[field] = custom.get(field) if field.startswith("custom_") else work.get(field)
        return result

    fields = ["id", "title", "original_title", "type", "subtype", "status", "year", "release_date",
              *SCORE_FIELDS, "score_mode", "custom_scores_json", "bangumi_score", "bangumi_total_votes", "bangumi_rank",
              "short_review", "bangumi_image_url", "bangumi_summary", "created_at", "updated_at"]
    result = []
    for work in list_works():
        row = {key: work.get(key) for key in fields}
        row["score_diff"] = work.get("score_diff")
        row["cover_url"] = work.get("bangumi_image_url") or (
            work.get("cover_url") if str(work.get("cover_url") or "").startswith(("https://", "http://")) else None
        )
        row["bangumi_tags"] = [value for value in (work.get("tag_names") or "").split(" · ") if value]
        row["score_breakdown"] = {
            "body": group_scores(work, "body"),
            "feeling": group_scores(work, "feeling"),
            "main": group_scores(work, "body"),
            "bonus": group_scores(work, "feeling"),
            "special": group_scores(work, "era"),
            "penalty": {"score_imbalance_penalty": work.get("score_imbalance_penalty")},
        }
        result.append(row)
    return result


def export_json(public: bool = False) -> bytes:
    now = datetime.now().isoformat(timespec="seconds")
    if public:
        payload = {"export_meta": {"site_name": "Yang-gumi", "project": "Yang-gumi", "exported_at": now, "public": True, "read_only": True}, "works": public_rows()}
    else:
        tables = {name: _table_rows(name) for name in ("works", "tags", "work_tags", "seasonal_anime_cache")}
        for work in tables["works"]:
            for field in ("cover_path", "resource_path"):
                work.pop(field, None)
        payload = {"export_meta": {"project": "Yang-gumi", "version": "15/17", "exported_at": now,
                   "work_count": len(tables["works"]), "tag_count": len(tables["tags"])}, **tables}
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def export_csv() -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name in ("works", "tags", "work_tags", "seasonal_anime_cache"):
            rows = _table_rows(name)
            output = io.StringIO()
            if rows:
                writer = csv.DictWriter(output, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
            zipped.writestr(f"{name}.csv", ("\ufeff" + output.getvalue()).encode("utf-8"))
    return archive.getvalue()


def orphan_tag_count() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM tags t WHERE NOT EXISTS(SELECT 1 FROM work_tags wt WHERE wt.tag_id=t.id)").fetchone()[0])


def cleanup_orphan_tags() -> int:
    with connect() as conn:
        rows = conn.execute("SELECT id FROM tags t WHERE NOT EXISTS(SELECT 1 FROM work_tags wt WHERE wt.tag_id=t.id)").fetchall()
        conn.executemany("DELETE FROM tags WHERE id=?", rows)
        return len(rows)


def rebuild_tag_statistics() -> int:
    with connect() as conn:
        for row in conn.execute("SELECT id,bangumi_tags_json FROM works"):
            _sync_bangumi_tags(conn, int(row["id"]), row["bangumi_tags_json"])
        return int(conn.execute("SELECT COUNT(*) FROM work_tags").fetchone()[0])


def test_work_candidates() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id,title FROM works WHERE lower(title) LIKE '%test%' OR title LIKE '%测试%' OR lower(title) LIKE '%demo%' ORDER BY id"
        )]


def delete_selected_works(ids: Iterable[int]) -> int:
    selected = [int(value) for value in ids]
    for work_id in selected:
        delete_work(work_id)
    return len(selected)


def recalculate_auto_scores() -> int:
    import scoring
    updated = 0
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM works WHERE score_mode='auto'")]
        for row in rows:
            total = scoring.calculate_total_score(row, row.get("bangumi_total_votes"))
            if total is not None:
                conn.execute("UPDATE works SET score_total=?,updated_at=? WHERE id=?", (total, datetime.now().isoformat(timespec="seconds"), row["id"]))
                updated += 1
    return updated


def health_check() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    def add(label: str, ok: bool, detail: str = "") -> None:
        checks.append({"label": label, "ok": bool(ok), "detail": detail})
    add("数据库文件存在", DB_PATH.exists(), str(DB_PATH))
    with connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ("works", "tags", "work_tags", "seasonal_anime_cache"):
            add(f"{name} 表存在", name in tables)
        duplicate = conn.execute("SELECT COUNT(*) FROM (SELECT bangumi_id FROM works WHERE bangumi_id IS NOT NULL GROUP BY bangumi_id HAVING COUNT(*)>1)").fetchone()[0]
        orphan_links = conn.execute("SELECT COUNT(*) FROM work_tags wt LEFT JOIN works w ON w.id=wt.work_id LEFT JOIN tags t ON t.id=wt.tag_id WHERE w.id IS NULL OR t.id IS NULL").fetchone()[0]
        add("Bangumi ID 无重复", duplicate == 0, f"{duplicate} 组")
        add("work_tags 无孤儿关联", orphan_links == 0, f"{orphan_links} 条")
        add("无孤儿标签", orphan_tag_count() == 0, f"{orphan_tag_count()} 个")
        queries = [
            ("作品标题完整", "SELECT COUNT(*) FROM works WHERE trim(COALESCE(title,''))=''"),
            ("作品类型合法", "SELECT COUNT(*) FROM works WHERE type NOT IN ('动画','漫画','轻小说','游戏','其他')"),
            ("作品状态合法", "SELECT COUNT(*) FROM works WHERE status NOT IN ('未看','想看','在看','已看','搁置','弃置','想重看','重看中')"),
            ("总评分范围合法", "SELECT COUNT(*) FROM works WHERE score_total<0 OR score_total>10"),
            ("Bangumi 评分人数合法", "SELECT COUNT(*) FROM works WHERE bangumi_total_votes<0"),
            ("日期格式合法", "SELECT COUNT(*) FROM works WHERE release_date IS NOT NULL AND release_date<>'' AND release_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"),
        ]
        for label, query in queries:
            count = int(conn.execute(query).fetchone()[0]); add(label, count == 0, f"{count} 条异常")
        missing_cover = int(conn.execute("SELECT COUNT(*) FROM works WHERE COALESCE(bangumi_image_url,cover_url,cover_path,'')='' ").fetchone()[0])
        add("封面字段统计", True, f"{missing_cover} 条无封面")
    try:
        import scoring
        scoring.calculate_total_score({field: 5 for field in SCORE_FIELDS}, 0)
        add("自动评分公式可运行", True)
    except Exception as exc:
        add("自动评分公式可运行", False, str(exc))
    return checks


init_db()
