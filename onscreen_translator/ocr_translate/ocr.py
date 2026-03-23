import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TextBox:
    text: str
    score: float
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class TextGroup:
    """A cluster of spatially adjacent text boxes forming one logical block."""
    lines: list  # list[str] — OCR lines in reading order
    x1: int
    y1: int
    x2: int
    y2: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_japanese(text: str) -> bool:
    """True if text contains hiragana, katakana, or CJK characters."""
    for ch in text:
        cp = ord(ch)
        if (0x3040 <= cp <= 0x309F or   # hiragana
                0x30A0 <= cp <= 0x30FF or   # katakana
                0x4E00 <= cp <= 0x9FFF):    # CJK unified ideographs
            return True
    return False


def cluster_groups(boxes: list, gap_factor: float = 1.8) -> list:
    """
    Group vertically-adjacent TextBoxes into TextGroups (logical paragraphs).
    Boxes within gap_factor * avg_box_height of each other are merged.
    Returns list[TextGroup].
    """
    if not boxes:
        return []
    sorted_boxes = sorted(boxes, key=lambda b: b.y1)
    groups = [[sorted_boxes[0]]]
    for box in sorted_boxes[1:]:
        last = groups[-1][-1]
        avg_h = max(last.y2 - last.y1, 1)
        if box.y1 <= last.y2 + avg_h * gap_factor:
            groups[-1].append(box)
        else:
            groups.append([box])
    result = []
    for g in groups:
        result.append(TextGroup(
            lines=[b.text for b in g],
            x1=min(b.x1 for b in g),
            y1=min(b.y1 for b in g),
            x2=max(b.x2 for b in g),
            y2=max(b.y2 for b in g),
        ))
    return result


# ── OCR Engine ────────────────────────────────────────────────────────────────

class OCREngine:
    """PaddleOCR wrapper. Keep one instance alive for the process lifetime."""

    def __init__(self):
        self._ocr = None

    def initialize(self, lang: str = "japan"):
        """Load models. Call in a background thread at startup."""
        import logging as _logging
        for _name in ("ppocr", "paddle", "paddleocr", "paddlex"):
            _logging.getLogger(_name).setLevel(_logging.ERROR)

        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(lang=lang, enable_mkldnn=False)
        logger.info(f"PaddleOCR initialized (lang={lang})")

    def extract(self, image_path: str) -> str:
        """Extract all text from image. Returns space-joined text lines."""
        boxes = self.extract_with_boxes(image_path)
        return " ".join(b.text for b in boxes)

    def extract_with_boxes(self, image_path: str) -> list:
        """
        Run OCR on image_path. Returns list[TextBox] for all detected text,
        regardless of language (caller filters as needed).
        """
        if self._ocr is None:
            raise RuntimeError("OCREngine not initialized. Call initialize() first.")

        result = self._ocr.ocr(image_path)
        if not result:
            return []

        boxes = []
        for page in result:
            if page is None:
                continue
            if isinstance(page, dict):
                # PaddleOCR 3.x / PaddleX pipeline
                texts = page.get("rec_texts", [])
                scores = page.get("rec_scores", [])
                polys = page.get("rec_polys", [])
                for text, score, poly in zip(texts, scores, polys):
                    if score < 0.3:
                        continue
                    try:
                        import numpy as np
                        poly = np.array(poly)
                        xs, ys = poly[:, 0], poly[:, 1]
                        boxes.append(TextBox(
                            text=text, score=score,
                            x1=int(xs.min()), y1=int(ys.min()),
                            x2=int(xs.max()), y2=int(ys.max()),
                        ))
                    except Exception:
                        boxes.append(TextBox(text=text, score=score,
                                             x1=0, y1=0, x2=0, y2=0))
            else:
                # PaddleOCR 2.x: list of [box, (text, score)]
                for item in page:
                    if hasattr(item, 'text'):
                        if item.score >= 0.3:
                            boxes.append(TextBox(item.text, item.score,
                                                 0, 0, 0, 0))
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        text_info = item[1]
                        if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                            text, score = text_info[0], text_info[1]
                            if score >= 0.3:
                                boxes.append(TextBox(text, score, 0, 0, 0, 0))
        return boxes

    def extract_japanese_groups(self, image_path: str) -> list:
        """
        Extract Japanese text from image, cluster into spatial groups.
        Returns list[TextGroup] — each group is a logical block of text
        (e.g. one speech bubble, one subtitle line).
        """
        boxes = self.extract_with_boxes(image_path)
        japanese = [b for b in boxes if _is_japanese(b.text)]
        return cluster_groups(japanese)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    engine = OCREngine()
    engine.initialize()
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python -m onscreen_translator.ocr_translate.ocr <image_path>")
        sys.exit(1)
    groups = engine.extract_japanese_groups(path)
    for i, g in enumerate(groups, 1):
        print(f"Group {i} at ({g.x1},{g.y1})-({g.x2},{g.y2}):")
        for line in g.lines:
            print(f"  {line}")
