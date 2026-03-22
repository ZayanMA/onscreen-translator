import dbus
import dbus.service
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ShortcutsPortal:
    """
    Wraps org.freedesktop.portal.GlobalShortcuts.
    Registers a system-wide hotkey that works even when the app is not focused.
    """

    BUS_NAME = "org.freedesktop.portal.Desktop"
    OBJECT_PATH = "/org/freedesktop/portal/desktop"
    INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
    SESSION_INTERFACE = "org.freedesktop.portal.Session"
    REQUEST_INTERFACE = "org.freedesktop.portal.Request"

    def __init__(self, session_bus: dbus.SessionBus):
        self._bus = session_bus
        desktop_obj = session_bus.get_object(self.BUS_NAME, self.OBJECT_PATH)
        self._iface = dbus.Interface(desktop_obj, self.INTERFACE)
        self._session_path: Optional[str] = None
        self._callback: Optional[Callable[[str], None]] = None

    def register(
        self,
        shortcut_id: str,
        description: str,
        preferred_trigger: str,
        callback: Callable[[str], None],
    ):
        """
        Registers a global shortcut. GNOME will prompt the user to confirm it once.
        callback(shortcut_id) is called whenever the hotkey fires.
        """
        self._callback = callback

        session_token = f"ost_session_{int(time.time())}"
        request_token = f"ost_req_{int(time.time())}"

        # Step 1: Create a session
        request_handle = self._iface.CreateSession({
            "session_handle_token": dbus.String(session_token),
            "handle_token": dbus.String(request_token),
        })

        def on_session_created(response_code, results):
            if response_code != 0:
                logger.error(f"Failed to create GlobalShortcuts session: {response_code}")
                return
            self._session_path = str(results.get("session_handle", ""))
            logger.debug(f"GlobalShortcuts session: {self._session_path}")
            self._bind_shortcuts(shortcut_id, description, preferred_trigger)

        req_obj = self._bus.get_object(self.BUS_NAME, request_handle)
        dbus.Interface(req_obj, self.REQUEST_INTERFACE).connect_to_signal(
            "Response", on_session_created
        )

    def _bind_shortcuts(self, shortcut_id: str, description: str, preferred_trigger: str):
        shortcuts = dbus.Array(
            [
                dbus.Struct(
                    [
                        dbus.String(shortcut_id),
                        dbus.Dictionary(
                            {
                                dbus.String("description"): dbus.String(description),
                                dbus.String("preferred_trigger"): dbus.String(preferred_trigger),
                            },
                            signature="sv",
                        ),
                    ],
                    signature="sa{sv}",
                )
            ],
            signature="(sa{sv})",
        )

        bind_token = f"ost_bind_{int(time.time())}"
        request_handle = self._iface.BindShortcuts(
            self._session_path,
            shortcuts,
            "",
            {"handle_token": dbus.String(bind_token)},
        )

        def on_bind_response(response_code, results):
            if response_code == 0:
                logger.info("Global shortcut registered successfully")
                # Subscribe to Activated signal on the session object
                session_obj = self._bus.get_object(self.BUS_NAME, self._session_path)
                session_iface = dbus.Interface(session_obj, self.INTERFACE)
                session_iface.connect_to_signal("Activated", self._on_activated)
            else:
                logger.error(f"Failed to bind shortcut: {response_code}")

        req_obj = self._bus.get_object(self.BUS_NAME, request_handle)
        dbus.Interface(req_obj, self.REQUEST_INTERFACE).connect_to_signal(
            "Response", on_bind_response
        )

    def _on_activated(self, session_handle, shortcut_id, timestamp, options):
        logger.debug(f"Shortcut activated: {shortcut_id}")
        if self._callback:
            self._callback(str(shortcut_id))
