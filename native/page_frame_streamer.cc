// Copyright 2026 Chromium Observer Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the Chromium LICENSE file.

#include "chrome/browser/observer/page_frame_streamer.h"

#include <fcntl.h>
#include <sys/socket.h>

#include <cstddef>
#include <utility>

#include "base/files/scoped_file.h"
#include "base/functional/bind.h"
#include "base/task/bind_post_task.h"
#include "base/task/thread_pool.h"
#include "base/threading/platform_thread.h"
#include "base/uuid.h"
#include "chrome/browser/observer/udp_frame_protocol.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/browser/ui/browser_window/public/global_browser_collection.h"
#include "components/tabs/public/tab_interface.h"
#include "components/viz/common/frame_sinks/copy_output_result.h"
#include "content/public/browser/navigation_handle.h"
#include "content/public/browser/web_contents.h"
#include "net/base/ip_address.h"
#include "net/base/ip_endpoint.h"
#include "net/base/url_util.h"
#include "third_party/skia/include/core/SkBitmap.h"
#include "third_party/zlib/zlib.h"
#include "ui/base/base_window.h"
#include "ui/gfx/codec/jpeg_codec.h"
#include "ui/gfx/geometry/rect.h"
#include "ui/gfx/geometry/size.h"
#include "ui/gfx/geometry/size_conversions.h"

namespace observer {
namespace {

constexpr base::TimeDelta kInterval = base::Seconds(1);
constexpr base::TimeDelta kCopyTimeout = base::Milliseconds(500);
constexpr base::TimeDelta kSendTimeout = base::Seconds(2);
constexpr size_t kMaxJpegBytes = 32 * 1024 * 1024;
constexpr double kFrameScale = PAGE_FRAME_STREAM_SCALE;
static_assert(kFrameScale > 0 && kFrameScale <= 1);

content::WebContents* SelectedContents() {
  auto* browser =
      GlobalBrowserCollection::GetInstance()->GetLastActiveBrowser();
  if (!browser || browser->IsDeleteScheduled() ||
      browser->GetProfile()->IsOffTheRecord()) {
    return nullptr;
  }
  auto* window = browser->GetWindow();
  if (!window || !window->IsVisible() || window->IsMinimized()) {
    return nullptr;
  }
  auto* tab = browser->GetActiveTabInterface();
  auto* contents = tab ? tab->GetContents() : nullptr;
  if (!contents || contents->GetVisibility() != content::Visibility::VISIBLE) {
    return nullptr;
  }
  return contents;
}

std::optional<std::vector<uint8_t>> Encode(SkBitmap bitmap) {
  return gfx::JPEGCodec::Encode(bitmap, 80);
}

std::optional<net::IPEndPoint> ParseEndpoint(std::string_view endpoint) {
  if (!endpoint.starts_with("udp://")) {
    return std::nullopt;
  }
  std::string host;
  int port = -1;
  net::IPAddress ip;
  if (!net::ParseHostAndPort(endpoint.substr(6), &host, &port) || port < 1 ||
      port > 65535 || !ip.AssignFromIPLiteral(host)) {
    return std::nullopt;
  }
  return net::IPEndPoint(ip, static_cast<uint16_t>(port));
}

bool SendUdpFrame(const std::string& endpoint,
                  const std::string& session,
                  uint64_t sequence,
                  base::Time captured_at,
                  std::vector<uint8_t> jpeg,
                  std::shared_ptr<std::atomic_bool> cancel) {
  const auto address = ParseEndpoint(endpoint);
  if (!address || cancel->load()) {
    return false;
  }
  sockaddr_storage storage{};
  auto* peer = reinterpret_cast<sockaddr*>(&storage);
  socklen_t peer_size = sizeof(storage);
  if (!address->ToSockAddr(peer, &peer_size)) {
    return false;
  }
  base::ScopedFD socket_fd(socket(peer->sa_family, SOCK_DGRAM, 0));
  if (!socket_fd.is_valid() ||
      fcntl(socket_fd.get(), F_SETFD, FD_CLOEXEC) < 0 ||
      fcntl(socket_fd.get(), F_SETFL, O_NONBLOCK) < 0 ||
      connect(socket_fd.get(), peer, peer_size) < 0) {
    return false;
  }
  const auto deadline = base::TimeTicks::Now() + kSendTimeout;
  const auto checksum = static_cast<uint32_t>(
      crc32(0, jpeg.data(), static_cast<uInt>(jpeg.size())));
  const uint64_t captured_ms =
      (captured_at - base::Time::UnixEpoch()).InMilliseconds();
  const size_t count =
      (jpeg.size() + udp::kPayloadBytes - 1) / udp::kPayloadBytes;
  for (size_t index = 0; index < count; ++index) {
    if (cancel->load() || base::TimeTicks::Now() >= deadline) {
      return false;
    }
    const auto packet = udp::MakeDatagram(session, sequence, captured_ms, jpeg,
                                          index, checksum);
    if (send(socket_fd.get(), packet.data(), packet.size(), 0) !=
        static_cast<ssize_t>(packet.size())) {
      return false;
    }
    // Pace at most four packets per millisecond to avoid a single UDP burst.
    if (index % 4 == 3) {
      base::PlatformThread::Sleep(base::Milliseconds(1));
    }
  }
  return true;
}

}  // namespace

PageFrameStreamer::PageFrameStreamer()
    : PageFrameStreamer(std::string(PAGE_FRAME_STREAM_ENDPOINT)) {}

PageFrameStreamer::PageFrameStreamer(std::string endpoint)
    : endpoint_(std::move(endpoint)),
      session_(base::Uuid::GenerateRandomV4().AsLowercaseString()) {}

PageFrameStreamer::~PageFrameStreamer() {
  PostMainMessageLoopRun();
}

void PageFrameStreamer::PostBrowserStart() {
  if (timer_.IsRunning() || !ParseEndpoint(endpoint_)) {
    return;
  }
  timer_.Start(FROM_HERE, kInterval, this, &PageFrameStreamer::Tick);
}

void PageFrameStreamer::PostMainMessageLoopRun() {
  timer_.Stop();
  weak_factory_.InvalidateWeakPtrs();
  FinishCycle();
  Observe(nullptr);
}

void PageFrameStreamer::FinishCycle() {
  active_cycle_ = 0;
  invalidated_ = false;
  if (send_cancel_) {
    send_cancel_->store(true);
    send_cancel_.reset();
  }
}

void PageFrameStreamer::InvalidateCycle() {
  if (active_cycle_ != 0) {
    // Keep the slot occupied until copy/encoding/sending has replied.
    invalidated_ = true;
    if (send_cancel_) {
      send_cancel_->store(true);
    }
  }
}

void PageFrameStreamer::Tick() {
  auto* selected = SelectedContents();
  if (selected != web_contents()) {
    InvalidateCycle();
    Observe(selected);
  }
  if (!selected || active_cycle_ != 0) {
    return;
  }
  auto* view = selected->GetRenderWidgetHostView();
  if (!view || !view->IsShowing() || !view->IsSurfaceAvailableForCopy()) {
    return;
  }
  gfx::Size output_size;
  if constexpr (kFrameScale < 1) {
    const auto native_size = view->GetVisibleViewportSizeDevicePx();
    if (native_size.IsEmpty()) {
      return;
    }
    output_size =
        gfx::ScaleToRoundedSize(native_size, static_cast<float>(kFrameScale));
    output_size.SetToMax(gfx::Size(1, 1));
  }
  const uint64_t cycle = ++next_cycle_;
  active_cycle_ = cycle;
  view->CopyFromSurface(
      gfx::Rect(), output_size, kCopyTimeout,
      base::BindPostTaskToCurrentDefault(base::BindOnce(
          &PageFrameStreamer::OnCopied, weak_factory_.GetWeakPtr(), cycle,
          base::Time::Now())));
}

bool PageFrameStreamer::IsCurrentCycle(uint64_t cycle) const {
  return !invalidated_ && active_cycle_ == cycle && web_contents() &&
         SelectedContents() == web_contents();
}

void PageFrameStreamer::OnCopied(uint64_t cycle,
                                 base::Time captured_at,
                                 const content::CopyFromSurfaceResult& result) {
  if (active_cycle_ != cycle) {
    return;
  }
  if (!IsCurrentCycle(cycle) || !result.has_value() ||
      result->bitmap.drawsNothing()) {
    FinishCycle();
    return;
  }
  base::ThreadPool::PostTaskAndReplyWithResult(
      FROM_HERE, {base::TaskPriority::BEST_EFFORT},
      base::BindOnce(&Encode, result->bitmap),
      base::BindOnce(&PageFrameStreamer::OnEncoded, weak_factory_.GetWeakPtr(),
                     cycle, captured_at));
}

void PageFrameStreamer::OnEncoded(uint64_t cycle,
                                  base::Time captured_at,
                                  std::optional<std::vector<uint8_t>> jpeg) {
  if (active_cycle_ != cycle) {
    return;
  }
  if (!IsCurrentCycle(cycle) || !jpeg || jpeg->empty() ||
      jpeg->size() > kMaxJpegBytes) {
    FinishCycle();
    return;
  }
  send_cancel_ = std::make_shared<std::atomic_bool>(false);
  base::ThreadPool::PostTaskAndReplyWithResult(
      FROM_HERE, {base::MayBlock(), base::TaskPriority::BEST_EFFORT},
      base::BindOnce(&SendUdpFrame, endpoint_, session_, cycle, captured_at,
                     std::move(*jpeg), send_cancel_),
      base::BindOnce(&PageFrameStreamer::OnSent, weak_factory_.GetWeakPtr(),
                     cycle));
}

void PageFrameStreamer::OnSent(uint64_t cycle, bool sent) {
  if (active_cycle_ == cycle) {
    FinishCycle();
  }
}

void PageFrameStreamer::DidStartNavigation(
    content::NavigationHandle* navigation) {
  if (navigation->IsInPrimaryMainFrame()) {
    InvalidateCycle();
  }
}

void PageFrameStreamer::WebContentsDestroyed() {
  InvalidateCycle();
  Observe(nullptr);
}

}  // namespace observer
