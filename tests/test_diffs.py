import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis import config, diffs


class DiffsTest(unittest.TestCase):
    def test_propose_show_and_accept_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            target = workspace / "app.py"
            target.write_text("old\n", encoding="utf-8")
            with patch.object(config, "WORKSPACE", workspace), \
                    patch.object(diffs, "_DB_PATH", root / "diffs.db"):
                self.assertIn("提案 #1", diffs.propose("app.py", "new\n"))
                self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
                shown = diffs.show(1)
                self.assertIn("-old", shown)
                self.assertIn("+new", shown)
                self.assertIn("撤销记录 #1", diffs.accept(1))
                self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
                self.assertIn("没有待审", diffs.list_proposals())
                self.assertIn("#1 [可撤销] app.py", diffs.list_undo())
                self.assertIn("+new", diffs.show_undo(1))
                self.assertIn("已撤销", diffs.apply_undo(1))
                self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
                self.assertIn("#1 [已撤销] app.py", diffs.list_undo())
                self.assertIn("不能重复", diffs.apply_undo(1))

    def test_create_reject_and_conflict_preserve_user_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            target = workspace / "existing.txt"
            target.write_text("base", encoding="utf-8")
            with patch.object(config, "WORKSPACE", workspace), \
                    patch.object(diffs, "_DB_PATH", root / "diffs.db"):
                diffs.propose("existing.txt", "proposal")
                target.write_text("user edit", encoding="utf-8")
                self.assertIn("内容已变化", diffs.accept(1))
                self.assertIn("#1", diffs.list_proposals())
                self.assertIn("已拒绝", diffs.reject(1))
                self.assertEqual(target.read_text(encoding="utf-8"), "user edit")

                new_file = workspace / "nested" / "new.txt"
                diffs.propose("nested/new.txt", "created")
                self.assertFalse(new_file.exists())
                self.assertIn("已接受", diffs.accept(2))
                self.assertEqual(new_file.read_text(encoding="utf-8"), "created")
                self.assertIn("已撤销", diffs.apply_undo(1))
                self.assertFalse(new_file.exists())

    def test_undo_refuses_to_overwrite_later_user_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            target = workspace / "notes.txt"
            target.write_text("before", encoding="utf-8")
            with patch.object(config, "WORKSPACE", workspace), \
                    patch.object(diffs, "_DB_PATH", root / "diffs.db"):
                diffs.propose("notes.txt", "accepted")
                diffs.accept(1)
                target.write_text("later edit", encoding="utf-8")
                self.assertIn("内容已变化", diffs.apply_undo(1))
                self.assertEqual(target.read_text(encoding="utf-8"), "later edit")
                self.assertIn("#1 [可撤销]", diffs.list_undo())

    def test_rejects_unsafe_binary_and_oversized_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "binary.bin").write_bytes(b"\xff\xfe")
            (workspace / "large.txt").write_bytes(b"x" * (diffs.MAX_BYTES + 1))
            with patch.object(config, "WORKSPACE", workspace), \
                    patch.object(diffs, "_DB_PATH", root / "diffs.db"):
                self.assertIn("越出工作区", diffs.propose("../outside", "x"))
                self.assertIn("不是 UTF-8", diffs.read_text("binary.bin"))
                self.assertIn("超过 256 KiB", diffs.read_text("large.txt"))
                self.assertIn("超过 256 KiB", diffs.propose(
                    "new.txt", "x" * (diffs.MAX_BYTES + 1)
                ))


if __name__ == "__main__":
    unittest.main()
