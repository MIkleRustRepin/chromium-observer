// Copyright 2026 Chromium Observer Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the Chromium LICENSE file.

#ifndef CHROME_BROWSER_OBSERVER_UDP_FRAME_PROTOCOL_H_
#define CHROME_BROWSER_OBSERVER_UDP_FRAME_PROTOCOL_H_

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>
#include <vector>

namespace observer::udp {

inline constexpr size_t kDatagramBytes = 1200;
inline constexpr size_t kHeaderBytes = 68;
inline constexpr size_t kPayloadBytes = kDatagramBytes - kHeaderBytes;
inline constexpr size_t kMaxFrameBytes = 32 * 1024 * 1024;

// COU1, ASCII UUID, sequence, capture time (Unix ms), JPEG size,
// chunk index/count, CRC32. Integers use network byte order.
inline std::vector<uint8_t> MakeDatagram(std::string_view session,
                                         uint64_t sequence,
                                         uint64_t captured_ms,
                                         std::span<const uint8_t> jpeg,
                                         size_t index,
                                         uint32_t checksum) {
  const size_t count = (jpeg.size() + kPayloadBytes - 1) / kPayloadBytes;
  if (session.size() != 36 || !sequence || !captured_ms || jpeg.size() < 4 ||
      jpeg.size() > kMaxFrameBytes || index >= count) {
    return {};
  }
  const auto payload = jpeg.subspan(
      index * kPayloadBytes,
      std::min(kPayloadBytes, jpeg.size() - index * kPayloadBytes));
  std::vector<uint8_t> result(kHeaderBytes + payload.size());
  std::copy_n("COU1", 4, result.begin());
  std::copy(session.begin(), session.end(), result.begin() + 4);
  auto write = [&](size_t offset, uint64_t value, size_t bytes) {
    for (size_t n = 0; n < bytes; ++n) {
      result[offset + bytes - 1 - n] = static_cast<uint8_t>(value & 0xff);
      value >>= 8;
    }
  };
  write(40, sequence, 8);
  write(48, captured_ms, 8);
  write(56, jpeg.size(), 4);
  write(60, index, 2);
  write(62, count, 2);
  write(64, checksum, 4);
  std::copy(payload.begin(), payload.end(), result.begin() + kHeaderBytes);
  return result;
}

}  // namespace observer::udp

#endif  // CHROME_BROWSER_OBSERVER_UDP_FRAME_PROTOCOL_H_
