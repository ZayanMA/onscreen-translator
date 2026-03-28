#!/usr/bin/env python3
"""
onscreen-translator: Continuous live translation overlay for Linux/Wayland.
Uses PaddleOCR + DeepL API.

Flow:
  Super+T → start live mode
    Every 0.5s: read frame from PipeWire ScreenCast stream (silent, no screenshot sound)
    → OCR full screen → find Japanese text groups
    → translate each group in parallel (cached) → overlay translations at exact screen positions
  Super+T again → stop, hide overlay
"""
import sys
import os
import socket
import logging
import hashlib
import tempfile
import concurrent.futures

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gdk

from onscreen_translator.config.settings import Settings
from onscreen_translator.ocr_translate.ocr import OCREngine, cluster_groups
from onscreen_translator.ocr_translate.translator import Translator
from onscreen_translator.portal.screencast import ScreenCastPortal
from onscreen_translator.overlay.translation_overlay import TranslationOverlay

SOCKET_PATH = "/tmp/onscreen-translator.sock"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    settings = Settings.load()

    ocr_engine = OCREngine()
    translator = Translator()
    overlay = TranslationOverlay()
    screencast_portal = ScreenCastPortal()
    thread_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=4, thread_name_prefix="ost-worker"
    )

    # ── Live-mode state ───────────────────────────────────────────────────────
    _live = {
        "active":    False,
        "pending":   False,   # True while an OCR job is running
        "last_hash": None,    # MD5 of last processed frame bytes
    }

    # ── Frame processing (runs in thread pool) ────────────────────────────────

    def _get_screen_width() -> int:
        monitors = Gdk.Display.get_default().get_monitors()
        if monitors.get_n_items() > 0:
            return monitors.get_item(0).get_geometry().width
        return 1920

    def _process_frame(img):
        """OCR the frame, translate groups in parallel, update overlay."""
        try:
            frame_hash = hashlib.md5(img.tobytes()).hexdigest()
            if frame_hash == _live["last_hash"]:
                logger.debug("[live] frame unchanged, skipping OCR")
                _live["pending"] = False
                return

            _live["last_hash"] = frame_hash
            logger.info("[live] frame changed → running OCR")

            # Save to tmp file for PaddleOCR (requires a file path)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            img.save(tmp.name)
            tmp.close()
            path = tmp.name

            try:
                groups = ocr_engine.extract_japanese_groups(path)
            finally:
                try:
                    os.unlink(path)
                except Exception:
                    pass

            logger.info(f"[live] found {len(groups)} Japanese text group(s)")

            screen_w = _get_screen_width()
            scale = img.width / max(screen_w, 1)
            logger.info(f"[live] img={img.width}x{img.height} screen_w={screen_w} scale={scale:.3f}")

            for g in groups:
                logger.info(f"[live] group at ({g.x1},{g.y1})-({g.x2},{g.y2}) → overlay ({int(g.x1/scale)},{int(g.y1/scale)})")

            # Translate groups in parallel
            futures = {
                thread_pool.submit(translator.translate_group, group, settings): group
                for group in groups
            }
            results = []
            for fut, group in futures.items():
                try:
                    translated = fut.result(timeout=20)
                except Exception as e:
                    logger.warning(f"[live] translation failed: {e}")
                    translated = ""
                results.append((group, translated))

            _live["pending"] = False
            GLib.idle_add(lambda: overlay.update(results, scale) or False)

        except Exception as e:
            logger.warning(f"[live] _process_frame error: {e}")
            _live["pending"] = False

    # ── Capture tick (called by GLib timer) ───────────────────────────────────

    def _live_tick() -> bool:
        if not _live["active"]:
            return GLib.SOURCE_REMOVE
        if _live["pending"]:
            logger.debug("[live] previous frame still processing, skipping tick")
            return GLib.SOURCE_CONTINUE
        if not screencast_portal.is_ready():
            return GLib.SOURCE_CONTINUE

        img = screencast_portal.get_frame()
        if img is None:
            return GLib.SOURCE_CONTINUE

        _live["pending"] = True
        thread_pool.submit(_process_frame, img)
        return GLib.SOURCE_CONTINUE

    # ── Start / stop live mode ────────────────────────────────────────────────

    def _start_live():
        logger.info("Live translation mode: ON")
        _live["active"] = True
        _live["last_hash"] = None
        translator.clear_cache()
        ocr_engine.clear_region_cache()
        overlay.show()
        GLib.timeout_add(500, _live_tick)

    def _stop_live():
        logger.info("Live translation mode: OFF")
        _live["active"] = False
        _live["pending"] = False
        overlay.hide()
        translator.clear_cache()
        ocr_engine.clear_region_cache()

    # ── Trigger handler ────────────────────────────────────────────────────────

    def _on_trigger():
        if _live["active"]:
            _stop_live()
        else:
            _start_live()

    # ── Unix socket listener ───────────────────────────────────────────────────

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    trigger_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    trigger_sock.bind(SOCKET_PATH)
    trigger_sock.listen(1)
    trigger_sock.setblocking(False)

    def on_socket_ready(fd, condition):
        try:
            conn, _ = trigger_sock.accept()
            conn.close()
        except Exception:
            pass
        GLib.idle_add(_on_trigger)
        return GLib.SOURCE_CONTINUE

    GLib.io_add_watch(trigger_sock.fileno(), GLib.IO_IN, on_socket_ready)
    logger.info(f"Listening on {SOCKET_PATH}")

    # ── Background init (OCR + ScreenCast portal) ──────────────────────────────

    def _init_background():
        logger.info(f"Initializing PaddleOCR (lang={settings.ocr_language})…")
        try:
            ocr_engine.initialize(lang=settings.ocr_language)
            logger.info("PaddleOCR ready")
        except Exception:
            logger.exception("PaddleOCR init failed")

        logger.info("Starting ScreenCast portal session…")
        try:
            screencast_portal.setup()
        except Exception:
            logger.exception("ScreenCast portal setup failed")

        logger.info("Ready — press Super+T to start live translation")

    thread_pool.submit(_init_background)

    # ── GTK app loop ───────────────────────────────────────────────────────────

    app = Gtk.Application(application_id="dev.zayan.onscreen-translator")

    def on_activate(app):
        app.hold()
        logger.info("onscreen-translator running — press Super+T to toggle live translation")

    app.connect("activate", on_activate)

    try:
        sys.exit(app.run(sys.argv))
    except KeyboardInterrupt:
        thread_pool.shutdown(wait=False)
        sys.exit(0)


if __name__ == "__main__":
    main()
