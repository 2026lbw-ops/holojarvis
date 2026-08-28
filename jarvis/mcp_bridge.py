"""MCP 桥接——把本地 MCP 服务器的工具接进贾维斯的大脑。

读取项目根目录的 mcp.json，启动里面配置的 MCP 服务器(stdio)，把它们的工具
转成 Claude 能调用的工具(名字加 mcp__<服务器>__<工具> 前缀)。

MCP SDK 是 asyncio 的，这里用一个独立事件循环线程承载，对外暴露同步接口，
方便在贾维斯的同步主循环里调用。整个模块对错误高度容忍：装没装 SDK、配置在不在、
某个服务器起没起来，都不影响主程序——起得来几个用几个。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
from contextlib import AsyncExitStack
from pathlib import Path

from . import config, tools

_CONFIG = Path(__file__).resolve().parent.parent / "mcp.json"


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:60]


def _resolve_command(cmd: str) -> str:
    """Windows 上 npx/npm/uvx 等是 .cmd/.exe，裸名字 subprocess 起不来，
    这里解析成可执行文件的绝对路径（解析不到就原样返回）。"""
    if not config.IS_WINDOWS or os.path.splitext(cmd)[1]:
        return cmd
    for cand in (cmd, cmd + ".cmd", cmd + ".exe", cmd + ".bat"):
        found = shutil.which(cand)
        if found:
            return found
    return cmd


def load_config() -> dict:
    if not _CONFIG.exists():
        return {}
    try:
        return json.loads(_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _playwright_config_error(conf: dict) -> str | None:
    args = [str(arg) for arg in conf.get("args", [])]
    if not any("@playwright/mcp" in arg for arg in args):
        return None
    forbidden = (
        "--allow-unrestricted-file-access", "--cdp-endpoint", "--extension",
        "--no-sandbox", "--save-session", "--storage-state", "--user-data-dir",
    )
    used = next((flag for flag in forbidden
                 if any(arg == flag or arg.startswith(flag + "=") for arg in args)),
                None)
    if used:
        return f"Playwright 隔离配置禁止参数 {used}"
    if "--isolated" not in args or conf.get("cwd") != "{workspace}":
        return "Playwright 必须使用 --isolated 且 cwd 必须是 {workspace}"
    indexes = [i for i, arg in enumerate(args) if arg == "--output-dir"]
    if len(indexes) != 1 or indexes[0] + 1 >= len(args):
        return "Playwright 必须设置唯一的 --output-dir"
    if config.workspace_path(args[indexes[0] + 1]) is None:
        return "Playwright 输出目录必须位于 workspace 内"
    return None


def _resolve_cwd(value: str | None) -> Path | None:
    if not value:
        return None
    if value == "{workspace}":
        return config.WORKSPACE
    path = Path(os.path.expanduser(value))
    return path.resolve() if path.is_absolute() else (_CONFIG.parent / path).resolve()


def _permissions_error(conf: dict) -> str | None:
    permissions = conf.get("permissions")
    if not isinstance(permissions, dict) or not permissions:
        return "必须配置非空 permissions；未列出的工具默认拒绝"
    invalid = [name for name, mode in permissions.items()
               if not isinstance(name, str) or mode not in {"allow", "confirm"}]
    return ("permissions 只支持 allow 或 confirm：" + ", ".join(map(str, invalid))
            if invalid else None)


def _source(conf: dict) -> str:
    command = str(conf.get("command", "未知"))
    package = next((str(arg) for arg in conf.get("args", [])
                    if isinstance(arg, str) and not arg.startswith("-")
                    and arg not in {".", "{workspace}"}), "")
    return f"{command} · {package}" if package else command


def permission_report(scope: str = "all") -> str:
    if scope not in {"all", "builtin", "mcp"}:
        return "用法：/skills [builtin|mcp]"
    sections = []
    if scope in {"all", "builtin"}:
        memory_allowed, _ = config.cloud_memory_policy()
        lines = ["内置 Skill（来源：jarvis.tools）"]
        for schema in tools.TOOL_SCHEMAS:
            name = schema["name"]
            if name == "list_memories" and not memory_allowed:
                status = "关闭：当前模型未获持久记忆权限"
            elif tools.TOOL_RISKS.get(name) == "high":
                if not config.ENABLE_DANGEROUS_TOOLS:
                    status = "关闭：危险工具未启用"
                elif name == "run_shell" and not config.ENABLE_SHELL:
                    status = "关闭：Shell 未启用"
                else:
                    status = "需确认"
            else:
                status = "允许"
            lines.append(f"- {name} [{status}]")
        sections.append("\n".join(lines))
    if scope in {"all", "mcp"}:
        global_status = "已启用" if config.ENABLE_MCP else "全局关闭"
        lines = [f"MCP Skill 配置（{global_status}）"]
        configs = load_config()
        if not configs:
            lines.append("- 没有可读取的 MCP 配置")
        for server, conf in configs.items():
            source = _source(conf)
            error = _permissions_error(conf)
            if error:
                lines.append(f"- {server}/* [关闭：{error}] 来源：{source}")
                continue
            server_status = ("MCP 全局关闭" if not config.ENABLE_MCP else
                             "服务器关闭" if not conf.get("enabled", True) else "")
            for name, mode in conf["permissions"].items():
                if name in {"browser_click", "browser_file_upload",
                            "browser_evaluate", "browser_run_code_unsafe"}:
                    status = "需确认：代码强制"
                elif mode == "allow" and name in {
                        "browser_type", "browser_press_key",
                        "browser_handle_dialog", "browser_drop"}:
                    status = "允许；敏感参数需确认"
                else:
                    status = "允许" if mode == "allow" else "需确认"
                if server_status:
                    status += f"；{server_status}"
                lines.append(f"- {server}/{name} [{status}] 来源：{source}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def handle_permissions_command(text: str) -> str | None:
    parts = text.split()
    if not parts or parts[0] != "/skills":
        return None
    scope = parts[1].lower() if len(parts) == 2 else "all"
    if len(parts) > 2 or scope == "help":
        return "用法：/skills [builtin|mcp]"
    return permission_report(scope)


class McpBridge:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stack: AsyncExitStack | None = None
        self._schemas: list[dict] = []
        self._dispatch: dict[str, tuple] = {}   # full_name -> (session, tool_name)
        self._permissions: dict[str, str] = {}
        self._ready = threading.Event()
        self.names: list[str] = []              # 成功连上的服务器名

    # ---- 启动 --------------------------------------------------------
    def start(self, config: dict, log=print, timeout: float = 60) -> None:
        if not config:
            return
        try:
            import mcp  # noqa: F401
        except ImportError:
            log("⚠ 未安装 mcp 库，跳过 MCP（pip install mcp 可启用）")
            return
        threading.Thread(target=self._run, args=(config, log),
                         daemon=True).start()
        self._ready.wait(timeout=timeout)

    def _run(self, config: dict, log) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._setup(config, log))
        finally:
            self._ready.set()
        self._loop.run_forever()

    async def _setup(self, config: dict, log) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        for name, conf in config.items():
            if not conf.get("enabled", True):
                continue
            try:
                error = _permissions_error(conf)
                if error:
                    log(f"  ⚠ MCP「{name}」未启动：{error}")
                    continue
                error = _playwright_config_error(conf)
                if error:
                    log(f"  ⚠ MCP「{name}」未启动：{error}")
                    continue
                args = [os.path.expanduser(a) for a in conf.get("args", [])]
                env = {**os.environ, **conf.get("env", {})}
                # 绕开损坏的 ~/.npm 权限：给 npx 用项目内可写缓存
                if "npx" in conf["command"] and "npm_config_cache" not in env:
                    cache = _CONFIG.parent / ".npm-cache"
                    cache.mkdir(exist_ok=True)
                    env["npm_config_cache"] = str(cache)
                params = StdioServerParameters(
                    command=_resolve_command(conf["command"]), args=args, env=env,
                    cwd=_resolve_cwd(conf.get("cwd")),
                )
                read, write = await self._stack.enter_async_context(
                    stdio_client(params))
                session = await self._stack.enter_async_context(
                    ClientSession(read, write))
                await session.initialize()
                resp = await session.list_tools()
                counts = {"allow": 0, "confirm": 0, "deny": 0}
                for t in resp.tools:
                    mode = conf["permissions"].get(t.name, "deny")
                    counts[mode] += 1
                    if mode == "deny":
                        continue
                    full = f"mcp__{_sanitize(name)}__{_sanitize(t.name)}"[:64]
                    self._schemas.append({
                        "name": full,
                        "description": (t.description or t.name)[:1000],
                    "input_schema": t.input_schema or {
                        "type": "object", "properties": {}},
                    })
                    self._dispatch[full] = (session, t.name)
                    self._permissions[full] = mode
                self.names.append(name)
                log(f"  ✓ MCP「{name}」已接入（允许 {counts['allow']}，"
                    f"需确认 {counts['confirm']}，拒绝 {counts['deny']}）")
            except Exception as e:  # noqa: BLE001
                log(f"  ⚠ MCP「{name}」启动失败：{e}")

    # ---- 对外接口 ----------------------------------------------------
    def tool_schemas(self) -> list[dict]:
        return self._schemas

    def has(self, name: str) -> bool:
        return name in self._dispatch

    def _tool_name(self, full_name: str) -> str:
        item = self._dispatch.get(full_name)
        return item[1] if item else ""

    def validation_error(self, full_name: str, args: dict) -> str | None:
        tool_name = self._tool_name(full_name)
        if tool_name not in {"browser_file_upload", "browser_drop"}:
            return None
        paths = args.get("paths")
        if paths is None:
            return None
        if not isinstance(paths, list) or any(not isinstance(path, str)
                                              for path in paths):
            return "上传文件路径必须是字符串列表"
        for path in paths:
            target = config.workspace_path(path)
            if target is None:
                return f"上传路径越出工作区：{path}"
            if not target.is_file():
                return f"上传文件不存在：{path}"
        return None

    def confirmation_reason(self, full_name: str, args: dict) -> str | None:
        tool_name = self._tool_name(full_name)
        if tool_name in {"browser_file_upload", "browser_drop"} and args.get("paths"):
            return "上传工作区文件"
        if tool_name == "browser_click":
            return "点击网页元素（可能提交、购买或付款）"
        if tool_name in {"browser_press_key", "browser_keydown", "browser_keyup"} \
                and str(args.get("key", "")).lower() in {"enter", "space"}:
            return "按键可能提交网页表单"
        if tool_name in {"browser_type", "browser_press_sequentially"} \
                and args.get("submit") is True:
            return "输入后提交网页表单"
        if tool_name == "browser_handle_dialog" and args.get("accept") is True:
            return "接受网页确认对话框"
        if tool_name in {"browser_evaluate", "browser_run_code_unsafe"}:
            return "执行可绕过页面操作确认的浏览器脚本"
        if self._permissions.get(full_name) == "confirm":
            return f"MCP 权限要求确认：{tool_name}"
        return None

    def requires_confirmation(self, full_name: str, args: dict) -> bool:
        return self.confirmation_reason(full_name, args) is not None

    def call(self, full_name: str, args: dict, *, confirmed: bool = False) -> str:
        if self._loop is None or full_name not in self._dispatch:
            return f"未知 MCP 工具：{full_name}"
        error = self.validation_error(full_name, args)
        if error:
            tools.audit(full_name, args, "blocked", risk="high")
            return error
        reason = self.confirmation_reason(full_name, args)
        if reason and not confirmed:
            tools.audit(full_name, args, "confirmation_required", risk="high")
            return (f"高风险浏览器操作“{reason}”尚未执行。请向用户说明操作内容，"
                    "并等待用户在下一轮明确回复“确认执行”或“取消”。")
        session, tool_name = self._dispatch[full_name]
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._call(session, tool_name, args or {}), self._loop)
            result = fut.result(timeout=90)
            if reason:
                tools.audit(full_name, args, "executed", risk="high")
            return result
        except Exception as e:  # noqa: BLE001
            if reason:
                tools.audit(full_name, args, "error", risk="high")
            return f"调用 MCP 工具出错：{e}"

    async def _call(self, session, tool_name: str, args: dict) -> str:
        res = await session.call_tool(tool_name, args)
        parts = []
        for c in getattr(res, "content", []) or []:
            text = getattr(c, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts) or "（已执行，无文本输出）"
