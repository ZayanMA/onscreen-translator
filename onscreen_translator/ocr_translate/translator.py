import logging

logger = logging.getLogger(__name__)


class Translator:
    """Argos Translate wrapper for offline translation."""

    def translate(self, text: str, target_lang: str = "en") -> dict:
        """
        Detect source language and translate text.
        Returns: {"source_language": str, "original": str, "translated": str}
        """
        if not text.strip():
            return {"source_language": "unknown", "original": text, "translated": text}

        src_lang = self._detect_language(text)

        if src_lang == target_lang:
            return {"source_language": src_lang, "original": text, "translated": text}

        translated = self._translate(text, src_lang, target_lang)
        return {"source_language": src_lang, "original": text, "translated": translated}

    def _detect_language(self, text: str) -> str:
        try:
            from langdetect import detect
            return detect(text)
        except Exception as e:
            logger.warning(f"Language detection failed: {e}")
            return "unknown"

    def _translate(self, text: str, src_lang: str, tgt_lang: str) -> str:
        try:
            import argostranslate.translate
            result = argostranslate.translate.translate(text, src_lang, tgt_lang)
            if result:
                return result
        except Exception as e:
            logger.error(f"Argos translation failed ({src_lang}→{tgt_lang}): {e}")

        return (
            f"[Translation unavailable: {src_lang}→{tgt_lang} package not installed.\n"
            f"Run: python -c \"import argostranslate.package; "
            f"argostranslate.package.update_package_index(); "
            f"pkgs = argostranslate.package.get_available_packages(); "
            f"pkg = next(p for p in pkgs if p.from_code=='{src_lang}' and p.to_code=='{tgt_lang}'); "
            f"argostranslate.package.install_from_path(pkg.download())\"]"
        )


if __name__ == "__main__":
    import sys
    import json
    logging.basicConfig(level=logging.INFO)
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Bonjour le monde"
    t = Translator()
    result = t.translate(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
