import json
import logging
import urllib.request
import urllib.error
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from onscreen_translator.config.settings import Settings

logger = logging.getLogger(__name__)


class Translator:
    """Ollama-backed translator. Runs fully offline via the local Ollama API."""

    def __init__(self):
        self._cache: dict = {}  # md5(text) → translated string

    def translate_group(self, group, settings) -> str:
        """
        Translate a TextGroup (from ocr.py). Uses a session cache so unchanged
        text is never sent to Ollama twice. group.lines are joined with \\n to
        preserve line structure for the LLM.
        """
        import hashlib
        joined = "\n".join(group.lines)
        key = hashlib.md5(joined.encode()).hexdigest()
        if key in self._cache:
            logger.debug(f"Translation cache hit: {joined[:40]!r}")
            return self._cache[key]
        result = self._translate_ollama(joined, settings)
        self._cache[key] = result
        logger.info(f"Translated (new): {joined[:40]!r} → {result[:60]!r}")
        return result

    def clear_cache(self):
        self._cache.clear()

    def translate(self, text: str, target_lang: str = "en",
                  settings: "Settings | None" = None) -> dict:
        """
        Detect source language and translate text via Ollama.
        Returns: {"source_language": str, "target_language": str,
                  "original": str, "translated": str}
        """
        if not text.strip():
            return {"source_language": "unknown", "target_language": target_lang,
                    "original": text, "translated": text}

        src_lang = self._detect_language(text)

        if settings is None:
            from onscreen_translator.config.settings import Settings as _S
            settings = _S.load()

        translated = self._translate_ollama(text, settings)
        return {"source_language": src_lang, "target_language": target_lang,
                "original": text, "translated": translated}

    def _detect_language(self, text: str) -> str:
        try:
            from langdetect import detect
            return detect(text)
        except Exception as e:
            logger.warning(f"Language detection failed: {e}")
            return "unknown"

    def _translate_ollama(self, text: str, settings) -> str:
        prompt = (
            "Translate the following text to English. "
            "Output only the translation — no explanations, no commentary, no quotes.\n\n"
            f"{text}"
        )
        payload = json.dumps({
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            f"{settings.ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                return data.get("response", "").strip()
        except urllib.error.URLError as e:
            logger.error(f"Ollama unreachable: {e}")
            return f"[Ollama not running — start it with: ollama serve]"
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            return f"[Translation error: {e}]"


if __name__ == "__main__":
    import sys
    import json as _json
    logging.basicConfig(level=logging.INFO)
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "こんにちは、世界"
    t = Translator()
    result = t.translate(text)
    print(_json.dumps(result, ensure_ascii=False, indent=2))
