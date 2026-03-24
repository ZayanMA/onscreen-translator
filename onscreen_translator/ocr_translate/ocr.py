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


def cluster_groups(boxes: list, gap_factor: float = 0.5) -> list:
    """
    Group spatially-adjacent TextBoxes into TextGroups (logical paragraphs).
    Two boxes are merged when they are both:
      - Vertically close: next box starts within gap_factor * box_height below the last
      - Horizontally close: X ranges overlap or are within 2 * box_height of each other

    Each group's lines are sorted by reading direction:
      - Horizontal text (width > height): top-to-bottom, left-to-right
      - Vertical text (height > width): right-to-left columns (Japanese tategumi)

    Returns list[TextGroup].
    """
    if not boxes:
        return []
    sorted_boxes = sorted(boxes, key=lambda b: b.y1)
    groups = [[sorted_boxes[0]]]
    for box in sorted_boxes[1:]:
        last = groups[-1][-1]
        avg_h = max(last.y2 - last.y1, 1)
        vertically_close = box.y1 <= last.y2 + avg_h * gap_factor
        x_tolerance = avg_h * 2
        horizontally_close = box.x1 <= last.x2 + x_tolerance and box.x2 >= last.x1 - x_tolerance
        if vertically_close and horizontally_close:
            groups[-1].append(box)
        else:
            groups.append([box])

    result = []
    for g in groups:
        n_vertical = sum(1 for b in g if (b.y2 - b.y1) > (b.x2 - b.x1))
        if n_vertical > len(g) / 2:
            # Vertical (tategumi): columns read right-to-left
            g_ordered = sorted(g, key=lambda b: -b.x1)
        else:
            # Horizontal (yokogumi): top-to-bottom, left-to-right
            g_ordered = sorted(g, key=lambda b: (b.y1, b.x1))
        result.append(TextGroup(
            lines=[b.text for b in g_ordered],
            x1=min(b.x1 for b in g),
            y1=min(b.y1 for b in g),
            x2=max(b.x2 for b in g),
            y2=max(b.y2 for b in g),
        ))
    return result


# ── OCR Engine ────────────────────────────────────────────────────────────────

class OCREngine:
    """
    EasyOCR-backed OCR engine for Japanese text.
    Uses CRAFT detection + CRNN recognition — better than PaddleOCR mobile for
    stylised game/visual-novel fonts. No preprocessing required.
    """

    def __init__(self):
        self._reader = None  # easyocr.Reader instance

    def initialize(self, lang: str = "japan"):
        """Load models. Call in a background thread at startup."""
        import easyocr
        # EasyOCR language code for Japanese is 'ja'
        # gpu=False: safe CPU-only default (set to True if CUDA is available)
        # Models (~200 MB) are downloaded automatically on first use
        self._reader = easyocr.Reader(['ja'], gpu=False, verbose=False)
        logger.info("EasyOCR initialized (lang=ja)")

    def extract_with_boxes(self, image_path: str) -> list:
        """Run OCR on image_path. Returns list[TextBox]."""
        if self._reader is None:
            raise RuntimeError("OCREngine not initialized. Call initialize() first.")
        # detail=1 → returns (bbox, text, confidence)
        # paragraph=False → one result per detected text line
        results = self._reader.readtext(image_path, detail=1, paragraph=False)
        boxes = []
        for bbox, text, score in results:
            if score < 0.3:
                continue
            # bbox = [[x1,y1],[x2,y1],[x2,y2],[x1,y2]] (quadrilateral)
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            boxes.append(TextBox(
                text=text, score=score,
                x1=int(min(xs)), y1=int(min(ys)),
                x2=int(max(xs)), y2=int(max(ys)),
            ))
        return boxes

    def extract_japanese_groups(self, image_path: str) -> list:
        """
        Extract Japanese text from image, cluster into spatial groups.
        Returns list[TextGroup].
        """
        boxes = self.extract_with_boxes(image_path)
        boxes = [b for b in boxes if _is_japanese(b.text)]
        return cluster_groups(boxes)


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
