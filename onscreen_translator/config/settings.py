import os
import tomllib
from pathlib import Path
from dataclasses import dataclass, field

CONFIG_PATH = Path.home() / ".config" / "onscreen-translator" / "config.toml"

DEFAULT_CONFIG = """
[translation]
target_language = "en"

[hotkey]
preferred_trigger = "Super+t"

[overlay]
auto_dismiss_seconds = 12
show_original = false
font_size_translated = 15
font_size_original = 11

[ocr]
# PaddleOCR language code for text recognition.
# Common values: japan, ch, korean, en, chinese_cht, latin
language = "japan"

[ollama]
# Local Ollama model used for translation.
# Smaller/faster: llama3.2  Better Japanese quality: qwen2.5:7b
model = "llama3.2"
url   = "http://localhost:11434"
""".strip()

@dataclass
class Settings:
    target_language: str = "en"
    preferred_trigger: str = "Super+t"
    auto_dismiss_seconds: int = 12
    show_original: bool = False
    font_size_translated: int = 15
    font_size_original: int = 11
    ocr_language: str = "japan"
    ollama_model: str = "llama3.2"
    ollama_url:   str = "http://localhost:11434"

    @classmethod
    def load(cls) -> "Settings":
        if not CONFIG_PATH.exists():
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(DEFAULT_CONFIG)

        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)

        t  = data.get("translation", {})
        h  = data.get("hotkey", {})
        o  = data.get("overlay", {})
        ocr = data.get("ocr", {})
        ol  = data.get("ollama", {})

        return cls(
            target_language=t.get("target_language", "en"),
            preferred_trigger=h.get("preferred_trigger", "Super+t"),
            auto_dismiss_seconds=o.get("auto_dismiss_seconds", 12),
            show_original=o.get("show_original", True),
            font_size_translated=o.get("font_size_translated", 15),
            font_size_original=o.get("font_size_original", 11),
            ocr_language=ocr.get("language", "japan"),
            ollama_model=ol.get("model", "llama3.2"),
            ollama_url=ol.get("url", "http://localhost:11434"),
        )
