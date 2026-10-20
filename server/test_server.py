from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import socket
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


if __name__ == "__main__":
    unittest.main()
