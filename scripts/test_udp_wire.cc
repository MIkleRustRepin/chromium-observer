// Compile/run from test_udp_wire.py. Exercises the actual native packetizer.
#include "../native/udp_frame_protocol.h"

#include <cstdio>
#include <vector>
#include <zlib.h>

int main() {
  std::vector<uint8_t> jpeg{0xff, 0xd8};
  for (int n = 0; n < 3840; ++n) {
    jpeg.push_back(static_cast<uint8_t>(n % 256));
  }
  jpeg.push_back(0xff);
  jpeg.push_back(0xd9);
  const uint32_t crc =
      static_cast<uint32_t>(crc32(0, jpeg.data(), jpeg.size()));
  const size_t count = (jpeg.size() + observer::udp::kPayloadBytes - 1) /
                       observer::udp::kPayloadBytes;
  for (size_t i = 0; i < count; ++i) {
    const auto packet = observer::udp::MakeDatagram(
        "00000000-0000-4000-8000-000000000001", 42, 1234, jpeg, i, crc);
    std::putchar(static_cast<int>(packet.size() >> 8));
    std::putchar(static_cast<int>(packet.size() & 0xff));
    std::fwrite(packet.data(), 1, packet.size(), stdout);
  }
}
