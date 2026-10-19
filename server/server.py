#!/usr/bin/env python3
"""Single-producer, memory-only Chromium frame viewer. Python 3.10+."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time

MAX_FRAME_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    session: str
    sequence: int
    captured_at_ms: int
    received_at_ms: int


class FrameStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest: Frame | None = None
        self.retired_sessions: deque[str] = deque(maxlen=32)

    def put(self, jpeg: bytes, session: str, sequence: int, captured_at_ms: int) -> bool:
        with self.lock:
            previous = self.latest
            if session in self.retired_sessions:
                return False
            if previous:
                if previous.session == session and sequence <= previous.sequence:
                    return False
                if previous.session != session:
                    self.retired_sessions.append(previous.session)
            self.latest = Frame(jpeg, session, sequence, captured_at_ms,
                                time.time_ns() // 1_000_000)
            return True

    def get(self) -> Frame | None:
        with self.lock:
            return self.latest


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address):
        self.store = FrameStore()
        self.page = Path(__file__).with_name("viewer.html").read_bytes()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: ViewerServer

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, fmt, *args):
        # Do not log one request per frame; errors remain visible.
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            super().log_message(fmt, *args)

    def respond(self, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' blob:; "
                         "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                         "frame-ancestors 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def local_request(self):
        # Reject DNS rebinding and cross-origin writes from visited pages.
        allowed = {f"127.0.0.1:{self.server.server_port}",
                   f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in allowed:
            self.respond(HTTPStatus.FORBIDDEN)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in allowed}:
            self.respond(HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        if self.path == "/":
            self.respond(HTTPStatus.OK, self.server.page, "text/html; charset=utf-8")
        elif self.path == "/api/frame":
            frame = self.server.store.get()
            if frame is None:
                self.respond(HTTPStatus.NO_CONTENT)
            else:
                self.respond(HTTPStatus.OK, frame.jpeg, "image/jpeg", {
                    "X-Frame-Session": frame.session,
                    "X-Frame-Sequence": frame.sequence,
                    "X-Captured-At": frame.captured_at_ms,
                    "X-Received-At": frame.received_at_ms,
                })
        elif self.path == "/api/status":
            frame = self.server.store.get()
            status = {"has_frame": frame is not None}
            if frame:
                status.update(session=frame.session, sequence=frame.sequence,
                              captured_at_ms=frame.captured_at_ms,
                              received_at_ms=frame.received_at_ms)
            self.respond(HTTPStatus.OK, json.dumps(status).encode(), "application/json")
        else:
            self.respond(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        self.close_connection = True
        if not self.local_request():
            return
        if self.path != "/api/frame":
            self.respond(HTTPStatus.NOT_FOUND)
            return
        if self.headers.get("Content-Type") != "image/jpeg":
            self.respond(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("chunked upload is unsupported")
            length = int(self.headers["Content-Length"])
            session = self.headers["X-Frame-Session"]
            sequence = int(self.headers["X-Frame-Sequence"])
            captured = int(self.headers["X-Captured-At"])
            if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", session or ""):
                raise ValueError("invalid session")
            if sequence < 1 or captured < 1 or length < 4:
                raise ValueError("invalid metadata")
        except (TypeError, ValueError):
            self.respond(HTTPStatus.BAD_REQUEST)
            return
        if length > MAX_FRAME_BYTES:
            self.respond(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            jpeg = self.rfile.read(length)
        except TimeoutError:
            self.respond(HTTPStatus.REQUEST_TIMEOUT)
            return
        if len(jpeg) != length or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            self.respond(HTTPStatus.BAD_REQUEST)
            return
        if not self.server.store.put(jpeg, session, sequence, captured):
            self.respond(HTTPStatus.CONFLICT)
            return
        self.respond(HTTPStatus.NO_CONTENT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ViewerServer(("127.0.0.1", args.port)) as server:
        print(f"Viewer: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
