import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from jarvis import config


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class ModelConfigTest(unittest.TestCase):
    def test_responsive_defaults_balance_speed_and_accuracy(self) -> None:
        env = os.environ.copy()
        for name in ("JARVIS_MAX_TOKENS", "JARVIS_WHISPER", "JARVIS_ASR_BEAM", "JARVIS_SILENCE_TAIL"):
            env.pop(name, None)
        code = "from jarvis import config; import json; print(json.dumps([config.MAX_TOKENS, config.WHISPER_MODEL, config.ASR_BEAM, config.SILENCE_TAIL]))"
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=Path(__file__).parents[1], env=env,
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), [384, "base", 3, 0.35])

    def test_ollama_is_used_only_when_no_endpoint_is_configured(self) -> None:
        response = _Response({"models": [{"name": "qwen3:8b"}]})
        with patch.object(config, "LLM_BASE_URL", ""), \
                patch.object(config, "MODEL", "deepseek-chat"), \
                patch.object(config, "LOCAL_PROVIDER", False), \
                patch("urllib.request.urlopen", return_value=response):
            self.assertTrue(config.auto_configure_ollama())
            self.assertEqual(config.LLM_BASE_URL, "http://127.0.0.1:11434/v1")
            self.assertEqual(config.MODEL, "qwen3:8b")
            self.assertTrue(config.LOCAL_PROVIDER)

        with patch.object(config, "LLM_BASE_URL", "https://example.com/v1"), \
                patch("urllib.request.urlopen") as request:
            self.assertFalse(config.auto_configure_ollama())
            request.assert_not_called()

    def test_cloud_memory_scope_defaults_off_and_local_defaults_all(self) -> None:
        with patch.object(config, "LLM_BASE_URL", "https://example.com/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False), \
                patch.dict(os.environ, {"JARVIS_CLOUD_MEMORY": "none"}):
            self.assertEqual(config.cloud_memory_policy(), ((), ()))

        with patch.object(config, "LLM_BASE_URL", "https://example.com/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False), \
                patch.dict(os.environ, {"JARVIS_CLOUD_MEMORY": "core,project"}):
            self.assertEqual(config.cloud_memory_policy(),
                             (("core", "project"), ()))

        with patch.object(config, "LLM_BASE_URL", "https://example.com/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False), \
                patch.dict(os.environ, {"JARVIS_CLOUD_MEMORY": "secret"}):
            self.assertEqual(config.cloud_memory_policy(), ((), ("secret",)))

        with patch.object(config, "LLM_BASE_URL", "http://127.0.0.1:11434/v1"), \
                patch.object(config, "LOCAL_PROVIDER", False), \
                patch.dict(os.environ, {"JARVIS_CLOUD_MEMORY": "none"}):
            self.assertEqual(config.cloud_memory_policy()[0],
                             config.MEMORY_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
