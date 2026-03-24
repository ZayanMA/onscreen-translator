"""
screencast.py — Persistent screen capture via org.freedesktop.portal.ScreenCast + PipeWire.

Flow:
  CreateSession
    → SelectSources (persist_mode=2, restore_token for silent restore)
      → Start (returns PipeWire node_id + new restore_token)
        → OpenPipeWireRemote (returns Unix fd)
          → GStreamer pipewiresrc pipeline
            → get_frame() pulls PIL Images instantly, silently

On first run the portal shows a source picker once.
On subsequent runs it restores the session silently using the saved token.

Uses the pre-subscribe D-Bus pattern (same as shortcuts.py) to avoid
Response signal race conditions.
"""
import logging
import time
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

_BUS_NAME  = "org.freedesktop.portal.Desktop"
_OBJ_PATH  = "/org/freedesktop/portal/desktop"
_IFACE     = "org.freedesktop.portal.ScreenCast"
_REQ_IFACE = "org.freedesktop.portal.Request"
_TOKEN_FILE = Path.home() / ".config" / "onscreen-translator" / "screencast-token"


def _load_restore_token() -> Optional[str]:
    try:
        return _TOKEN_FILE.read_text().strip() or None
    except Exception:
        return None


def _save_restore_token(token: str):
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(token)


class ScreenCastPortal:
    """
    Persistent ScreenCast portal client.
    Call setup() once (in a background thread); then call get_frame() freely.
    """

    def __init__(self):
        self._bus: Gio.DBusConnection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        self._session_path: Optional[str] = None
        self._node_id: Optional[int] = None
        self._pw_fd: Optional[int] = None
        self._pipeline = None
        self._appsink = None
        self._ready: bool = False

    def _request_path(self, token: str) -> str:
        return f"/org/freedesktop/portal/desktop/request/{self._sender}/{token}"

    def is_ready(self) -> bool:
        return self._ready

    # ── Public ────────────────────────────────────────────────────────────────

    def setup(self):
        """
        Initialize the ScreenCast session. Blocks until ready (or fails).
        Call this in a background thread — the portal signals are dispatched
        via the GLib main loop.
        """
        try:
            self._create_session()
        except Exception:
            logger.exception("ScreenCast portal setup failed")

    def get_frame(self):
        """
        Pull the latest frame from the PipeWire stream.
        Returns a PIL Image, or None if no frame is available yet.
        """
        if not self._ready or self._appsink is None:
            return None
        try:
            from gi.repository import Gst
            sample = self._appsink.emit("try-pull-sample", 0)
            if sample is None:
                return None
            buf = sample.get_buffer()
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = s.get_value("width")
            h = s.get_value("height")
            ok, info = buf.map(Gst.MapFlags.READ)
            if not ok:
                return None
            data = bytes(info.data)
            buf.unmap(info)
            from PIL import Image
            return Image.frombytes("RGB", (w, h), data)
        except Exception as e:
            logger.warning(f"get_frame error: {e}")
            return None

    # ── CreateSession ─────────────────────────────────────────────────────────

    def _create_session(self):
        ts = str(int(time.time() * 1000))[-9:]
        session_token = f"ost_sc{ts}"
        request_token = f"ost_scr{ts}"

        self._bus.signal_subscribe(
            _BUS_NAME, _REQ_IFACE, "Response",
            self._request_path(request_token),
            None, Gio.DBusSignalFlags.NONE,
            self._on_session_response,
        )

        self._bus.call_sync(
            _BUS_NAME, _OBJ_PATH, _IFACE, "CreateSession",
            GLib.Variant("(a{sv})", ({
                "session_handle_token": GLib.Variant("s", session_token),
                "handle_token":         GLib.Variant("s", request_token),
            },)),
            None, Gio.DBusCallFlags.NONE, 10_000, None,
        )
        logger.info("ScreenCast CreateSession call sent")

    def _on_session_response(self, bus, sender, path, iface, signal, params):
        code = params.get_child_value(0).get_uint32()
        if code != 0:
            logger.error(f"ScreenCast CreateSession response code {code}")
            return
        results = params.get_child_value(1).unpack()
        self._session_path = results.get("session_handle")
        if not self._session_path:
            logger.error("ScreenCast: session_handle missing in response")
            return
        logger.info(f"ScreenCast session created: {self._session_path}")
        GLib.idle_add(self._select_sources)

    # ── SelectSources ─────────────────────────────────────────────────────────

    def _select_sources(self):
        ts = str(int(time.time() * 1000))[-9:]
        request_token = f"ost_scs{ts}"

        self._bus.signal_subscribe(
            _BUS_NAME, _REQ_IFACE, "Response",
            self._request_path(request_token),
            None, Gio.DBusSignalFlags.NONE,
            self._on_sources_response,
        )

        sources_opts = {
            "handle_token": GLib.Variant("s", request_token),
            "types":        GLib.Variant("u", 1),    # MONITOR = 1
            "multiple":     GLib.Variant("b", False),
            "persist_mode": GLib.Variant("u", 2),    # persistent across restarts
        }
        restore_token = _load_restore_token()
        if restore_token:
            sources_opts["restore_token"] = GLib.Variant("s", restore_token)
            logger.info("ScreenCast: using saved restore_token (silent restore)")
        else:
            logger.info("ScreenCast: no restore_token — source picker will appear")

        try:
            self._bus.call_sync(
                _BUS_NAME, _OBJ_PATH, _IFACE, "SelectSources",
                GLib.Variant("(oa{sv})", (self._session_path, sources_opts)),
                None, Gio.DBusCallFlags.NONE, 30_000, None,
            )
        except Exception as exc:
            logger.error(f"ScreenCast SelectSources call failed: {exc}")

    def _on_sources_response(self, bus, sender, path, iface, signal, params):
        code = params.get_child_value(0).get_uint32()
        if code != 0:
            logger.error(f"ScreenCast SelectSources response code {code}")
            return
        logger.info("ScreenCast SelectSources OK → calling Start")
        GLib.idle_add(self._start)

    # ── Start ─────────────────────────────────────────────────────────────────

    def _start(self):
        ts = str(int(time.time() * 1000))[-9:]
        request_token = f"ost_scst{ts}"

        self._bus.signal_subscribe(
            _BUS_NAME, _REQ_IFACE, "Response",
            self._request_path(request_token),
            None, Gio.DBusSignalFlags.NONE,
            self._on_start_response,
        )

        try:
            self._bus.call_sync(
                _BUS_NAME, _OBJ_PATH, _IFACE, "Start",
                GLib.Variant("(osa{sv})", (
                    self._session_path,
                    "",   # parent_window
                    {"handle_token": GLib.Variant("s", request_token)},
                )),
                None, Gio.DBusCallFlags.NONE, 30_000, None,
            )
        except Exception as exc:
            logger.error(f"ScreenCast Start call failed: {exc}")

    def _on_start_response(self, bus, sender, path, iface, signal, params):
        code = params.get_child_value(0).get_uint32()
        if code != 0:
            logger.error(f"ScreenCast Start response code {code}")
            return

        results = params.get_child_value(1).unpack()
        streams = results.get("streams", [])
        if not streams:
            logger.error("ScreenCast Start: no streams in response")
            return

        self._node_id = streams[0][0]
        logger.info(f"ScreenCast Start OK — PipeWire node_id={self._node_id}")

        new_token = results.get("restore_token")
        if new_token:
            _save_restore_token(new_token)
            logger.info("ScreenCast restore_token saved")

        GLib.idle_add(self._open_pipewire_remote)

    # ── OpenPipeWireRemote ────────────────────────────────────────────────────

    def _open_pipewire_remote(self):
        try:
            result, out_fd_list = self._bus.call_with_unix_fd_list_sync(
                _BUS_NAME, _OBJ_PATH, _IFACE, "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self._session_path, {})),
                None, Gio.DBusCallFlags.NONE, 5_000, None, None,
            )
            fd_index = result.get_child_value(0).get_handle()
            self._pw_fd = out_fd_list.get(fd_index)
            logger.info(f"ScreenCast PipeWire remote fd={self._pw_fd}")
            self._build_gst_pipeline()
        except Exception as exc:
            logger.error(f"ScreenCast OpenPipeWireRemote failed: {exc}")

    # ── GStreamer pipeline ────────────────────────────────────────────────────

    def _build_gst_pipeline(self):
        try:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)

            pipeline_str = (
                f"pipewiresrc fd={self._pw_fd} path={self._node_id} always-copy=true "
                f"! videoconvert "
                f"! video/x-raw,format=RGB "
                f"! appsink name=sink sync=false max-buffers=1 drop=true emit-signals=false"
            )
            self._pipeline = Gst.parse_launch(pipeline_str)
            self._appsink = self._pipeline.get_by_name("sink")
            self._pipeline.set_state(Gst.State.PLAYING)
            self._ready = True
            logger.info(f"ScreenCast ready — PipeWire node_id={self._node_id}, fd={self._pw_fd}")
        except Exception as exc:
            logger.error(f"ScreenCast GStreamer pipeline build failed: {exc}")
