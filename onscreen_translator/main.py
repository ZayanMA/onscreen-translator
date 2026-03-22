#!/usr/bin/env python3
"""
onscreen-translator: Live on-screen translator for Linux/Wayland.
Uses PaddleOCR + Argos Translate (fully offline, no API keys).
"""
import sys
import logging
import concurrent.futures
import dbus
import dbus.mainloop.glib

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

from onscreen_translator.config.settings import Settings
from onscreen_translator.ocr_translate.ocr import OCREngine
from onscreen_translator.ocr_translate.translator import Translator
from onscreen_translator.portal.screenshot import ScreenshotPortal
from onscreen_translator.portal.shortcuts import ShortcutsPortal
from onscreen_translator.overlay.window import TranslationOverlay

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

    # Initialize component objects
    ocr_engine = OCREngine()
    translator = Translator()
    overlay = TranslationOverlay(settings)
    screenshot_portal = ScreenshotPortal(session_bus)

    # Thread pool for CPU-bound OCR + translation (keeps GLib loop responsive)
    thread_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="ost-worker"
    )

    def on_shortcut_activated(shortcut_id: str):
        """Called from the GLib main loop when the registered hotkey fires."""
        if shortcut_id != "translate":
            return
        logger.info("Hotkey triggered — opening region picker")
        GLib.idle_add(overlay.show_status, "Select region…")
        screenshot_portal.take_interactive(on_screenshot_taken)

    def on_screenshot_taken(uri: str):
        """Called (from GLib loop, via DBus signal) when the portal returns a URI."""
        image_path = uri.removeprefix("file://")
        logger.info(f"Screenshot at: {image_path}")
        GLib.idle_add(overlay.show_status, "Recognizing text…")

        future = thread_pool.submit(_process, image_path)
        future.add_done_callback(_on_done)

    def _process(image_path: str) -> dict:
        """Heavy lifting — runs in a worker thread."""
        text = ocr_engine.extract(image_path)
        logger.info(f"OCR extracted: {repr(text[:80])}")
        if not text.strip():
            return {
                "source_language": "?",
                "original": "",
                "translated": "(no text detected)",
            }
        return translator.translate(text, target_lang=settings.target_language)

    def _on_done(future: concurrent.futures.Future):
        """Done callback — bridges the worker thread back to the GLib main loop."""
        try:
            result = future.result()
        except Exception as e:
            logger.exception("Processing error")
            GLib.idle_add(overlay.show_error, f"Error: {e}")
            return
        GLib.idle_add(overlay.show_translation, result)

    # Eagerly initialize PaddleOCR in background so the first hotkey press is fast
    def _init_ocr():
        logger.info("Initializing PaddleOCR models…")
        try:
            ocr_engine.initialize()
            logger.info("PaddleOCR ready")
        except Exception:
            logger.exception(
                "PaddleOCR failed to initialize — OCR will not work. "
                "Check that paddleocr and paddlepaddle are installed."
            )

    thread_pool.submit(_init_ocr)

    # Register global hotkey via xdg-desktop-portal
    shortcuts_portal = ShortcutsPortal(session_bus)
    shortcuts_portal.register(
        shortcut_id="translate",
        description="Translate text on screen",
        preferred_trigger=settings.preferred_trigger,
        callback=on_shortcut_activated,
    )
    logger.info(f"Registered hotkey: {settings.preferred_trigger}")

    # GTK application — drives the GLib/DBus event loop
    app = Gtk.Application(application_id="dev.zayan.onscreen-translator")

    def on_activate(app):
        # Hold the application alive without showing a main window
        app.hold()
        logger.info(
            "onscreen-translator running. "
            f"Press {settings.preferred_trigger} to translate."
        )

    app.connect("activate", on_activate)

    try:
        sys.exit(app.run(sys.argv))
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down.")
        thread_pool.shutdown(wait=False)
        sys.exit(0)


if __name__ == "__main__":
    main()
