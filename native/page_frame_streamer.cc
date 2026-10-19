// Copyright 2026 Chromium Observer Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the Chromium LICENSE file.

#include "chrome/browser/observer/page_frame_streamer.h"

#include <cstddef>
#include <utility>

#include "base/functional/bind.h"
#include "base/strings/string_number_conversions.h"
#include "base/task/bind_post_task.h"
#include "base/task/thread_pool.h"
#include "base/uuid.h"
#include "chrome/browser/net/system_network_context_manager.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/browser/ui/browser_window/public/global_browser_collection.h"
#include "components/tabs/public/tab_interface.h"
#include "components/viz/common/frame_sinks/copy_output_result.h"
#include "content/public/browser/navigation_handle.h"
#include "content/public/browser/web_contents.h"
#include "net/base/load_flags.h"
#include "net/base/request_priority.h"
#include "net/http/http_response_headers.h"
#include "net/traffic_annotation/network_traffic_annotation.h"
#include "services/network/public/cpp/resource_request.h"
#include "services/network/public/cpp/shared_url_loader_factory.h"
#include "services/network/public/cpp/simple_url_loader.h"
#include "services/network/public/mojom/fetch_api.mojom.h"
#include "third_party/skia/include/core/SkBitmap.h"
#include "ui/base/base_window.h"
#include "ui/gfx/codec/jpeg_codec.h"
#include "ui/gfx/geometry/rect.h"
#include "ui/gfx/geometry/size.h"

namespace observer {
namespace {

constexpr base::TimeDelta kInterval = base::Seconds(1);
constexpr base::TimeDelta kCopyTimeout = base::Milliseconds(500);
constexpr base::TimeDelta kUploadTimeout = base::Seconds(2);
constexpr size_t kMaxJpegBytes = 32 * 1024 * 1024;

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

constexpr net::NetworkTrafficAnnotationTag kTrafficAnnotation =
    net::DefineNetworkTrafficAnnotation("chromium_observer_page_frame", R"(
      semantics {
        sender: "Chromium Observer"
        description:
          "This dedicated Chromium fork sends a JPEG of the visible active "
          "page to the owner's observation server."
        trigger: "Once per second while an eligible browser window is visible."
        data: "Rendered page pixels, capture time, session ID and frame number."
        destination: OTHER
        destination_other: "The observation endpoint configured at build time."
      }
      policy {
        cookies_allowed: NO
        setting: "Enabled automatically in this dedicated observation build."
        policy_exception_justification:
          "This is an owner-operated custom browser build for observing an AI agent."
      }
    )");

}  // namespace

PageFrameStreamer::PageFrameStreamer()
    : PageFrameStreamer(GURL(PAGE_FRAME_STREAM_ENDPOINT)) {}

PageFrameStreamer::PageFrameStreamer(const GURL& endpoint)
    : endpoint_(endpoint),
      session_(base::Uuid::GenerateRandomV4().AsLowercaseString()) {}

PageFrameStreamer::~PageFrameStreamer() {
  PostMainMessageLoopRun();
}

void PageFrameStreamer::PostBrowserStart() {
  if (timer_.IsRunning() || !endpoint_.SchemeIsHTTPOrHTTPS()) {
    return;
  }
  // The system context is separate from profiles and page storage partitions.
  factory_ =
      SystemNetworkContextManager::GetInstance()->GetSharedURLLoaderFactory();
  timer_.Start(FROM_HERE, kInterval, this, &PageFrameStreamer::Tick);
}

void PageFrameStreamer::PostMainMessageLoopRun() {
  timer_.Stop();
  weak_factory_.InvalidateWeakPtrs();
  FinishCycle();
  Observe(nullptr);
  factory_.reset();
}

void PageFrameStreamer::FinishCycle() {
  active_cycle_ = 0;
  invalidated_ = false;
  upload_.reset();
}

void PageFrameStreamer::InvalidateCycle() {
  if (upload_) {
    FinishCycle();
  } else if (active_cycle_ != 0) {
    // Copy/encoding work cannot be cancelled. Keep the slot occupied until
    // its callback returns, so repeated navigations cannot queue bitmaps.
    invalidated_ = true;
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
  const uint64_t cycle = ++next_cycle_;
  active_cycle_ = cycle;
  view->CopyFromSurface(
      gfx::Rect(), gfx::Size(), kCopyTimeout,
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
  auto request = std::make_unique<network::ResourceRequest>();
  request->url = endpoint_;
  request->method = "POST";
  request->priority = net::IDLE;
  request->credentials_mode = network::mojom::CredentialsMode::kOmit;
  request->redirect_mode = network::mojom::RedirectMode::kError;
  request->load_flags = net::LOAD_DISABLE_CACHE | net::LOAD_BYPASS_CACHE;
  request->do_not_prompt_for_login = true;
  request->headers.SetHeader("X-Frame-Session", session_);
  request->headers.SetHeader("X-Frame-Sequence", base::NumberToString(cycle));
  request->headers.SetHeader(
      "X-Captured-At",
      base::NumberToString(
          (captured_at - base::Time::UnixEpoch()).InMilliseconds()));
  upload_ =
      network::SimpleURLLoader::Create(std::move(request), kTrafficAnnotation);
  upload_->SetTimeoutDuration(kUploadTimeout);
  upload_->AttachStringForUpload(
      std::string(reinterpret_cast<const char*>(jpeg->data()), jpeg->size()),
      "image/jpeg");
  upload_->DownloadHeadersOnly(
      factory_.get(), base::BindOnce(&PageFrameStreamer::OnUploaded,
                                     weak_factory_.GetWeakPtr(), cycle));
}

void PageFrameStreamer::OnUploaded(
    uint64_t cycle,
    scoped_refptr<net::HttpResponseHeaders> headers) {
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
