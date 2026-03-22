import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OCREngine:
    """PaddleOCR wrapper. Keep one instance alive for the process lifetime."""

    def __init__(self):
        self._ocr = None

    def initialize(self):
        """Load models. Call in a background thread at startup."""
        from paddleocr import PaddleOCR
        # lang='ch' enables multilingual (Latin + CJK). use_angle_cls handles rotated text.
        self._ocr = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)
        logger.info("PaddleOCR initialized")

    def extract(self, image_path: str) -> str:
        """Extract all text from image. Returns newline-joined text lines."""
        if self._ocr is None:
            raise RuntimeError("OCREngine not initialized. Call initialize() first.")

        result = self._ocr.ocr(image_path, cls=True)
        if not result or not result[0]:
            return ""

        lines = []
        for block in result:
            if block is None:
                continue
            for line in block:
                text, confidence = line[1]
                if confidence > 0.5:
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
