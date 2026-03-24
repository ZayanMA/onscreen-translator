import os
import tomllib
from pathlib import Path
from dataclasses import dataclass, field

CONFIG_PATH = Path.home() / ".config" / "onscreen-translator" / "config.toml"

DEFAULT_CONFIG = """
[hotkey]
preferred_trigger = "Super+t"

[overlay]
auto_dismiss_seconds = 12
show_original = false
font_size_translated = 15
font_size_original = 11

[ocr]
# Language for text detection.
# For Japanese (manga, games, anime): "japan"
# Other options: "ch", "korean", "en", "chinese_cht", "latin"
language = "japan"

[deepl]
# Free API key from https://www.deepl.com/pro-api (500,000 chars/month free)
api_key = ""
# Target language code. Examples: EN-US, EN-GB, DE, FR, ES, ZH
target_language = "EN-US"
""".strip()

@dataclass
class Settings:
    preferred_trigger: str = "Super+t"
    auto_dismiss_seconds: int = 12
    show_original: bool = False
    font_size_translated: int = 15
    font_size_original: int = 11
    ocr_language: str = "japan"
    deepl_api_key:     str = ""
    deepl_target_lang: str = "EN-US"

    @classmethod
    def load(cls) -> "Settings":
        if not CONFIG_PATH.exists():
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(DEFAULT_CONFIG)

        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)

        h   = data.get("hotkey", {})
        o   = data.get("overlay", {})
        ocr = data.get("ocr", {})
        dl  = data.get("deepl", {})

        return cls(
            preferred_trigger=h.get("preferred_trigger", "Super+t"),
            auto_dismiss_seconds=o.get("auto_dismiss_seconds", 12),
            show_original=o.get("show_original", False),
            font_size_translated=o.get("font_size_translated", 15),
            font_size_original=o.get("font_size_original", 11),
            ocr_language=ocr.get("language", "japan"),
            deepl_api_key=dl.get("api_key", ""),
            deepl_target_lang=dl.get("target_language", "EN-US"),
        )
