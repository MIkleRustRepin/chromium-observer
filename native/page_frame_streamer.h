// Copyright 2026 Chromium Observer Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the Chromium LICENSE file.

#ifndef CHROME_BROWSER_OBSERVER_PAGE_FRAME_STREAMER_H_
#define CHROME_BROWSER_OBSERVER_PAGE_FRAME_STREAMER_H_

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "base/memory/scoped_refptr.h"
#include "base/memory/weak_ptr.h"
#include "base/time/time.h"
#include "base/timer/timer.h"
#include "chrome/browser/chrome_browser_main_extra_parts.h"
#include "content/public/browser/render_widget_host_view.h"
#include "content/public/browser/web_contents_observer.h"
#include "url/gurl.h"

namespace net {
class HttpResponseHeaders;
}
namespace network {
class SharedURLLoaderFactory;
class SimpleURLLoader;
}  // namespace network

namespace observer {

// Browser-process-only capture. Nothing is installed into renderer contexts.
class PageFrameStreamer : public ChromeBrowserMainExtraParts,
                          public content::WebContentsObserver {
 public:
  PageFrameStreamer();
  explicit PageFrameStreamer(const GURL& endpoint);
  ~PageFrameStreamer() override;

  void PostBrowserStart() override;
  void PostMainMessageLoopRun() override;

 private:
  friend class PageFrameStreamerBrowserTest;

  void Tick();
  void InvalidateCycle();
  void FinishCycle();
  bool IsCurrentCycle(uint64_t cycle) const;
  void OnCopied(uint64_t cycle,
                base::Time captured_at,
                const content::CopyFromSurfaceResult& result);
  void OnEncoded(uint64_t cycle,
                 base::Time captured_at,
                 std::optional<std::vector<uint8_t>> jpeg);
  void OnUploaded(uint64_t cycle,
                  scoped_refptr<net::HttpResponseHeaders> headers);

  void DidStartNavigation(content::NavigationHandle* navigation) override;
  void WebContentsDestroyed() override;

  const GURL endpoint_;
  const std::string session_;
  uint64_t next_cycle_ = 0;
  uint64_t active_cycle_ = 0;
  bool invalidated_ = false;
  base::RepeatingTimer timer_;
  scoped_refptr<network::SharedURLLoaderFactory> factory_;
  std::unique_ptr<network::SimpleURLLoader> upload_;
  base::WeakPtrFactory<PageFrameStreamer> weak_factory_{this};
};

}  // namespace observer

#endif  // CHROME_BROWSER_OBSERVER_PAGE_FRAME_STREAMER_H_
