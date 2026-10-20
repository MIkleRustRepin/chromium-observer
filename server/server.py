#!/usr/bin/env python3
"""Single-producer, memory-only Chromium frame viewer. Python 3.10+."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from .udp_transport import MAX_FRAME_BYTES, UdpReceiver


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
                    if captured_at_ms < previous.captured_at_ms:
                        return False
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
        self.public_viewer = not ipaddress.ip_address(self.server_address[0]).is_loopback


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

    def allowed_request(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}",
                   f"localhost:{self.server.server_port}",
                   f"{self.server.server_address[0]}:{self.server.server_port}"}
        if self.server.public_viewer:
            # A public listener accepts its public IP or DNS name, including
            # deployments whose public address is mapped through NAT.
            try:
                authority = urlsplit("http://" + host)
                if (not host or any(c.isspace() for c in host)
                        or any(c in host for c in "/\\@?#")
                        or not authority.hostname or authority.port == 0):
                    raise ValueError("Invalid Host header")
            except ValueError:
                self.respond(HTTPStatus.BAD_REQUEST)
                return False
            allowed = {host}
        # Explicit loopback binding retains DNS-rebinding protection.
        if host not in allowed:
            self.respond(HTTPStatus.FORBIDDEN)
            return False
        origin = self.headers.get("Origin")
        origins = {f"{scheme}://{name}" for name in allowed for scheme in ("http", "https")}
        if origin and origin not in origins:
            self.respond(HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self):
        if not self.allowed_request():
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
        self.respond(HTTPStatus.METHOD_NOT_ALLOWED)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind address (default: all IPv4 interfaces)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--udp-host", default="0.0.0.0", help="UDP bind address (default: all IPv4 interfaces)")
    parser.add_argument("--udp-port", type=int, default=8766)
    args = parser.parse_args()
    with ViewerServer((args.host, args.port)) as server, UdpReceiver(
            server.store, args.udp_host, args.udp_port) as receiver:
        viewer_host = "SERVER_IP" if args.host == "0.0.0.0" else args.host
        print(f"Viewer: http://{viewer_host}:{server.server_port}/ "
              f"(HTTP bind {args.host}) | UDP: {receiver.address}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
