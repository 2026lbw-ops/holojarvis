import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis import tts


class MacVoiceResolutionTest(unittest.TestCase):
    def test_temp_audio_is_removed_after_blocking_playback(self) -> None:
        path = Path(tempfile.NamedTemporaryFile(delete=False).name)
        with patch("jarvis.tts._play_file"):
            tts._play_temp_file(str(path), blocking=True)
        self.assertFalse(path.exists())

    def test_temp_audio_is_removed_after_async_playback(self) -> None:
        class FinishedProcess:
            def wait(self) -> None:
                return None

        path = Path(tempfile.NamedTemporaryFile(delete=False).name)
        with patch("jarvis.tts._play_file", return_value=FinishedProcess()):
            tts._play_temp_file(str(path), blocking=False)
        deadline = time.monotonic() + 1
        while path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(path.exists())

    def test_output_device_accepts_index_or_name(self) -> None:
        devices = [
            {"name": "USB Audio", "max_output_channels": 2},
            {"name": "Conexant SmartAudio HD", "max_output_channels": 2},
        ]
        self.assertEqual(tts._pick_output_device(devices, "1"), 1)
        self.assertEqual(tts._pick_output_device(devices, "conexant"), 1)
        with self.assertRaisesRegex(RuntimeError, "找不到音频输出设备"):
            tts._pick_output_device(devices, "missing")

    def test_prefers_matching_simplified_chinese_voice_variant(self) -> None:
        voices = (
            "Eddy (英语（美国）)       en_US    # Hello! My name is Eddy.\n"
            "Eddy (中文（中国大陆）)   zh_CN    # 你好！我叫Eddy。\n"
            "Tingting                 zh_CN    # 你好！我叫婷婷。\n"
        )
        completed = subprocess.CompletedProcess(
            args=["say", "-v", "?"], returncode=0, stdout=voices, stderr=""
        )

        with patch("jarvis.tts.subprocess.run", return_value=completed):
            resolved = tts._resolve_macos_voice("Eddy")

        self.assertEqual(resolved, "Eddy (中文（中国大陆）)")


if __name__ == "__main__":
    unittest.main()
