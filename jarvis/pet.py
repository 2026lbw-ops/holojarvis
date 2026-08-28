"""桌面贾维斯——钢铁侠 JARVIS 风格的全息控制台面板（电影深青版）。

灵感来自 Rainmeter「JARVIS Display System」皮肤，宽版双栏布局：
  左栏：时钟/日期、中央弧反应堆、状态、音频波形
  右栏：天气、系统遥测(磁盘/电量/CPU/运行时长)、笔记待办、转写台账
深青电影色调 + 扫描线/网格 CRT 背景 + 缓慢扫掠高光线。

特性：
  - 无边框、始终置顶、可鼠标拖动（拖面板任意处）
  - 中央弧反应堆随语音状态变色/动画：
      待机(青) / 聆听(青绿) / 思考(琥珀+旋转) / 说话(亮青脉动)
  - 音频波形随状态起伏（说话/聆听时活跃，待机时平缓）
  - 笔记栏读取项目根 notes.txt（每行一条，自动刷新）；无则读 memory.json
  - 实时系统遥测；底部转写台账滚动显示「你说的话 / 贾维斯回答」
  - 点一下反应堆即可开始说话（无需喊唤醒词）；双击或 Esc 关闭

GUI 跑在主线程，语音助手跑在后台线程，通过线程安全队列通信。
对外接口与旧版一致：set_state / log / heard / reply / poll_talk / run。
"""

from __future__ import annotations

import glob
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import config

if config.IS_WINDOWS:
    from . import winops


def _fix_tcltk_env() -> None:
    """uv/独立版 Python 自带 tkinter 但常找不到 Tcl/Tk 数据文件，
    导入 tkinter 前把 TCL_LIBRARY/TK_LIBRARY 指到 base_prefix/lib 下。"""
    specs = [("TCL_LIBRARY", "tcl*", "init.tcl"),
             ("TK_LIBRARY", "tk*", "tk.tcl")]
    for var, pattern, marker in specs:
        if os.environ.get(var):
            continue
        for prefix in (sys.base_prefix, sys.prefix):
            for d in sorted(glob.glob(os.path.join(prefix, "lib", pattern)),
                            reverse=True):
                if os.path.exists(os.path.join(d, marker)):
                    os.environ[var] = d
                    break
            if os.environ.get(var):
                break


_fix_tcltk_env()

import tkinter as tk  # noqa: E402

from PIL import Image, ImageDraw, ImageFont, ImageTk  # noqa: E402


def _enable_windows_dpi() -> None:
    """让 Tk 按显示器原生像素渲染，避免 Windows 再次放大而发糊。"""
    if not config.IS_WINDOWS:
        return
    try:
        import ctypes
        if ctypes.windll.shcore.SetProcessDpiAwareness(2):
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


_enable_windows_dpi()

# ---- 面板尺寸（逻辑像素）+ 超采样倍率 -------------------------------
W, H = 1206, 694
S = 2                           # 内部 2x 渲染再缩小，边缘更顺滑
ROOT = Path(__file__).resolve().parent.parent

# 参考图宽屏布局
LX = 72                         # 左侧主 HUD 边界
RX = 900                        # 右侧 notes / reader 区
RXE = W - 74                    # 右侧 HUD 边界
CXL, CYL = 620, 320             # 中央弧反应堆中心
REACTOR_R = 84

# 深青电影色调
TEAL = (40, 188, 205)           # 主色
TEAL_HI = (130, 240, 248)       # 高亮
TEAL_DIM = (24, 92, 104)        # 暗
GRID = (32, 120, 130)           # 网格线
INK = (4, 9, 11)                # 面板底色

STATE_COLOR = {
    "idle": (40, 190, 208),
    "listening": (52, 220, 158),
    "thinking": (46, 225, 222),
    "speaking": (118, 236, 248),
}
STATE_LABEL = {
    "idle": "STANDBY", "listening": "LISTENING",
    "thinking": "PROCESSING", "speaking": "SPEAKING",
}

AVATAR_CX, AVATAR_CY = 948, 346


def _motion_boost(state: str) -> float:
    return {
        "idle": 1.0,
        "listening": 1.8,
        "thinking": 2.7,
        "speaking": 2.2,
    }.get(state, 1.0)


def _runtime_orbit_angles(phase: float, state: str) -> tuple[float, float, float]:
    """Angles for the always-on HUD orbit rings.

    The rings never stop, but active states make the motion visibly more alive.
    """
    boost = _motion_boost(state)
    return (
        phase * 12.0 * boost,
        115.0 - phase * 7.0 * boost,
        248.0 + phase * 4.4 * boost,
    )


def _avatar_eye_alpha(state: str, phase: float) -> int:
    if state == "thinking":
        wave = 0.5 + 0.5 * math.sin(phase * 2.0 - math.pi / 2)
        return int(86 + wave * 152)
    if state == "speaking":
        return 118
    if state == "listening":
        return 92
    return 38

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
_WINDIR = os.environ.get("WINDIR", r"C:\Windows")
_MONO_PATHS = (
    # macOS
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    # Windows
    os.path.join(_WINDIR, "Fonts", "consola.ttf"),
    os.path.join(_WINDIR, "Fonts", "cour.ttf"),
)
_HAN_PATHS = (
    # macOS
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    # Windows（微软雅黑 / 黑体）
    os.path.join(_WINDIR, "Fonts", "msyh.ttc"),
    os.path.join(_WINDIR, "Fonts", "msyh.ttf"),
    os.path.join(_WINDIR, "Fonts", "simhei.ttf"),
)


def _font(paths: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    key = (paths[0], size)
    if key not in _FONT_CACHE:
        for p in paths:
            if os.path.exists(p):
                _FONT_CACHE[key] = ImageFont.truetype(p, size)
                break
        else:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def _mono(size: int) -> ImageFont.FreeTypeFont:
    return _font(_MONO_PATHS, size * S)


def _han(size: int) -> ImageFont.FreeTypeFont:
    return _font(_HAN_PATHS, size * S)


# ---- 系统遥测 / 天气 / 笔记：全部标准库，带节流缓存 -----------------

class Telemetry:
    def __init__(self) -> None:
        self._boot = self._read_boottime()
        self._ncpu = os.cpu_count() or 8
        self._cache: dict = {"batt": (None, False), "disk": (0, 0)}
        self._next = {"batt": 0.0, "disk": 0.0}
        self._cpu = winops.CpuSampler() if config.IS_WINDOWS else None
        self._disk_root = ((os.environ.get("SystemDrive") or "C:") + "\\"
                           if config.IS_WINDOWS else "/")

    @staticmethod
    def _read_boottime() -> float:
        if config.IS_WINDOWS:
            try:
                return winops.boot_epoch()
            except Exception:  # noqa: BLE001
                return time.time()
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "kern.boottime"], text=True)
            return float(out.split("sec =")[1].split(",")[0].strip())
        except Exception:  # noqa: BLE001
            return time.time()

    def disk(self) -> tuple[float, float]:
        if time.time() >= self._next["disk"]:
            du = shutil.disk_usage(self._disk_root)
            self._cache["disk"] = (du.total / 1e9, du.free / 1e9)
            self._next["disk"] = time.time() + 10
        return self._cache["disk"]

    def battery(self) -> tuple[int | None, bool]:
        if time.time() >= self._next["batt"]:
            pct, charging = None, False
            if config.IS_WINDOWS:
                try:
                    pct, charging = winops.battery()
                except Exception:  # noqa: BLE001
                    pass
            else:
                try:
                    out = subprocess.check_output(["pmset", "-g", "batt"],
                                                  text=True)
                    line = out.strip().splitlines()[-1]
                    for tok in line.replace(";", " ").split():
                        if tok.endswith("%"):
                            pct = int(tok[:-1])
                            break
                    charging = ("charging" in line) or ("charged" in line)
                except Exception:  # noqa: BLE001
                    pass
            self._cache["batt"] = (pct, charging)
            self._next["batt"] = time.time() + 5
        return self._cache["batt"]

    def uptime(self) -> str:
        secs = max(0, int(time.time() - self._boot))
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        return f"{d}D {h:02d}H {m:02d}M" if d else f"{h:02d}H {m:02d}M"

    def load(self) -> tuple[float, float]:
        if self._cpu is not None:                  # Windows：用 CPU 占用率近似
            frac = self._cpu.percent()
            return frac * self._ncpu, frac
        try:
            la = os.getloadavg()[0]
        except (OSError, AttributeError):
            la = 0.0
        return la, min(1.0, la / self._ncpu)


class Weather:
    def __init__(self) -> None:
        self.text: str | None = None
        self.temp: str | None = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                req = urllib.request.Request(
                    "https://wttr.in/?format=%t|%C",
                    headers={"User-Agent": "curl/8"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    raw = r.read().decode("utf-8").strip()
                if "|" in raw and "Unknown" not in raw:
                    t, c = raw.split("|", 1)
                    self.temp = t.replace("+", "").strip()
                    self.text = c.strip().upper()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1200)


class Notes:
    """笔记/待办来源：优先 notes.txt（每行一条），否则读 memory.json 的 facts。
    每 5 秒重读一次，编辑文件后面板自动更新。"""

    def __init__(self) -> None:
        self._items: list[str] = []
        self._next = 0.0

    def items(self) -> list[str]:
        if time.time() >= self._next:
            self._items = self._read()
            self._next = time.time() + 5
        return self._items

    @staticmethod
    def _read() -> list[str]:
        nf = ROOT / "notes.txt"
        if nf.exists():
            out = []
            for ln in nf.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    out.append(ln.lstrip("-•* ").strip())
            if out:
                return out
        mj = ROOT / "memory.json"
        if mj.exists():
            try:
                data = json.loads(mj.read_text(encoding="utf-8"))
                return [it.get("fact", "") for it in data if it.get("fact")]
            except (json.JSONDecodeError, OSError):
                pass
        return ["（暂无笔记 · 编辑 notes.txt 或对我说「记住…」）"]


class DesktopPet:
    def __init__(self) -> None:
        self._q: queue.Queue[tuple] = queue.Queue()
        self._state = "idle"
        self._phase = 0.0
        self._lines: list[tuple[str, str]] = []
        self._tele = Telemetry()
        self._weather = Weather()
        self._notes = Notes()
        self._bg: Image.Image | None = None
        self.talk_event = threading.Event()

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        if config.IS_WINDOWS:
            # Windows：把纯黑设为透明色，黑色区域即变透明（面板本身是近黑非纯黑，保留）
            self.root.config(bg="black")
            try:
                self.root.wm_attributes("-transparentcolor", "black")
            except tk.TclError:
                pass
        else:
            for attr in ("-transparent",):
                try:
                    self.root.wm_attributes(attr, True)
                except tk.TclError:
                    pass
            try:
                self.root.config(bg="systemTransparent")
            except tk.TclError:
                self.root.config(bg="black")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self._x, self._y = max(12, sw - W - 32), max(18, (sh - H) // 2)
        self.root.geometry(f"{W}x{H}+{self._x}+{self._y}")

        try:
            self.canvas = tk.Canvas(self.root, width=W, height=H,
                                    bg="systemTransparent",
                                    highlightthickness=0, bd=0)
        except tk.TclError:
            self.canvas = tk.Canvas(self.root, width=W, height=H,
                                    bg="black", highlightthickness=0, bd=0)
        self.canvas.pack()
        self._img_id = self.canvas.create_image(0, 0, anchor="nw")
        self._photo = None

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", lambda e: self.root.destroy())
        self.root.bind("<Escape>", lambda e: self.root.destroy())

        self.root.after(50, self._tick)

    # ---- 线程安全接口（签名与旧版一致）----------------------------
    def set_state(self, state: str) -> None:
        self._q.put(("state", state))

    def log(self, text: str) -> None:
        print(text)

    def heard(self, text: str) -> None:
        self._q.put(("line", ("you", text)))

    def reply(self, text: str) -> None:
        self._q.put(("line", ("jarvis", text)))

    def poll_talk(self) -> bool:
        if self.talk_event.is_set():
            self.talk_event.clear()
            return True
        return False

    def run(self) -> None:
        self.root.mainloop()

    # ---- 鼠标交互 ----------------------------------------------------
    def _press(self, e: tk.Event) -> None:
        self._drag_origin = (e.x_root, e.y_root, self._x, self._y)
        self._moved = False
        self._press_xy = (e.x, e.y)

    def _drag(self, e: tk.Event) -> None:
        ox, oy, wx, wy = self._drag_origin
        dx, dy = e.x_root - ox, e.y_root - oy
        if abs(dx) > 3 or abs(dy) > 3:
            self._moved = True
        self._x, self._y = wx + dx, wy + dy
        self.root.geometry(f"+{self._x}+{self._y}")

    def _release(self, e: tk.Event) -> None:
        if self._moved:
            return
        px, py = self._press_xy
        if (px - CXL) ** 2 + (py - CYL) ** 2 <= (REACTOR_R * 1.4) ** 2:
            self.talk_event.set()
            self._state = "listening"
            self._push_line("sys", "我在听，请说…")

    def _push_line(self, role: str, text: str) -> None:
        self._lines.append((role, text))
        self._lines = self._lines[-6:]

    # ---- 缩放绘图原语（逻辑坐标 → 超采样画布）----------------------
    def _ell(self, d, cx, cy, r, **kw) -> None:
        d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], **kw)

    def _arc(self, d, cx, cy, r, a0, a1, width, fill) -> None:
        d.arc([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S],
              a0, a1, fill=fill, width=max(1, int(width * S)))

    def _line(self, d, x0, y0, x1, y1, width, fill) -> None:
        d.line([x0 * S, y0 * S, x1 * S, y1 * S], fill=fill,
               width=max(1, int(width * S)))

    def _rect(self, d, x0, y0, x1, y1, **kw) -> None:
        d.rectangle([x0 * S, y0 * S, x1 * S, y1 * S], **kw)

    def _poly(self, d, pts, fill, outline=None, width=1) -> None:
        scaled = [(x * S, y * S) for x, y in pts]
        d.polygon(scaled, fill=fill)
        if outline is not None:
            d.line(scaled + [scaled[0]], fill=outline,
                   width=max(1, int(width * S)))

    def _txt(self, d, x, y, text, font, fill, anchor="la") -> None:
        d.text((x * S, y * S), text, font=font, fill=fill, anchor=anchor)

    def _seg_ring(self, d, cx, cy, r, width, color, segs, gap, rot) -> None:
        step = 360 / segs
        for k in range(segs):
            self._arc(d, cx, cy, r, rot + k * step + gap / 2,
                      rot + (k + 1) * step - gap / 2, width, color)

    # ---- 渲染循环 ----------------------------------------------------
    def _tick(self) -> None:
        try:
            while True:
                kind, val = self._q.get_nowait()
                if kind == "state":
                    self._state = val
                elif kind == "line":
                    self._push_line(*val)
        except queue.Empty:
            pass

        self._phase += 0.14
        img = self._render()
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfig(self._img_id, image=self._photo)
        self.root.after(70, self._tick)

    def _render(self) -> Image.Image:
        if self._bg is None:
            self._bg = self._build_bg()
        big = self._bg.copy()
        d = ImageDraw.Draw(big)
        color = STATE_COLOR.get(self._state, STATE_COLOR["idle"])

        self._draw_sweep(d)
        self._draw_avatar(d, color)
        self._draw_static_hud(d, color)
        self._draw_header(d)
        self._draw_reactor(d, color)
        self._draw_waveform(d, color)
        self._draw_stats(d)
        self._draw_notes(d)
        self._draw_transcript(d, color)
        return big.resize((W, H), Image.LANCZOS)

    # ---- 静态背景层（只构建一次，每帧 copy 复用）------------------
    def _build_bg(self) -> Image.Image:
        big = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        self._rect(d, 0, 0, W, H, fill=(0, 0, 0, 255))

        # 极暗绿的右侧光晕，贴近参考图里面甲背后的荧光底色。
        for i in range(42, 0, -1):
            frac = i / 42
            alpha = int(2 * (1 - frac) ** 2)
            d.ellipse([(770 - i * 3) * S, (24 - i * 2) * S,
                       (1200 + i * 2) * S, (706 + i * 2) * S],
                      fill=(10, 54, 24, alpha))

        # 左侧竖栏与小三角标。
        self._line(d, 72, 64, 72, 666, 4, (*TEAL, 230))
        self._line(d, 84, 64, 292, 64, 2, (*TEAL, 220))
        self._line(d, 84, 64, 84, 210, 2, (*TEAL_DIM, 180))
        self._line(d, 58, 488, 302, 488, 2, (*TEAL, 205))
        for y in (78, 100, 122):
            self._poly(d, [(70, y), (76, y + 6), (70, y + 12)],
                       fill=(*TEAL, 230))

        # 中央线路分支，围绕反应堆挂载应用/媒体项目。
        for y, label in (
            (210, "Images"), (232, "Documents"), (286, "Videos"),
            (326, "Music"), (369, "Skinspath"),
        ):
            self._line(d, 448, y, 516, y, 2, (*TEAL, 215))
            self._ell(d, 448, y, 7, fill=(*TEAL, 210))
            self._txt(d, 486, y - 13, label, _mono(10), (*TEAL, 230))
        self._line(d, 718, CYL, 806, CYL, 2, (*TEAL, 190))
        self._line(d, 806, CYL, 806, 292, 2, (*TEAL, 190))
        for i, label in enumerate(
            ("gmail", "wikipedia", "duckmeter", "baconit", "lifehacker",
             "io9", "gizmodo", "kotaku", "twitter", "facebook", "youtube")
        ):
            y = 286 + i * 20
            self._line(d, 742, y, 806, y, 1.5, (*TEAL_DIM, 190))
            self._ell(d, 808, y, 5, fill=(*TEAL, 225))
            self._txt(d, 742, y - 8, label, _mono(9), (*TEAL, 215))

        # 右上 Notes 框与 Reader 区主线。
        self._txt(d, 902, 70, "Notes", _mono(11), (*TEAL, 235))
        self._line(d, 900, 80, 1060, 80, 2, (*TEAL, 225))
        self._line(d, 900, 80, 900, 310, 2, (*TEAL, 225))
        self._line(d, 1060, 80, 1060, 310, 2, (*TEAL, 225))
        self._line(d, 900, 310, 1060, 310, 2, (*TEAL, 225))
        self._line(d, 900, 80, 876, 80, 1, (*TEAL, 180))
        self._line(d, 948, 388, 1124, 388, 2, (*TEAL_DIM, 190))
        self._txt(d, 1024, 374, "Reader", _mono(10), (*TEAL, 230))
        self._txt(d, 1040, 407, "gizmodo", _mono(12), (*TEAL, 240))

        # 底部 JARVIS Display System 微缩清单。
        self._txt(d, 604, 548, "JARVIS DISPLAY SYSTEM", _mono(9), (*TEAL, 210))
        x = 604
        for title, rows in (
            ("LEFT PANEL:", ("TIME", "DATE", "PRIMARY DRIVE", "POWER STATUS", "WASTE STATUS")),
            ("WEATHER BAR:", ("TEMPERATURE", "CLIMATE", "VISUALS", "NOTES")),
            ("CENTRAL INTERFACE:", ("FOLDER LINK", "WEB CONNECTS", "PRIMARY APPS", "RSS FEEDS")),
        ):
            self._txt(d, x, 568, title, _mono(8), (*TEAL, 220))
            for j, row in enumerate(rows):
                self._txt(d, x, 584 + j * 12, f"· {row}", _mono(7), (*TEAL_DIM, 210))
            x += 148
        return big

    def _draw_static_hud(self, d, color) -> None:
        """State-colored scan overlays that should sit above the face but below text."""
        rr, gg, bb = color
        ph = self._phase
        for idx, ang in enumerate(_runtime_orbit_angles(ph + 1.1, self._state)):
            self._arc(d, 332, 606, 55 + idx * 7, ang, ang + 108,
                      7 if idx == 0 else 2, (rr, gg, bb, 125 - idx * 25))
        self._ell(d, 558, 486, 12, outline=(rr, gg, bb, 210),
                  width=max(1, int(2 * S)))
        self._line(d, 558, 426, 558, 474, 2, (rr, gg, bb, 190))
        self._line(d, 558, 498, 558, 528, 2, (rr, gg, bb, 190))

        for i, label in enumerate(
            ("gmail", "wikipedia", "duckmeter", "baconit", "lifehacker",
             "io9", "gizmodo", "kotaku", "twitter", "facebook", "youtube")
        ):
            y = 286 + i * 20
            self._line(d, 742, y, 806, y, 1.5, (*TEAL_DIM, 210))
            self._ell(d, 808, y, 5, fill=(*TEAL, 235))
            self._txt(d, 742, y - 8, label, _mono(9), (*TEAL, 235))

        # 右侧浮层线框必须压在面甲上方。
        self._txt(d, 902, 70, "Notes", _mono(11), (*TEAL, 245))
        self._line(d, 900, 80, 1060, 80, 2, (*TEAL, 235))
        self._line(d, 900, 80, 900, 310, 2, (*TEAL, 235))
        self._line(d, 1060, 80, 1060, 310, 2, (*TEAL, 235))
        self._line(d, 900, 310, 1060, 310, 2, (*TEAL, 235))
        for y in (210, 232, 254, 276):
            self._rect(d, 898, y, 908, y + 14, fill=(*TEAL, 190))
        self._txt(d, 1038, 292, "+", _mono(30), (*TEAL, 240), anchor="ma")
        self._line(d, 948, 388, 1124, 388, 2, (*TEAL_DIM, 220))
        self._line(d, 1040, 398, 1130, 398, 2, (*TEAL, 220))
        self._txt(d, 1024, 374, "Reader", _mono(10), (*TEAL, 235))
        self._txt(d, 1040, 407, "gizmodo", _mono(12), (*TEAL, 245))
        for x in (965, 1004, 1044, 1084):
            self._rect(d, x, 610, x + 12, 622, fill=(*TEAL, 210))

    def _draw_sweep(self, d) -> None:
        """缓慢向下扫掠的高光线，增强 CRT 全息感。"""
        span = H - 24
        y = 12 + (self._phase * 6) % span
        for off, a in ((0, 26), (-2, 14), (2, 14)):
            self._line(d, 12, y + off, W - 12, y + off, 1, (*TEAL, a))

    # ---- 顶栏 --------------------------------------------------------
    def _draw_header(self, d) -> None:
        now = time.localtime()
        cx, cy, r = 174, 150, 74
        ph = self._phase
        self._ell(d, cx, cy, r, outline=(*TEAL, 230), width=max(1, int(3 * S)))
        self._ell(d, cx, cy, r - 10, outline=(*TEAL, 180), width=max(1, int(2 * S)))
        self._arc(d, cx, cy, r - 2, -8 + ph * 18, 84 + ph * 18,
                  12, (*TEAL, 235))
        self._arc(d, cx, cy, r + 8, 112 - ph * 8, 316 - ph * 8,
                  2, (*TEAL, 180))
        month = time.strftime("%B", now).lower()
        self._txt(d, cx, cy - 40, month, _han(18), (*TEAL, 235), anchor="ma")
        self._txt(d, cx, cy + 2, f"{now.tm_mday:02d}", _mono(45),
                  (*TEAL_HI, 255), anchor="ma")
        self._arc(d, cx, cy + 7, 30, 12, 168, 2, (*TEAL, 220))
        self._txt(d, 225, 84, time.strftime("%H:%M", now), _mono(12),
                  (*TEAL_HI, 255))
        self._txt(d, 272, 84, time.strftime("%S", now), _mono(7),
                  (*TEAL, 235))
        self._txt(d, 224, 106, time.strftime("%A", now).lower(), _mono(12),
                  (*TEAL, 235))

        temp = (self._weather.temp or "--°").replace("+", "")
        cond = (self._weather.text or "Atmospheric\nAnalysis").title()
        wx, wy, wr = 462, 132, 58
        self._txt(d, 360, 70, "Fog", _mono(10), (*TEAL, 235))
        self._line(d, 340, 80, 424, 80, 2, (*TEAL, 205))
        self._line(d, 424, 80, 444, 100, 2, (*TEAL, 205))
        self._ell(d, wx, wy, wr, outline=(*TEAL, 225), width=max(1, int(5 * S)))
        self._ell(d, wx, wy, wr - 13, outline=(*TEAL_DIM, 200),
                  width=max(1, int(4 * S)))
        self._arc(d, wx, wy, wr + 7, -72 - ph * 14, 160 - ph * 14,
                  2, (*TEAL, 200))
        self._txt(d, wx, wy + 8, temp[:3], _mono(36), (*TEAL_HI, 255),
                  anchor="ma")
        self._txt(d, 342, 108, "Atmospheric", _mono(9), (*TEAL_DIM, 210))
        self._txt(d, 342, 126, "Analysis", _mono(9), (*TEAL_DIM, 210))
        self._txt(d, wx, wy + wr + 16, cond[:18], _mono(8), (*TEAL_DIM, 190),
                  anchor="ma")

    # ---- 弧反应堆 ----------------------------------------------------
    def _draw_reactor(self, d, color) -> None:
        rr, gg, bb = color
        ph = self._phase
        spin = ph * (60 if self._state == "thinking" else 9)
        if self._state == "speaking":
            pulse = 1.0 + 0.05 * math.sin(ph * 2.4)
        elif self._state == "listening":
            pulse = 1.0 + 0.035 * math.sin(ph * 1.7)
        else:
            pulse = 1.0 + 0.03 * math.sin(ph * 0.9)
        R = REACTOR_R * pulse
        cx, cy = CXL, CYL

        self._draw_runtime_orbits(d, cx, cy, R, color)

        for i in range(16, 0, -1):
            frac = i / 16
            self._ell(d, cx, cy, R * (1.0 + 0.42 * frac),
                      fill=(rr, gg, bb, int(26 * (1 - frac) ** 2)))
        self._seg_ring(d, cx, cy, R * 1.22, 2.5, (rr, gg, bb, 205),
                       segs=54, gap=2.2, rot=-spin * 0.4)
        self._seg_ring(d, cx, cy, R * 1.05, 6, (rr, gg, bb, 150),
                       segs=12, gap=9, rot=spin)
        self._ell(d, cx, cy, R * 0.92, outline=(rr, gg, bb, 220),
                  width=max(1, int(1.5 * S)))

        n = 10
        r_in, r_out = R * 0.42, R * 0.82
        for k in range(n):
            a = math.radians(k * 360 / n - spin * 0.25)
            aw = math.radians(360 / n * 0.36)
            pts = []
            for sign in (-1, 1):
                ang = a + sign * aw
                pts.append(((cx + math.cos(ang) * r_out) * S,
                            (cy + math.sin(ang) * r_out) * S))
            for sign in (1, -1):
                ang = a + sign * aw * 0.55
                pts.append(((cx + math.cos(ang) * r_in) * S,
                            (cy + math.sin(ang) * r_in) * S))
            d.polygon(pts, fill=(rr, gg, bb, 92), outline=(rr, gg, bb, 230))

        self._ell(d, cx, cy, R * 0.4, outline=(rr, gg, bb, 230),
                  width=max(1, int(1.2 * S)))
        core = 10
        for i in range(core, 0, -1):
            frac = i / core
            col = (int(rr + (255 - rr) * (1 - frac) ** 1.4),
                   int(gg + (255 - gg) * (1 - frac) ** 1.4),
                   int(bb + (255 - bb) * (1 - frac) ** 1.4), 255)
            self._ell(d, cx, cy, R * 0.32 * frac, fill=col)

        self._txt(d, cx, cy + R * 1.34, STATE_LABEL.get(self._state, "STANDBY"),
                  _mono(12), (rr, gg, bb, 255), anchor="ma")

    def _draw_runtime_orbits(self, d, cx, cy, r, color) -> None:
        rr, gg, bb = color
        boost = _motion_boost(self._state)
        active = min(1.0, (boost - 1.0) / 1.7)
        for idx, ang in enumerate(_runtime_orbit_angles(self._phase, self._state)):
            ring_r = r * (1.36 + idx * 0.16)
            alpha = int(68 + active * 72 - idx * 13)
            width = 1.2 + idx * 0.35
            self._arc(d, cx, cy, ring_r, ang, ang + 76 - idx * 8,
                      width, (rr, gg, bb, alpha))
            self._arc(d, cx, cy, ring_r, ang + 170, ang + 224 + idx * 9,
                      width, (rr, gg, bb, max(34, alpha - 24)))

            dot_a = math.radians(ang + 76 - idx * 16)
            dot_x = cx + math.cos(dot_a) * ring_r
            dot_y = cy + math.sin(dot_a) * ring_r
            dot_r = 2.1 + idx * 0.45 + active * 0.9
            self._ell(d, dot_x, dot_y, dot_r * 3.1,
                      fill=(rr, gg, bb, int(24 + active * 24)))
            self._ell(d, dot_x, dot_y, dot_r, fill=(rr, gg, bb, 235))

    # ---- 右侧头像 ----------------------------------------------------
    def _draw_avatar(self, d, color) -> None:
        rr, gg, bb = color
        cx, cy = AVATAR_CX, AVATAR_CY
        eye_alpha = _avatar_eye_alpha(self._state, self._phase)
        boost = _motion_boost(self._state)

        # 参考图里的面甲是右半屏的主体：暗绿金属面 + 青色眼缝。
        for idx, ang in enumerate(_runtime_orbit_angles(self._phase + 0.6, self._state)):
            ring_r = 150 + idx * 42
            alpha = int(18 + min(boost, 2.7) * 7 - idx * 4)
            self._arc(d, cx, cy, ring_r, ang + idx * 26, ang + 72 + idx * 18,
                      1.0, (*TEAL, alpha))
            self._arc(d, cx, cy, ring_r, ang + 184, ang + 232,
                      1.0, (*TEAL_DIM, max(14, alpha - 8)))

        helmet_outer = [
            (812, 52), (916, 38), (1036, 44), (1140, 118), (1188, 304),
            (1164, 516), (1080, 666), (930, 690), (794, 586), (740, 392),
            (756, 192),
        ]
        self._poly(d, helmet_outer, fill=(14, 57, 32, 92),
                   outline=(52, 160, 78, 78), width=1.3)

        forehead = [
            (858, 62), (956, 56), (1048, 82), (1010, 220), (914, 198),
            (832, 222), (792, 164),
        ]
        brow = [
            (796, 242), (914, 252), (960, 300), (864, 322), (766, 292),
        ]
        cheek = [
            (808, 340), (926, 332), (1052, 360), (1110, 456), (1054, 584),
            (924, 636), (824, 552), (772, 420),
        ]
        jaw = [
            (924, 402), (1028, 392), (1088, 494), (1022, 628), (932, 656),
            (874, 586), (858, 454),
        ]
        for pts, fill, outline in (
            (forehead, (42, 116, 48, 76), (70, 168, 78, 58)),
            (brow, (18, 76, 50, 92), (52, 144, 74, 62)),
            (cheek, (16, 86, 55, 84), (44, 154, 78, 62)),
            (jaw, (10, 54, 48, 90), (38, 128, 86, 62)),
        ):
            self._poly(d, pts, fill=fill, outline=outline, width=1.0)

        self._line(d, 924, 386, 924, 646, 1, (74, 178, 112, 72))
        self._line(d, 790, 426, 874, 586, 1, (42, 154, 94, 64))
        self._line(d, 1084, 182, 1160, 308, 1, (70, 160, 80, 52))
        self._line(d, 1116, 514, 1032, 628, 1, (52, 150, 90, 68))

        eye_fill_alpha = eye_alpha
        if self._state == "thinking":
            eye_level = max(0.0, min(1.0, (eye_alpha - 86) / 152))
            eye_color = tuple(
                int(TEAL_DIM[i] + (TEAL_HI[i] - TEAL_DIM[i]) * eye_level)
                for i in range(3)
            )
            eye_fill_alpha = int(112 + eye_level * 143)
        else:
            eye_color = tuple(int(c * 0.78) for c in (rr, gg, bb))
        left_eye = [
            (782, 374), (906, 338), (924, 354), (802, 396),
        ]
        right_eye = [
            (936, 340), (1042, 310), (1068, 324), (960, 364),
        ]
        glow = max(30, int(eye_alpha * 0.42))
        for pts in (left_eye, right_eye):
            mx0 = (pts[0][0] + pts[3][0]) / 2
            my0 = (pts[0][1] + pts[3][1]) / 2
            mx1 = (pts[1][0] + pts[2][0]) / 2
            my1 = (pts[1][1] + pts[2][1]) / 2
            self._line(d, mx0, my0, mx1, my1, 12, (*eye_color, int(glow * 0.34)))
            self._line(d, mx0, my0, mx1, my1, 5.5, (*eye_color, int(glow * 0.62)))
            self._poly(d, pts, fill=(*eye_color, eye_fill_alpha),
                       outline=(*TEAL_HI, min(255, eye_fill_alpha + 20)), width=0.8)

        if self._state == "thinking":
            flare = int((eye_alpha - 86) / 152 * 70)
            self._line(d, 790, 396, 910, 354, 4.0,
                       (*TEAL_HI, 82 + flare))
            self._line(d, 944, 364, 1058, 322, 4.0,
                       (*TEAL_HI, 82 + flare))

    # ---- 音频波形 ----------------------------------------------------
    def _draw_waveform(self, d, color) -> None:
        rr, gg, bb = color
        x0, x1 = 114, 352
        midy = 332
        self._line(d, x0, midy, x1, midy, 1, (*TEAL_DIM, 120))

        amp = {"speaking": 18, "listening": 13, "thinking": 6}.get(self._state, 4)
        bars = 42
        step = (x1 - x0) / bars
        ph = self._phase
        for i in range(bars):
            v = (0.45 * math.sin(ph * 3.0 + i * 0.55)
                 + 0.3 * math.sin(ph * 5.3 + i * 0.27)
                 + 0.25 * math.sin(ph * 1.7 + i * 0.9))
            env = 0.55 + 0.45 * math.sin(ph * 2.0 + i * 0.4)  # 语音包络
            h = abs(v) * amp * (env if self._state in ("speaking", "listening")
                                else 1.0) + 1.5
            x = x0 + i * step + step / 2
            peak = abs(v) > 0.7
            col = (*(TEAL_HI if peak else color), 235)
            self._line(d, x, midy - h, x, midy + h, max(1.6, step * 0.42), col)
        self._txt(d, 260, 318, "Player", _mono(8), (*TEAL, 220))
        self._txt(d, 190, 350, "18. Scels of Arraycraft ft. Black Cobra",
                  _mono(7), (*TEAL, 210))

    # ---- 系统遥测 ----------------------------------------------------
    def _draw_stats(self, d) -> None:
        total, free = self._tele.disk()
        used = (total - free) / total if total else 0
        pct, charging = self._tele.battery()
        la, cpu = self._tele.load()
        # 左上存储读数，靠近参考图的 PRIMARY STORAGE 区块。
        self._txt(d, 108, 252, f"Full Capacity: {total:.0f} G", _mono(10),
                  (*TEAL, 235))
        self._txt(d, 108, 292, "PRIMARY STORAGE", _mono(9), (*TEAL_DIM, 220))
        self._txt(d, 108, 312, f"Free Capacity: {free:.0f} G", _mono(10),
                  (*TEAL, 235))
        self._line(d, 108, 320, 220, 320, 2, (*TEAL_DIM, 160))
        self._line(d, 108, 320, 108 + 112 * max(0.04, 1 - used), 320, 2,
                   (*TEAL, 235))

        # 电源圆表。
        px, py, pr = 160, 366, 44
        battery_text = f"{pct}%" if pct is not None else "--"
        self._ell(d, px, py, pr, outline=(*TEAL_DIM, 210), width=max(1, int(3 * S)))
        self._ell(d, px, py, pr - 10, outline=(*TEAL, 230), width=max(1, int(2 * S)))
        self._arc(d, px, py, pr + 8, -90, -90 + 360 * ((pct or 0) / 100),
                  4, (*TEAL, 235))
        self._txt(d, px, py - 8, "Power", _mono(8), (*TEAL, 220), anchor="ma")
        self._txt(d, px, py + 10, battery_text, _mono(16), (*TEAL_HI, 255),
                  anchor="ma")
        self._txt(d, px, py + 26, "High" if charging else "Level", _mono(8),
                  (*TEAL, 220), anchor="ma")

        # 底部左侧小状态。
        self._txt(d, 110, 438, "Waste Status", _mono(9), (*TEAL, 220))
        self._txt(d, 128, 458, "0 Files(s)", _mono(9), (*TEAL, 235))
        self._ell(d, 202, 428, 9, fill=(*TEAL, 235))
        self._txt(d, 100, 478, f"Uptime: {self._tele.uptime()}", _mono(9),
                  (*TEAL, 230))
        self._txt(d, 208, 338, "Player", _mono(8), (*TEAL, 220))
        self._line(d, 236, 326, 350, 326, 2, (*TEAL_DIM, 170))
        self._txt(d, 232, 352, f"CPU {cpu * 100:.0f}% · Load {la:.2f}",
                  _mono(8), (*TEAL, 220))

        # 反应堆下方主应用分支。
        for i, label in enumerate(("Photoshop", "Word", "Excel", "Powerpoint")):
            y = 528 + i * 22
            self._ell(d, 486, y, 6, fill=(*TEAL, 215))
            self._line(d, 486, y, 552, y, 2, (*TEAL, 170))
            self._txt(d, 505, y - 9, label, _mono(9), (*TEAL, 230))

    # ---- 笔记 / 待办 -------------------------------------------------
    def _draw_notes(self, d) -> None:
        font = _han(10)
        x, w, y = 924, 112, 100
        for item in self._notes.items():
            if y > 286:
                break
            self._txt(d, x, y + 2, "·", _mono(8), (120, 226, 240, 255))
            for ln in self._wrap(d, item, font, w)[:3]:
                if y > 286:
                    break
                self._txt(d, x + 12, y, ln, font, (105, 235, 238, 235))
                y += 13
            y += 2

    # ---- 转写台账（底部通栏）----------------------------------------
    def _draw_transcript(self, d, color) -> None:
        bx0, by0, bx1, by1 = 94, 512, 282, 646
        self._line(d, bx0, by0, bx1, by0, 2, (*TEAL, 210))
        self._line(d, bx0, by0, bx0, by1, 2, (*TEAL_DIM, 190))
        self._line(d, bx1, by0, bx1, by1 - 34, 2, (*TEAL_DIM, 170))
        self._txt(d, bx0 + 4, by0 - 14, "Communication   compose new",
                  _mono(9), (*TEAL, 235))
        self._txt(d, bx0 + 16, by0 + 22, "RainMeter", _mono(8), (*TEAL, 205))
        self._txt(d, bx0 + 16, by0 + 42, "Resources", _mono(8), (*TEAL_DIM, 220))

        font = _han(10)
        rendered: list[tuple[str, str]] = []
        for role, text in self._lines:
            prefix = {"you": "你: ", "jarvis": "J: ", "sys": "» "}.get(role, "")
            for ln in self._wrap(d, prefix + text, font, bx1 - bx0 - 26):
                rendered.append((role, ln))
        lh = 15
        avail = 5
        jc = tuple(int(c + (255 - c) * 0.35) for c in color)   # 回答色提亮
        rolecol = {"you": (205, 238, 247, 255), "jarvis": (*jc, 255),
                   "sys": (175, 215, 224, 240)}
        ty = by0 + 66
        for role, ln in rendered[-avail:]:
            self._txt(d, bx0 + 16, ty, ln, font, rolecol.get(role, TEAL))
            ty += lh

        # 右下 Reader feed，贴近参考图 gizmodo 列表。
        rx, ry = 950, 430
        reader = [text for role, text in self._lines if role == "jarvis"][-4:]
        if not reader:
            reader = [
                "Eye-Fi Direct Mode Beams Photos From",
                "Your Camera to Your Mobile",
                "It's Time to Install Some Apps On Your",
                "Toyota [Automotive]",
            ]
        for item in reader[:5]:
            for ln in self._wrap(d, item, _han(9), 174)[:2]:
                self._txt(d, 1122, ry, ln, _han(9), (*TEAL, 210), anchor="ra")
                ry += 16
            ry += 6

    def _wrap(self, d, text, font, max_w) -> list[str]:
        limit = max_w * S
        lines, cur = [], ""
        for ch in text:
            if ch == "\n" or d.textlength(cur + ch, font=font) > limit:
                if cur:
                    lines.append(cur)
                cur = "" if ch == "\n" else ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines or [""]
