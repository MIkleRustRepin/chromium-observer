// Copyright 2026 Chromium Observer Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the Chromium LICENSE file.

#include "chrome/browser/observer/page_frame_streamer.h"

#include "base/containers/span.h"
#include "base/functional/bind.h"
#include "base/memory/raw_ptr.h"
#include "base/run_loop.h"
#include "base/test/run_until.h"
#include "base/test/scoped_run_loop_timeout.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/test/base/in_process_browser_test.h"
#include "chrome/test/base/ui_test_utils.h"
#include "components/tabs/public/tab_interface.h"
#include "content/public/browser/web_contents.h"
#include "content/public/test/browser_test.h"
#include "content/public/test/browser_test_utils.h"
#include "net/http/http_status_code.h"
#include "services/network/public/cpp/weak_wrapper_shared_url_loader_factory.h"
#include "services/network/test/test_url_loader_factory.h"
#include "services/network/test/test_utils.h"
#include "testing/gtest/include/gtest/gtest.h"
#include "third_party/skia/include/core/SkBitmap.h"
#include "third_party/skia/include/core/SkColor.h"
#include "ui/gfx/codec/jpeg_codec.h"
#include "ui/gfx/geometry/size.h"

namespace observer {

class PageFrameStreamerBrowserTest : public InProcessBrowserTest {
 protected:
  void SetUpOnMainThread() override {
    ASSERT_TRUE(ui_test_utils::NavigateToURL(
        browser(), GURL("data:text/html,<body "
                        "style='margin:0;background:rgb(20,80,200)'>")));
    contents_ = browser()->GetActiveTabInterface()->GetContents();
    content::WaitForCopyableViewInWebContents(contents_);
    streamer_ = std::make_unique<PageFrameStreamer>(endpoint_);
    streamer_->PostBrowserStart();
    // Tests advance individual ticks without depending on wall-clock timing.
    streamer_->timer_.Stop();
    streamer_->factory_ = factory_.GetSafeWeakWrapper();
    factory_.SetInterceptor(base::BindRepeating(
        [](int* count, const network::ResourceRequest&) { ++*count; },
        &requests_));
  }

  void TearDownOnMainThread() override {
    streamer_.reset();
    contents_ = nullptr;
  }

  void Tick() { streamer_->Tick(); }
  bool Idle() const { return streamer_->active_cycle_ == 0; }
  void WaitForRequests(int count) {
    base::test::ScopedRunLoopTimeout timeout(FROM_HERE, base::Seconds(15));
    ASSERT_TRUE(base::test::RunUntil([&] { return requests_ >= count; }));
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

  const GURL endpoint_{"http://127.0.0.1:18766/api/frame"};
  network::TestURLLoaderFactory factory_;
  int requests_ = 0;
  raw_ptr<content::WebContents> contents_ = nullptr;
  std::unique_ptr<PageFrameStreamer> streamer_;
};

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       CapturesPagePixelsWithoutCredentials) {
  Tick();
  WaitForRequests(1);
  auto* pending = factory_.GetPendingRequest(0);
  ASSERT_TRUE(pending);
  const auto& request = pending->request;
  EXPECT_EQ("POST", request.method);
  EXPECT_EQ(network::mojom::CredentialsMode::kOmit, request.credentials_mode);
  EXPECT_EQ(network::mojom::RedirectMode::kError, request.redirect_mode);
  EXPECT_TRUE(request.headers.HasHeader("X-Frame-Session"));
  EXPECT_TRUE(request.headers.HasHeader("X-Frame-Sequence"));
  EXPECT_TRUE(request.headers.HasHeader("X-Captured-At"));
  const std::string bytes = network::GetUploadData(request);
  SkBitmap bitmap = gfx::JPEGCodec::Decode(base::as_byte_span(bytes));
  ASSERT_FALSE(bitmap.drawsNothing());
  const auto size =
      contents_->GetRenderWidgetHostView()->GetVisibleViewportSizeDevicePx();
  EXPECT_EQ(size.width(), bitmap.width());
  EXPECT_EQ(size.height(), bitmap.height());
  const SkColor center =
      bitmap.getColor(bitmap.width() / 2, bitmap.height() / 2);
  EXPECT_NEAR(20, SkColorGetR(center), 5);
  EXPECT_NEAR(80, SkColorGetG(center), 5);
  EXPECT_NEAR(200, SkColorGetB(center), 5);
}

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       SlowUploadSkipsTicksAndErrorAllowsRecovery) {
  Tick();
  WaitForRequests(1);
  Tick();
  Tick();
  Tick();
  base::RunLoop().RunUntilIdle();
  EXPECT_EQ(1, requests_);
  ASSERT_TRUE(factory_.SimulateResponseForPendingRequest(
      endpoint_.spec(), "", net::HTTP_SERVICE_UNAVAILABLE));
  ASSERT_TRUE(base::test::RunUntil([&] { return Idle(); }));
  Tick();
  WaitForRequests(2);
}

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       NavigationDiscardsOldEncoding) {
  const uint64_t cycle = BeginSyntheticCapture(contents_);
  ASSERT_TRUE(ui_test_utils::NavigateToURL(
      browser(),
      GURL("data:text/html,<body style='background:green'>new page")));
  // Invalidation must keep the old encoding slot occupied until its reply.
  Tick();
  EXPECT_FALSE(Idle());
  EXPECT_EQ(0, requests_);
  DeliverEncoding(cycle);
  base::RunLoop().RunUntilIdle();
  EXPECT_EQ(0, requests_);
  EXPECT_TRUE(Idle());
  content::WaitForCopyableViewInWebContents(contents_);
  Tick();
  WaitForRequests(1);
}

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       DestroyedContentsDiscardsOldEncoding) {
  auto temporary = content::WebContents::Create(
      content::WebContents::CreateParams(browser()->GetProfile()));
  const uint64_t cycle = BeginSyntheticCapture(temporary.get());
  temporary.reset();
  DeliverEncoding(cycle);
  base::RunLoop().RunUntilIdle();
  EXPECT_EQ(0, requests_);
  EXPECT_TRUE(Idle());
  Tick();
  WaitForRequests(1);
}

IN_PROC_BROWSER_TEST_F(PageFrameStreamerBrowserTest,
                       ShutdownCancelsPendingUpload) {
  Tick();
  WaitForRequests(1);
  streamer_->PostMainMessageLoopRun();
  base::RunLoop().RunUntilIdle();
  EXPECT_TRUE(Idle());
  EXPECT_EQ(1, requests_);
}

}  // namespace observer
