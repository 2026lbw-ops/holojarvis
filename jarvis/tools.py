"""贾维斯能调用的「手脚」——控制电脑的各种工具（macOS / Windows 通用）。

每个工具都有：
    - 一个 Claude 可识别的 JSON schema（放进 TOOL_SCHEMAS）
    - 一个实际执行的 Python 函数（放进 DISPATCH）

平台差异：macOS 走 osascript / open / pmset 等；Windows 走 PowerShell / ctypes
（实现集中在 winops.py）。两边对外暴露同一套工具，大脑无需关心底层差异。
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, diffs, memory, tts

if config.IS_WINDOWS:
    from . import winops

# --- 各工具实现 --------------------------------------------------------


def _osascript(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return (r.stdout or r.stderr).strip()


def _key(combo: str) -> None:
    """发送一个按键组合给系统，例如 'command down' + 'v'。"""
    _osascript(f'tell application "System Events" to {combo}')


def _get_clipboard() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def _set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))


def open_app(name: str) -> str:
    if config.IS_WINDOWS:
        return winops.open_app(name)
    r = subprocess.run(["open", "-a", name], capture_output=True, text=True)
    return f"已打开 {name}" if r.returncode == 0 else f"没找到应用「{name}」"


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if config.IS_WINDOWS:
        os.startfile(url)  # noqa: S606 — Windows 默认浏览器打开
    else:
        subprocess.run(["open", url])
    return "已在浏览器打开"


def web_search(query: str) -> str:
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/search?q={q}"
    if config.IS_WINDOWS:
        os.startfile(url)  # noqa: S606
    else:
        subprocess.run(["open", url])
    return f"已帮你搜索「{query}」"


def set_volume(level: int) -> str:
    if config.IS_WINDOWS:
        return winops.set_volume(level)
    level = max(0, min(100, int(level)))
    _osascript(f"set volume output volume {level}")
    return f"音量已设为 {level}"


def get_time() -> str:
    now = datetime.datetime.now()
    week = "一二三四五六日"[now.weekday()]
    return now.strftime(f"现在是 %Y年%m月%d日 星期{week} %H点%M分")


def get_weather(city: str) -> str:
    """用 wttr.in 查天气（无需 API key）。"""
    try:
        c = urllib.parse.quote(city)
        fmt = urllib.parse.quote("%l：%C，%t，体感%f，湿度%h")
        url = f"https://wttr.in/{c}?format={fmt}&lang=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "curl"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception as e:  # noqa: BLE001
        return f"查天气失败：{e}"


def control_music(action: str) -> str:
    if config.IS_WINDOWS:
        return winops.media(action)
    mapping = {
        "play": "play", "pause": "pause", "playpause": "playpause",
        "next": "next track", "previous": "previous track",
    }
    cmd = mapping.get(action, "playpause")
    _osascript(f'tell application "Music" to {cmd}')
    names = {"play": "播放", "pause": "暂停", "playpause": "切换播放",
             "next": "下一首", "previous": "上一首"}
    return names.get(action, "已操作") + "音乐"


def set_timer(seconds: int, message: str = "时间到") -> str:
    def fire() -> None:
        tts.speak(message, blocking=False)

    threading.Timer(max(1, int(seconds)), fire).start()
    mins = seconds // 60
    desc = f"{mins}分钟" if mins else f"{seconds}秒"
    return f"好的，{desc}后提醒你：{message}"


def take_screenshot() -> str:
    name = datetime.datetime.now().strftime("截图-%Y%m%d-%H%M%S.png")
    path = os.path.join(os.path.expanduser("~/Desktop"), name)
    if config.IS_WINDOWS:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(path)
    else:
        subprocess.run(["screencapture", path])
    return "截图已保存到桌面"


def system_power(action: str) -> str:
    if config.IS_WINDOWS:
        if action == "lock":
            winops.lock()
            return "已锁屏"
        if action == "sleep":
            winops.sleep_pc()
            return "电脑准备休眠"
        return "为安全起见，关机/重启请手动操作"
    if action == "lock":
        _osascript('tell application "System Events" to keystroke "q" using {control down, command down}')
        return "已锁屏"
    if action == "sleep":
        subprocess.run(["pmset", "sleepnow"])
        return "电脑准备休眠"
    return "为安全起见，关机/重启请手动操作"


def read_screen() -> list:
    """截取当前屏幕，把图片回传给大脑，让它"看"屏幕并总结/回答。

    返回的是一个内容块列表（含 image），会作为工具结果直接喂给 Claude 视觉。
    """
    if config.IS_WINDOWS:
        import tempfile
        from PIL import ImageGrab
        path = os.path.join(tempfile.gettempdir(), "jarvis_screen.jpg")
        try:
            img = ImageGrab.grab()
            img.thumbnail((1568, 1568))            # 长边 1568px，省 token
            img.convert("RGB").save(path, "JPEG", quality=80)
        except Exception as e:  # noqa: BLE001
            return f"截屏失败：{e}"
    else:
        path = "/tmp/jarvis_screen.jpg"
        # -x 静音截图，-m 只截主显示器，截成 jpg
        subprocess.run(["screencapture", "-x", "-m", "-t", "jpg", path],
                       capture_output=True)
        if not os.path.exists(path):
            return "截屏失败，请检查「屏幕录制」权限。"
        # 缩放到长边 1568px（Claude 视觉的最佳尺寸，省 token）
        subprocess.run(["sips", "-Z", "1568", path], capture_output=True)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return [
        {"type": "text", "text": "这是用户当前的屏幕截图，请据此回答："},
        {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": data}},
    ]


def send_wechat(contact: str, message: str) -> str:
    """给微信联系人发消息。

    原理：用 UI 自动化操作 Mac 版微信——激活窗口 → Cmd+F 搜索联系人 →
    回车打开会话 → 粘贴消息 → 回车发送。中文用剪贴板粘贴以保证可靠。
    需要"辅助功能"权限，且微信已登录。Windows 上走 winops.send_wechat。
    """
    if config.IS_WINDOWS:
        return winops.send_wechat(contact, message)
    saved = _get_clipboard()                       # 备份剪贴板，事后还原
    try:
        _osascript('tell application "WeChat" to activate')
        time.sleep(0.8)
        _key('keystroke "f" using command down')   # 打开搜索
        time.sleep(0.5)
        _set_clipboard(contact)
        _key('keystroke "v" using command down')   # 粘贴联系人名
        time.sleep(1.0)
        _key("key code 36")                        # 回车，打开最匹配的会话
        time.sleep(0.8)
        _set_clipboard(message)
        _key('keystroke "v" using command down')   # 粘贴消息
        time.sleep(0.4)
        _key("key code 36")                        # 回车，发送
        time.sleep(0.3)
        return f"已尝试给「{contact}」发送：{message}"
    finally:
        time.sleep(0.3)
        _set_clipboard(saved)


def remember(fact: str, category: str = "long_term") -> str:
    """写入核心、长期或项目记忆。"""
    return memory.add(fact, category)


def list_memories(category: str | None = None) -> str:
    """查看记忆，可按分类筛选。"""
    allowed, invalid = config.cloud_memory_policy()
    if invalid:
        return config.context_disclosure()
    if category and category not in allowed:
        return f"记忆分类 {category} 未授权发送给当前云模型"
    items = memory.load(category)
    if category is None:
        items = [item for item in items if item["category"] in allowed]
    if not items:
        return "没有找到记忆"
    return "\n".join(
        f"#{item['id']} [{item['category']}] {item['fact']}" for item in items
    )


def update_memory(memory_id: int, fact: str,
                  category: str | None = None) -> str:
    """按编号修改记忆。"""
    return memory.update(memory_id, fact, category)


def export_memories(path: str = "memory-export.json",
                    category: str | None = None) -> str:
    """把记忆导出为工作区内的 JSON 文件。"""
    target = _workspace_path(path)
    if target is None:
        return f"拒绝导出：只允许写入工作区 {config.WORKSPACE}"
    return memory.export_json(Path(target), category)


def clear_memories(category: str | None = None) -> str:
    """清空全部或指定分类的记忆。"""
    return memory.clear(category)


def forget(keyword: str) -> str:
    """删除含某关键词的长期记忆。"""
    return memory.forget(keyword)


# --- 多步任务：文件 / 命令行 ------------------------------------------

def read_text_file(path: str) -> str:
    """读取工作区内的小型 UTF-8 文本；内容会返回给模型。"""
    return diffs.read_text(path)


def propose_file_change(path: str, content: str) -> str:
    """创建待审文件提案，不修改目标文件。"""
    return diffs.propose(path, content)

def list_directory(path: str = "~") -> str:
    """列出目录内容（给多步文件任务用）。"""
    p = _workspace_path(path)
    if p is None:
        return f"拒绝访问：只允许工作区 {config.WORKSPACE}"
    if not os.path.isdir(p):
        return f"目录不存在：{path}"
    entries = []
    for name in sorted(os.listdir(p))[:200]:
        full = os.path.join(p, name)
        entries.append(f"{'📁' if os.path.isdir(full) else '📄'} {name}")
    return f"{p} 共 {len(entries)} 项：\n" + "\n".join(entries)


def run_shell(command: str) -> str:
    """执行一条系统命令并返回输出（多步任务的万能手段）。

    macOS 走 shell(zsh)，Windows 走 PowerShell。
    危险/批量/删除类操作应先经用户确认；删除请用 move_to_trash 而非 rm/del。
    """
    if not config.ENABLE_SHELL:
        return "run_shell 默认关闭；确认风险后设置 JARVIS_ENABLE_SHELL=1 才能启用"
    try:
        if config.IS_WINDOWS:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 command],
                capture_output=True, timeout=60, creationflags=0x08000000)
            out = (r.stdout.decode("utf-8", "ignore")
                   + r.stderr.decode("utf-8", "ignore")).strip()
        else:
            r = subprocess.run(command, shell=True, capture_output=True,
                               text=True, timeout=60)
            out = (r.stdout + r.stderr).strip()
        if len(out) > 2000:
            out = out[:2000] + "\n…(输出已截断)"
        return out or f"（命令已执行，无输出，退出码 {r.returncode}）"
    except subprocess.TimeoutExpired:
        return "命令超时（超过 60 秒）已中止"
    except Exception as e:  # noqa: BLE001
        return f"执行出错：{e}"


def move_to_trash(path: str) -> str:
    """把文件/文件夹移到废纸篓/回收站（比 rm/del 安全，可恢复）。"""
    p = _workspace_path(path)
    if p is None:
        return f"拒绝访问：只允许工作区 {config.WORKSPACE}"
    if not os.path.exists(p):
        return f"路径不存在：{path}"
    if config.IS_WINDOWS:
        err = winops.recycle(p)
        return f"已把「{os.path.basename(p)}」移到回收站" if not err \
            else f"移动失败：{err}"
    posix = p.replace('"', '\\"')
    out = _osascript(
        f'tell application "Finder" to delete (POSIX file "{posix}" as alias)'
    )
    return f"已把「{os.path.basename(p)}」移到废纸篓" if "error" not in out.lower() \
        else f"移动失败：{out}"


# --- 给 Claude 看的工具说明 -------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "open_app",
        "description": "打开一个应用程序，例如 微信、浏览器、备忘录/记事本、计算器、音乐。",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "应用名称"}},
            "required": ["name"],
        },
    },
    {
        "name": "open_url",
        "description": "在默认浏览器打开一个网址。",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "web_search",
        "description": "在浏览器里搜索某个关键词。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "set_volume",
        "description": "设置系统音量，范围 0 到 100。",
        "input_schema": {
            "type": "object",
            "properties": {"level": {"type": "integer"}},
            "required": ["level"],
        },
    },
    {
        "name": "get_time",
        "description": "获取当前的日期和时间。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_weather",
        "description": "查询某个城市的天气，城市名用拼音或英文，例如 Beijing、Shanghai。",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "control_music",
        "description": "控制 Music 应用：play 播放, pause 暂停, playpause 切换, next 下一首, previous 上一首。",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "playpause", "next", "previous"],
                }
            },
            "required": ["action"],
        },
    },
    {
        "name": "set_timer",
        "description": "设置一个倒计时提醒，到点用语音提醒用户。",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "倒计时秒数"},
                "message": {"type": "string", "description": "到点要说的提醒内容"},
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "截取当前屏幕并保存到桌面（只是存文件，不分析内容）。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_screen",
        "description": "看用户当前的屏幕内容。当用户问『屏幕上是什么』『帮我总结一下这个页面/这段』『这是什么意思』等需要看屏幕才能回答的问题时调用，调用后你会收到屏幕截图。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_wechat",
        "description": "用微信给某个联系人发送一条文字消息（需微信已登录并能被唤起到前台）。调用前务必已向用户口头确认『发给谁、发什么内容』。",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact": {"type": "string", "description": "联系人备注名或昵称"},
                "message": {"type": "string", "description": "要发送的消息内容"},
            },
            "required": ["contact", "message"],
        },
    },
    {
        "name": "system_power",
        "description": "电源/锁屏操作：lock 锁屏, sleep 休眠。关机重启不支持。",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["lock", "sleep"]}},
            "required": ["action"],
        },
    },
    {
        "name": "remember",
        "description": "写入跨重启记忆。core 用于身份、硬性偏好和长期规则；project 用于特定项目；其他一般信息用 long_term。",
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "要记住的一句话事实"},
                "category": {"type": "string", "enum": ["core", "long_term", "project"]},
            },
            "required": ["fact"],
        },
    },
    {
        "name": "list_memories",
        "description": "查看已保存的记忆及编号，可按 core、long_term 或 project 分类筛选。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["core", "long_term", "project"]},
            },
        },
    },
    {
        "name": "update_memory",
        "description": "按编号修改一条记忆的内容或分类。",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer"},
                "fact": {"type": "string"},
                "category": {"type": "string", "enum": ["core", "long_term", "project"]},
            },
            "required": ["memory_id", "fact"],
        },
    },
    {
        "name": "export_memories",
        "description": "把全部或指定分类的记忆导出为工作区内的 JSON 文件。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "工作区内的相对路径"},
                "category": {"type": "string", "enum": ["core", "long_term", "project"]},
            },
        },
    },
    {
        "name": "clear_memories",
        "description": "清空全部或指定分类的记忆。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["core", "long_term", "project"]},
            },
        },
    },
    {
        "name": "forget",
        "description": "删除长期记忆中含某关键词的条目。",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "read_text_file",
        "description": "读取 workspace 内不超过 256 KiB 的 UTF-8 文本。文件内容会发送给当前模型，必须先获得用户确认。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "propose_file_change",
        "description": "为 workspace 内的 UTF-8 文本创建待审修改或新文件提案；不会直接改变目标文件。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string", "description": "完整的新文件内容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "列出某个目录下的文件和子文件夹。做多步文件任务时先用它了解现状。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录路径，支持 ~，如 ~/Downloads"}},
            "required": ["path"],
        },
    },
    {
        "name": "run_shell",
        "description": "执行一条系统命令并返回输出（macOS 为 shell/zsh，Windows 为 PowerShell），用于多步任务（建文件夹、批量移动/重命名、查询等）。注意：删除文件请改用 move_to_trash；批量或有风险的操作请先向用户口头确认再执行。",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "move_to_trash",
        "description": "把指定文件或文件夹移到废纸篓（可恢复，比 rm 安全）。删除操作一律用它。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]

DISPATCH = {
    "open_app": open_app,
    "open_url": open_url,
    "web_search": web_search,
    "set_volume": set_volume,
    "get_time": get_time,
    "get_weather": get_weather,
    "control_music": control_music,
    "set_timer": set_timer,
    "take_screenshot": take_screenshot,
    "read_screen": read_screen,
    "send_wechat": send_wechat,
    "system_power": system_power,
    "remember": remember,
    "list_memories": list_memories,
    "update_memory": update_memory,
    "export_memories": export_memories,
    "clear_memories": clear_memories,
    "forget": forget,
    "read_text_file": read_text_file,
    "propose_file_change": propose_file_change,
    "list_directory": list_directory,
    "run_shell": run_shell,
    "move_to_trash": move_to_trash,
}

TOOL_RISKS = {
    "send_wechat": "high",
    "system_power": "high",
    "forget": "high",
    "update_memory": "high",
    "export_memories": "high",
    "clear_memories": "high",
    "read_text_file": "high",
    "run_shell": "high",
    "move_to_trash": "high",
}

_AUDIT_LOCK = threading.Lock()


def audit(name: str, args: dict, status: str, risk: str | None = None) -> None:
    """记录操作结果，不保存命令、消息正文或其他参数值。"""
    raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    event = {
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "tool": name,
        "risk": risk or TOOL_RISKS.get(name, "low"),
        "status": status,
        "arg_keys": sorted(args),
        "args_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }
    try:
        config.AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK, config.AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def requires_confirmation(name: str) -> bool:
    return (TOOL_RISKS.get(name) == "high"
            and config.ENABLE_DANGEROUS_TOOLS)


def _workspace_path(path: str) -> str | None:
    """把相对路径限定到工作区；越界返回 None。"""
    candidate = config.workspace_path(path)
    return str(candidate) if candidate else None


def tool_schemas() -> list[dict]:
    """默认不把高风险工具交给模型。"""
    memory_allowed, _ = config.cloud_memory_policy()
    return [schema for schema in TOOL_SCHEMAS
            if (config.ENABLE_DANGEROUS_TOOLS
                or TOOL_RISKS.get(schema["name"], "low") != "high")
            and (schema["name"] != "list_memories" or memory_allowed)]


def run(name: str, args: dict, *, confirmed: bool = False) -> str:
    """执行某个工具，返回结果文本。"""
    if TOOL_RISKS.get(name) == "high" and not config.ENABLE_DANGEROUS_TOOLS:
        audit(name, args, "blocked")
        return f"工具 {name} 默认关闭；需要用户显式启用危险工具"
    if requires_confirmation(name) and not confirmed:
        audit(name, args, "confirmation_required")
        return (f"高风险操作 {name} 尚未执行。请向用户说明操作内容，"
                "并等待用户在下一轮明确回复“确认执行”或“取消”。")
    fn = DISPATCH.get(name)
    if not fn:
        audit(name, args, "unknown")
        return f"未知工具：{name}"
    try:
        result = fn(**args)
        audit(name, args, "executed")
        return result
    except Exception as e:  # noqa: BLE001
        audit(name, args, "error")
        return f"执行 {name} 出错：{e}"
