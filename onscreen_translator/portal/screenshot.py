import dbus
import time
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ScreenshotPortal:
    """
    Wraps org.freedesktop.portal.Screenshot for interactive region selection.
    On GNOME Wayland, this triggers the native crosshair region picker.
    """

    BUS_NAME = "org.freedesktop.portal.Desktop"
    OBJECT_PATH = "/org/freedesktop/portal/desktop"
    INTERFACE = "org.freedesktop.portal.Screenshot"
    REQUEST_INTERFACE = "org.freedesktop.portal.Request"

    def __init__(self, session_bus: dbus.SessionBus):
        self._bus = session_bus
        desktop_obj = session_bus.get_object(self.BUS_NAME, self.OBJECT_PATH)
        self._iface = dbus.Interface(desktop_obj, self.INTERFACE)
        self._pending_callback: Optional[Callable] = None

    def take_interactive(self, callback: Callable[[str], None]):
        """
        Opens GNOME's native region picker. callback(uri) is called on success,
        where uri is like 'file:///tmp/screenshot_xxx.png'.
        On cancel or error, callback is not called.
        """
        token = f"ost{int(time.time())}"
        options = {
            "interactive": dbus.Boolean(True),
            "handle_token": dbus.String(token),
        }

        # The returned handle path is where we subscribe to the Response signal
        handle_path = self._iface.Screenshot("", options)
        logger.debug(f"Screenshot request handle: {handle_path}")

        def on_response(response_code, results):
            if response_code == 0:
                uri = str(results.get("uri", ""))
                if uri:
                    logger.info(f"Screenshot captured: {uri}")
                    callback(uri)
                else:
                    logger.warning("Screenshot portal returned no URI")
            elif response_code == 1:
                logger.info("Screenshot cancelled by user")
            else:
                logger.warning(f"Screenshot portal error: response_code={response_code}")

        # Subscribe to Response signal on the request handle object
        request_obj = self._bus.get_object(self.BUS_NAME, handle_path)
        request_iface = dbus.Interface(request_obj, self.REQUEST_INTERFACE)
        request_iface.connect_to_signal("Response", on_response)
