import unittest
import urllib.error
import numpy as np
from unittest.mock import patch

from jarvis import asr, audio
from jarvis.brain import _network_error, _open_request


class ErrorMessagesTest(unittest.TestCase):
    def test_network_errors_have_actionable_messages(self) -> None:
        unauthorized = urllib.error.HTTPError("url", 401, "", {}, None)
        self.assertIn("API Key", _network_error(unauthorized))
        self.assertIn("无法连接", _network_error(
            urllib.error.URLError("connection refused")))
        self.assertIn("格式不兼容", _network_error(KeyError("choices")))

    def test_transient_model_connection_is_retried_once(self) -> None:
        response = object()
        with patch("jarvis.brain.urllib.request.urlopen", side_effect=[
                urllib.error.URLError("temporary"), response]), \
                patch("jarvis.brain.time.sleep") as sleep:
            self.assertIs(_open_request("request"), response)
            sleep.assert_called_once_with(0.5)

    def test_microphone_open_error_is_explained(self) -> None:
        with patch.object(audio.config, "AUDIO_BACKEND", "sounddevice"), \
                patch.object(audio.sd, "InputStream", side_effect=OSError("device")):
            with self.assertRaisesRegex(RuntimeError, "麦克风权限"):
                audio.Microphone()

    def test_soundcard_frame_selects_stronger_channel_and_resamples(self) -> None:
        block = np.zeros((1440, 2), dtype=np.float32)
        block[:, 1] = 0.5
        frame = audio._soundcard_frame(block)
        self.assertEqual(frame.shape, (480,))
        self.assertTrue(np.all(frame == 16384))

    def test_whisper_load_error_is_explained(self) -> None:
        with patch.object(asr, "_model", None), \
                patch.object(asr, "WhisperModel", side_effect=OSError("download")):
            with self.assertRaisesRegex(RuntimeError, "网络和磁盘空间"):
                asr.load()


if __name__ == "__main__":
    unittest.main()
