import socket
import time
import unittest

from server.server import FrameStore
from server.udp_transport import (ASSEMBLY_TIMEOUT, DATAGRAM_BYTES, HEADER,
                                  MAX_INFLIGHT, PAYLOAD_BYTES, Reassembler,
                                  UdpReceiver, encode_datagrams)

SESSION = "00000000-0000-4000-8000-000000000001"
JPEG = b"\xff\xd8" + bytes(range(256)) * 15 + b"\xff\xd9"


class UdpTest(unittest.TestCase):
    def setUp(self):
        self.store = FrameStore()
        self.reader = Reassembler(self.store)

    def packets(self, sequence=1, jpeg=JPEG, session=SESSION):
        return list(encode_datagrams(jpeg, session, sequence, 1234))

    def deliver(self, packets, peer=("127.0.0.1", 9999), now=0):
        return [self.reader.accept(p, peer, now) for p in packets]

    def test_reordered_duplicate_chunks_and_monotonic_publication(self):
        packets = self.packets()
        self.assertTrue(all(len(p) <= DATAGRAM_BYTES for p in packets))
        self.deliver([packets[-1], packets[-1], *reversed(packets[:-1])])
        self.assertEqual(self.store.get().jpeg, JPEG)
        self.deliver(self.packets(3))
        self.deliver(self.packets(2))
        self.assertEqual(self.store.get().sequence, 3)

    def test_loss_keeps_previous_frame_and_next_frame_recovers(self):
        self.deliver(self.packets(1))
        self.deliver(self.packets(2)[:-1])
        self.assertEqual(self.store.get().sequence, 1)
        self.reader.expire(ASSEMBLY_TIMEOUT)
        self.assertFalse(self.reader.pending)
        self.deliver(self.packets(3), now=ASSEMBLY_TIMEOUT + 1)
        self.assertEqual(self.store.get().sequence, 3)

    def test_malformed_packets_crc_and_conflicting_duplicates(self):
        packets = self.packets()
        for packet in (b"", packets[0][:50], b"WRNG" + packets[0][4:], packets[0] + b"x",
                       b"x" * (DATAGRAM_BYTES + 1)):
            self.assertFalse(self.reader.accept(packet, ("x", 1), 0))
        values = list(HEADER.unpack_from(packets[0]))
        for field, bad in ((2, 0), (3, 0), (4, 33 * 1024**2), (5, 65535), (6, 65535)):
            changed = values.copy()
            changed[field] = bad
            self.assertFalse(self.reader.accept(HEADER.pack(*changed) + packets[0][HEADER.size:], ("x", 1), 0))
        broken = packets.copy()
        broken[1] = broken[1][:-1] + bytes([broken[1][-1] ^ 1])
        self.deliver(broken)
        self.assertIsNone(self.store.get())
        self.deliver([packets[0], packets[0][:-1] + bytes([packets[0][-1] ^ 1])])
        self.assertFalse(self.reader.pending)

    def test_peer_isolation_limits_and_retired_sessions(self):
        self.deliver(self.packets()[:1], peer=("x", 1))
        self.deliver(self.packets()[1:], peer=("x", 2))
        self.assertIsNone(self.store.get())
        for sequence in range(2, 100):
            self.deliver(self.packets(sequence)[:1])
        self.assertEqual(len(self.reader.pending), MAX_INFLIGHT)
        self.deliver(self.packets(101))
        second = SESSION[:-1] + "2"
        self.deliver(self.packets(1, session=second))
        self.deliver(self.packets(102))
        self.assertEqual(self.store.get().session, second)

    def test_real_udp_receiver_restarts_on_same_port(self):
        port = 0
        for _ in range(2):
            with UdpReceiver(self.store, port=port) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                port = receiver.address[1]
                for packet in self.packets(self.store.get().sequence + 1 if self.store.get() else 1):
                    sender.sendto(packet, receiver.address)
                target = 1 if _ == 0 else 2
                until = time.monotonic() + 2
                while (not self.store.get() or self.store.get().sequence != target) and time.monotonic() < until:
                    time.sleep(0.01)
                self.assertEqual(self.store.get().sequence, target)

    def test_delayed_unknown_session_does_not_replace_newer_capture(self):
        self.store.put(JPEG, SESSION, 1, 2000)
        self.deliver(self.packets(1, session=SESSION[:-1] + "3"))
        self.assertEqual(self.store.get().session, SESSION)

    @unittest.skipUnless(socket.has_ipv6, "IPv6 unavailable")
    def test_ipv6_loopback(self):
        try:
            receiver = UdpReceiver(self.store, host="::1", port=0)
        except OSError as error:
            self.skipTest(str(error))
        with receiver, socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sender:
            for packet in self.packets():
                sender.sendto(packet, receiver.address)
            until = time.monotonic() + 2
            while self.store.get() is None and time.monotonic() < until:
                time.sleep(0.01)
            self.assertEqual(self.store.get().jpeg, JPEG)


if __name__ == "__main__":
    unittest.main()
