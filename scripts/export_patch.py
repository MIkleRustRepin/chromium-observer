#!/usr/bin/env python3
"""Export only this fork's changes; preserve other checkout modifications."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "chromium/src"
files = [
    "chrome/browser/observer/BUILD.gn",
    "chrome/browser/observer/udp_frame_protocol.h",
    "chrome/browser/observer/page_frame_streamer.h",
    "chrome/browser/observer/page_frame_streamer.cc",
    "chrome/browser/observer/page_frame_streamer_browsertest.cc",
]
subprocess.run(["git", "add", "--intent-to-add", "--", *files], cwd=SRC, check=True)
patch = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--", *files,
                                 "chrome/browser/BUILD.gn",
                                 "chrome/browser/chrome_browser_main.cc",
                                 "chrome/test/BUILD.gn"], cwd=SRC)
folder = ROOT / "patches"
folder.mkdir(exist_ok=True)
(folder / "observer.patch").write_bytes(patch)
print(f"Exported {len(patch)} bytes to patches/observer.patch")
