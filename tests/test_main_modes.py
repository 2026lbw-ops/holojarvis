import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis import config, diffs, mcp_bridge, tasks
from jarvis import __main__ as app


class _UI:
    def __init__(self) -> None:
        self.output = []

    def log(self, text: str) -> None:
        self.output.append(text)

    def reply(self, text: str) -> None:
        self.output.append(text)


class _Brain:
    def __init__(self) -> None:
        self.received = []

    def ask_stream(self, text: str):
        self.received.append(text)
        yield f"收到：{text}"

    def reset(self) -> None:
        pass


class MainModesTest(unittest.TestCase):
    def test_help_exits_without_running_startup_checks(self) -> None:
        output = io.StringIO()
        with patch.object(sys, "argv", ["jarvis", "--help"]), \
                patch.object(config, "auto_configure_ollama") as auto_configure, \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            app.main()
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--text", output.getvalue())
        auto_configure.assert_not_called()

    def test_text_mode_runs_without_audio(self) -> None:
        ui = _UI()
        brain = _Brain()
        inputs = iter(["/task add 写测试", "/task start 1", "/task done 1",
                       "/task list done", "/task reminders", "/diff list", "/undo list",
                       "/skills builtin", "你好", "/exit"])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tasks, "_DB_PATH", Path(tmp) / "tasks.db"), \
                patch.object(diffs, "_DB_PATH", Path(tmp) / "diffs.db"), \
                patch.object(mcp_bridge, "load_config", return_value={}), \
                patch.object(app, "_build_brain", return_value=brain):
            code = app.run_text(ui, lambda _: next(inputs))
        self.assertEqual(code, 0)
        self.assertIn("#1 [已完成 100%] 写测试", ui.output)
        self.assertIn("当前没有符合条件的提醒", ui.output)
        self.assertIn("当前没有待审文件提案", ui.output)
        self.assertIn("当前没有撤销记录", ui.output)
        self.assertTrue(any("get_time [允许]" in line for line in ui.output))
        self.assertIn("收到：你好", ui.output)
        self.assertEqual(brain.received, ["你好"])

    def test_text_startup_check_does_not_probe_microphone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "WORKSPACE", Path(tmp)), \
                patch.object(config, "LLM_BASE_URL", "http://127.0.0.1:11434/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False), \
                patch.object(config, "load_api_key", return_value=None):
            checks = app.startup_checks(require_audio=False)
        self.assertTrue(all(ok for ok, _ in checks))


if __name__ == "__main__":
    unittest.main()
