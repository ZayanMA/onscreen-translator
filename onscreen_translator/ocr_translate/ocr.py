import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OCREngine:
    """PaddleOCR wrapper. Keep one instance alive for the process lifetime."""

    def __init__(self):
        self._ocr = None

    def initialize(self, lang: str = "japan"):
        """Load models. Call in a background thread at startup."""
        import logging as _logging
        # Silence paddle's very verbose internal loggers
        for _name in ("ppocr", "paddle", "paddleocr", "paddlex"):
            _logging.getLogger(_name).setLevel(_logging.ERROR)

        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(lang=lang, enable_mkldnn=False)
        logger.info(f"PaddleOCR initialized (lang={lang})")

    def extract(self, image_path: str) -> str:
        """Extract all text from image. Returns newline-joined text lines."""
        if self._ocr is None:
            raise RuntimeError("OCREngine not initialized. Call initialize() first.")

        result = self._ocr.ocr(image_path)
        if not result:
            logger.debug("OCR returned empty/None result")
            return ""

        lines = []
        for page in result:
            if page is None:
                continue
            # PaddleOCR 3.x (PaddleX pipeline): page is a dict with rec_texts/rec_scores
            if isinstance(page, dict):
                texts = page.get("rec_texts", [])
                scores = page.get("rec_scores", [])
                for text, score in zip(texts, scores):
                    logger.debug(f"OCR item: text={text!r} score={score:.3f}")
                    if score > 0.3:
                        lines.append(text)
            else:
                # PaddleOCR 2.x: page is a list of [box, (text, score)]
                for item in page:
                    if hasattr(item, 'text'):
                        logger.debug(f"OCR item: text={item.text!r} score={item.score:.3f}")
                        if item.score > 0.3:
                            lines.append(item.text)
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        text_info = item[1]
                        if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                            text, score = text_info[0], text_info[1]
                            logger.debug(f"OCR item: text={text!r} score={score:.3f}")
                            if score > 0.3:
                                lines.append(text)

        return " ".join(lines)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    engine = OCREngine()
    engine.initialize()
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python -m onscreen_translator.ocr_translate.ocr <image_path>")
        sys.exit(1)
    text = engine.extract(path)
    print("Extracted text:")
    print(text)
