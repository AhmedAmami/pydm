"""Local HTTP server for tests: /ranges/, /norange/, /chunked/, /missing, and
/static/<path> (files under STATIC_ROOT, with Range support).
Query params: delay=<s per 64KB>, slow0=<s per 64KB, only for the range at byte 0>,
token=<t> (a link that expires: 403 once t is in EXPIRED; its ETag differs per token, like
different CDN servers), trim=<n> (serve n bytes less: "a different file")."""
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

DATA = os.urandom(12 * 1024 * 1024 + 12345)  # odd size on purpose
stats = {"ranges": [], "static": []}  # ranged requests on DATA; paths requested under /static/
EXPIRED: set[str] = set()  # tokens whose links no longer work
STATIC_ROOT: Path | None = None
STATIC_DELAY = 0.0  # seconds per static request, to make pausing mid-download testable
CONTENT_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".mpd": "application/dash+xml",
                 ".ts": "video/mp2t", ".mp4": "video/mp4", ".m4s": "video/iso.segment"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        delay = float(q.get("delay", ["0"])[0])
        slow0 = float(q.get("slow0", ["0"])[0])
        mode = u.path.split("/")[1]
        if mode == "static":
            return self._static(unquote(u.path[len("/static/"):]))
        token = q.get("token", [None])[0]
        if mode == "missing" or token in EXPIRED:
            self.send_response(403 if token in EXPIRED else 404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = DATA[:len(DATA) - int(q.get("trim", ["0"])[0])]
        rng = self.headers.get("Range")
        start, end, status = 0, len(data) - 1, 200
        if mode == "ranges" and rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else len(data) - 1, len(data) - 1)
            status = 206
            stats["ranges"].append((start, end))
        if slow0 and start == 0 and end > 0:
            delay = slow0
        body = memoryview(data)[start:end + 1]
        self.send_response(status)
        if mode == "ranges":
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", f'"{token or 'abc'}"')
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.send_header("Content-Disposition", 'attachment; filename="test file.bin"')
        if mode == "chunked":
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(0, len(body), 65536):
                piece = body[i:i + 65536]
                self.wfile.write(f"{len(piece):X}\r\n".encode() + piece + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            for i in range(0, len(body), 65536):
                self.wfile.write(body[i:i + 65536])
                if delay:
                    time.sleep(delay)
        except OSError:
            pass


    def _static(self, rel: str):
        path = (STATIC_ROOT / rel).resolve() if STATIC_ROOT else None
        if not path or STATIC_ROOT.resolve() not in path.parents or not path.is_file():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        stats["static"].append(rel)
        if STATIC_DELAY:
            time.sleep(STATIC_DELAY)
        data = path.read_bytes()
        status, body = 200, data
        m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if m:
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else len(data) - 1, len(data) - 1)
            status, body = 206, data[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # clients hanging up early (pause/steal) is expected


def start() -> str:
    srv = QuietServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"
