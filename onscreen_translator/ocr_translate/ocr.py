import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

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


# Cache entry: (text, score, quality, miss_count)
#   quality    — _japanese_quality_score() at write time
#   miss_count — how many frames this low-quality entry has been reused
_LOW_QUALITY_THRESHOLD = 0.35
_LOW_QUALITY_RETRY_AFTER = 3  # re-process a weak cached region after N reuses

# Synthetic confidence for Manga OCR results (model returns no score)
_MANGA_OCR_SYNTHETIC_SCORE = 0.85


# ── Quality / filter helpers ──────────────────────────────────────────────────

def _is_jp_char(cp: int) -> bool:
    return (0x3040 <= cp <= 0x309F or   # hiragana
            0x30A0 <= cp <= 0x30FF or   # katakana
            0x4E00 <= cp <= 0x9FFF)     # CJK unified ideographs


def _is_japanese(text: str) -> bool:
    """True if text contains any hiragana, katakana, or CJK character."""
    return any(_is_jp_char(ord(ch)) for ch in text)


def _japanese_quality_score(text: str, score: float) -> float:
    """
    Score how likely this string is real Japanese text (0.0–1.0).

    Considers:
    - Ratio of Japanese script characters to total
    - Minimum character counts (rejects single-char noise)
    - Penalty for symbol/digit-heavy garbage
    - OCR confidence as a multiplier
    - Mild bonus for longer strings
    """
    if not text:
        return 0.0

    stripped = text.strip()
    total = len(stripped)
    if total == 0:
        return 0.0

    jp_count = 0
    symbol_count = 0
    for ch in stripped:
        cp = ord(ch)
        if _is_jp_char(cp):
            jp_count += 1
        elif not ch.isspace() and not ch.isalpha():
            symbol_count += 1

    if jp_count < 2:
        return 0.0

    garbage_ratio = symbol_count / total
    if garbage_ratio > 0.5:
        return 0.0

    jp_ratio = jp_count / total
    quality = jp_ratio * score
    if total >= 4:
        quality *= 1.1
    if total >= 8:
        quality *= 1.05

    return min(quality, 1.0)


def _is_valid_japanese(text: str, score: float, min_quality: float = 0.20) -> bool:
    """
    Stricter filter: requires meaningful JP char presence and rejects
    garbage strings that happen to contain a stray Japanese character.
    """
    return _japanese_quality_score(text, score) >= min_quality


# ── Refinement decision ───────────────────────────────────────────────────────

def _should_refine(tb: TextBox, img_h: int) -> bool:
    """
    Decide whether to run Manga OCR refinement on this box.

    Fires when ANY of:
    - Box height < 80 px      (catches dialogue lines, not just tiny text)
    - Wide horizontal text    (subtitle / dialogue bars, width > 3× height)
    - Lower 60 % of screen    (typical game dialogue zone)
    - Low first-pass confidence (< 0.70)
    """
    h = tb.y2 - tb.y1
    w = tb.x2 - tb.x1
    if h <= 0:
        return False
    if h < 80:
        return True
    if w > 3 * h:
        return True
    if img_h > 0 and tb.y1 > img_h * 0.60:
        return True
    if tb.score < 0.70:
        return True
    return False


# ── Group clustering ──────────────────────────────────────────────────────────

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
        horizontally_close = (box.x1 <= last.x2 + x_tolerance and
                               box.x2 >= last.x1 - x_tolerance)
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


# ── Manga OCR wrapper ─────────────────────────────────────────────────────────

class MangaOcrEngine:
    """
    Wraps kha-white/manga-ocr-base — a ViT+mBERT model trained specifically
    on Japanese manga/comic/game text.

    This is the same recognition engine used by YomiNinja and gives significantly
    better results on stylised game fonts than PaddleOCR PP-OCRv3.

    Loaded lazily on first use.  Install: pip install manga-ocr
    """

    def __init__(self):
        self._mocr = None
        self._load_attempted = False

    def _load(self):
        if self._load_attempted:
            return
        self._load_attempted = True
        try:
            from manga_ocr import MangaOcr
            logger.info("Loading Manga OCR model (kha-white/manga-ocr-base) …")
            self._mocr = MangaOcr()
            logger.info("Manga OCR model ready")
        except ImportError:
            logger.warning(
                "manga-ocr not installed — falling back to PaddleOCR crop refinement. "
                "For best quality: pip install manga-ocr"
            )
        except Exception as e:
            logger.warning(f"Manga OCR failed to load: {e}")

    @property
    def available(self) -> bool:
        return self._mocr is not None

    def recognize(self, pil_image: Image.Image) -> str:
        """
        Recognize Japanese text from a PIL Image crop.
        Returns empty string on failure.
        Upscales very small images so the model has enough detail.
        """
        if not self._load_attempted:
            self._load()
        if self._mocr is None:
            return ""
        try:
            w, h = pil_image.size
            if h < 32 and h > 0:
                scale = max(2, 32 // h)
                pil_image = pil_image.resize((w * scale, h * scale), Image.LANCZOS)
            return self._mocr(pil_image)
        except Exception as e:
            logger.debug(f"MangaOcr.recognize failed: {e}")
            return ""


# ── OCR Engine ────────────────────────────────────────────────────────────────

class OCREngine:
    """
    PaddleOCR-backed detection + Manga OCR recognition.

    Pipeline:
    1. Full-frame PaddleOCR predict() — acts as a region proposal / detector,
       giving bounding box coordinates and a first-pass text guess.
    2. Per-crop: quality-aware cache lookup.
    3. For boxes that need refinement: run Manga OCR on the crop.
       Manga OCR (kha-white/manga-ocr-base) is trained on Japanese manga/game
       text and handles stylised fonts far better than PP-OCRv3.
    4. If Manga OCR is unavailable: fall back to PaddleOCR 2×/3× crop variants.
    5. Group-level re-read for dialogue-like blocks with weak quality.
    """

    def __init__(self):
        self._ocr = None
        self._manga_ocr = MangaOcrEngine()
        # crop_hash → (text, score, quality, miss_count)
        self._region_cache: dict = {}

    def initialize(self, lang: str = "japan"):
        """Load models. Call in a background thread at startup."""
        import logging as _logging
        for _name in ("ppocr", "paddle", "paddleocr", "paddlex"):
            _logging.getLogger(_name).setLevel(_logging.ERROR)

        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(
            lang=lang,
            ocr_version="PP-OCRv3",       # mobile models — server models hang on this hardware
            enable_mkldnn=False,           # MKL-DNN crashes with PP-OCRv5
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        logger.info(f"PaddleOCR initialized (lang={lang})")

        # Pre-warm Manga OCR so the model is ready before the first live tick
        self._manga_ocr._load()

    def clear_region_cache(self):
        self._region_cache.clear()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _predict_array(self, arr) -> list:
        """Write array to a temp PNG, run PaddleOCR predict(), return list[TextBox]."""
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            Image.fromarray(arr).save(tmp.name)
            tmp.close()
            result = self._ocr.predict(tmp.name)
            return self._parse_predict_result(result)
        except Exception as e:
            logger.debug(f"_predict_array failed: {e}")
            return []
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    def _best_from_variants(self, crop_arr, tb_text: str, tb_score: float) -> tuple:
        """
        Return the best (text, score, quality) for a crop.

        Primary path — Manga OCR available:
          0. PaddleOCR baseline (from full-frame pass)
          1. Manga OCR on the raw crop
          → winner = higher _japanese_quality_score

        Fallback path — Manga OCR not installed:
          0. PaddleOCR baseline
          1. 2× RGB LANCZOS PaddleOCR re-predict
          2. 3× grayscale + autocontrast PaddleOCR re-predict (only if still weak)
        """
        ch, cw = crop_arr.shape[:2]
        pil_crop = Image.fromarray(crop_arr)

        # Baseline
        q0 = _japanese_quality_score(tb_text, tb_score)
        candidates = [(tb_text, tb_score, q0)]

        if self._manga_ocr.available:
            # Manga OCR path — higher quality for game/manga stylised fonts
            manga_text = self._manga_ocr.recognize(pil_crop)
            if manga_text:
                q_manga = _japanese_quality_score(manga_text, _MANGA_OCR_SYNTHETIC_SCORE)
                candidates.append((manga_text, _MANGA_OCR_SYNTHETIC_SCORE, q_manga))

        else:
            # PaddleOCR fallback variants
            # Variant 1: 2× RGB LANCZOS
            w2, h2 = max(cw * 2, 64), max(ch * 2, 64)
            arr2 = np.array(pil_crop.resize((w2, h2), Image.LANCZOS))
            boxes2 = self._predict_array(arr2)
            if boxes2:
                best2 = max(boxes2, key=lambda b: _japanese_quality_score(b.text, b.score))
                q2 = _japanese_quality_score(best2.text, best2.score)
                candidates.append((best2.text, best2.score, q2))
            else:
                q2 = 0.0

            # Variant 2: 3× grayscale + autocontrast (only when small or still weak)
            best_q = max(q0, q2)
            if ch < 50 or best_q < 0.40:
                pil_gray = ImageOps.autocontrast(pil_crop.convert("L"))
                w3, h3 = max(cw * 3, 96), max(ch * 3, 96)
                arr3 = np.array(pil_gray.resize((w3, h3), Image.LANCZOS).convert("RGB"))
                boxes3 = self._predict_array(arr3)
                if boxes3:
                    best3 = max(boxes3, key=lambda b: _japanese_quality_score(b.text, b.score))
                    q3 = _japanese_quality_score(best3.text, best3.score)
                    candidates.append((best3.text, best3.score, q3))

        return max(candidates, key=lambda c: c[2])

    # ── Main extraction ───────────────────────────────────────────────────────

    def extract_with_boxes(self, image_path: str) -> list:
        """
        Multi-stage pipeline:
          1. Full-frame PaddleOCR predict() → candidate bounding boxes
          2. Per-crop quality-aware cache lookup
          3. Refinement via Manga OCR (or PaddleOCR variants as fallback)
        Returns list[TextBox].
        """
        if self._ocr is None:
            raise RuntimeError("OCREngine not initialized. Call initialize() first.")

        # Stage 1: full-frame detect + first-pass recognition
        try:
            result = self._ocr.predict(image_path)
        except Exception as e:
            logger.warning(f"Full-frame predict failed: {e}")
            return []

        initial_boxes = self._parse_predict_result(result)
        if not initial_boxes:
            return []

        logger.debug(f"Full-frame predict: {len(initial_boxes)} candidate box(es)")

        full_img = Image.open(image_path).convert("RGB")
        full_arr = np.array(full_img)
        img_h, img_w = full_arr.shape[:2]

        boxes = []
        new_cache = {}
        pad = 4

        for tb in initial_boxes:
            cx1 = max(0, tb.x1 - pad)
            cy1 = max(0, tb.y1 - pad)
            cx2 = min(img_w, tb.x2 + pad)
            cy2 = min(img_h, tb.y2 + pad)

            crop = full_arr[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                continue

            crop_hash = hashlib.md5(crop.tobytes()).hexdigest()

            # Cache lookup
            if crop_hash in self._region_cache:
                entry = self._region_cache[crop_hash]
                # Gracefully handle old-format (text, score) 2-tuples
                if len(entry) == 2:
                    cached_text, cached_score = entry
                    cached_quality = _japanese_quality_score(cached_text, cached_score)
                    cached_miss = 0
                else:
                    cached_text, cached_score, cached_quality, cached_miss = entry

                if cached_quality >= _LOW_QUALITY_THRESHOLD:
                    # High-quality: serve from cache, reset miss counter
                    new_cache[crop_hash] = (cached_text, cached_score, cached_quality, 0)
                    if cached_text:
                        boxes.append(TextBox(text=cached_text, score=cached_score,
                                             x1=tb.x1, y1=tb.y1, x2=tb.x2, y2=tb.y2))
                    continue

                if cached_miss < _LOW_QUALITY_RETRY_AFTER:
                    # Low-quality but not yet time to retry
                    new_cache[crop_hash] = (cached_text, cached_score,
                                            cached_quality, cached_miss + 1)
                    if cached_text and cached_score >= 0.3:
                        boxes.append(TextBox(text=cached_text, score=cached_score,
                                             x1=tb.x1, y1=tb.y1, x2=tb.x2, y2=tb.y2))
                    continue
                # Low-quality and overdue — fall through to re-process

            # Process this crop (new or stale)
            if _should_refine(tb, img_h):
                text, score, quality = self._best_from_variants(crop, tb.text, tb.score)
            else:
                text = tb.text
                score = tb.score
                quality = _japanese_quality_score(text, score)

            new_cache[crop_hash] = (text, score, quality, 0)
            if score >= 0.3 and text:
                boxes.append(TextBox(text=text, score=score,
                                     x1=tb.x1, y1=tb.y1, x2=tb.x2, y2=tb.y2))

        self._region_cache = new_cache
        logger.debug(f"Returning {len(boxes)} box(es), cache={len(new_cache)}")
        return boxes

    def _reread_group_region(self, group: TextGroup, full_arr, img_h: int):
        """
        Re-read the entire bounding region of a dialogue-like group as a single
        padded crop, using Manga OCR (or PaddleOCR if unavailable).

        Only applied when:
        - The group looks like a dialogue block (wide, multi-line, or lower-screen)
        - Average per-line quality is still below 0.55

        Returns an improved TextGroup if quality increased, else None.
        """
        gw = group.x2 - group.x1
        gh = group.y2 - group.y1
        if gh <= 0 or gw <= 0:
            return None

        is_dialogue_like = (
            gw > gh * 2 or
            len(group.lines) >= 2 or
            (img_h > 0 and group.y1 > img_h * 0.50)
        )
        if not is_dialogue_like:
            return None

        # Skip if lines already look like high-quality Japanese
        orig_quality = sum(
            _japanese_quality_score(line, 0.75) for line in group.lines
        ) / max(len(group.lines), 1)
        if orig_quality >= 0.55:
            return None

        pad = max(8, gh // 4)
        arr_h, arr_w = full_arr.shape[:2]
        rx1 = max(0, group.x1 - pad)
        ry1 = max(0, group.y1 - pad)
        rx2 = min(arr_w, group.x2 + pad)
        ry2 = min(arr_h, group.y2 + pad)

        region = full_arr[ry1:ry2, rx1:rx2]
        if region.size == 0:
            return None

        rh, rw = region.shape[:2]
        pil_region = Image.fromarray(region)

        if self._manga_ocr.available:
            # Manga OCR handles the whole block in one shot
            manga_text = self._manga_ocr.recognize(pil_region)
            if not manga_text or not _is_valid_japanese(manga_text, _MANGA_OCR_SYNTHETIC_SCORE):
                return None
            new_quality = _japanese_quality_score(manga_text, _MANGA_OCR_SYNTHETIC_SCORE)
            if new_quality <= orig_quality:
                return None
            return TextGroup(
                lines=[manga_text],
                x1=group.x1, y1=group.y1,
                x2=group.x2, y2=group.y2,
            )

        else:
            # PaddleOCR fallback: upscale and re-predict the region
            scale = max(2, min(4, 96 // max(rh, 1)))
            arr_up = np.array(pil_region.resize((rw * scale, rh * scale), Image.LANCZOS))
            boxes = self._predict_array(arr_up)
            if not boxes:
                return None
            jp_boxes = [b for b in boxes if _is_valid_japanese(b.text, b.score)]
            if not jp_boxes:
                return None
            new_quality = sum(
                _japanese_quality_score(b.text, b.score) for b in jp_boxes
            ) / len(jp_boxes)
            if new_quality <= orig_quality:
                return None
            sorted_boxes = sorted(jp_boxes, key=lambda b: (b.y1, b.x1))
            return TextGroup(
                lines=[b.text for b in sorted_boxes],
                x1=group.x1, y1=group.y1,
                x2=group.x2, y2=group.y2,
            )

    def _parse_predict_result(self, result) -> list:
        """Parse predict() output → list[TextBox]."""
        if not result:
            return []
        boxes = []
        for page in result:
            if page is None:
                continue
            if isinstance(page, dict):
                texts  = page.get("rec_texts", [])
                scores = page.get("rec_scores", [])
                polys  = page.get("rec_polys", [])
                for text, score, poly in zip(texts, scores, polys):
                    if score < 0.3:
                        continue
                    try:
                        poly_arr = np.array(poly)
                        xs, ys = poly_arr[:, 0], poly_arr[:, 1]
                        boxes.append(TextBox(
                            text=text, score=score,
                            x1=int(xs.min()), y1=int(ys.min()),
                            x2=int(xs.max()), y2=int(ys.max()),
                        ))
                    except Exception:
                        boxes.append(TextBox(text=text, score=score,
                                             x1=0, y1=0, x2=0, y2=0))
            else:
                for item in page:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        text_info = item[1]
                        if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                            text, score = text_info[0], text_info[1]
                            if score >= 0.3:
                                boxes.append(TextBox(text, score, 0, 0, 0, 0))
        return boxes

    def extract_japanese_groups(self, image_path: str) -> list:
        """Extract Japanese text from image, cluster into spatial groups."""
        boxes = self.extract_with_boxes(image_path)
        boxes = [b for b in boxes if _is_valid_japanese(b.text, b.score)]
        groups = cluster_groups(boxes)

        # Group-level re-read for dialogue-like blocks with weak quality
        full_arr = np.array(Image.open(image_path).convert("RGB"))
        img_h = full_arr.shape[0]

        improved = []
        for g in groups:
            better = self._reread_group_region(g, full_arr, img_h)
            improved.append(better if better is not None else g)

        return improved


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
