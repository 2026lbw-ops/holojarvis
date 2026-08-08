import subprocess
import unittest
from unittest.mock import patch

from jarvis import tts


class MacVoiceResolutionTest(unittest.TestCase):
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
