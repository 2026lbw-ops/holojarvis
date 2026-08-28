"""工作区文本文件的待审 Diff。"""

from __future__ import annotations

import difflib
import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from . import config

_DB_PATH = Path(__file__).resolve().parent.parent / "diffs.db"
MAX_BYTES = 256 * 1024
_HELP = "Diff 命令：/diff list；/diff show 编号；/diff accept 编号；/diff reject 编号"
_UNDO_HELP = "撤销命令：/undo list；/undo show 编号；/undo apply 编号"


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(_DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL,
        base_content TEXT,
        base_sha256 TEXT,
        new_content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS undo_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL,
        before_content TEXT,
        before_sha256 TEXT,
        after_content TEXT NOT NULL,
        after_sha256 TEXT NOT NULL,
        applied_at TEXT NOT NULL,
        undone_at TEXT
    )""")
    return db


def _write_atomic(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=target.parent, prefix=".jarvis-",
                                     suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as temp:
            temp.write(content.encode("utf-8"))
        os.replace(temp_path, target)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _read(path: str, *, required: bool) -> tuple[Path, str, str | None, str | None]:
    target = config.workspace_path(path)
    if target is None:
        raise ValueError(f"路径越出工作区：{path}")
    relative = target.relative_to(config.WORKSPACE).as_posix()
    if not target.exists():
        if required:
            raise ValueError(f"文件不存在：{relative}")
        return target, relative, None, None
    if not target.is_file():
        raise ValueError(f"不是普通文件：{relative}")
    raw = target.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError(f"文件超过 256 KiB：{relative}")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"文件不是 UTF-8 文本：{relative}") from None
    return target, relative, content, hashlib.sha256(raw).hexdigest()


def read_text(path: str) -> str:
    try:
        _, relative, content, _ = _read(path, required=True)
    except (OSError, ValueError) as error:
        return str(error)
    return f"{relative} 内容：\n{content}"


def propose(path: str, content: str) -> str:
    try:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            return "新内容超过 256 KiB"
        _, relative, base, digest = _read(path, required=False)
        with closing(_connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO proposals(path, base_content, base_sha256, "
                "new_content, created_at) VALUES(?, ?, ?, ?, ?)",
                (relative, base, digest, content, datetime.now(UTC).isoformat()),
            )
    except (OSError, ValueError) as error:
        return str(error)
    return f"已创建文件提案 #{cursor.lastrowid}；请用 /diff show {cursor.lastrowid} 审阅"


def list_proposals() -> str:
    with closing(_connect()) as db:
        rows = db.execute(
            "SELECT id, path, created_at FROM proposals ORDER BY id"
        ).fetchall()
    if not rows:
        return "当前没有待审文件提案"
    return "\n".join(f"#{row['id']} {row['path']}" for row in rows)


def _proposal(proposal_id: int) -> sqlite3.Row | None:
    with closing(_connect()) as db:
        return db.execute(
            "SELECT id, path, base_content, base_sha256, new_content, created_at "
            "FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()


def show(proposal_id: int) -> str:
    row = _proposal(proposal_id)
    if row is None:
        return f"提案 #{proposal_id} 不存在"
    before = row["base_content"] or ""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        row["new_content"].splitlines(keepends=True),
        fromfile=f"a/{row['path']}" if row["base_content"] is not None else "/dev/null",
        tofile=f"b/{row['path']}",
    )
    diff = "".join(lines)
    return diff or f"提案 #{proposal_id} 没有内容变化"


def accept(proposal_id: int) -> str:
    row = _proposal(proposal_id)
    if row is None:
        return f"提案 #{proposal_id} 不存在"
    try:
        target = config.workspace_path(row["path"])
        if target is None:
            return "目标路径已越出工作区，拒绝接受"
        if row["base_sha256"] is None:
            if target.exists():
                return "目标内容已变化，拒绝覆盖；提案仍保留"
        else:
            # ponytail: optimistic check; add OS-level locking only if races
            # between this check and replace become a real desktop workload.
            if (not target.is_file()
                    or target.stat().st_size > MAX_BYTES
                    or hashlib.sha256(target.read_bytes()).hexdigest()
                    != row["base_sha256"]):
                return "目标内容已变化，拒绝覆盖；提案仍保留"
        after_sha256 = hashlib.sha256(
            row["new_content"].encode("utf-8")
        ).hexdigest()
        with closing(_connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO undo_records(path, before_content, before_sha256, "
                "after_content, after_sha256, applied_at) VALUES(?, ?, ?, ?, ?, ?)",
                (row["path"], row["base_content"], row["base_sha256"],
                 row["new_content"], after_sha256, datetime.now(UTC).isoformat()),
            )
            undo_id = cursor.lastrowid
        try:
            _write_atomic(target, row["new_content"])
        except OSError:
            with closing(_connect()) as db, db:
                db.execute("DELETE FROM undo_records WHERE id = ?", (undo_id,))
            raise
        with closing(_connect()) as db, db:
            db.execute("DELETE FROM proposals WHERE id = ?", (proposal_id,))
    except OSError as error:
        return f"接受提案失败：{error}"
    return f"已接受提案 #{proposal_id}：{row['path']}；撤销记录 #{undo_id}"


def reject(proposal_id: int) -> str:
    with closing(_connect()) as db, db:
        cursor = db.execute("DELETE FROM proposals WHERE id = ?", (proposal_id,))
    return (f"已拒绝提案 #{proposal_id}" if cursor.rowcount
            else f"提案 #{proposal_id} 不存在")


def handle_command(text: str) -> str | None:
    parts = text.split(maxsplit=2)
    if not parts or parts[0] != "/diff":
        return None
    if len(parts) == 1 or parts[1].lower() == "help":
        return _HELP
    action = parts[1].lower()
    if action == "list":
        return list_proposals()
    if action not in {"show", "accept", "reject"} or len(parts) < 3:
        return _HELP
    try:
        proposal_id = int(parts[2])
    except ValueError:
        return "提案编号必须是整数"
    return {"show": show, "accept": accept, "reject": reject}[action](proposal_id)


def _undo_record(record_id: int) -> sqlite3.Row | None:
    with closing(_connect()) as db:
        return db.execute(
            "SELECT id, path, before_content, before_sha256, after_content, "
            "after_sha256, applied_at, undone_at FROM undo_records WHERE id = ?",
            (record_id,),
        ).fetchone()


def list_undo() -> str:
    with closing(_connect()) as db:
        rows = db.execute(
            "SELECT id, path, undone_at FROM undo_records ORDER BY id DESC"
        ).fetchall()
    if not rows:
        return "当前没有撤销记录"
    return "\n".join(
        f"#{row['id']} [{'已撤销' if row['undone_at'] else '可撤销'}] {row['path']}"
        for row in rows
    )


def show_undo(record_id: int) -> str:
    row = _undo_record(record_id)
    if row is None:
        return f"撤销记录 #{record_id} 不存在"
    lines = difflib.unified_diff(
        (row["before_content"] or "").splitlines(keepends=True),
        row["after_content"].splitlines(keepends=True),
        fromfile=(f"a/{row['path']}"
                  if row["before_content"] is not None else "/dev/null"),
        tofile=f"b/{row['path']}",
    )
    diff = "".join(lines)
    status = "已撤销" if row["undone_at"] else "可撤销"
    return f"撤销记录 #{record_id} [{status}]\n{diff or '没有内容变化'}"


def apply_undo(record_id: int) -> str:
    row = _undo_record(record_id)
    if row is None:
        return f"撤销记录 #{record_id} 不存在"
    if row["undone_at"]:
        return f"撤销记录 #{record_id} 已撤销，不能重复执行"
    target = config.workspace_path(row["path"])
    if target is None:
        return "目标路径已越出工作区，拒绝撤销"
    try:
        if (not target.is_file()
                or target.stat().st_size > MAX_BYTES
                or hashlib.sha256(target.read_bytes()).hexdigest()
                != row["after_sha256"]):
            return "目标内容已变化，拒绝撤销；撤销记录仍保留"
        if row["before_content"] is None:
            target.unlink()
        else:
            _write_atomic(target, row["before_content"])
        with closing(_connect()) as db, db:
            db.execute(
                "UPDATE undo_records SET undone_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), record_id),
            )
    except OSError as error:
        return f"撤销失败：{error}"
    return f"已撤销记录 #{record_id}：{row['path']}"


def handle_undo_command(text: str) -> str | None:
    parts = text.split(maxsplit=2)
    if not parts or parts[0] != "/undo":
        return None
    if len(parts) == 1 or parts[1].lower() == "help":
        return _UNDO_HELP
    action = parts[1].lower()
    if action == "list":
        return list_undo()
    if action not in {"show", "apply"} or len(parts) < 3:
        return _UNDO_HELP
    try:
        record_id = int(parts[2])
    except ValueError:
        return "撤销记录编号必须是整数"
    return {"show": show_undo, "apply": apply_undo}[action](record_id)
