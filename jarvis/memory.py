"""SQLite 持久记忆；首次使用时非破坏性导入旧 memory.json。"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "memory.db"
_LEGACY_PATH = _ROOT / "memory.json"
CATEGORIES = ("core", "long_term", "project")


def _valid_category(category: str) -> bool:
    return category in CATEGORIES


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(_DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY,
        fact TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL DEFAULT 'long_term',
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    _migrate_legacy(db)
    return db


def _migrate_legacy(db: sqlite3.Connection) -> None:
    if db.execute("SELECT 1 FROM meta WHERE key = 'legacy_json_migrated'").fetchone():
        return
    items = []
    if _LEGACY_PATH.exists():
        try:
            value = json.loads(_LEGACY_PATH.read_text(encoding="utf-8"))
            items = value if isinstance(value, list) else []
        except (json.JSONDecodeError, OSError):
            items = []
    for item in items:
        fact = str(item.get("fact", "")).strip() if isinstance(item, dict) else ""
        if fact:
            db.execute(
                "INSERT OR IGNORE INTO facts(fact, category, created_at) VALUES(?, ?, ?)",
                (fact, "long_term", item.get("at") or time.strftime("%Y-%m-%d")),
            )
    db.execute("INSERT INTO meta(key, value) VALUES('legacy_json_migrated', '1')")
    db.commit()


def load(category: str | None = None) -> list[dict]:
    with closing(_connect()) as db:
        if category:
            rows = db.execute(
                "SELECT id, fact, category, created_at FROM facts "
                "WHERE category = ? ORDER BY id", (category,)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, fact, category, created_at FROM facts ORDER BY id"
            ).fetchall()
    return [{"id": row["id"], "fact": row["fact"],
             "category": row["category"], "at": row["created_at"]}
            for row in rows]


def add(fact: str, category: str = "long_term") -> str:
    fact = (fact or "").strip()
    if not fact:
        return "（没有要记的内容）"
    if not _valid_category(category):
        return f"不支持的记忆分类：{category}"
    try:
        with closing(_connect()) as db, db:
            db.execute(
                "INSERT INTO facts(fact, category, created_at) VALUES(?, ?, ?)",
                (fact, category, time.strftime("%Y-%m-%d")),
            )
    except sqlite3.IntegrityError:
        return "这个我早就记下了"
    return f"好的，我记住了：{fact}"


def update(memory_id: int, fact: str, category: str | None = None) -> str:
    fact = (fact or "").strip()
    if not fact:
        return "记忆内容不能为空"
    if category is not None and not _valid_category(category):
        return f"不支持的记忆分类：{category}"
    try:
        with closing(_connect()) as db, db:
            if category is None:
                cursor = db.execute(
                    "UPDATE facts SET fact = ? WHERE id = ?", (fact, memory_id)
                )
            else:
                cursor = db.execute(
                    "UPDATE facts SET fact = ?, category = ? WHERE id = ?",
                    (fact, category, memory_id),
                )
    except sqlite3.IntegrityError:
        return "已有相同内容的记忆"
    return (f"已修改记忆 {memory_id}" if cursor.rowcount
            else f"没找到记忆 {memory_id}")


def forget(keyword: str) -> str:
    keyword = (keyword or "").strip()
    if not keyword:
        return "请告诉我要忘记的关键词"
    with closing(_connect()) as db, db:
        cursor = db.execute("DELETE FROM facts WHERE instr(fact, ?) > 0", (keyword,))
    return (f"已经忘掉 {cursor.rowcount} 条相关记忆" if cursor.rowcount
            else "没找到相关的记忆")


def clear(category: str | None = None) -> str:
    if category is not None and not _valid_category(category):
        return f"不支持的记忆分类：{category}"
    with closing(_connect()) as db, db:
        if category is None:
            cursor = db.execute("DELETE FROM facts")
        else:
            cursor = db.execute("DELETE FROM facts WHERE category = ?", (category,))
    target = f"{category} 分类" if category else "全部"
    return f"已清空{target}记忆，共 {cursor.rowcount} 条"


def export_json(path: Path, category: str | None = None) -> str:
    if category is not None and not _valid_category(category):
        return f"不支持的记忆分类：{category}"
    items = load(category)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "exported_at": datetime.now(UTC).isoformat(),
        "category": category,
        "items": items,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"已导出 {len(items)} 条记忆到 {path}"


def as_prompt(categories: tuple[str, ...] | None = None) -> str:
    items = load()
    if categories is not None:
        items = [item for item in items if item["category"] in categories]
    if not items:
        return ""
    lines = "\n".join(
        f"- [{item['category']}] {item['fact']}" for item in items
    )
    return "\n\n# 关于用户你已经记住的事（自然地运用，别刻意复述）：\n" + lines
