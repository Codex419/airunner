import pytest

pytest.skip(
    "Test requires espeak and is not suitable for headless CI. Skipped by default.",
    allow_module_level=True,
)
import unittest
from unittest.mock import MagicMock
from airunner.handlers.tts.espeak_model_manager import EspeakModelManager


class TestEspeakModelManager(unittest.TestCase):
    def setUp(self):
        self.mock_settings = MagicMock()
        self.mock_settings.rate = 150
        self.mock_settings.pitch = 100
        self.mock_settings.volume = 80
        self.mock_settings.voice = "english"
        self.mock_settings.language = "en"
        self.mock_settings.gender = "male"

        self.handler = EspeakModelManager(
            espeak_settings=self.mock_settings, path_settings=MagicMock()
        )
        # Patch in a dummy api attribute to avoid AttributeError
        self.handler.api = MagicMock()

    def test_load(self):
        self.handler.load()
        self.assertIsNotNone(self.handler._engine)

    def test_unload(self):
        self.handler.load()
        self.handler.unload()
        self.assertIsNone(self.handler._engine)

    def test_generate(self):
        self.handler.load()
        from airunner.handlers.tts.tts_request import EspeakTTSRequest

        tts_request = EspeakTTSRequest(message="Test message")
        result = self.handler.generate(tts_request)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
