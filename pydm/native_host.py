"""Native messaging host: the browser starts this and talks to it over stdio.

Each message is a 4-byte little-endian length followed by UTF-8 JSON. Nothing
else may be written to stdout, or the browser drops the connection.
"""
import json
import struct
import sys

from . import __version__, ipc


def read_message():
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def write_message(msg: dict) -> None:
    data = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
    sys.stdout.buffer.flush()


def allow_foreground() -> None:
    """The browser (in front, the user just clicked) started this host, so it may
    let another process take the foreground: PyDM's Add dialog."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass


def handle(msg: dict) -> dict:
    kind = msg.get("type")
    if kind == "ping":
        return {"ok": True, "version": __version__, "gui_running": ipc.send({"type": "ping"}, 500)}
    if kind == "download":
        url = str(msg.get("url", ""))
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "Only http(s) URLs are supported"}
        forwarded = {k: msg.get(k) for k in ("url", "filename", "referrer", "cookies", "userAgent", "fileSize",
                                             "kind", "title")}
        allow_foreground()
        if ipc.send_or_launch({"type": "download", **forwarded}):
            return {"ok": True}
        return {"ok": False, "error": "Could not reach the PyDM window"}
    return {"ok": False, "error": f"Unknown message type: {kind!r}"}


def main() -> None:
    while True:
        try:
            msg = read_message()
        except (ValueError, OSError) as e:
            write_message({"ok": False, "error": f"Bad message: {e}"})
            return
        if msg is None:
            return  # browser closed the pipe
        try:
            write_message(handle(msg))
        except Exception as e:  # noqa: BLE001 - never die without answering
            write_message({"ok": False, "error": str(e)})


if __name__ == "__main__":
    main()
