// Copyright 2026 Chromium Observer Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the Chromium LICENSE file.

#include "chrome/browser/observer/page_frame_streamer.h"

#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>

#include <array>
#include <map>

#include "base/containers/span.h"
#include "base/files/scoped_file.h"
#include "base/memory/raw_ptr.h"
#include "base/run_loop.h"
#include "base/strings/string_number_conversions.h"
#include "base/test/run_until.h"
#include "base/test/scoped_run_loop_timeout.h"
#include "chrome/browser/observer/udp_frame_protocol.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/test/base/in_process_browser_test.h"
#include "chrome/test/base/ui_test_utils.h"
#include "components/tabs/public/tab_interface.h"
#include "content/public/browser/web_contents.h"
#include "content/public/test/browser_test.h"
#include "content/public/test/browser_test_utils.h"
#include "testing/gtest/include/gtest/gtest.h"
#include "third_party/skia/include/core/SkBitmap.h"
#include "third_party/skia/include/core/SkColor.h"
#include "ui/gfx/codec/jpeg_codec.h"
#include "ui/gfx/geometry/size.h"
#include "ui/gfx/geometry/size_conversions.h"
#include "url/gurl.h"

namespace observer {

class PageFrameStreamerBrowserTest : public InProcessBrowserTest {
 protected:
  void SetUpOnMainThread() override {
    receiver_.reset(socket(AF_INET, SOCK_DGRAM, 0));
    ASSERT_TRUE(receiver_.is_valid());
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    ASSERT_EQ(0, bind(receiver_.get(), reinterpret_cast<sockaddr*>(&address),
                      sizeof(address)));
    socklen_t length = sizeof(address);
    ASSERT_EQ(0, getsockname(receiver_.get(),
                             reinterpret_cast<sockaddr*>(&address), &length));
    ASSERT_EQ(0, fcntl(receiver_.get(), F_SETFL, O_NONBLOCK));
    endpoint_ =
        "udp://127.0.0.1:" + base::NumberToString(ntohs(address.sin_port));
    ASSERT_TRUE(ui_test_utils::NavigateToURL(
        browser(), GURL("data:text/html,<body "
                        "style='margin:0;background:rgb(20,80,200)'>")));
    contents_ = browser()->GetActiveTabInterface()->GetContents();
    content::WaitForCopyableViewInWebContents(contents_);
    streamer_ = std::make_unique<PageFrameStreamer>(endpoint_);
    streamer_->PostBrowserStart();
    streamer_->timer_.Stop();
  }

  void TearDownOnMainThread() override {
    streamer_.reset();
    contents_ = nullptr;
    receiver_.reset();
  }

  void Tick() { streamer_->Tick(); }
  bool Idle() const { return streamer_->active_cycle_ == 0; }
  void WaitIdle() {
    ASSERT_TRUE(base::test::RunUntil([&] { return Idle(); }));
  }
  bool ReceiveFrame() {
    std::array<uint8_t, udp::kDatagramBytes> packet;
    for (;;) {
      const ssize_t length =
          recv(receiver_.get(), packet.data(), packet.size(), 0);
      if (length <= static_cast<ssize_t>(udp::kHeaderBytes)) {
        return false;
      }
      const size_t index = (static_cast<size_t>(packet[60]) << 8) | packet[61];
      const size_t count = (static_cast<size_t>(packet[62]) << 8) | packet[63];
      chunks_[index] = std::vector<uint8_t>(packet.begin() + udp::kHeaderBytes,
                                            packet.begin() + length);
      if (chunks_.size() != count) {
        continue;
      }
      jpeg_.clear();
      for (size_t n = 0; n < count; ++n) {
        const auto& chunk = chunks_.at(n);
        jpeg_.insert(jpeg_.end(), chunk.begin(), chunk.end());
      }
      chunks_.clear();
      return true;
    }
  }
  void WaitForFrame() {
    base::test::ScopedRunLoopTimeout timeout(FROM_HERE, base::Seconds(15));
    ASSERT_TRUE(base::test::RunUntil([&] { return ReceiveFrame(); }));
  }
  uint64_t BeginSyntheticCapture(content::WebContents* contents) {
    streamer_->Observe(contents);
    const auto cycle = ++streamer_->next_cycle_;
    streamer_->active_cycle_ = cycle;
    return cycle;
  }
  void DeliverEncoding(uint64_t cycle) {
    streamer_->OnEncoded(cycle, base::Time::Now(),
                         std::vector<uint8_t>{0xff, 0xd8, 0xff, 0xd9});
  }
  void ExpectCancelledOnShutdown() {
    auto cancel = std::make_shared<std::atomic_bool>(false);
    streamer_->send_cancel_ = cancel;
    streamer_->PostMainMessageLoopRun();
    EXPECT_TRUE(cancel->load());
    EXPECT_TRUE(Idle());
  }

  base::ScopedFD receiver_;
  std::string endpoint_;
  raw_ptr<content::WebContents> contents_ = nullptr;
  std::unique_ptr<PageFrameStreamer> streamer_;
  std::map<size_t, std::vector<uint8_t>> chunks_;
  std::vector<uint8_t> jpeg_;
};

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       CapturesPagePixelsOverUdp) {
  Tick();
  WaitForFrame();
  SkBitmap bitmap = gfx::JPEGCodec::Decode(jpeg_);
  ASSERT_FALSE(bitmap.drawsNothing());
  auto size = gfx::ScaleToRoundedSize(
      contents_->GetRenderWidgetHostView()->GetVisibleViewportSizeDevicePx(),
      static_cast<float>(PAGE_FRAME_STREAM_SCALE));
  size.SetToMax(gfx::Size(1, 1));
  EXPECT_EQ(size.width(), bitmap.width());
  EXPECT_EQ(size.height(), bitmap.height());
  const SkColor center =
      bitmap.getColor(bitmap.width() / 2, bitmap.height() / 2);
  EXPECT_NEAR(20, SkColorGetR(center), 5);
  EXPECT_NEAR(80, SkColorGetG(center), 5);
  EXPECT_NEAR(200, SkColorGetB(center), 5);
}

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       BusyCycleSkipsTicksAndRecovers) {
  BeginSyntheticCapture(contents_);
  Tick();
  EXPECT_FALSE(ReceiveFrame());
  DeliverEncoding(1);
  WaitForFrame();
  WaitIdle();
  Tick();
  WaitForFrame();
}

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       NavigationDiscardsOldEncoding) {
  const uint64_t cycle = BeginSyntheticCapture(contents_);
  ASSERT_TRUE(ui_test_utils::NavigateToURL(
      browser(),
      GURL("data:text/html,<body style='background:green'>new page")));
  Tick();
  EXPECT_FALSE(Idle());
  DeliverEncoding(cycle);
  EXPECT_TRUE(Idle());
  EXPECT_FALSE(ReceiveFrame());
  content::WaitForCopyableViewInWebContents(contents_);
  Tick();
  WaitForFrame();
}

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       DestroyedContentsDiscardsOldEncoding) {
  auto temporary = content::WebContents::Create(
      content::WebContents::CreateParams(browser()->GetProfile()));
  const uint64_t cycle = BeginSyntheticCapture(temporary.get());
  temporary.reset();
  DeliverEncoding(cycle);
  EXPECT_TRUE(Idle());
  EXPECT_FALSE(ReceiveFrame());
  Tick();
  WaitForFrame();
}

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       ShutdownCancelsPendingSend) {
  BeginSyntheticCapture(contents_);
  ExpectCancelledOnShutdown();
  EXPECT_FALSE(ReceiveFrame());
}

}  // namespace observer
