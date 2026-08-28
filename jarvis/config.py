"""配置与密钥加载。

API Key 查找顺序：
    1. 环境变量 JARVIS_API_KEY（兼容 ANTHROPIC_API_KEY）
    2. 项目根目录下的 api_key.txt 文件（只放一行 key）
    3. ~/.jarvis_key 文件
"""

from __future__ import annotations

import os
import sys
import json
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# ---- 平台判定（贾维斯支持 macOS 与 Windows）---------------------------
IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
OS_NAME = "Windows" if IS_WINDOWS else "macOS" if IS_MACOS else "Linux"


def _enabled(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ---- 安全边界 ----------------------------------------------------------
# 默认只允许项目 workspace 内的文件操作。危险工具和 MCP 必须显式开启。
WORKSPACE = Path(os.environ.get("JARVIS_WORKSPACE", _ROOT / "workspace")).resolve()
ENABLE_DANGEROUS_TOOLS = _enabled("JARVIS_ENABLE_DANGEROUS_TOOLS")
ENABLE_SHELL = _enabled("JARVIS_ENABLE_SHELL")
ENABLE_MCP = _enabled("JARVIS_ENABLE_MCP")
AUDIT_LOG = Path(os.environ.get("JARVIS_AUDIT_LOG", _ROOT / "audit.jsonl")).resolve()


def workspace_path(path: str) -> Path | None:
    """把路径限定在工作区内；越界返回 None。"""
    raw = os.path.expanduser(path)
    candidate = (WORKSPACE / raw).resolve() if not os.path.isabs(raw) \
        else Path(raw).resolve()
    try:
        if os.path.commonpath((str(WORKSPACE), str(candidate))) != str(WORKSPACE):
            return None
    except ValueError:
        return None
    return candidate

# ---- 可调参数 ----------------------------------------------------------


def _read_first_line(filename: str) -> str:
    """读项目根某文件第一行非空、非 # 注释的内容；没有就返回空串。"""
    p = _ROOT / filename
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return ""


# 大模型名（贾维斯的大脑）。优先级：环境变量 JARVIS_MODEL > model.txt > 默认。
# 走中转站时这里填中转站支持的模型名，如 deepseek-v4-flash / gpt-4o / claude-...
_MODEL_VALUE = (os.environ.get("JARVIS_MODEL")
                or _read_first_line("model.txt")).strip()
MODEL = _MODEL_VALUE or "deepseek-v4-flash"
MAX_TOKENS = max(64, int(os.environ.get("JARVIS_MAX_TOKENS", "1024")))
HISTORY_TURNS = max(1, int(os.environ.get("JARVIS_HISTORY_TURNS", "12")))

# 中转站（OpenAI 兼容网关）地址。优先级：环境变量 JARVIS_BASE_URL > base_url.txt。
# 例：https://你的中转站/v1   （贾维斯会自动在后面接 /chat/completions）
LLM_BASE_URL = (os.environ.get("JARVIS_BASE_URL")
                or _read_first_line("base_url.txt")).strip()
LOCAL_PROVIDER = False
MEMORY_CATEGORIES = ("core", "long_term", "project")


def is_local_model() -> bool:
    """仅把本机回环接口视作本地模型，局域网地址仍按外部发送处理。"""
    host = urllib.parse.urlparse(LLM_BASE_URL).hostname
    return LOCAL_PROVIDER or host in {"127.0.0.1", "localhost", "::1"}


def is_deepseek_api() -> bool:
    """仅为 DeepSeek 官方端点发送其专用请求参数。"""
    return urllib.parse.urlparse(LLM_BASE_URL).hostname == "api.deepseek.com"


def cloud_memory_policy() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """返回允许发给当前模型的记忆分类和无效配置项。"""
    if is_local_model():
        return MEMORY_CATEGORIES, ()
    raw = os.environ.get("JARVIS_CLOUD_MEMORY", "none").strip().lower()
    if raw in {"", "none"}:
        return (), ()
    if raw == "all":
        return MEMORY_CATEGORIES, ()
    requested = {item.strip() for item in raw.split(",") if item.strip()}
    invalid = tuple(sorted(requested - set(MEMORY_CATEGORIES)))
    allowed = tuple(item for item in MEMORY_CATEGORIES if item in requested)
    return allowed, invalid


def context_disclosure() -> str:
    """生成启动时展示的模型上下文发送说明。"""
    categories, invalid = cloud_memory_policy()
    if invalid:
        return ("云记忆配置无效：" + ", ".join(invalid)
                + "（仅支持 none/all/core/long_term/project）")
    if is_local_model():
        return "本地模型：当前对话、工具结果和全部持久记忆只发往本机接口"
    if not categories:
        return "云模型：发送当前对话和调用产生的工具结果；不发送持久记忆"
    return ("云模型：发送当前对话、调用产生的工具结果及记忆分类："
            + ", ".join(categories))


def auto_configure_ollama(timeout: float = 0.6) -> bool:
    """没有显式模型地址时探测本机 Ollama；成功则更新当前运行配置。"""
    global LLM_BASE_URL, MODEL, LOCAL_PROVIDER
    if LLM_BASE_URL:
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags",
                                    timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", [])
                 if m.get("name")]
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not names:
        return False
    LLM_BASE_URL = "http://127.0.0.1:11434/v1"
    if not _MODEL_VALUE or MODEL not in names:
        MODEL = names[0]
    LOCAL_PROVIDER = True
    return True


def llm_endpoint() -> str:
    """拼出 chat/completions 端点。base_url.txt 填到 /v1 即可。"""
    base = LLM_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"

# Whisper 语音识别模型大小：tiny / base / small / medium / large-v3
# small 平衡；要更准用 medium（中文识别明显更好，但更慢、首次下载更大）；机器弱可改 base
WHISPER_MODEL = os.environ.get("JARVIS_WHISPER", "small")
WHISPER_COMPUTE = "int8"          # CPU 上用 int8 最快
ASR_LANGUAGE = "zh"

# ---- 识别精度 / 速度 旋钮（越大越准越慢）------------------------------
# beam 搜索宽度：1=贪心最快(精度一般)；3 折中；5 最准(约 +350ms)。环境变量 JARVIS_ASR_BEAM 可调。
ASR_BEAM = int(os.environ.get("JARVIS_ASR_BEAM", "5"))
# 是否再做一遍 VAD 静音过滤：开=更少把噪音/静音听成幻觉文字，略慢。
ASR_VAD = os.environ.get("JARVIS_ASR_VAD", "1") not in ("0", "false", "False")
# 可选的识别提示；默认留空，避免静音时复述提示内容产生幻觉。
ASR_INITIAL_PROMPT = os.environ.get("JARVIS_ASR_PROMPT", "")

# ---- 讯飞云端识别（语音听写 IAT）-------------------------------------
# 三个密钥：环境变量优先；否则读项目根 xfyun.txt（三行：APPID/APIKey/APISecret）。


def _load_xfyun() -> tuple[str, str, str]:
    appid = os.environ.get("JARVIS_XFYUN_APPID", "").strip()
    apikey = os.environ.get("JARVIS_XFYUN_APIKEY", "").strip()
    secret = os.environ.get("JARVIS_XFYUN_APISECRET", "").strip()
    if not (appid and apikey and secret):
        p = _ROOT / "xfyun.txt"
        if p.exists():
            vals = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]
            if len(vals) >= 3:
                appid = appid or vals[0]
                apikey = apikey or vals[1]
                secret = secret or vals[2]
    return appid, apikey, secret


XFYUN_APP_ID, XFYUN_API_KEY, XFYUN_API_SECRET = _load_xfyun()

# 识别后端：local=本地 whisper；xfyun=讯飞云端（更准）。
# 默认：凑齐讯飞三个密钥就用 xfyun，否则 local。环境变量 JARVIS_ASR_BACKEND 可强制覆盖。
ASR_BACKEND = (os.environ.get("JARVIS_ASR_BACKEND")
               or ("xfyun" if (XFYUN_APP_ID and XFYUN_API_KEY
                               and XFYUN_API_SECRET) else "local")).strip()

# TTS 后端：
#   gptsovits = 调用本地 GPT-SoVITS API，用参考音色克隆说话（推荐，你已有此项目）
#   clone     = 用内置 XTTS 克隆音服务(voice_clone/serve.py)
#   say       = 系统自带嗓音（macOS 的 say / Windows 的 SAPI 语音合成）
# 任何克隆后端连不上时都会自动回退到系统嗓音。
TTS_BACKEND = os.environ.get("JARVIS_TTS", "gptsovits")
VOICE_SERVER = os.environ.get("JARVIS_VOICE_SERVER", "http://127.0.0.1:5111")
OUTPUT_DEVICE = os.environ.get("JARVIS_OUTPUT_DEVICE", "").strip()

# ---- GPT-SoVITS 后端参数 ----
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GPTSOVITS_URL = os.environ.get("GPTSOVITS_URL", "http://127.0.0.1:9880")
# 参考音色 wav（决定贾维斯的嗓音）+ 它对应的文字
GPTSOVITS_REF = os.environ.get(
    "GPTSOVITS_REF", os.path.join(_ROOT_DIR, "jarvis_ref.wav"))
GPTSOVITS_PROMPT = os.environ.get(
    "GPTSOVITS_PROMPT",
    "在一段时间中，让我告诉你一个故事，这里有一个很有趣的东西。")
GPTSOVITS_TEXT_LANG = os.environ.get("GPTSOVITS_TEXT_LANG", "zh")
GPTSOVITS_PROMPT_LANG = os.environ.get("GPTSOVITS_PROMPT_LANG", "zh")

# say 后端的声音（`say -v '?'` 可查看全部）。
# 默认中文男声 Eddy；想换成婷婷(女声)设 JARVIS_VOICE=Tingting。
TTS_VOICE = os.environ.get("JARVIS_VOICE", "Eddy")
TTS_RATE = int(os.environ.get("JARVIS_RATE", "190"))   # 语速，字/分钟

# 唤醒词（及 Whisper 常见的同音误写变体，做模糊匹配用）
WAKE_WORDS = [
    "贾维斯", "贾维斯", "杰维斯", "佳维斯", "嘉维斯", "假维斯",
    "贾威斯", "加维斯", "甲维斯", "jarvis",
]

# 唤醒后保持「清醒」可继续对话的时长（秒）；超时无话则回到待机
ACTIVE_TIMEOUT = 25

# ---- 防误唤醒（噪音/电视声）-------------------------------------------
# 待机时，只有同时满足下面两个置信度条件的识别结果才会去判断唤醒词，
# 借此过滤掉 Whisper 对噪音/背景人声产生的「幻听」。
WAKE_MAX_NO_SPEECH = float(os.environ.get("JARVIS_WAKE_MAX_NO_SPEECH", "0.5"))
WAKE_MIN_LOGPROB = float(os.environ.get("JARVIS_WAKE_MIN_LOGPROB", "-1.0"))
WAKE_MIN_LEN = 3            # 唤醒句太短(<3字)多半是误识别，丢弃
WAKE_SIM = float(os.environ.get("JARVIS_WAKE_SIM", "0.8"))

# ---- 音频参数 ----------------------------------------------------------

SAMPLE_RATE = 16000               # Whisper 要求 16k
FRAME_MS = 30                     # 每帧时长
AUDIO_BACKEND = os.environ.get("JARVIS_AUDIO_BACKEND", "sounddevice").strip().lower()
MIC_MIN_THRESHOLD = max(1.0, float(os.environ.get("JARVIS_MIC_THRESHOLD", "400")))
SILENCE_TAIL = max(0.2, float(os.environ.get("JARVIS_SILENCE_TAIL", "0.5")))
                                  # 句尾静音多久判定说完；USB 音频断流可适当调高
MIN_SPEECH = 0.3                  # 太短的声音(<0.3s)忽略，多半是噪音
MAX_SEGMENT = 15                  # 单段录音上限（秒）

# ---- 密钥 --------------------------------------------------------------


def load_api_key() -> str | None:
    """中转站（或任意 LLM 后端）的 API Key。
    优先级：环境变量 JARVIS_API_KEY > ANTHROPIC_API_KEY > api_key.txt > ~/.jarvis_key。
    走中转站时，把中转站的 key 填进 api_key.txt 即可。"""
    for var in ("JARVIS_API_KEY", "ANTHROPIC_API_KEY"):
        key = os.environ.get(var)
        if key and key.strip():
            return key.strip()
    for path in (_ROOT / "api_key.txt", Path.home() / ".jarvis_key"):
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    return None
