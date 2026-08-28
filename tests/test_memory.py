import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis import memory


class MemoryTest(unittest.TestCase):
    def test_legacy_json_is_imported_once_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "memory.json"
            legacy.write_text(json.dumps([
                {"fact": "用户喜欢安静", "at": "2026-01-02"}
            ], ensure_ascii=False), encoding="utf-8")
            with patch.object(memory, "_DB_PATH", root / "memory.db"), \
                    patch.object(memory, "_LEGACY_PATH", legacy):
                self.assertEqual(len(memory.load()), 1)
                self.assertEqual(len(memory.load()), 1)
                self.assertTrue(legacy.exists())

    def test_memory_management_and_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(memory, "_DB_PATH", Path(tmp) / "memory.db"), \
                patch.object(memory, "_LEGACY_PATH", Path(tmp) / "missing.json"):
            self.assertIn("记住了", memory.add("项目使用 Python 3.12", "core"))
            self.assertIn("早就记下", memory.add("项目使用 Python 3.12", "core"))
            item = memory.load("core")[0]
            self.assertEqual(item["category"], "core")
            self.assertIn("已修改", memory.update(
                item["id"], "HoloJarvis 使用 Python 3.12", "project"
            ))
            export_path = Path(tmp) / "export.json"
            self.assertIn("1 条", memory.export_json(export_path, "project"))
            exported = json.loads(export_path.read_text(encoding="utf-8"))
            self.assertEqual(exported["items"][0]["category"], "project")
            self.assertNotIn("HoloJarvis", memory.as_prompt(("core",)))
            self.assertIn("HoloJarvis", memory.as_prompt(("project",)))
            self.assertIn("1 条", memory.clear("project"))
            self.assertEqual(memory.load(), [])
            self.assertIn("不支持", memory.add("错误分类", "session"))


if __name__ == "__main__":
    unittest.main()
