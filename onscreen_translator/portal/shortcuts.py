"""
shortcuts.py — Global hotkey registration via org.freedesktop.portal.GlobalShortcuts.

Uses the pre-subscribe pattern (same as libportal):
  - Compute the request object path deterministically BEFORE making each call
  - Subscribe to Response on that path BEFORE the call
  - Fire the call

This avoids the race condition where the portal emits Response before we can
subscribe — which was causing Super+T to be silently ignored.

No sudo, no gsettings, no input-group membership required.
"""
import logging
import time
from typing import Callable, Optional

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

_BUS_NAME  = "org.freedesktop.portal.Desktop"
_OBJ_PATH  = "/org/freedesktop/portal/desktop"
_IFACE     = "org.freedesktop.portal.GlobalShortcuts"
_REQ_IFACE = "org.freedesktop.portal.Request"


class ShortcutsPortal:
    """
    Async GlobalShortcuts portal client using the pre-subscribe pattern.
    Call register() once; callback fires every time the hotkey is pressed.
    """

    def __init__(self):
        self._bus: Gio.DBusConnection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Sender token: unique name ':1.559' → '1_559'
        self._sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        self._session_path: Optional[str] = None
        self._callback: Optional[Callable[[str], None]] = None

    def _request_path(self, token: str) -> str:
        return f"/org/freedesktop/portal/desktop/request/{self._sender}/{token}"

    # ── Public ────────────────────────────────────────────────────────────────

    def register(
        self,
        shortcut_id: str,
        description: str,
        preferred_trigger: str,
        callback: Callable[[str], None],
    ):
        """Register a global shortcut. callback(shortcut_id) fires on each press."""
        self._callback = callback

        # Subscribe to Activated on the portal object (fires on every keypress).
        self._bus.signal_subscribe(
            _BUS_NAME, _IFACE, "Activated", _OBJ_PATH,
            None, Gio.DBusSignalFlags.NONE,
            self._on_activated,
        )

        ts = str(int(time.time() * 1000))[-9:]
        session_token = f"ost_s{ts}"
        request_token = f"ost_r{ts}"

        # Pre-subscribe to Response BEFORE calling CreateSession so we don't
        # miss the signal if the portal responds before the async dispatch.
        self._bus.signal_subscribe(
            _BUS_NAME, _REQ_IFACE, "Response",
            self._request_path(request_token),
            None, Gio.DBusSignalFlags.NONE,
            self._on_session_response,
            (shortcut_id, description, preferred_trigger),
        )

        try:
            self._bus.call_sync(
                _BUS_NAME, _OBJ_PATH, _IFACE, "CreateSession",
                GLib.Variant("(a{sv})", ({
                    "session_handle_token": GLib.Variant("s", session_token),
                    "handle_token":         GLib.Variant("s", request_token),
                },)),
                None,
                Gio.DBusCallFlags.NONE, 5_000, None,
            )
        except Exception as exc:
            logger.error(f"GlobalShortcuts CreateSession call failed: {exc}")

    # ── Session response → bind shortcuts ─────────────────────────────────────

    def _on_session_response(
        self, bus, sender, path, iface, signal, params, user_data
    ):
        code = params.get_child_value(0).get_uint32()
        if code != 0:
            logger.error(f"GlobalShortcuts CreateSession response code {code}")
            return

        results = params.get_child_value(1).unpack()
        session_handle = results.get("session_handle")
        if not session_handle:
            logger.error(f"GlobalShortcuts: session_handle missing. Response keys: {list(results.keys())}")
            return

        self._session_path = session_handle
        logger.info(f"GlobalShortcuts session created: {self._session_path}")

        shortcut_id, description, preferred_trigger = user_data
        # Defer BindShortcuts out of the signal callback — calling call_sync
        # from inside a D-Bus signal handler creates a re-entrant main context
        # that causes GNOME Shell to respond with error code 2.
        GLib.idle_add(self._bind_shortcuts, shortcut_id, description, preferred_trigger)

    # ── BindShortcuts ─────────────────────────────────────────────────────────

    def _bind_shortcuts(self, shortcut_id: str, description: str, preferred_trigger: str):
        ts = str(int(time.time() * 1000))[-9:]
        bind_token = f"ost_b{ts}"

        # Pre-subscribe before calling BindShortcuts.
        self._bus.signal_subscribe(
            _BUS_NAME, _REQ_IFACE, "Response",
            self._request_path(bind_token),
            None, Gio.DBusSignalFlags.NONE,
            self._on_bind_response,
        )

        def _on_call_done(bus, result, _):
            try:
                bus.call_finish(result)
            except Exception as exc:
                logger.error(f"GlobalShortcuts BindShortcuts call error: {exc}")

        self._bus.call(
            _BUS_NAME, _OBJ_PATH, _IFACE, "BindShortcuts",
            GLib.Variant("(oa(sa{sv})sa{sv})", (
                self._session_path,
                [(shortcut_id, {
                    "description":       GLib.Variant("s", description),
                    "preferred_trigger": GLib.Variant("s", preferred_trigger),
                })],
                "",   # parent_window: empty = no modality
                {"handle_token": GLib.Variant("s", bind_token)},
            )),
            None,
            Gio.DBusCallFlags.NONE, 30_000, None,
            _on_call_done, None,
        )

    # ── Bind response ─────────────────────────────────────────────────────────

    def _on_bind_response(self, bus, sender, path, iface, signal, params):
        code = params.get_child_value(0).get_uint32()
        if code == 0:
            logger.info("GlobalShortcuts: shortcut registered successfully")
        else:
            logger.error(f"GlobalShortcuts: BindShortcuts response code {code}")

    # ── Activated signal ──────────────────────────────────────────────────────

    def _on_activated(self, bus, sender, path, iface, signal, params):
        session_handle = params.get_child_value(0).get_string()
        shortcut_id    = params.get_child_value(1).get_string()
        logger.debug(f"GlobalShortcuts Activated: {shortcut_id!r}")

        if self._session_path and session_handle != self._session_path:
            return
        if self._callback:
            self._callback(shortcut_id)
