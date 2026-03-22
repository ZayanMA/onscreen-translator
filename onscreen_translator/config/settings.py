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
show_original = true
font_size_translated = 15
font_size_original = 11
""".strip()

@dataclass
class Settings:
    target_language: str = "en"
    preferred_trigger: str = "Super+t"
    auto_dismiss_seconds: int = 12
    show_original: bool = True
    font_size_translated: int = 15
    font_size_original: int = 11

    @classmethod
    def load(cls) -> "Settings":
        if not CONFIG_PATH.exists():
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(DEFAULT_CONFIG)

        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)

        t = data.get("translation", {})
        h = data.get("hotkey", {})
        o = data.get("overlay", {})

        return cls(
            target_language=t.get("target_language", "en"),
            preferred_trigger=h.get("preferred_trigger", "Super+t"),
            auto_dismiss_seconds=o.get("auto_dismiss_seconds", 12),
            show_original=o.get("show_original", True),
            font_size_translated=o.get("font_size_translated", 15),
            font_size_original=o.get("font_size_original", 11),
        )
