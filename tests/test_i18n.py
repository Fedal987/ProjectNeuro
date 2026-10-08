"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

import os
import unittest
from unittest.mock import patch

from src.main.ui import i18n


class LanguageDetectionTests(unittest.TestCase):
    def test_environment_preferences(self):
        cases = [
            ({"LANG": "zh_CN.UTF-8"}, "zh_CN"),
            ({"LANG": "zh-Hant-HK.UTF-8"}, "zh_TW"),
            ({"LANG": "ja_JP.UTF-8"}, "ja"),
            ({"LANG": "pt_BR.UTF-8"}, "pt"),
            ({"LANG": "es_ES.UTF-8"}, "en"),
            ({"LC_ALL": "fr_FR.UTF-8", "LANG": "de_DE.UTF-8"}, "fr"),
            ({"LC_MESSAGES": "de_DE.UTF-8", "LANG": "ja_JP.UTF-8"}, "de"),
            ({"LANGUAGE": "es:zh_TW:en", "LANG": "en_US.UTF-8"}, "zh_TW"),
            ({"NEURO_LANG": "ja", "LANG": "zh_CN.UTF-8"}, "ja"),
            ({"NEURO_LANG": "invalid", "LANG": "zh_CN.UTF-8"}, "zh_CN"),
            ({"LC_ALL": "C", "LANGUAGE": "fr"}, "en"),
        ]
        for environment, expected in cases:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                self.assertEqual(i18n.detect_language(), expected)

    def test_system_locale_without_environment(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            i18n.locale, "getlocale", return_value=("ru_RU", "UTF-8")
        ):
            self.assertEqual(i18n.detect_language(), "ru")

    def test_unavailable_system_locale(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            i18n.locale, "getlocale", side_effect=ValueError("unknown locale")
        ):
            self.assertEqual(i18n.detect_language(), "en")

    def test_manual_selection_remains_active(self):
        original = i18n.get_language()
        self.addCleanup(i18n.set_language, original)
        with patch.dict(os.environ, {"LANG": "ja_JP.UTF-8"}, clear=True):
            i18n.set_language("zh_CN")
            self.assertEqual(i18n.get_language(), "zh_CN")
            self.assertEqual(i18n.tr("user_prompt"), i18n._load_catalog("zh_CN")["user_prompt"])


if __name__ == "__main__":
    unittest.main()
