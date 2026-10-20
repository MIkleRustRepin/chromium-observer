"""COU1: bounded JPEG reassembly over UDP. All integers use network byte order."""
from collections import OrderedDict
from dataclasses import dataclass, field
import math
import re
import socket
import struct
import threading
import time
import zlib

MAX_FRAME_BYTES = 32 * 1024 * 1024
HEADER = struct.Struct("!4s36sQQIHHI")
MAGIC = b"COU1"
DATAGRAM_BYTES = 1200
PAYLOAD_BYTES = DATAGRAM_BYTES - HEADER.size
ASSEMBLY_TIMEOUT = 3.0
MAX_INFLIGHT = 2
SESSION = re.compile(rb"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def encode_datagrams(jpeg, session, sequence, captured_at_ms):
    """Reference sender used by transport tests and the diagnostic CLI."""
    session = session.encode("ascii")
    if not SESSION.fullmatch(session) or not 4 <= len(jpeg) <= MAX_FRAME_BYTES:
        raise ValueError("Invalid session or frame size")
    if not 0 < sequence < 2**64 or not 0 < captured_at_ms < 2**64:
        raise ValueError("Invalid frame metadata")
    count = math.ceil(len(jpeg) / PAYLOAD_BYTES)
    checksum = zlib.crc32(jpeg)
    for index in range(count):
        yield HEADER.pack(MAGIC, session, sequence, captured_at_ms, len(jpeg),
                          index, count, checksum) + jpeg[index * PAYLOAD_BYTES:(index + 1) * PAYLOAD_BYTES]


@dataclass
class Assembly:
    created: float
    captured: int
    size: int
    count: int
    checksum: int
    chunks: dict = field(default_factory=dict)


class Reassembler:
    def __init__(self, store):
        self.store = store
        self.pending = OrderedDict()

    def expire(self, now=None):
        now = time.monotonic() if now is None else now
        for key, assembly in list(self.pending.items()):
            if now - assembly.created >= ASSEMBLY_TIMEOUT:
                del self.pending[key]

    def accept(self, packet, peer, now=None):
        now = time.monotonic() if now is None else now
        self.expire(now)
        if not HEADER.size < len(packet) <= DATAGRAM_BYTES:
            return False
        magic, session, sequence, captured, size, index, count, checksum = HEADER.unpack_from(packet)
        if (magic != MAGIC or not SESSION.fullmatch(session) or sequence < 1 or captured < 1
                or not 4 <= size <= MAX_FRAME_BYTES or count != math.ceil(size / PAYLOAD_BYTES)
                or index >= count or len(packet) - HEADER.size != min(PAYLOAD_BYTES, size - index * PAYLOAD_BYTES)):
            return False
        session = session.decode("ascii")
        latest = self.store.get()
        if latest and latest.session == session and sequence <= latest.sequence:
            return False
        key = (peer, session, sequence)
        assembly = self.pending.get(key)
        if assembly is None:
            while len(self.pending) >= MAX_INFLIGHT:
                self.pending.popitem(last=False)
            assembly = Assembly(now, captured, size, count, checksum)
            self.pending[key] = assembly
        elif (assembly.captured, assembly.size, assembly.count, assembly.checksum) != (captured, size, count, checksum):
            return False
        payload = packet[HEADER.size:]
        if index in assembly.chunks and assembly.chunks[index] != payload:
            del self.pending[key]
            return False
        assembly.chunks[index] = payload
        if len(assembly.chunks) != count:
            return False
        del self.pending[key]
        jpeg = b"".join(assembly.chunks[i] for i in range(count))
        if (zlib.crc32(jpeg) != checksum or not jpeg.startswith(b"\xff\xd8")
                or not jpeg.endswith(b"\xff\xd9")):
            return False
        return self.store.put(jpeg, session, sequence, captured)


class UdpReceiver:
    def __init__(self, store, host="127.0.0.1", port=8766):
        family, kind, protocol, _, address = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
        self.socket = socket.socket(family, kind, protocol)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            self.socket.bind(address)
            self.socket.settimeout(0.1)
        except BaseException:
            self.socket.close()
            raise
        self.address = self.socket.getsockname()
        self.reassembler = Reassembler(store)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name="udp-frame-receiver", daemon=True)

    def run(self):
        while not self.stopped.is_set():
            try:
                packet, peer = self.socket.recvfrom(DATAGRAM_BYTES + 1)
            except socket.timeout:
                self.reassembler.expire()
                continue
            except OSError:
                if self.stopped.is_set():
                    return
                raise
            self.reassembler.accept(packet, peer)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stopped.set()
        self.thread.join()
        self.socket.close()
