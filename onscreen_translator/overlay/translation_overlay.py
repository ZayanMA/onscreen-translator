"""
translation_overlay.py — full-screen transparent overlay that positions
English translations directly on top of the detected Japanese text.

Uses GtkLayerShell when available (Wayland layer-shell protocol).
Falls back to a maximized transparent window on GNOME where layer-shell
is unavailable for regular apps.
"""
import logging
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib

logger = logging.getLogger(__name__)

# Try to load GtkLayerShell (installed as system package)
_HAVE_LAYER_SHELL = False
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    _HAVE_LAYER_SHELL = True
    logger.info("GtkLayerShell available — using layer-shell overlay")
except (ValueError, ImportError):
    logger.info("GtkLayerShell not available — using maximized window fallback")

_CSS = """
window.ost-live-overlay {
    background: transparent;
}
.ost-live-label {
    background: rgba(8, 8, 18, 0.88);
    color: rgba(240, 240, 255, 0.97);
    font-size: 14px;
    font-weight: 500;
    border-radius: 6px;
    padding: 4px 10px;
    border: 1px solid rgba(100, 160, 255, 0.25);
}
"""


class TranslationOverlay:
    """
    Transparent full-screen overlay that shows translated text labels
    positioned at the bounding box coordinates of the original Japanese text.
    """

    def __init__(self):
        self._labels: list = []
        self._visible = False

        css = Gtk.CssProvider()
        css.load_from_string(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self._window = Gtk.Window()
        self._window.set_decorated(False)
        self._window.set_title("ost-live-overlay")
        self._window.add_css_class("ost-live-overlay")

        # Gtk.Fixed lets us place labels at arbitrary (x, y) positions
        self._fixed = Gtk.Fixed()
        self._window.set_child(self._fixed)

        if _HAVE_LAYER_SHELL:
            self._setup_layer_shell()
        else:
            self._setup_fallback()

        # Make click-through after the window surface is created
        self._window.connect("realize", self._on_realize)

    def _setup_layer_shell(self):
        GtkLayerShell.init_for_window(self._window)
        GtkLayerShell.set_layer(self._window, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self._window, edge, True)
        # exclusive_zone = -1: don't push other windows away
        GtkLayerShell.set_exclusive_zone(self._window, -1)
        # No keyboard grab
        GtkLayerShell.set_keyboard_mode(self._window, GtkLayerShell.KeyboardMode.NONE)

    def _setup_fallback(self):
        """Maximized transparent window — not truly click-through, but functional."""
        self._window.maximize()

    def _on_realize(self, win):
        """Set empty input region so pointer events pass through to apps below."""
        try:
            import cairo
            surface = win.get_surface()
            if surface and hasattr(surface, "set_input_region"):
                surface.set_input_region(cairo.Region())
        except Exception as e:
            logger.debug(f"Could not set input region (non-fatal): {e}")

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self):
        self._visible = True
        self._window.present()

    def hide(self):
        self._visible = False
        self._clear_labels()
        self._window.hide()

    def update(self, groups_with_translations: list, scale: float):
        """
        Refresh overlay labels.

        groups_with_translations: list of (TextGroup, translated_str)
        scale: screenshot_width / screen_logical_width (for HiDPI conversion)
        """
        self._clear_labels()

        for group, translated in groups_with_translations:
            if not translated or translated.startswith("["):
                continue  # skip error/pending entries

            # Convert screenshot pixel coords → screen logical pixel coords
            sx = max(0, int(group.x1 / scale))
            sy = max(0, int(group.y1 / scale))

            lbl = Gtk.Label()
            lbl.set_text(translated)
            box_w = max(80, int((group.x2 - group.x1) / scale))
            lbl.set_wrap(True)
            lbl.set_size_request(box_w, -1)
            lbl.set_xalign(0)
            lbl.add_css_class("ost-live-label")

            self._fixed.put(lbl, sx, sy)
            self._labels.append(lbl)

        if self._visible:
            self._window.queue_draw()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _clear_labels(self):
        for lbl in self._labels:
            self._fixed.remove(lbl)
        self._labels.clear()
