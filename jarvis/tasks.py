"""本地 SQLite 任务看板和文字命令。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "tasks.db"
STATUSES = ("todo", "doing", "done")
_LABELS = {"todo": "待办", "doing": "进行中", "done": "已完成"}
_HELP = ("任务命令：/task add 内容；/task list [active|all|todo|doing|done]；"
         "/task start 编号；/task progress 编号 百分比 [说明]；"
         "/task done 编号；/task reopen 编号；"
         "/task remind 编号 YYYY-MM-DD HH:MM [说明]；"
         "/task reminders [active|due|all]；/task unremind 编号")


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(_DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'todo'
            CHECK(status IN ('todo', 'doing', 'done')),
        progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
        progress_note TEXT NOT NULL DEFAULT '',
        remind_at TEXT,
        reminder_note TEXT NOT NULL DEFAULT '',
        reminded_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
    if "progress" not in columns:
        db.execute("ALTER TABLE tasks ADD COLUMN progress INTEGER NOT NULL "
                   "DEFAULT 0 CHECK(progress BETWEEN 0 AND 100)")
    if "progress_note" not in columns:
        db.execute("ALTER TABLE tasks ADD COLUMN progress_note TEXT NOT NULL DEFAULT ''")
    if "remind_at" not in columns:
        db.execute("ALTER TABLE tasks ADD COLUMN remind_at TEXT")
    if "reminder_note" not in columns:
        db.execute("ALTER TABLE tasks ADD COLUMN reminder_note TEXT NOT NULL DEFAULT ''")
    if "reminded_at" not in columns:
        db.execute("ALTER TABLE tasks ADD COLUMN reminded_at TEXT")
    db.commit()
    return db


def add(title: str) -> str:
    title = (title or "").strip()
    if not title:
        return "任务内容不能为空"
    if len(title) > 500:
        return "任务内容不能超过 500 字"
    now = datetime.now(UTC).isoformat()
    with closing(_connect()) as db, db:
        cursor = db.execute(
            "INSERT INTO tasks(title, status, progress, progress_note, "
            "created_at, updated_at) VALUES(?, 'todo', 0, '', ?, ?)",
            (title, now, now),
        )
    return f"已添加任务 #{cursor.lastrowid}：{title}"


def list_tasks(status: str = "active") -> list[dict]:
    if status not in (*STATUSES, "active", "all"):
        return []
    where, args = "", ()
    if status == "active":
        where = "WHERE status != 'done'"
    elif status != "all":
        where, args = "WHERE status = ?", (status,)
    with closing(_connect()) as db:
        rows = db.execute(
            "SELECT id, title, status, progress, progress_note, remind_at, "
            "reminder_note, reminded_at, "
            "created_at, updated_at FROM tasks "
            f"{where} ORDER BY CASE status WHEN 'doing' THEN 0 "
            "WHEN 'todo' THEN 1 ELSE 2 END, id", args
        ).fetchall()
    return [dict(row) for row in rows]


def set_status(task_id: int, status: str) -> str:
    if status not in STATUSES:
        return f"不支持的任务状态：{status}"
    with closing(_connect()) as db, db:
        now = datetime.now(UTC).isoformat()
        if status == "done":
            cursor = db.execute(
                "UPDATE tasks SET status = 'done', progress = 100, "
                "remind_at = NULL, reminder_note = '', reminded_at = NULL, "
                "updated_at = ? WHERE id = ?", (now, task_id)
            )
        elif status == "todo":
            cursor = db.execute(
                "UPDATE tasks SET status = 'todo', progress = 0, "
                "progress_note = '', updated_at = ? WHERE id = ?",
                (now, task_id),
            )
        else:
            cursor = db.execute(
                "UPDATE tasks SET status = 'doing', updated_at = ? "
                "WHERE id = ? AND status != 'done'",
                (now, task_id),
            )
            if not cursor.rowcount and db.execute(
                    "SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
                return f"任务 #{task_id} 已完成，请先使用 /task reopen {task_id}"
    return (f"任务 #{task_id} 已设为{_LABELS[status]}" if cursor.rowcount
            else f"没找到任务 #{task_id}")


def update_progress(task_id: int, percent: int, note: str = "") -> str:
    if not 0 <= percent <= 100:
        return "任务进度必须是 0 到 100 的整数"
    with closing(_connect()) as db, db:
        row = db.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return f"没找到任务 #{task_id}"
        if row["status"] == "done" and percent < 100:
            return f"任务 #{task_id} 已完成，请先使用 /task reopen {task_id}"
        status = "done" if percent == 100 else (
            "doing" if row["status"] == "todo" and percent > 0
            else row["status"]
        )
        db.execute(
            "UPDATE tasks SET status = ?, progress = ?, progress_note = ?, "
            "remind_at = CASE WHEN ? = 'done' THEN NULL ELSE remind_at END, "
            "reminder_note = CASE WHEN ? = 'done' THEN '' ELSE reminder_note END, "
            "reminded_at = CASE WHEN ? = 'done' THEN NULL ELSE reminded_at END, "
            "updated_at = ? WHERE id = ?",
            (status, percent, note.strip(), status, status, status,
             datetime.now(UTC).isoformat(), task_id),
        )
    return f"任务 #{task_id} 进度已更新为 {percent}%（{_LABELS[status]}）"


def board(status: str = "active") -> str:
    if status not in (*STATUSES, "active", "all"):
        return _HELP
    items = list_tasks(status)
    if not items:
        return "当前没有符合条件的任务"
    lines = []
    for item in items:
        note = f" — {item['progress_note']}" if item["progress_note"] else ""
        lines.append(
            f"#{item['id']} [{_LABELS[item['status']]} {item['progress']}%] "
            f"{item['title']}{note}"
        )
    return "\n".join(lines)


def set_reminder(task_id: int, when: str, note: str = "") -> str:
    try:
        local_time = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
    except ValueError:
        return "提醒时间格式必须是 YYYY-MM-DD HH:MM"
    remind_at = local_time.astimezone(UTC)
    if remind_at <= datetime.now(UTC):
        return "提醒时间必须晚于当前时间"
    with closing(_connect()) as db, db:
        row = db.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            return f"没找到任务 #{task_id}"
        if row["status"] == "done":
            return f"任务 #{task_id} 已完成，请先使用 /task reopen {task_id}"
        db.execute(
            "UPDATE tasks SET remind_at = ?, reminder_note = ?, "
            "reminded_at = NULL, updated_at = ? WHERE id = ?",
            (remind_at.isoformat(), note.strip(), datetime.now(UTC).isoformat(),
             task_id),
        )
    return f"任务 #{task_id} 将在 {when} 提醒"


def clear_reminder(task_id: int) -> str:
    with closing(_connect()) as db, db:
        cursor = db.execute(
            "UPDATE tasks SET remind_at = NULL, reminder_note = '', "
            "reminded_at = NULL, updated_at = ? "
            "WHERE id = ? AND remind_at IS NOT NULL",
            (datetime.now(UTC).isoformat(), task_id),
        )
        exists = cursor.rowcount or db.execute(
            "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return (f"已取消任务 #{task_id} 的提醒" if cursor.rowcount
            else f"任务 #{task_id} 没有提醒" if exists
            else f"没找到任务 #{task_id}")


def reminders(mode: str = "active") -> str:
    if mode not in {"active", "due", "all"}:
        return _HELP
    now = datetime.now(UTC).isoformat()
    where = "WHERE remind_at IS NOT NULL"
    if mode == "active":
        where += " AND reminded_at IS NULL AND status != 'done'"
    elif mode == "due":
        where += (" AND reminded_at IS NULL AND status != 'done' "
                  "AND remind_at <= ?")
    args = (now,) if mode == "due" else ()
    with closing(_connect()) as db:
        rows = db.execute(
            "SELECT id, title, remind_at, reminder_note, reminded_at "
            f"FROM tasks {where} ORDER BY remind_at, id", args
        ).fetchall()
    if not rows:
        return "当前没有符合条件的提醒"
    lines = []
    for row in rows:
        local_time = datetime.fromisoformat(row["remind_at"]).astimezone()
        status = ("已提醒" if row["reminded_at"] else
                  "已到期" if row["remind_at"] <= now else "待提醒")
        note = f" — {row['reminder_note']}" if row["reminder_note"] else ""
        lines.append(
            f"#{row['id']} [{status} {local_time:%Y-%m-%d %H:%M}] "
            f"{row['title']}{note}"
        )
    return "\n".join(lines)


def pop_due_reminders() -> str:
    now = datetime.now(UTC).isoformat()
    with closing(_connect()) as db, db:
        rows = db.execute(
            "SELECT id, title, reminder_note FROM tasks "
            "WHERE remind_at IS NOT NULL AND remind_at <= ? "
            "AND reminded_at IS NULL AND status != 'done' ORDER BY remind_at, id",
            (now,),
        ).fetchall()
        if rows:
            db.executemany(
                "UPDATE tasks SET reminded_at = ?, updated_at = ? WHERE id = ?",
                [(now, now, row["id"]) for row in rows],
            )
    if not rows:
        return ""
    return "⏰ 到期提醒：\n" + "\n".join(
        f"#{row['id']} {row['title']}"
        f"{' — ' + row['reminder_note'] if row['reminder_note'] else ''}"
        for row in rows
    )


def handle_command(text: str) -> str | None:
    parts = text.split(maxsplit=2)
    if not parts or parts[0] != "/task":
        return None
    if len(parts) == 1 or parts[1].lower() == "help":
        return _HELP
    action = parts[1].lower()
    value = parts[2].strip() if len(parts) == 3 else ""
    if action == "add":
        return add(value)
    if action == "list":
        return board(value.lower() or "active")
    if action == "reminders":
        return reminders(value.lower() or "active")
    if action == "remind":
        values = value.split(maxsplit=3)
        if len(values) < 3:
            return "用法：/task remind 编号 YYYY-MM-DD HH:MM [提醒说明]"
        try:
            task_id = int(values[0])
        except ValueError:
            return "任务编号必须是整数"
        return set_reminder(task_id, f"{values[1]} {values[2]}",
                            values[3] if len(values) == 4 else "")
    if action == "unremind":
        try:
            task_id = int(value)
        except ValueError:
            return "任务编号必须是整数"
        return clear_reminder(task_id)
    if action == "progress":
        values = value.split(maxsplit=2)
        if len(values) < 2:
            return "用法：/task progress 编号 百分比 [进度说明]"
        try:
            task_id, percent = int(values[0]), int(values[1])
        except ValueError:
            return "任务编号和进度必须是整数"
        return update_progress(task_id, percent,
                               values[2] if len(values) == 3 else "")
    status = {"start": "doing", "done": "done", "reopen": "todo"}.get(action)
    if status:
        try:
            task_id = int(value)
        except ValueError:
            return "任务编号必须是整数"
        return set_status(task_id, status)
    return _HELP
