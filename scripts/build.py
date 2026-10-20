#!/usr/bin/env python3
"""Build this fork natively, stopping if disk reserve falls below 5 GiB."""
import argparse
import json
import ipaddress
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import urlparse

from devtools_types import prepare_devtools_types
from build_isolation import isolate_build_command

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "chromium" / "src"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--jobs", type=int, default=2)
parser.add_argument("--endpoint", default="udp://127.0.0.1:8766")
parser.add_argument("--scale", type=float, default=1.0,
                    help="Frame width/height relative to rendered pixels, 0 < scale <= 1 (default: 1.0)")
parser.add_argument("--tests", action="store_true", help="Also build browser_tests")
parser.add_argument("--target", action="append", help="Build a specific Ninja target instead of chrome")
parser.add_argument("--generate-only", action="store_true", help="Validate/generate GN configuration without compiling")
args = parser.parse_args()
if not math.isfinite(args.scale) or not 0 < args.scale <= 1:
    parser.error("--scale must be greater than 0 and at most 1")
try:
    endpoint = urlparse(args.endpoint)
    ipaddress.ip_address(endpoint.hostname)
    if (endpoint.scheme != "udp" or not endpoint.port or endpoint.path or endpoint.query
            or endpoint.fragment or endpoint.username is not None or endpoint.password is not None
            or "%" in endpoint.hostname):
        raise ValueError("Invalid UDP endpoint")
except (TypeError, ValueError):
    sys.exit("Endpoint must be udp://IP:PORT, for example udp://127.0.0.1:8766 or udp://[::1]:8766.")
if args.jobs < 1:
    sys.exit("--jobs must be positive")
if sys.platform not in ("linux", "darwin"):
    sys.exit("Only native Linux and macOS builds are supported.")
if shutil.disk_usage(ROOT).free < 15 * 1024**3:
    sys.exit("Need at least 15 GiB of free disk before starting the build.")
if not (SRC / "chrome/browser/observer/page_frame_streamer.cc").is_file():
    sys.exit("Apply the observer patch before building (scripts/apply_patch.py).")
prepare_devtools_types(SRC)
config = ["is_debug = false", "is_component_build = false", "is_official_build = false",
          "symbol_level = 0", "blink_symbol_level = 0", "v8_symbol_level = 0",
          "use_remoteexec = false", "use_siso = false", "concurrent_links = 1",
          'page_frame_stream_endpoint = ' + json.dumps(args.endpoint),
          'page_frame_stream_scale = ' + json.dumps(str(args.scale))]
if sys.platform == "darwin":
    config.append('target_cpu = "arm64"')
else:
    config.append('target_cpu = "x64"')
env = os.environ.copy()
env["PATH"] = str(ROOT / "tools/depot_tools") + os.pathsep + env["PATH"]
env["DEPOT_TOOLS_UPDATE"] = "0"
output = SRC / "out/Observer"
output.mkdir(parents=True, exist_ok=True)
(output / "args.gn").write_text("\n".join(config) + "\n")
subprocess.run(["gn", "gen", "out/Observer", "--fail-on-unused-args"], cwd=SRC, env=env, check=True)
if args.generate_only:
    sys.exit(0)
targets = (args.target or ["chrome"]) + (["browser_tests"] if args.tests else [])
command = ["autoninja", "-C", "out/Observer", "-j", str(args.jobs), *targets]
command = isolate_build_command(command, SRC)
process = subprocess.Popen(command, cwd=SRC, env=env, start_new_session=True)
try:
    while process.poll() is None:
        if shutil.disk_usage(ROOT).free < 5 * 1024**3:
            print("Disk reserve below 5 GiB: stopping this build; no files will be removed.", flush=True)
            os.killpg(process.pid, signal.SIGTERM)
            process.wait()
            sys.exit(2)
        time.sleep(5)
except KeyboardInterrupt:
    os.killpg(process.pid, signal.SIGTERM)
    process.wait()
    sys.exit(130)
sys.exit(process.returncode)
