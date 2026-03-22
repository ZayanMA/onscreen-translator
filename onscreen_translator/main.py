#!/usr/bin/env python3
"""
onscreen-translator: Live on-screen translator for Linux/Wayland.
Uses PaddleOCR + Argos Translate (fully offline, no API keys).
"""
import sys
import os
import socket
import logging
import concurrent.futures
import tempfile
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
    # DBus main loop must be set before any dbus usage
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

    # ── Live mode state ────────────────────────────────────────────────────────
    _state = {
        "live_region": None,   # (x, y, w, h) while live, None when stopped
        "live_timer": None,
        "last_text": "",
        "busy": False,         # True while an OCR job is in flight
    }

    def _process(image_path: str) -> dict:
        text = ocr_engine.extract(image_path)
        logger.info(f"OCR: {repr(text[:60])}")
        if not text.strip():
            return {
                "source_language": "?",
                "target_language": settings.target_language,
                "original": "",
                "translated": "(no text detected)",
            }
        result = translator.translate(text, target_lang=settings.target_language)
        result.setdefault("target_language", settings.target_language)
        return result

    # ── Live mode ──────────────────────────────────────────────────────────────

    def start_live_mode(x: int, y: int, w: int, h: int):
        _state["live_region"] = (x, y, w, h)
        _state["last_text"] = ""
        _state["busy"] = False

        def _on_stop():
            stop_live_mode()

        overlay.start_live(x, y, w, h, on_stop=_on_stop,
                           show_original=settings.show_original)
        overlay.show_status(f"Watching {w}×{h} region…")

        # Kick off first tick immediately (one-shot), then every 2 seconds
        GLib.idle_add(lambda: _live_tick() and False)
        _state["live_timer"] = GLib.timeout_add(2000, _live_tick)
        logger.info(f"Live mode started: region ({x},{y}) {w}×{h}")

    def stop_live_mode():
        timer = _state["live_timer"]
        if timer is not None:
            GLib.source_remove(timer)
            _state["live_timer"] = None
        _state["live_region"] = None
        _state["busy"] = False
        logger.info("Live mode stopped")

    def _live_tick():
        if _state["live_region"] is None:
            return GLib.SOURCE_REMOVE
        if _state["busy"]:
            return GLib.SOURCE_CONTINUE

        _state["busy"] = True
        logger.debug("Live tick: requesting screenshot")

        def _on_screenshot(uri: str):
            image_path = unquote(uri.removeprefix("file://"))
            region = _state["live_region"]
            if region is None:
                _state["busy"] = False
                return
            x, y, w, h = region
            cropped = _crop_screenshot(image_path, x, y, w, h)
            if cropped is None:
                _state["busy"] = False
                return
            future = thread_pool.submit(_process, cropped)
            future.add_done_callback(_on_live_done)

        screenshot_portal.take_noninteractive(_on_screenshot)
        return GLib.SOURCE_CONTINUE

    def _crop_screenshot(full_path: str, x: int, y: int, w: int, h: int) -> str | None:
        """Crop the full-screen PNG to the watched region. Returns temp file path."""
        try:
            from PIL import Image
            img = Image.open(full_path)
            # Account for HiDPI scaling: image may be larger than logical pixels
            scale = img.width / _get_screen_width()
            sx = int(x * scale)
            sy = int(y * scale)
            sw = int(w * scale)
            sh = int(h * scale)
            crop = img.crop((sx, sy, sx + sw, sy + sh))
            out = "/tmp/ost_live_crop.png"
            crop.save(out)
            return out
        except Exception as e:
            logger.warning(f"Crop failed: {e}")
            return None

    def _get_screen_width() -> int:
        import gi as _gi
        _gi.require_version('Gdk', '4.0')
        from gi.repository import Gdk as _Gdk
        display = _Gdk.Display.get_default()
        monitors = display.get_monitors()
        if monitors.get_n_items() > 0:
            return monitors.get_item(0).get_geometry().width
        return 1920

    def _on_live_done(future):
        _state["busy"] = False
        if _state["live_region"] is None:
            return
        try:
            result = future.result()
        except Exception:
            logger.exception("Live OCR error")
            return
        orig = result.get("original", "").strip()
        if orig and orig != _state["last_text"].strip():
            _state["last_text"] = orig
            GLib.idle_add(lambda: overlay.update_translation(
                result, show_original=settings.show_original) or False)

    # ── Trigger handler ────────────────────────────────────────────────────────

    def _on_trigger():
        if _state["live_region"] is not None:
            # Second press stops live mode (overlay.stop() fires the stop callback)
            logger.info("Trigger: stopping live mode")
            overlay.stop()
            return

        # Start region selection
        logger.info("Trigger: showing region selector")

        def on_region_selected(x: int, y: int, w: int, h: int):
            if w < 10 or h < 10:
                logger.info("Region too small or cancelled")
                return
            start_live_mode(x, y, w, h)

        overlay.start_selecting(on_region_selected)

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
        logger.info("Initializing PaddleOCR…")
        try:
            ocr_engine.initialize()
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
