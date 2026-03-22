import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gtk4LayerShell', '1.0')
from gi.repository import Gtk, Gtk4LayerShell as GtkLayerShell, GLib, Gdk, Pango
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CSS_PATH = Path(__file__).parent / "style.css"


class TranslationOverlay:
    """
    A GTK4 layer-shell overlay window that shows translation results
    floating above all other windows.
    """

    def __init__(self, settings):
        self.settings = settings
        self._dismiss_timer_id = None
        self._window = None
        self._original_label = None
        self._translated_label = None
        self._lang_label = None
        self._build()

    def _build(self):
        # Load CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_path(str(CSS_PATH))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self._window = Gtk.Window()
        self._window.set_decorated(False)
        self._window.set_resizable(False)

        # Configure as layer shell surface (overlay layer, above all app windows)
        GtkLayerShell.init_for_window(self._window)
        GtkLayerShell.set_layer(self._window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_namespace(self._window, "onscreen-translator")
        GtkLayerShell.set_keyboard_mode(self._window, GtkLayerShell.KeyboardMode.NONE)

        # Build content
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.add_css_class("translation-card")

        # Language badge row: badge left, close button right
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        self._lang_label = Gtk.Label(label="")
        self._lang_label.add_css_class("lang-badge")
        self._lang_label.set_halign(Gtk.Align.START)
        self._lang_label.set_hexpand(True)
        header_row.append(self._lang_label)

        # Close button so the user can dismiss early
        close_btn = Gtk.Button(label="✕")
        close_btn.add_css_class("close-btn")
        close_btn.set_valign(Gtk.Align.CENTER)
        close_btn.connect("clicked", lambda _: self.hide())
        header_row.append(close_btn)

        outer.append(header_row)

        # Original text (shown dimmed, smaller)
        self._original_label = Gtk.Label(label="")
        self._original_label.add_css_class("original-text")
        self._original_label.set_wrap(True)
        self._original_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._original_label.set_halign(Gtk.Align.START)
        self._original_label.set_xalign(0)
        self._original_label.set_selectable(True)
        outer.append(self._original_label)

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.add_css_class("card-sep")
        outer.append(sep)

        # Translated text (main, prominent)
        self._translated_label = Gtk.Label(label="")
        self._translated_label.add_css_class("translated-text")
        self._translated_label.set_wrap(True)
        self._translated_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._translated_label.set_halign(Gtk.Align.START)
        self._translated_label.set_xalign(0)
        self._translated_label.set_selectable(True)
        # Apply font sizes from settings via inline CSS
        self._apply_font_sizes()
        outer.append(self._translated_label)

        self._window.set_child(outer)

    def _apply_font_sizes(self):
        """Apply font-size overrides from settings via a secondary CSS provider."""
        dynamic_css = f"""
.translated-text {{ font-size: {self.settings.font_size_translated}px; }}
.original-text   {{ font-size: {self.settings.font_size_original}px; }}
"""
        provider = Gtk.CssProvider()
        provider.load_from_string(dynamic_css)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
            )

    def _set_anchors(self):
        """Anchor the window to top-right corner."""
        for edge in [GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.RIGHT]:
            GtkLayerShell.set_anchor(self._window, edge, True)
        for edge in [GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT]:
            GtkLayerShell.set_anchor(self._window, edge, False)
        GtkLayerShell.set_margin(self._window, GtkLayerShell.Edge.TOP, 48)
        GtkLayerShell.set_margin(self._window, GtkLayerShell.Edge.RIGHT, 24)

    def show_translation(self, result: dict):
        """
        Display the translation overlay.
        result keys: source_language, original, translated.
        """
        src_lang = result.get("source_language", "?")
        original = result.get("original", "")
        translated = result.get("translated", "")

        # Build a readable direction label, e.g. "JA → EN"
        target = self.settings.target_language.upper()
        self._lang_label.set_text(f"  {src_lang.upper()} → {target}  ")

        if self.settings.show_original and original:
            self._original_label.set_text(original)
            self._original_label.set_visible(True)
        else:
            self._original_label.set_visible(False)

        self._translated_label.set_text(translated)

        self._set_anchors()
        self._window.present()

        # Cancel any existing dismiss timer
        if self._dismiss_timer_id is not None:
            GLib.source_remove(self._dismiss_timer_id)
            self._dismiss_timer_id = None

        # Auto-dismiss
        secs = self.settings.auto_dismiss_seconds
        self._dismiss_timer_id = GLib.timeout_add_seconds(secs, self._dismiss)

    def show_error(self, message: str):
        """Show an error message in the overlay."""
        self.show_translation({
            "source_language": "err",
            "original": "",
            "translated": message,
        })

    def show_status(self, message: str):
        """Show a status message (e.g. 'Processing...')."""
        self._lang_label.set_text("  ···  ")
        self._original_label.set_visible(False)
        self._translated_label.set_text(message)
        self._set_anchors()
        self._window.present()
        # Status messages don't auto-dismiss — the real result will replace them.
        return GLib.SOURCE_REMOVE  # safe to use as idle_add callback

    def hide(self):
        self._dismiss()

    def _dismiss(self):
        if self._window:
            self._window.hide()
        self._dismiss_timer_id = None
        return GLib.SOURCE_REMOVE
