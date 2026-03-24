import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from onscreen_translator.config.settings import Settings

logger = logging.getLogger(__name__)


class Translator:
    """DeepL-backed translator. Requires a DeepL API key in config."""

    def __init__(self):
        self._cache: dict = {}        # md5(text) → translated string
        self._client = None           # deepl.Translator instance (created on first use)
        self._client_api_key: str = ""  # key used to build _client

    def translate_group(self, group, settings) -> str:
        """
        Translate a TextGroup (from ocr.py). Uses a session cache so unchanged
        text is never sent to DeepL twice. group.lines are joined with \\n to
        preserve line structure.
        """
        import hashlib
        joined = "\n".join(group.lines)
        key = hashlib.md5(joined.encode()).hexdigest()
        if key in self._cache:
            logger.debug(f"Translation cache hit: {joined[:40]!r}")
            return self._cache[key]
        result = self._translate_deepl(joined, settings)
        self._cache[key] = result
        logger.info(f"Translated: {joined[:40]!r} → {result[:60]!r}")
        return result

    def clear_cache(self):
        self._cache.clear()

    def _get_client(self, api_key: str):
        """Return cached deepl.Translator, creating it if the key has changed."""
        import deepl
        if self._client is None or self._client_api_key != api_key:
            self._client = deepl.Translator(api_key)
            self._client_api_key = api_key
        return self._client

    def _translate_deepl(self, text: str, settings) -> str:
        if not settings.deepl_api_key:
            return "[ERROR: DeepL API key not set — add it to ~/.config/onscreen-translator/config.toml]"
        try:
            import deepl
            client = self._get_client(settings.deepl_api_key)
            key_hint = settings.deepl_api_key[:8] + "..."
            logger.info(f"Calling DeepL API (key={key_hint}, chars={len(text)})")
            result = client.translate_text(
                text,
                target_lang=settings.deepl_target_lang,
                # source_lang=None → DeepL auto-detects
            )
            logger.info(f"DeepL OK — returned {len(result.text)} chars")
            return result.text
        except deepl.AuthorizationException:
            logger.error("DeepL API key is INVALID — check ~/.config/onscreen-translator/config.toml")
            return "[ERROR: DeepL API key invalid]"
        except deepl.QuotaExceededException:
            logger.error("DeepL free quota exceeded")
            return "[ERROR: DeepL quota exceeded]"
        except Exception as e:
            logger.error(f"DeepL request failed: {e}")
            return f"[ERROR: {e}]"


if __name__ == "__main__":
    import sys
    import json
    logging.basicConfig(level=logging.INFO)
    from onscreen_translator.config.settings import Settings
    settings = Settings.load()
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "こんにちは、世界"
    t = Translator()

    class _FakeGroup:
        lines = [text]

    result = t.translate_group(_FakeGroup(), settings)
    print(json.dumps({"original": text, "translated": result}, ensure_ascii=False, indent=2))
