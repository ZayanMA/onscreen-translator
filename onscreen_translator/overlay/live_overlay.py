"""
live_overlay.py — overlay for region selection + live translation card.

States:
  idle      — window hidden
  selecting — maximized window showing a frozen screenshot background + dark
               mask; user drags to pick region (no transparency required)
  live      — small floating card window showing translation output
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Gdk, GLib, Pango, GdkPixbuf

import cairo
import math
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_CARD_CSS = """
window.ost-overlay {
    background: transparent;
}
.ost-card {
    background: rgba(12, 12, 22, 0.96);
    border-radius: 16px;
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.15);
    box-shadow: 0 12px 40px rgba(0,0,0,0.65);
    min-width: 260px;
}
.ost-handle {
    padding: 10px 16px 6px 16px;
    border-radius: 16px 16px 0 0;
}
.ost-handle:hover {
    background: rgba(255, 255, 255, 0.04);
}
.ost-badge {
    color: rgba(100, 180, 255, 0.9);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.5px;
    background: rgba(100, 180, 255, 0.13);
    border-radius: 5px;
    padding: 3px 8px;
}
.ost-content {
    padding: 4px 16px 14px 16px;
}
.ost-original {
    color: rgba(160, 160, 185, 0.75);
    font-size: 12px;
    font-style: italic;
    margin-bottom: 8px;
}
.ost-translated {
    color: rgba(240, 240, 255, 0.97);
    font-size: 16px;
    font-weight: 500;
}
.ost-sep {
    margin: 0 0 8px 0;
    background: rgba(255, 255, 255, 0.08);
}
.ost-refresh-btn {
    background: rgba(60, 120, 220, 0.18);
    border: 1px solid rgba(60, 120, 220, 0.4);
    color: rgba(120, 180, 255, 0.9);
    font-size: 12px;
    font-weight: 700;
    padding: 5px 14px;
    border-radius: 6px;
    min-width: 0;
    min-height: 28px;
}
.ost-refresh-btn:hover { background: rgba(60, 120, 220, 0.38); }
.ost-close-btn {
    background: transparent;
    border: none;
    color: rgba(160, 160, 185, 0.6);
    font-size: 14px;
    padding: 5px 10px;
    border-radius: 6px;
    min-width: 0;
    min-height: 28px;
}
.ost-close-btn:hover {
    color: rgba(240, 240, 255, 0.9);
    background: rgba(255, 255, 255, 0.10);
}
.ost-toggle-btn {
    background: transparent;
    border: none;
    color: rgba(160, 160, 185, 0.55);
    font-size: 12px;
    padding: 5px 8px;
    border-radius: 6px;
    min-width: 0;
    min-height: 28px;
}
.ost-toggle-btn:hover {
    color: rgba(200, 200, 220, 0.85);
    background: rgba(255, 255, 255, 0.07);
}
.ost-toggle-btn.active {
    color: rgba(120, 180, 255, 0.85);
}
"""

_CARD_W = 620


class LiveOverlay:
    """
    Single long-lived GTK4 window for region selection + live translation card.

    SELECTING: maximized, shows a frozen screenshot as background so the user
               can see their screen while drawing a selection rectangle.
    LIVE:      small floating card; no full-screen overlay needed.
    """

    def __init__(self):
        self._state = "idle"
        self._region: Optional[tuple] = None

        # Background screenshot pixbuf (for SELECTING state)
        self._bg_pixbuf: Optional[GdkPixbuf.Pixbuf] = None

        # Drag state
        self._start_x = 0.0
        self._start_y = 0.0
        self._cur_x = 0.0
        self._cur_y = 0.0
        self._dragging = False

        # Callbacks
        self._on_region_cb: Optional[Callable] = None
        self._on_stop_cb: Optional[Callable] = None
        self._refresh_cb: Optional[Callable] = None

        # Original text visibility (toggled by button in card header)
        self._show_original: bool = False

        # Widgets (set in _build)
        self._window: Optional[Gtk.Window] = None
        self._draw_area: Optional[Gtk.DrawingArea] = None
        self._fixed: Optional[Gtk.Fixed] = None
        self._card: Optional[Gtk.Box] = None
        self._lang_label: Optional[Gtk.Label] = None
        self._original_label: Optional[Gtk.Label] = None
        self._translated_label: Optional[Gtk.Label] = None
        self._toggle_btn: Optional[Gtk.Button] = None
        self._last_result: Optional[dict] = None

        self._build()

    # ── Build ────────────────────────────────────────────────────────────────

    def _build(self):
        css = Gtk.CssProvider()
        css.load_from_string(_CARD_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        win = Gtk.Window()
        win.set_decorated(False)
        win.set_title("onscreen-translator")
        win.add_css_class("ost-overlay")

        gtk_overlay = Gtk.Overlay()

        self._draw_area = Gtk.DrawingArea()
        self._draw_area.set_draw_func(self._draw)
        gtk_overlay.set_child(self._draw_area)

        self._fixed = Gtk.Fixed()
        self._fixed.set_can_target(False)  # start pass-through (SELECTING default)
        gtk_overlay.add_overlay(self._fixed)
        gtk_overlay.set_measure_overlay(self._fixed, False)

        self._card = self._build_card()

        # Drag gesture for rubber-band selection
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self._draw_area.add_controller(drag)

        # Escape key
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        win.add_controller(key)

        win.set_child(gtk_overlay)
        self._window = win

    def _build_card(self) -> Gtk.Box:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.add_css_class("ost-card")

        # ── Header (wrapped in WindowHandle so dragging it moves the window) ──
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._lang_label = Gtk.Label(label="  ···  ")
        self._lang_label.add_css_class("ost-badge")
        self._lang_label.set_halign(Gtk.Align.START)
        self._lang_label.set_hexpand(True)
        header.append(self._lang_label)

        self._toggle_btn = Gtk.Button(label="原")
        self._toggle_btn.add_css_class("ost-toggle-btn")
        self._toggle_btn.set_valign(Gtk.Align.CENTER)
        self._toggle_btn.set_tooltip_text("Show/hide original text")
        self._toggle_btn.connect("clicked", self._on_toggle_original)
        header.append(self._toggle_btn)

        self._refresh_btn = Gtk.Button(label="⟳ Refresh")
        self._refresh_btn.add_css_class("ost-refresh-btn")
        self._refresh_btn.set_valign(Gtk.Align.CENTER)
        self._refresh_btn.connect("clicked", self._on_refresh_clicked)
        header.append(self._refresh_btn)

        close_btn = Gtk.Button(label="✕")
        close_btn.add_css_class("ost-close-btn")
        close_btn.set_valign(Gtk.Align.CENTER)
        close_btn.connect("clicked", lambda _: self.stop())
        header.append(close_btn)

        handle = Gtk.WindowHandle()
        handle.set_child(header)
        handle.add_css_class("ost-handle")
        outer.append(handle)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.add_css_class("ost-sep")
        outer.append(sep)

        # ── Scrollable content area ───────────────────────────────────────────
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(360)
        scroll.set_propagate_natural_height(True)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.add_css_class("ost-content")

        self._original_label = Gtk.Label(label="")
        self._original_label.add_css_class("ost-original")
        self._original_label.set_wrap(True)
        self._original_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._original_label.set_halign(Gtk.Align.START)
        self._original_label.set_xalign(0)
        self._original_label.set_selectable(True)
        self._original_label.set_visible(False)
        content_box.append(self._original_label)

        self._translated_label = Gtk.Label(label="")
        self._translated_label.add_css_class("ost-translated")
        self._translated_label.set_wrap(True)
        self._translated_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._translated_label.set_halign(Gtk.Align.START)
        self._translated_label.set_xalign(0)
        self._translated_label.set_selectable(True)
        content_box.append(self._translated_label)

        scroll.set_child(content_box)
        outer.append(scroll)

        return outer

    # ── Public API ───────────────────────────────────────────────────────────

    def start_selecting(self, on_region: Callable[[int, int, int, int], None],
                        bg_path: Optional[str] = None):
        """
        Show the region-selection overlay.
        bg_path: path to a full-screen screenshot to use as the visible background.
        on_region(x, y, w, h) is called when the user finishes dragging.
        """
        if bg_path:
            try:
                self._bg_pixbuf = GdkPixbuf.Pixbuf.new_from_file(bg_path)
            except Exception as e:
                logger.warning(f"Could not load background screenshot: {e}")
                self._bg_pixbuf = None
        else:
            self._bg_pixbuf = None

        self._state = "selecting"
        self._on_region_cb = on_region
        self._dragging = False
        self._card.set_visible(False)
        self._fixed.set_can_target(False)   # events pass through to DrawingArea
        self._draw_area.set_can_target(True)
        self._draw_area.set_size_request(-1, -1)
        self._window.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        self._window.maximize()
        self._window.present()
        self._draw_area.queue_draw()

    def start_live(self, x: int, y: int, w: int, h: int,
                   on_stop: Callable, show_original: bool = False):
        """
        Enter LIVE state — show a small floating translation card.
        """
        self._state = "live"
        self._region = (x, y, w, h)
        self._on_stop_cb = on_stop
        self._show_original = show_original
        self._last_result = None
        self._bg_pixbuf = None   # free screenshot memory
        # Sync toggle button visual state
        if self._toggle_btn:
            if show_original:
                self._toggle_btn.add_css_class("active")
            else:
                self._toggle_btn.remove_css_class("active")

        # Place card at origin of the small window
        if self._card.get_parent() is not None:
            self._fixed.remove(self._card)
        self._fixed.put(self._card, 0, 0)
        self._card.set_visible(True)

        # Allow clicks to reach card buttons
        self._fixed.set_can_target(True)
        self._draw_area.set_can_target(False)

        # Auto height — card grows with content, scrolls if too tall
        self._draw_area.set_size_request(_CARD_W, -1)
        self._window.set_resizable(True)
        self._window.set_cursor(None)
        self._window.unfullscreen()
        self._window.unmaximize()
        self._window.present()
        self._draw_area.queue_draw()

    def update_translation(self, result: dict, show_original: bool = False):
        """Update card content with a translation result."""
        self._last_result = result
        src = result.get("source_language", "?")
        original = result.get("original", "")
        translated = result.get("translated", "")
        tgt = result.get("target_language", "EN")

        self._lang_label.set_text(f"  {src.upper()} → {tgt.upper()}  ")

        if self._show_original and original:
            self._original_label.set_text(original)
            self._original_label.set_visible(True)
        else:
            self._original_label.set_visible(False)

        self._translated_label.set_text(translated)

    def show_status(self, msg: str):
        """Show a status/loading message in the card."""
        if self._lang_label:
            self._lang_label.set_text("  ···  ")
        if self._original_label:
            self._original_label.set_visible(False)
        if self._translated_label:
            self._translated_label.set_text(msg)

    def set_refresh_callback(self, cb: Optional[Callable]):
        """Wire the ⟳ Refresh button. Pass None to clear."""
        self._refresh_cb = cb

    def stop(self):
        """Return to IDLE — hide window and fire stop callback."""
        self._state = "idle"
        self._region = None
        self._dragging = False
        self._bg_pixbuf = None
        self._refresh_cb = None
        if self._card:
            self._card.set_visible(False)
        cb = self._on_stop_cb
        self._on_stop_cb = None
        self._on_region_cb = None
        if self._window:
            self._window.hide()
        if cb:
            GLib.idle_add(lambda: cb() or False)

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_toggle_original(self, btn):
        self._show_original = not self._show_original
        if self._show_original:
            self._toggle_btn.add_css_class("active")
        else:
            self._toggle_btn.remove_css_class("active")
        # Re-render current result with updated visibility
        if self._last_result:
            self.update_translation(self._last_result)

    def _on_refresh_clicked(self, btn):
        if self._refresh_cb:
            self._refresh_cb()

    def _on_key(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            if self._state == "selecting":
                self._cancel_selection()
            elif self._state == "live":
                self.stop()
        return False

    # ── Drag gesture (SELECTING state) ───────────────────────────────────────

    def _drag_begin(self, gesture, x, y):
        if self._state != "selecting":
            return
        self._start_x = x
        self._start_y = y
        self._cur_x = x
        self._cur_y = y
        self._dragging = True
        self._draw_area.queue_draw()

    def _drag_update(self, gesture, dx, dy):
        if self._state != "selecting":
            return
        self._cur_x = self._start_x + dx
        self._cur_y = self._start_y + dy
        self._draw_area.queue_draw()

    def _drag_end(self, gesture, dx, dy):
        if self._state != "selecting":
            return
        self._dragging = False
        x0 = min(self._start_x, self._start_x + dx)
        y0 = min(self._start_y, self._start_y + dy)
        x1 = max(self._start_x, self._start_x + dx)
        y1 = max(self._start_y, self._start_y + dy)
        rx, ry = int(x0), int(y0)
        rw, rh = int(x1 - x0), int(y1 - y0)
        logger.info(f"Region selected: ({rx},{ry}) {rw}×{rh}")

        self._state = "idle"
        self._window.hide()

        cb = self._on_region_cb
        self._on_region_cb = None
        if cb:
            GLib.idle_add(lambda: cb(rx, ry, rw, rh) or False)

    def _cancel_selection(self):
        self._dragging = False
        self._state = "idle"
        self._window.hide()
        cb = self._on_region_cb
        self._on_region_cb = None
        if cb:
            GLib.idle_add(lambda: cb(0, 0, 0, 0) or False)

    # ── Cairo drawing ─────────────────────────────────────────────────────────

    def _draw(self, area, cr, width, height):
        if self._state == "selecting":
            self._draw_selecting(cr, width, height)
        # In LIVE state the DrawingArea is transparent background only

    def _draw_selecting(self, cr, width, height):
        # 1. Paint the frozen screenshot background (if available)
        if self._bg_pixbuf is not None:
            cr.save()
            pw = self._bg_pixbuf.get_width()
            ph = self._bg_pixbuf.get_height()
            if pw > 0 and ph > 0:
                cr.scale(width / pw, height / ph)
            Gdk.cairo_set_source_pixbuf(cr, self._bg_pixbuf, 0, 0)
            cr.paint()
            cr.restore()

        # 2. Dark semi-transparent overlay
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.paint()

        if not self._dragging:
            # 3. Centred hint pill
            self._draw_hint(cr, width, height)
            return

        x0 = min(self._start_x, self._cur_x)
        y0 = min(self._start_y, self._cur_y)
        x1 = max(self._start_x, self._cur_x)
        y1 = max(self._start_y, self._cur_y)
        w = x1 - x0
        h = y1 - y0

        # 4. Punch clear hole inside selection
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.rectangle(x0, y0, w, h)
        cr.fill()
        cr.restore()

        # 5. Blue selection border
        cr.set_source_rgba(0.2, 0.7, 1.0, 0.9)
        cr.set_line_width(2.0)
        cr.rectangle(x0, y0, w, h)
        cr.stroke()

        # 6. Corner handles
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
        for hx, hy in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
            cr.arc(hx, hy, 4.0, 0, 2 * math.pi)
            cr.fill()

        # 7. Dimension label
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
        cr.select_font_face("Sans")
        cr.set_font_size(13)
        label = f"{int(w)} × {int(h)}"
        cr.move_to(x0 + 6, y0 - 8 if y0 > 24 else y0 + 20)
        cr.show_text(label)

    def _draw_hint(self, cr, width, height):
        """Draw a centred instruction pill over the dark overlay."""
        hint = "Click and drag to select a region   •   Esc to cancel"
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(15)
        ext = cr.text_extents(hint)
        pad_x, pad_y = 22, 12
        rw = ext.width + pad_x * 2
        rh = ext.height + pad_y * 2
        rx = (width - rw) / 2
        ry = height * 0.12   # near the top

        # Rounded pill background
        r = rh / 2
        cr.new_sub_path()
        cr.arc(rx + r,      ry + r,      r, math.pi,       3 * math.pi / 2)
        cr.arc(rx + rw - r, ry + r,      r, 3 * math.pi / 2, 0)
        cr.arc(rx + rw - r, ry + rh - r, r, 0,              math.pi / 2)
        cr.arc(rx + r,      ry + rh - r, r, math.pi / 2,    math.pi)
        cr.close_path()
        cr.set_source_rgba(0.05, 0.05, 0.15, 0.88)
        cr.fill_preserve()
        cr.set_source_rgba(0.3, 0.6, 1.0, 0.5)
        cr.set_line_width(1.0)
        cr.stroke()

        # Text
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
        cr.move_to(rx + pad_x - ext.x_bearing,
                   ry + pad_y - ext.y_bearing)
        cr.show_text(hint)
