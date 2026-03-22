import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OCREngine:
    """PaddleOCR wrapper. Keep one instance alive for the process lifetime."""

    def __init__(self):
        self._ocr = None

    def initialize(self):
        """Load models. Call in a background thread at startup."""
        import logging as _logging
        # Silence paddle's very verbose internal loggers
        for _name in ("ppocr", "paddle", "paddleocr", "paddlex"):
            _logging.getLogger(_name).setLevel(_logging.ERROR)

        from paddleocr import PaddleOCR
        # PaddleOCR 3.x API: lang='ch' covers multilingual (CJK + Latin scripts)
        self._ocr = PaddleOCR(lang='ch')
        logger.info("PaddleOCR initialized")

    def extract(self, image_path: str) -> str:
        """Extract all text from image. Returns newline-joined text lines."""
        if self._ocr is None:
            raise RuntimeError("OCREngine not initialized. Call initialize() first.")

        result = self._ocr.ocr(image_path)
        if not result:
            return ""

        lines = []
        for page in result:
            if page is None:
                continue
            for item in page:
                # PaddleOCR 3.x returns objects with .text and .score attributes
                # PaddleOCR 2.x returned [[box, (text, score)], ...]
                if hasattr(item, 'text'):
                    if item.score > 0.5:
                        lines.append(item.text)
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    text_info = item[1]
                    if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                        text, score = text_info[0], text_info[1]
                        if score > 0.5:
                            lines.append(text)

        return "\n".join(lines)


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
