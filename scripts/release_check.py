"""HoloJarvis 发布前自动验收。使用项目 Python 3.12 直接运行。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def run(label: str, command: list[str], *, input_text: str | None = None,
        capture: bool = False) -> subprocess.CompletedProcess[str]:
    print(f"[检查] {label}")
    result = subprocess.run(
        command, cwd=ROOT, input=input_text, text=True, encoding="utf-8",
        errors="replace", capture_output=capture, check=False,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if result.returncode:
        if capture:
            print(result.stdout, result.stderr, sep="\n", file=sys.stderr)
        raise SystemExit(f"[失败] {label}（退出码 {result.returncode}）")
    print(f"[通过] {label}")
    return result


def main() -> int:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(f"[失败] 需要 Python 3.12，当前为 {sys.version.split()[0]}")
    print(f"[通过] Python {sys.version.split()[0]}")

    json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    print("[通过] mcp.json 是合法 JSON")

    python = sys.executable
    run("源码与测试可编译", [python, "-m", "compileall", "-q", "jarvis", "tests"])
    run("完整单元测试", [python, "-m", "unittest", "discover", "-s", "tests", "-v"])
    run("Python 依赖一致", [python, "-m", "pip", "check"])
    run("Git Diff 无空白错误", ["git", "diff", "--check"])

    tracked = run("读取 Git 跟踪文件", ["git", "ls-files"], capture=True).stdout.splitlines()
    forbidden_names = {
        "api_key.txt", "base_url.txt", "model.txt", "xfyun.txt",
        "memory.json", "audit.jsonl",
    }
    leaked = [path for path in tracked
              if path.replace("\\", "/") in forbidden_names
              or path.endswith((".db", ".db-shm", ".db-wal", ".wav", ".mp3"))
              or path.replace("\\", "/").startswith("workspace/")]
    if leaked:
        raise SystemExit("[失败] Git 正在跟踪敏感/运行时文件：" + ", ".join(leaked))
    print("[通过] 未跟踪密钥、数据库、工作区或音频文件")

    safe_env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "JARVIS_BASE_URL": "http://127.0.0.1:11434/v1",
        "JARVIS_ENABLE_MCP": "0",
        "JARVIS_ENABLE_DANGEROUS_TOOLS": "0",
        "JARVIS_ENABLE_SHELL": "0",
    }
    print("[检查] 无云调用启动自检")
    checked = subprocess.run(
        [python, "-m", "jarvis", "--check", "--text"], cwd=ROOT,
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        check=False, env=safe_env,
    )
    if checked.returncode:
        print(checked.stdout, checked.stderr, sep="\n", file=sys.stderr)
        raise SystemExit("[失败] 启动自检")
    print("[通过] 无云调用启动自检")

    print("[检查] 本地文字命令 smoke")
    smoke = subprocess.run(
        [python, "-m", "jarvis", "--text"], cwd=ROOT,
        input="/skills builtin\n/task list\n/diff list\n/undo list\n/exit\n",
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        check=False, env=safe_env,
    )
    expected = ("get_time [允许]", "当前没有符合条件的任务",
                "当前没有待审文件提案", "当前没有撤销记录")
    if smoke.returncode or any(text not in smoke.stdout for text in expected):
        print(smoke.stdout, smoke.stderr, sep="\n", file=sys.stderr)
        raise SystemExit("[失败] 本地文字命令 smoke")
    print("[通过] 本地文字命令 smoke")
    print("\n自动验收全部通过；发布前仍须完成 docs/RELEASE_CHECKLIST.md 的人工项目。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
