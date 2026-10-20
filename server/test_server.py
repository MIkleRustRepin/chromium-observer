from concurrent.futures import ThreadPoolExecutor
import ast
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import re
import select
import socket
import subprocess
import sys
import threading
import time
import unittest

from server.server import ViewerServer
from server.udp_transport import UdpReceiver, encode_datagrams

JPEG_A = b"\xff\xd8frame-a\xff\xd9"
JPEG_B = b"\xff\xd8frame-b\xff\xd9"
SESSION = "00000000-0000-4000-8000-000000000001"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.server = ViewerServer(("127.0.0.1", 0))
        self.receiver = UdpReceiver(self.server.store, port=0)
        self.receiver.__enter__()
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.receiver.__exit__()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def upload(self, body=JPEG_A, sequence=1):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for packet in encode_datagrams(body, SESSION, sequence, time.time_ns() // 1_000_000):
                sender.sendto(packet, self.receiver.address)
        until = time.monotonic() + 2
        while time.monotonic() < until:
            frame = self.server.store.get()
            if frame and frame.sequence == sequence:
                return
            time.sleep(0.01)
        self.fail("UDP frame not received")

    def test_empty_then_udp_latest_only_and_no_cache(self):
        self.assertEqual(204, self.request("GET", "/api/frame")[0])
        self.upload()
        self.upload(JPEG_B, sequence=2)
        code, headers, body = self.request("GET", "/api/frame")
        self.assertEqual((200, JPEG_B), (code, body))
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual("2", headers["X-Frame-Sequence"])
        self.assertEqual(2, json.loads(self.request("GET", "/api/status")[2])["sequence"])

    def test_http_upload_disabled_and_viewer_host_guard(self):
        self.assertEqual(405, self.request("POST", "/api/frame", JPEG_A)[0])
        self.assertEqual(403, self.request("GET", "/", headers={"Host": "attacker.example"})[0])
        self.assertEqual(403, self.request("GET", "/", headers={"Origin": "https://example.com"})[0])

    def test_concurrent_store_is_monotonic(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda i: self.server.store.put(JPEG_A, SESSION, i, 1234), range(1,31)))
        self.assertEqual(30, self.server.store.get().sequence)

    def test_stale_frame_keeps_original_times_and_page_is_served(self):
        self.upload()
        first = json.loads(self.request("GET", "/api/status")[2])
        self.assertEqual(first, json.loads(self.request("GET", "/api/status")[2]))
        code, _, page = self.request("GET", "/")
        self.assertEqual(200, code)
        self.assertIn('Нет свежих кадров'.encode(), page)
        self.assertEqual(404, self.request("GET", "/unknown")[0])


class ServerBindingTest(unittest.TestCase):
    @contextmanager
    def running_cli(self, *args):
        process = subprocess.Popen(
            [sys.executable, "-m", "server.server", "--port", "0", "--udp-port", "0", *args],
            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0], "Server did not start")
            line = process.stdout.readline()
            self.assertIn("Viewer:", line)
            port = int(re.search(r":(\d+)/", line).group(1))
            udp_address = ast.literal_eval(line.split(" | UDP: ", 1)[1])
            yield port, udp_address, line
        finally:
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()

    def request(self, port, path="/", host=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            headers = {"Host": host} if host else {}
            if origin:
                headers["Origin"] = origin
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_cli_public_defaults_accept_external_ip_and_dns_host(self):
        with self.running_cli() as (port, udp_address, line):
            self.assertIn("HTTP bind 0.0.0.0", line)
            self.assertEqual(udp_address[0], "0.0.0.0")
            public_host = f"203.0.113.10:{port}"
            self.assertEqual(200, self.request(port, host=public_host)[0])
            self.assertEqual(200, self.request(port, host=f"viewer.example:{port}")[0])
            self.assertEqual(200, self.request(port, host=public_host, origin=f"http://{public_host}")[0])
            self.assertEqual(403, self.request(port, host=public_host, origin="https://other.example")[0])
            self.assertEqual(400, self.request(port, host="bad/host")[0])
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for packet in encode_datagrams(JPEG_A, SESSION, 1, time.time_ns() // 1_000_000):
                    sender.sendto(packet, ("127.0.0.1", udp_address[1]))
            until = time.monotonic() + 2
            while time.monotonic() < until:
                response = self.request(port, "/api/frame", host=public_host)
                if response[0] == 200:
                    break
                time.sleep(0.01)
            self.assertEqual((200, JPEG_A), response)

    def test_cli_explicit_loopback_keeps_host_guard(self):
        with self.running_cli("--host", "127.0.0.1", "--udp-host", "127.0.0.1") as (port, udp_address, line):
            self.assertIn("HTTP bind 127.0.0.1", line)
            self.assertEqual(udp_address[0], "127.0.0.1")
            self.assertEqual(200, self.request(port)[0])
            self.assertEqual(403, self.request(port, host="viewer.example")[0])


if __name__ == "__main__":
    unittest.main()
