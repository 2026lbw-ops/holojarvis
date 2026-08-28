import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from jarvis import tasks


class TasksTest(unittest.TestCase):
    def test_old_database_is_migrated_without_losing_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.db"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("""CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
                db.execute("INSERT INTO tasks VALUES(1, '旧任务', 'todo', 'a', 'a')")
            with patch.object(tasks, "_DB_PATH", path):
                item = tasks.list_tasks("all")[0]
            self.assertEqual((item["title"], item["progress"], item["progress_note"]),
                             ("旧任务", 0, ""))
            self.assertIsNone(item["remind_at"])
            self.assertEqual(item["reminder_note"], "")
            self.assertIsNone(item["reminded_at"])

    def test_progress_lifecycle_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tasks, "_DB_PATH", Path(tmp) / "tasks.db"):
            tasks.add("实现进度")
            self.assertIn("25%", tasks.update_progress(1, 25, "完成需求分析"))
            item = tasks.list_tasks("all")[0]
            self.assertEqual((item["status"], item["progress"],
                              item["progress_note"]),
                             ("doing", 25, "完成需求分析"))
            self.assertIn("0 到 100", tasks.update_progress(1, 101))
            self.assertIn("必须是整数",
                          tasks.handle_command("/task progress 1 abc"))
            tasks.set_status(1, "done")
            self.assertIn("请先使用", tasks.update_progress(1, 80))
            self.assertIn("请先使用", tasks.set_status(1, "doing"))
            tasks.set_status(1, "todo")
            reopened = tasks.list_tasks("all")[0]
            self.assertEqual((reopened["status"], reopened["progress"],
                              reopened["progress_note"]), ("todo", 0, ""))
            future = (datetime.now().astimezone() + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M"
            )
            tasks.set_reminder(1, future)
            tasks.update_progress(1, 100, "全部完成")
            completed = tasks.list_tasks("all")[0]
            self.assertEqual(completed["status"], "done")
            self.assertIsNone(completed["remind_at"])

    def test_persistent_reminder_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tasks, "_DB_PATH", Path(tmp) / "tasks.db"):
            tasks.add("提交报告")
            future = (datetime.now().astimezone() + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M"
            )
            self.assertIn(future, tasks.set_reminder(1, future, "带上附件"))
            self.assertIn("带上附件", tasks.reminders())
            self.assertIn("格式", tasks.set_reminder(1, "tomorrow"))
            with closing(tasks._connect()) as db, db:
                db.execute(
                    "UPDATE tasks SET remind_at = ? WHERE id = 1",
                    ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),),
                )
            self.assertIn("已到期", tasks.reminders("due"))
            self.assertIn("⏰ 到期提醒", tasks.pop_due_reminders())
            self.assertEqual(tasks.pop_due_reminders(), "")
            self.assertIn("已提醒", tasks.reminders("all"))
            self.assertIn("已取消", tasks.clear_reminder(1))


if __name__ == "__main__":
    unittest.main()
