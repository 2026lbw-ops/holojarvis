"""文字转语音。两种后端：
  - clone / gptsovits：调本地克隆音服务，用参考音色说话；服务没开则自动回退
  - say  ：系统自带嗓音——macOS 用 `say`，Windows 用 SAPI 语音合成，零依赖、即时
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from functools import lru_cache

from . import config

_proc: subprocess.Popen | None = None

# Windows 上隐藏 PowerShell 黑窗
_NO_WINDOW = 0x08000000 if config.IS_WINDOWS else 0


@lru_cache(maxsize=None)
def _resolve_macos_voice(preferred: str) -> str:
    """Resolve an ambiguous macOS voice name to its zh_CN variant."""
    try:
        result = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, check=False,
        )
    except OSError:
        return preferred
    if result.returncode != 0:
        return preferred
    for line in result.stdout.splitlines():
        match = re.match(r"^(.*?)\s+([a-z]{2}_[A-Z]{2})\s+#", line)
        if not match:
            continue
        name, locale = match.groups()
        if locale == "zh_CN" and (
                name == preferred or name.startswith(preferred + " (")
        ):
            return name
    return preferred


def _clean(text: str) -> str:
    """去掉不适合朗读的 markdown 符号 / emoji，让朗读更自然。"""
    text = re.sub(r"```.*?```", "", text, flags=re.S)      # 代码块
    text = re.sub(r"[*_`#>\-]+", "", text)                  # markdown 标记
    text = re.sub(r"https?://\S+", "网址链接", text)        # URL
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)  # emoji
    return text.strip()


def _pick_output_device(devices, requested: str) -> int:
    """把设备编号或名称片段解析为 sounddevice 输出编号。"""
    if requested.isdecimal():
        return int(requested)
    needle = requested.casefold()
    for index, device in enumerate(devices):
        if (device["max_output_channels"] > 0
                and needle in device["name"].casefold()):
            return index
    raise RuntimeError(f"找不到音频输出设备：{requested}")


def _play_sounddevice_file(path: str, requested: str) -> None:
    """用指定 Windows 音频设备播放 PCM WAV。"""
    import wave
    import numpy as np
    import sounddevice as sd

    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2:
            raise RuntimeError("仅支持 16-bit PCM WAV 播放")
        channels = wav.getnchannels()
        rate = wav.getframerate()
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    device = _pick_output_device(sd.query_devices(), requested)
    sd.play(data.reshape(-1, channels), rate, device=device)
    sd.wait()


def _play_file(path: str, blocking: bool) -> subprocess.Popen | None:
    global _proc
    env = None
    if config.IS_WINDOWS and config.OUTPUT_DEVICE:
        cmd = [sys.executable, "-c",
               "import os; from jarvis.tts import _play_sounddevice_file; "
               "_play_sounddevice_file(os.environ['JV_AUDIO'], "
               "os.environ['JV_OUTPUT_DEVICE'])"]
        env = {**os.environ, "JV_AUDIO": path,
               "JV_OUTPUT_DEVICE": config.OUTPUT_DEVICE}
    elif config.IS_WINDOWS:
        # 用 PowerShell 的 SoundPlayer 播放 wav；放进独立进程，stop() 可终止它
        cmd = ["powershell", "-NoProfile", "-Command",
               f"(New-Object System.Media.SoundPlayer '{path}').PlaySync()"]
    else:
        cmd = ["afplay", path]
    if blocking:
        subprocess.run(cmd, env=env, creationflags=_NO_WINDOW)
        return None
    else:
        _proc = subprocess.Popen(cmd, env=env, creationflags=_NO_WINDOW)
        return _proc


def _remove_files(*paths: str) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def _remove_when_done(proc: subprocess.Popen, *paths: str) -> None:
    def cleanup() -> None:
        try:
            proc.wait()
        finally:
            _remove_files(*paths)

    threading.Thread(target=cleanup, daemon=True).start()


def _play_temp_file(path: str, blocking: bool) -> None:
    if blocking:
        try:
            _play_file(path, True)
        finally:
            _remove_files(path)
        return
    try:
        proc = _play_file(path, False)
    except Exception:
        _remove_files(path)
        raise
    if proc is None:
        _remove_files(path)
    else:
        _remove_when_done(proc, path)


def _speak_say(text: str, blocking: bool) -> None:
    """系统自带嗓音：macOS=say，Windows=SAPI(System.Speech)。"""
    global _proc
    if config.IS_WINDOWS:
        _speak_sapi(text, blocking)
        return
    voice = _resolve_macos_voice(config.TTS_VOICE)
    cmd = ["say", "-v", voice, "-r", str(config.TTS_RATE), text]
    if blocking:
        subprocess.run(cmd)
    else:
        _proc = subprocess.Popen(cmd)


def _speak_sapi(text: str, blocking: bool) -> None:
    """Windows 系统语音合成（SAPI）。文本经临时文件传入以避免引号转义问题；
    默认自动挑一个中文(zh)嗓音，可用环境变量 JARVIS_VOICE 指定具体嗓音名。"""
    global _proc
    import tempfile
    tf = tempfile.NamedTemporaryFile(suffix=".txt", delete=False,
                                     mode="w", encoding="utf-8")
    tf.write(text)
    tf.close()
    rate = max(-10, min(10, round((config.TTS_RATE - 200) / 20)))  # 字/分→SAPI档(-10~10)
    wav_path = ""
    output_setup = ""
    if config.OUTPUT_DEVICE:
        wav_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav_path = wav_file.name
        wav_file.close()
        output_setup = "$s.SetOutputToWaveFile($env:JV_WAV);"
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.Rate={rate};"
        "$v=$env:JV_VOICE;"
        "if($v){try{$s.SelectVoice($v)}catch{}}else{"
        "foreach($iv in $s.GetInstalledVoices()){"
        "if($iv.VoiceInfo.Culture.Name -like 'zh*'){"
        "$s.SelectVoice($iv.VoiceInfo.Name);break}}};"
        "$t=Get-Content -Raw -Encoding UTF8 $env:JV_TXT;"
        + output_setup + "$s.Speak($t);$s.Dispose()"
    )
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    env = {**os.environ, "JV_TXT": tf.name, "JV_WAV": wav_path,
           "JV_VOICE": os.environ.get("JARVIS_VOICE", "")}
    if config.OUTPUT_DEVICE:
        try:
            subprocess.run(cmd, env=env, creationflags=_NO_WINDOW)
        except Exception:
            _remove_files(wav_path)
            raise
        finally:
            _remove_files(tf.name)
        _play_temp_file(wav_path, blocking)
        return
    if blocking:
        try:
            subprocess.run(cmd, env=env, creationflags=_NO_WINDOW)
        finally:
            _remove_files(tf.name)
    else:
        try:
            _proc = subprocess.Popen(cmd, env=env, creationflags=_NO_WINDOW)
        except Exception:
            _remove_files(tf.name)
            raise
        _remove_when_done(_proc, tf.name)


def _speak_clone(text: str, blocking: bool) -> bool:
    """请求克隆音服务合成并播放；成功返回 True，失败(服务没开等)返回 False。"""
    try:
        req = urllib.request.Request(
            config.VOICE_SERVER + "/tts",
            data=text.encode("utf-8"), method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        path = obj.get("path")
        if not path:
            return False
        _play_file(path, blocking)
        return True
    except Exception:  # noqa: BLE001
        return False


def _speak_gptsovits(text: str, blocking: bool) -> bool:
    """请求本地 GPT-SoVITS API(v2) 合成并播放；失败返回 False 以便回退。"""
    import tempfile
    payload = json.dumps({
        "text": text,
        "text_lang": config.GPTSOVITS_TEXT_LANG,
        "ref_audio_path": config.GPTSOVITS_REF,
        "prompt_text": config.GPTSOVITS_PROMPT,
        "prompt_lang": config.GPTSOVITS_PROMPT_LANG,
        "text_split_method": "cut5",
        "media_type": "wav",
        "streaming_mode": False,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            config.GPTSOVITS_URL + "/tts", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if not data or len(data) < 100:        # 出错时多半返回的是 json 错误
            return False
        path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        with open(path, "wb") as f:
            f.write(data)
        _play_temp_file(path, blocking)
        return True
    except Exception:  # noqa: BLE001
        return False


def speak(text: str, blocking: bool = True) -> None:
    """朗读文字。克隆后端不可用时自动回退到 say。"""
    text = _clean(text)
    if not text:
        return
    stop()  # 先打断上一句
    backend = config.TTS_BACKEND
    if backend == "gptsovits" and _speak_gptsovits(text, blocking):
        return
    if backend == "clone" and _speak_clone(text, blocking):
        return
    _speak_say(text, blocking)


def stop() -> None:
    """打断当前朗读。"""
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
    _proc = None
