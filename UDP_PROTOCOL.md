# COU1

Native Chromium sends one JPEG per capture cycle as independent UDP datagrams.
Default receiver: `udp://127.0.0.1:8766`. The HTTP viewer uses TCP port 8765.
Every datagram has a 68-byte header followed by up to 1132 JPEG bytes. Maximum
UDP payload including the header is 1200 bytes. All integers are unsigned and
encoded in network byte order. Python header format: `!4s36sQQIHHI`.

| Offset | Bytes | Field |
| --- | --- | --- |
| 0 | 4 | ASCII `COU1` |
| 4 | 36 | Lowercase UUID session ID, ASCII with hyphens |
| 40 | 8 | Frame sequence, starts at 1, gaps permitted |
| 48 | 8 | Capture time, Unix milliseconds |
| 56 | 4 | Complete JPEG size, 4 through 33554432 bytes |
| 60 | 2 | Chunk index, starts at 0 |
| 62 | 2 | Chunk count, `ceil(JPEG size / 1132)` |
| 64 | 4 | CRC32 of the complete JPEG (`zlib.crc32`) |
| 68 | variable | JPEG bytes starting at `index * 1132` |

Each chunk repeats the frame metadata. All chunks except the last have 1132
payload bytes. A frame is identified by sender address/port, session and sequence.
Identical duplicate chunks are ignored; conflicting duplicates discard the
assembly. Chunks may arrive in any order. Publication requires all chunks,
matching CRC32 and JPEG SOI/EOI markers. The server keeps only the latest complete
frame. Older sequences and retired sessions cannot replace it.

There are no acknowledgements or retransmissions. Assemblies expire three
seconds after their first chunk; at most two assemblies are buffered. Sending
is paced at four packets per millisecond and stops after two seconds or capture
cancellation. Lost frames leave the previous image visible until a complete
new frame arrives. No encryption or authentication is provided by COU1.
