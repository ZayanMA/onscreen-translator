#!/usr/bin/env python3
"""
onscreen-translator: On-screen translator for Linux/Wayland.
Uses PaddleOCR + Argos Translate (fully offline, no API keys).

Flow:
  Super+T → takes ONE screenshot → shows region selector
  User drags region → crops SAME screenshot → OCR → translate → show card
  ⟳ Refresh button → takes ONE new screenshot → re-translate same region
  ✕ or Super+T again → dismiss card
"""
import sys
import os
import socket
import logging
import concurrent.futures
from urllib.parse import unquote

import dbus
import dbus.mainloop.glib

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

from onscreen_translator.config.settings import Settings
from onscreen_translator.ocr_translate.ocr import OCREngine
from onscreen_translator.ocr_translate.translator import Translator
from onscreen_translator.portal.screenshot import ScreenshotPortal
from onscreen_translator.overlay.live_overlay import LiveOverlay

SOCKET_PATH = "/tmp/onscreen-translator.sock"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    settings = Settings.load()
    session_bus = dbus.SessionBus()

    ocr_engine = OCREngine()
    translator = Translator()
    overlay = LiveOverlay()
    screenshot_portal = ScreenshotPortal(session_bus)

    thread_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="ost-worker"
    )

    # ── State ─────────────────────────────────────────────────────────────────
    _state = {
        "region":    None,   # (x, y, w, h) of the currently translated region
        "busy":      False,  # True while a screenshot request or OCR job is running
        "fail_count": 0,
        "_bg_path":  None,   # path to the most recent full-screen screenshot
    }

    # ── Core pipeline ─────────────────────────────────────────────────────────

    def _process(image_path: str) -> dict:
        """OCR + translate. Runs in a thread-pool worker."""
        text = ocr_engine.extract(image_path)
        logger.info(f"OCR text ({len(text)} chars):\n{text}")
        if not text.strip():
            return {
                "source_language": "?",
                "target_language": settings.target_language,
                "original": "",
                "translated": "(no text detected)",
            }
        result = translator.translate(text, target_lang=settings.target_language, settings=settings)
        result.setdefault("target_language", settings.target_language)
        logger.info(f"Translation ({result.get('source_language','?')} → {result.get('target_language','?')}):\n{result.get('translated','')}")
        return result

    def _crop_and_process(full_path: str, x: int, y: int, w: int, h: int) -> dict | None:
        """Crop full-screen screenshot to region, then OCR+translate. Runs in thread pool."""
        from PIL import Image
        try:
            img = Image.open(full_path)
        except Exception as e:
            logger.warning(f"Cannot open screenshot {full_path}: {e}")
            return None

        # Handle HiDPI: screenshot pixels may be 2× logical pixels
        from gi.repository import Gdk as _Gdk
        display = _Gdk.Display.get_default()
        monitors = display.get_monitors()
        screen_w = monitors.get_item(0).get_geometry().width if monitors.get_n_items() > 0 else 1920
        scale = img.width / screen_w
        sx, sy = int(x * scale), int(y * scale)
        sw, sh = int(w * scale), int(h * scale)

        try:
            crop = img.crop((sx, sy, sx + sw, sy + sh))
            out = "/tmp/ost_crop.png"
            crop.save(out)
            logger.debug(f"Cropped {full_path} → {out} (scale={scale:.2f})")
        except Exception as e:
            logger.warning(f"Crop failed: {e}")
            return None

        return _process(out)

    def _on_translate_done(future):
        """Called from thread pool when OCR+translate completes."""
        _state["busy"] = False
        try:
            result = future.result()
            _state["fail_count"] = 0
        except Exception:
            logger.exception("OCR/translate error")
            _state["fail_count"] += 1
            GLib.idle_add(lambda: overlay.show_status("OCR failed — click Refresh to retry.") or False)
            return

        if result is None:
            GLib.idle_add(lambda: overlay.show_status("Could not read screenshot.") or False)
            return

        GLib.idle_add(lambda: overlay.update_translation(
            result, show_original=settings.show_original) or False)

    def _translate_region(x: int, y: int, w: int, h: int, bg_path: str | None):
        """Submit a one-shot OCR+translate job for the given region of bg_path."""
        if _state["busy"]:
            logger.debug("translate_region: busy, skipping")
            return
        if not bg_path:
            GLib.idle_add(lambda: overlay.show_status("No screenshot available.") or False)
            return
        _state["busy"] = True
        _state["region"] = (x, y, w, h)
        GLib.idle_add(lambda: overlay.show_status("Recognising text…") or False)
        future = thread_pool.submit(_crop_and_process, bg_path, x, y, w, h)
        future.add_done_callback(_on_translate_done)

    # ── Refresh (on-demand, user-initiated) ───────────────────────────────────

    def _refresh_translation():
        """Take one new screenshot and re-translate the current region."""
        region = _state["region"]
        if region is None or _state["busy"]:
            return
        _state["busy"] = True   # hold lock during portal request
        x, y, w, h = region
        GLib.idle_add(lambda: overlay.show_status("Refreshing…") or False)

        def _on_refresh_screenshot(uri: str):
            bg_path = unquote(uri.removeprefix("file://"))
            _state["_bg_path"] = bg_path
            _state["busy"] = False   # release so _translate_region can re-acquire
            _translate_region(x, y, w, h, bg_path)

        sent = screenshot_portal.take_noninteractive(_on_refresh_screenshot)
        if not sent:
            _state["busy"] = False
            GLib.idle_add(lambda: overlay.show_status("Screenshot unavailable.") or False)

    # ── Trigger handler ────────────────────────────────────────────────────────

    def _on_trigger():
        if _state["region"] is not None:
            # Second press: dismiss card and reset
            logger.info("Trigger: dismissing card")
            overlay.stop()
            _state["region"] = None
            _state["busy"] = False
            return

        if _state["busy"]:
            logger.debug("Trigger: busy, ignoring")
            return

        logger.info("Trigger: capturing screenshot for selector…")
        _state["busy"] = True   # hold during portal request

        def on_region_selected(x: int, y: int, w: int, h: int):
            if w < 10 or h < 10:
                logger.info("Region too small or cancelled")
                return
            # Show card immediately, then kick off translation
            def _on_stop():
                _state["region"] = None
                _state["busy"] = False

            overlay.start_live(x, y, w, h, on_stop=_on_stop,
                               show_original=settings.show_original)
            overlay.set_refresh_callback(_refresh_translation)
            # Reuse the selector screenshot for the initial translation
            _state["busy"] = False   # release so _translate_region can acquire
            _translate_region(x, y, w, h, _state["_bg_path"])

        def _on_bg_screenshot(uri: str):
            bg_path = unquote(uri.removeprefix("file://"))
            _state["_bg_path"] = bg_path
            _state["busy"] = False   # release before showing selector
            logger.info(f"Background screenshot ready: {bg_path}")
            GLib.idle_add(
                lambda: overlay.start_selecting(on_region_selected, bg_path) or False
            )

        sent = screenshot_portal.take_noninteractive(_on_bg_screenshot)
        if not sent:
            _state["busy"] = False
            logger.warning("Background screenshot unavailable — showing selector without background")
            overlay.start_selecting(on_region_selected, None)

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

    # ── Background OCR init ────────────────────────────────────────────────────

    def _init_ocr():
        logger.info(f"Initializing PaddleOCR (lang={settings.ocr_language})…")
        try:
            ocr_engine.initialize(lang=settings.ocr_language)
            logger.info("PaddleOCR ready — press Super+T to start")
        except Exception:
            logger.exception("PaddleOCR init failed")

    thread_pool.submit(_init_ocr)

    # ── GTK app loop ───────────────────────────────────────────────────────────

    app = Gtk.Application(application_id="dev.zayan.onscreen-translator")

    def on_activate(app):
        app.hold()
        logger.info("onscreen-translator running — waiting for hotkey trigger")

    app.connect("activate", on_activate)

    try:
        sys.exit(app.run(sys.argv))
    except KeyboardInterrupt:
        thread_pool.shutdown(wait=False)
        sys.exit(0)


if __name__ == "__main__":
    main()
