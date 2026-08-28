"""麦克风采集 + 基于音量的简单断句(VAD)。

思路：持续读取小帧音频，计算音量(RMS)。音量超过阈值认为有人说话，开始录音；
句尾连续静音超过 SILENCE_TAIL 秒就判定一句说完，产出这段音频交给识别。
启动时自动采集一小段环境噪音来校准阈值。
"""

from __future__ import annotations

import queue
import threading
import warnings
from collections.abc import Iterator

import numpy as np
import sounddevice as sd

from . import config

_FRAME = int(config.SAMPLE_RATE * config.FRAME_MS / 1000)   # 每帧采样点数


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)) + 1e-9)


def _soundcard_frame(block: np.ndarray) -> np.ndarray:
    """选择最强输入通道，并把 48 kHz float 音频降到 16 kHz int16。"""
    channel = int(np.argmax(np.mean(block.astype(np.float64) ** 2, axis=0)))
    mono = block[:, channel][::3]
    return np.clip(mono * 32768, -32768, 32767).astype(np.int16)


class _SoundCardStream:
    """把 Windows MediaFoundation 采集包装成 sounddevice 风格的回调流。"""

    def __init__(self, callback) -> None:  # noqa: ANN001
        import soundcard as sc
        self._mic = sc.default_microphone()
        if self._mic is None:
            raise RuntimeError("Windows 没有默认麦克风")
        self._callback = callback
        self._context = self._mic.recorder(samplerate=48000)
        self._recorder = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._recorder = self._context.__enter__()
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self) -> None:
        from soundcard.mediafoundation import SoundcardRuntimeWarning
        assert self._recorder is not None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SoundcardRuntimeWarning)
            while not self._stop.is_set():
                # ponytail: 5 秒块规避部分老 USB 驱动的短块断流；驱动正常后可缩短。
                block = self._recorder.record(numframes=240000)
                frame = _soundcard_frame(block)
                for start in range(0, len(frame), _FRAME):
                    chunk = frame[start:start + _FRAME]
                    if len(chunk) == _FRAME:
                        self._callback(chunk[:, None], len(chunk), None, None)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=6)
        if self._recorder is not None:
            self._context.__exit__(None, None, None)
            self._recorder = None

    def close(self) -> None:
        if self._recorder is not None:
            self.stop()


class Microphone:
    """以 16k/单声道/int16 持续采集，产出一段段语音(float32, 归一化)。"""

    def __init__(self) -> None:
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        try:
            if config.IS_WINDOWS and config.AUDIO_BACKEND == "soundcard":
                self._stream = _SoundCardStream(self._on_audio)
            else:
                self._stream = sd.InputStream(
                    samplerate=config.SAMPLE_RATE, channels=1, dtype="int16",
                    blocksize=_FRAME, callback=self._on_audio,
                )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "无法打开麦克风，请检查 Windows 麦克风权限和默认输入设备"
            ) from e
        self.threshold = 500.0   # 会在 calibrate() 里更新
        self.on_speech_start = None   # 检测到有人开口时回调（给桌宠显示"聆听"用）

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        self._q.put(indata[:, 0].copy())

    def __enter__(self) -> "Microphone":
        try:
            self._stream.start()
            self.calibrate()
            self.flush()
        except Exception as e:  # noqa: BLE001
            self._stream.close()
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError("麦克风启动失败，请关闭占用麦克风的应用后重试") from e
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self._stream.stop()
        self._stream.close()

    def flush(self) -> None:
        """清空缓冲队列——朗读完后调用，丢弃把自己声音录进去的那段音频，防止自言自语。"""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def calibrate(self, seconds: float = 1.0) -> None:
        """采集环境噪音，把阈值设为噪音的若干倍。"""
        levels = []
        need = int(seconds * 1000 / config.FRAME_MS)
        timeout = 7 if (config.IS_WINDOWS
                        and config.AUDIO_BACKEND == "soundcard") else 2
        while len(levels) < need:
            try:
                levels.append(_rms(self._q.get(timeout=timeout)))
            except queue.Empty:
                raise RuntimeError("麦克风没有收到音频，请检查设备是否被禁用") from None
        floor = float(np.median(levels))
        self.threshold = max(floor * 3.5, config.MIC_MIN_THRESHOLD)

    def _frames(self) -> Iterator[np.ndarray]:
        while True:
            yield self._q.get()

    def segments(self) -> Iterator[np.ndarray]:
        """阻塞式产出一段段语音（float32, [-1,1]）。"""
        tail = int(config.SILENCE_TAIL * 1000 / config.FRAME_MS)
        max_frames = int(config.MAX_SEGMENT * 1000 / config.FRAME_MS)
        min_frames = int(config.MIN_SPEECH * 1000 / config.FRAME_MS)

        buf: list[np.ndarray] = []
        silence = 0
        speaking = False

        for frame in self._frames():
            loud = _rms(frame) > self.threshold
            if speaking:
                buf.append(frame)
                silence = 0 if loud else silence + 1
                if silence >= tail or len(buf) >= max_frames:
                    if len(buf) >= min_frames:
                        audio = np.concatenate(buf).astype(np.float32) / 32768.0
                        yield audio
                    buf, silence, speaking = [], 0, False
            elif loud:
                speaking = True
                buf = [frame]
                silence = 0
                if self.on_speech_start:
                    self.on_speech_start()
