"""Send a JPEG from stdin using the same UDP framing as the native browser."""
import argparse
import socket
import sys
import time
import uuid

from .udp_transport import MAX_FRAME_BYTES, encode_datagrams


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--session", default=str(uuid.uuid4()))
    parser.add_argument("--sequence", type=int, default=1)
    parser.add_argument("--captured", type=int, default=time.time_ns() // 1_000_000)
    args = parser.parse_args()
    jpeg = sys.stdin.buffer.read(MAX_FRAME_BYTES + 1)
    family, kind, proto, _, address = socket.getaddrinfo(args.host, args.port, type=socket.SOCK_DGRAM)[0]
    with socket.socket(family, kind, proto) as sender:
        for index, packet in enumerate(encode_datagrams(jpeg, args.session, args.sequence, args.captured)):
            sender.sendto(packet, address)
            if index % 4 == 3:
                time.sleep(0.001)


if __name__ == "__main__":
    main()
