import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest.mock import patch

from jarvis import config, mcp_bridge, tools
from jarvis.brain import Brain


class ToolSecurityTest(unittest.TestCase):
    def test_local_requests_disable_reasoning(self) -> None:
        brain = Brain("test")
        with patch.object(config, "LLM_BASE_URL", "http://127.0.0.1:11434/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False):
            self.assertEqual(
                brain._request_body([], stream=True)["reasoning_effort"], "none")
        with patch.object(config, "LLM_BASE_URL", "https://example.com/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False):
            self.assertNotIn("reasoning_effort",
                             brain._request_body([], stream=False))
        with patch.object(config, "LLM_BASE_URL", "https://api.deepseek.com"), \
                patch.object(config, "LOCAL_PROVIDER", False):
            body = brain._request_body([], stream=False)
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertNotIn("reasoning_effort", body)

    def test_high_risk_tools_are_hidden_and_blocked_by_default(self) -> None:
        with patch.object(config, "ENABLE_DANGEROUS_TOOLS", False), \
                patch.object(config, "LLM_BASE_URL", "https://example.com/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False), \
                patch.dict(os.environ, {"JARVIS_CLOUD_MEMORY": "none"}):
            names = {schema["name"] for schema in tools.tool_schemas()}
            self.assertNotIn("run_shell", names)
            self.assertNotIn("read_text_file", names)
            self.assertNotIn("clear_memories", names)
            self.assertNotIn("list_memories", names)
            self.assertIn("propose_file_change", names)
            self.assertIn("默认关闭", tools.run("run_shell", {"command": "echo no"}))
            self.assertIn("默认关闭", tools.run("read_text_file", {"path": "notes.txt"}))

    def test_file_read_requires_cross_turn_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "ENABLE_DANGEROUS_TOOLS", True), \
                patch.object(config, "AUDIT_LOG", Path(tmp) / "audit.jsonl"):
            brain = Brain("test")
            self.assertIn("尚未执行", brain._dispatch(
                "read_text_file", {"path": "notes.txt"}
            ))
            self.assertEqual(brain._resolve_pending("取消"), "已取消这个操作。")

    def test_cloud_memory_tool_only_returns_allowed_categories(self) -> None:
        items = [
            {"id": 1, "category": "core", "fact": "核心"},
            {"id": 2, "category": "project", "fact": "项目"},
        ]
        with patch.object(config, "LLM_BASE_URL", "https://example.com/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False), \
                patch.dict(os.environ, {"JARVIS_CLOUD_MEMORY": "core"}), \
                patch.object(tools.memory, "load", return_value=items):
            self.assertEqual(tools.list_memories(), "#1 [core] 核心")
            self.assertIn("未授权", tools.list_memories("project"))

    def test_file_access_stays_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            with patch.object(config, "WORKSPACE", workspace):
                self.assertEqual(tools._workspace_path("notes"), str(workspace / "notes"))
                self.assertIsNone(tools._workspace_path("../outside"))

    def test_dangerous_tool_requires_a_separate_confirmation_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "ENABLE_DANGEROUS_TOOLS", True), \
                patch.object(config, "AUDIT_LOG", Path(tmp) / "audit.jsonl"), \
                patch.dict(tools.DISPATCH, {"system_power": lambda action: f"done:{action}"}):
            brain = Brain("test")
            first = brain._dispatch("system_power", {"action": "lock"})
            self.assertIn("尚未执行", first)
            self.assertEqual(brain._resolve_pending("确认执行"), "done:lock")
            events = [json.loads(line) for line in config.AUDIT_LOG.read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual([e["status"] for e in events],
                             ["confirmation_required", "executed"])
            self.assertNotIn("lock", config.AUDIT_LOG.read_text(encoding="utf-8"))

    def test_non_confirmation_cancels_pending_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "ENABLE_DANGEROUS_TOOLS", True), \
                patch.object(config, "AUDIT_LOG", Path(tmp) / "audit.jsonl"):
            brain = Brain("test")
            brain._dispatch("system_power", {"action": "sleep"})
            self.assertEqual(brain._resolve_pending("取消"), "已取消这个操作。")
            self.assertIsNone(brain._pending)
            brain._dispatch("system_power", {"action": "sleep"})
            brain.reset()
            self.assertIsNone(brain._pending)

    def test_browser_click_requires_cross_turn_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "AUDIT_LOG", Path(tmp) / "audit.jsonl"):
            bridge = mcp_bridge.McpBridge()
            name = "mcp__browser__browser_click"
            bridge._loop = object()
            bridge._dispatch[name] = (None, "browser_click")
            brain = Brain("test", mcp=bridge)
            first = brain._dispatch(name, {"element": "Pay now", "target": "e1"})
            self.assertIn("尚未执行", first)
            self.assertIsNotNone(brain._pending)
            with patch.object(bridge, "call", return_value="已点击") as call:
                self.assertEqual(brain._resolve_pending("确认执行"), "已点击")
            call.assert_called_once_with(
                name, {"element": "Pay now", "target": "e1"}, confirmed=True
            )
            event = json.loads(config.AUDIT_LOG.read_text(
                encoding="utf-8").splitlines()[0])
            self.assertEqual((event["risk"], event["status"]),
                             ("high", "confirmation_required"))

    def test_pending_browser_action_stops_later_tools_in_same_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "AUDIT_LOG", Path(tmp) / "audit.jsonl"):
            bridge = mcp_bridge.McpBridge()
            bridge._loop = object()
            click = "mcp__browser__browser_click"
            navigate = "mcp__browser__browser_navigate"
            bridge._dispatch = {
                click: (None, "browser_click"),
                navigate: (None, "browser_navigate"),
            }
            brain = Brain("test", mcp=bridge)
            brain._run_tools([
                {"id": "1", "function": {
                    "name": click,
                    "arguments": '{"element":"Pay now","target":"e1"}',
                }},
                {"id": "2", "function": {
                    "name": navigate,
                    "arguments": '{"url":"https://example.com/success"}',
                }},
            ])
            self.assertEqual(brain._pending[0], click)
            self.assertIn("前一个高风险操作", brain._messages[-1]["content"])

    def test_pending_browser_action_stops_later_model_rounds(self) -> None:
        click = "mcp__browser__browser_click"

        class FakeMcp:
            def tool_schemas(self):
                return []

            def validation_error(self, name, args):
                return None

            def requires_confirmation(self, name, args):
                return name == click

            def call(self, name, args, confirmed=False):
                return "尚未执行，等待确认" if not confirmed else "已执行"

        brain = Brain("test", mcp=FakeMcp())
        first = {"content": "", "tool_calls": [{"id": "1", "function": {
            "name": click,
            "arguments": '{"element":"first","target":"e1"}',
        }}]}
        with patch.object(brain, "_chat", side_effect=[first, AssertionError]):
            self.assertIn("尚未执行", brain.ask("执行第一个动作"))
        self.assertEqual(brain._pending, (click, {"element": "first", "target": "e1"}))

        def first_stream(_):
            yield ("tool", (0, "1", click,
                             '{"element":"first","target":"e1"}'))

        brain = Brain("test", mcp=FakeMcp())
        with patch.object(brain, "_stream_once", side_effect=[first_stream(None), AssertionError]):
            self.assertTrue(any("尚未执行" in text
                                for text in brain.ask_stream("执行第一个动作")))
        self.assertEqual(brain._pending, (click, {"element": "first", "target": "e1"}))

    def test_history_is_trimmed_at_user_turn_boundaries(self) -> None:
        brain = Brain("test")
        brain._messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ]
        with patch.object(config, "HISTORY_TURNS", 3):
            brain._start_turn("u4")
        users = [m["content"] for m in brain._messages if m["role"] == "user"]
        self.assertEqual(users, ["u2", "u3", "u4"])


if __name__ == "__main__":
    unittest.main()
