"""Cross-language test: native C++ packetizer -> Python UDP reassembler."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.server import FrameStore
from server.udp_transport import Reassembler, encode_datagrams


@unittest.skipUnless(shutil.which("c++"), "C++20 compiler is required")
class WireCompatibilityTest(unittest.TestCase):
    def test_native_bytes_match_reference_and_reassemble(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "wire-test"
            subprocess.run(["c++", "-std=c++20", str(ROOT / "scripts/test_udp_wire.cc"),
                            "-lz", "-o", str(binary)], check=True)
            data = subprocess.check_output([str(binary)])
        packets = []
        while data:
            length = int.from_bytes(data[:2], "big")
            self.assertGreater(length, 68)
            packets.append(data[2:2 + length])
            data = data[2 + length:]
        jpeg = b"\xff\xd8" + bytes(range(256)) * 15 + b"\xff\xd9"
        reference = list(encode_datagrams(jpeg, "00000000-0000-4000-8000-000000000001", 42, 1234))
        self.assertEqual(packets, reference)
        store = FrameStore()
        receiver = Reassembler(store)
        for packet in reversed(packets):
            receiver.accept(packet, ("127.0.0.1", 12345))
        self.assertEqual(store.get().jpeg, jpeg)
        self.assertEqual(store.get().sequence, 42)


if __name__ == "__main__":
    unittest.main()
