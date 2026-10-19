from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import threading
import time
import unittest

from server.server import MAX_FRAME_BYTES, ViewerServer


# Payloads exercise transport/storage; browser integration tests use rendered JPEGs.
JPEG_A = b"\xff\xd8frame-a\xff\xd9"
JPEG_B = b"\xff\xd8frame-b\xff\xd9"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.server = ViewerServer(("127.0.0.1", 0))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
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

    def upload(self, body=JPEG_A, session="run-1", sequence=1, extra=None):
        headers = {"Content-Type": "image/jpeg", "X-Frame-Session": session,
                   "X-Frame-Sequence": str(sequence),
                   "X-Captured-At": str(time.time_ns() // 1_000_000)}
        headers.update(extra or {})
        return self.request("POST", "/api/frame", body, headers)

    def test_empty_then_latest_only_and_no_cache(self):
        self.assertEqual(204, self.request("GET", "/api/frame")[0])
        self.assertEqual({"has_frame": False}, json.loads(self.request("GET", "/api/status")[2]))
        self.assertEqual(204, self.upload()[0])
        self.assertEqual(204, self.upload(JPEG_B, sequence=2)[0])
        code, headers, body = self.request("GET", "/api/frame")
        self.assertEqual((200, JPEG_B), (code, body))
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual("2", headers["X-Frame-Sequence"])
        status = json.loads(self.request("GET", "/api/status")[2])
        self.assertEqual(2, status["sequence"])
        self.assertGreaterEqual(status["received_at_ms"], status["captured_at_ms"])

    def test_old_requests_cannot_replace_new_frames(self):
        self.upload(sequence=10)
        self.assertEqual(409, self.upload(JPEG_B, sequence=9)[0])
        self.assertEqual(409, self.upload(JPEG_B, sequence=10)[0])
        self.assertEqual(204, self.upload(JPEG_B, session="run-2")[0])
        self.assertEqual(409, self.upload(session="run-1", sequence=11)[0])
        self.assertEqual(JPEG_B, self.request("GET", "/api/frame")[2])

    def test_concurrent_uploads_remain_monotonic(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda i: self.upload(sequence=i), range(1, 31)))
        status = json.loads(self.request("GET", "/api/status")[2])
        self.assertEqual(30, status["sequence"])

    def test_rejects_invalid_input_without_replacing_frame(self):
        self.upload()
        for extra, body, expected in [
            ({"Content-Type": "text/plain"}, JPEG_B, 415),
            ({"X-Frame-Sequence": "bad"}, JPEG_B, 400),
            ({"X-Frame-Sequence": "0"}, JPEG_B, 400),
            ({"X-Frame-Session": ""}, JPEG_B, 400),
            ({}, b"not a jpeg", 400),
            ({"Content-Length": str(MAX_FRAME_BYTES + 1)}, JPEG_B, 413),
            ({"Origin": "https://example.com"}, JPEG_B, 403),
            ({"Host": "attacker.example"}, JPEG_B, 403),
        ]:
            with self.subTest(extra=extra, body=body):
                self.assertEqual(expected, self.upload(body, sequence=2, extra=extra)[0])
        self.assertEqual(JPEG_A, self.request("GET", "/api/frame")[2])

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
