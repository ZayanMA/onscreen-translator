"""
Wrapper for org.gnome.Shell.Screenshot.ScreenshotArea — takes a screenshot
of a specific screen region without user interaction.

This is the non-interactive complement to the xdg-desktop-portal Screenshot
interface, which requires user region selection every time.
"""
import dbus
import logging
import time

logger = logging.getLogger(__name__)

BUS_NAME = "org.gnome.Shell"
OBJECT_PATH = "/org/gnome/Shell/Screenshot"
INTERFACE = "org.gnome.Shell.Screenshot"


class GnomeScreenshot:
    def __init__(self, session_bus: dbus.SessionBus):
        self._bus = session_bus
        self._iface = None
        self._available = None
        self._init()

    def _init(self):
        try:
            obj = self._bus.get_object(BUS_NAME, OBJECT_PATH)
            self._iface = dbus.Interface(obj, INTERFACE)
            self._available = True
        except Exception as e:
            logger.warning(f"org.gnome.Shell.Screenshot not accessible: {e}")
            self._available = False

    def is_available(self) -> bool:
        return bool(self._available)

    def capture_area(self, x: int, y: int, w: int, h: int, dest_path: str) -> bool:
        """
        Capture screen region (x, y, w, h) into dest_path.
        Returns True on success. Fast (~50ms), no user interaction.
        """
        if not self._available:
            return False
        try:
            success, _ = self._iface.ScreenshotArea(
                dbus.Int32(x), dbus.Int32(y),
                dbus.Int32(w), dbus.Int32(h),
                dbus.Boolean(False),   # no flash
                dbus.String(dest_path),
            )
            return bool(success)
        except dbus.DBusException as e:
            logger.warning(f"ScreenshotArea failed: {e}")
            self._available = False
            return False
