import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis import config, mcp_bridge


class McpConfigTest(unittest.TestCase):
    def test_playwright_config_is_isolated_and_workspace_bound(self) -> None:
        conf = mcp_bridge.load_config()["_示例_隔离浏览器"]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "WORKSPACE", Path(tmp).resolve()):
            self.assertFalse(conf["enabled"])
            self.assertIsNone(mcp_bridge._playwright_config_error(conf))
            self.assertEqual(mcp_bridge._resolve_cwd(conf["cwd"]),
                             config.WORKSPACE)
            unsafe = {**conf, "args": [*conf["args"], "--user-data-dir", "."]}
            self.assertIn("禁止参数",
                          mcp_bridge._playwright_config_error(unsafe))
            escaped = {**conf, "args": [
                *conf["args"][:-5], "--output-dir", "../outside",
                "--block-service-workers", "--codegen", "none",
            ]}
            self.assertIn("workspace",
                          mcp_bridge._playwright_config_error(escaped))

    def test_mcp_permissions_are_explicit_and_fail_closed(self) -> None:
        configs = mcp_bridge.load_config()
        for conf in configs.values():
            self.assertIsNone(mcp_bridge._permissions_error(conf))
        self.assertIn("必须配置", mcp_bridge._permissions_error({}))
        self.assertIn("只支持", mcp_bridge._permissions_error({
            "permissions": {"dangerous_tool": "sometimes"}
        }))

        bridge = mcp_bridge.McpBridge()
        name = "mcp__custom__delete_everything"
        bridge._dispatch[name] = (None, "delete_everything")
        bridge._permissions[name] = "confirm"
        self.assertTrue(bridge.requires_confirmation(name, {}))

    def test_skill_report_shows_source_and_permission_without_secrets(self) -> None:
        conf = {
            "demo": {
                "command": "npx",
                "args": ["-y", "@example/server@1.2.3"],
                "env": {"TOKEN": "must-not-leak"},
                "permissions": {
                    "read": "allow", "write": "confirm",
                    "browser_click": "allow",
                },
                "enabled": True,
            }
        }
        with patch.object(mcp_bridge, "load_config", return_value=conf), \
                patch.object(config, "ENABLE_MCP", False), \
                patch.object(config, "ENABLE_DANGEROUS_TOOLS", False):
            report = mcp_bridge.permission_report()
        self.assertIn("get_time [允许]", report)
        self.assertIn("run_shell [关闭：危险工具未启用]", report)
        self.assertIn("demo/read [允许；MCP 全局关闭]", report)
        self.assertIn("demo/write [需确认；MCP 全局关闭]", report)
        self.assertIn("browser_click [需确认：代码强制；MCP 全局关闭]", report)
        self.assertIn("@example/server@1.2.3", report)
        self.assertNotIn("must-not-leak", report)

    def test_browser_risk_policy_covers_upload_submit_and_script_bypasses(self) -> None:
        bridge = mcp_bridge.McpBridge()
        tools = [
            "browser_file_upload", "browser_click", "browser_type",
            "browser_press_key", "browser_handle_dialog", "browser_evaluate",
        ]
        bridge._dispatch = {
            f"mcp__browser__{name}": (None, name) for name in tools
        }
        self.assertTrue(bridge.requires_confirmation(
            "mcp__browser__browser_file_upload", {"paths": ["report.txt"]}
        ))
        self.assertTrue(bridge.requires_confirmation(
            "mcp__browser__browser_click", {"element": "Pay now", "target": "e1"}
        ))
        self.assertTrue(bridge.requires_confirmation(
            "mcp__browser__browser_type", {"text": "yes", "submit": True}
        ))
        self.assertTrue(bridge.requires_confirmation(
            "mcp__browser__browser_press_key", {"key": "Enter"}
        ))
        self.assertTrue(bridge.requires_confirmation(
            "mcp__browser__browser_handle_dialog", {"accept": True}
        ))
        self.assertTrue(bridge.requires_confirmation(
            "mcp__browser__browser_evaluate", {"function": "() => 1"}
        ))
        self.assertFalse(bridge.requires_confirmation(
            "mcp__browser__browser_type", {"text": "draft"}
        ))

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, "WORKSPACE", Path(tmp).resolve()):
            self.assertIn("越出工作区", bridge.validation_error(
                "mcp__browser__browser_file_upload", {"paths": ["../secret.txt"]}
            ))


if __name__ == "__main__":
    unittest.main()
