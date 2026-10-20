#!/bin/bash
# Build Chromium Observer on an Apple Silicon Mac. Compatible with macOS Bash 3.2.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./build-mac.sh [--jobs N] [--endpoint URL] [--scale FACTOR] [--tests] [--clean]

Downloads the pinned Chromium/toolchain, applies the observer patch, builds an
arm64 Chromium.app and creates artifacts/chromium-observer-macos-arm64.tar.gz.

Requirements: Apple Silicon, full Xcode opened once, APFS, 100 GiB free for a
fresh checkout (150+ recommended). No Homebrew or globally installed Python
packages are required. Downloads and compilation resume on the next run.

  --jobs N       Parallel compile jobs (default chosen from RAM and CPU count)
  --endpoint URL UDP frame receiver, udp://IP:PORT (default udp://127.0.0.1:8766)
  --scale FACTOR Frame width/height scale, 0 < scale <= 1 (default: 0.75)
  --tests        Also build browser_tests; does not run the tests
  --clean        Clear only out/Observer build artifacts before compiling
  -h, --help     Print this help

If downloaded as a standalone script, clones the project to
~/chromium-observer, or to CHROMIUM_OBSERVER_DIR when set.
EOF
}

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

jobs=''
endpoint='udp://127.0.0.1:8766'
scale='0.75'
tests=0
clean=0
original_argc=$#
original_args=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs)
      [[ $# -ge 2 ]] || fail '--jobs needs a positive integer'
      jobs="$2"; shift 2 ;;
    --endpoint)
      [[ $# -ge 2 ]] || fail '--endpoint needs udp://IP:PORT'
      endpoint="$2"; shift 2 ;;
    --scale)
      [[ $# -ge 2 ]] || fail '--scale needs a number greater than 0 and at most 1'
      scale="$2"; shift 2 ;;
    --tests) tests=1; shift ;;
    --clean) clean=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown argument: $1 (see --help)" ;;
  esac
done
if [[ -n "$jobs" ]]; then
  [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || fail '--jobs must be a positive integer'
fi
[[ "$(uname -s)" == Darwin ]] || fail 'Run this script on macOS.'
[[ "$(uname -m)" == arm64 ]] || fail 'Use an Apple Silicon Mac and a native arm64 terminal (not Rosetta).'

# Select an installed Xcode for this process without changing global settings.
if [[ -z "${DEVELOPER_DIR:-}" ]]; then
  developer_dir="$(xcode-select -p 2>/dev/null || true)"
  if [[ "$developer_dir" != */Contents/Developer ]] && [[ -d /Applications/Xcode.app/Contents/Developer ]]; then
    export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  fi
fi
command -v xcodebuild >/dev/null || fail 'Install full Xcode from the Mac App Store and open it once.'
xcodebuild -version >/dev/null 2>&1 || fail 'Full Xcode is required. Install/open Xcode, or set DEVELOPER_DIR to its Contents/Developer directory.'
xcodebuild -checkFirstLaunchStatus >/dev/null 2>&1 || fail 'Open Xcode once to accept its license and install its components, then rerun.'
sdk_path="$(xcrun --sdk macosx --show-sdk-path)"
[[ -d "$sdk_path" ]] || fail 'The macOS SDK is missing from the selected Xcode.'
sdk_version="$(xcrun --sdk macosx --show-sdk-version)"
command -v git >/dev/null || fail 'Git is missing; install the Xcode command-line tools.'
command -v python3 >/dev/null || fail 'Python 3 from Xcode command-line tools is missing.'
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || fail 'Bootstrap needs Python 3.9+ from Xcode command-line tools.'
python3 -c 'import math,sys; value=float(sys.argv[1]); sys.exit(0 if math.isfinite(value) and 0 < value <= 1 else 1)' "$scale" || fail '--scale must be a number greater than 0 and at most 1'
python3 -c 'import ipaddress,sys; from urllib.parse import urlparse; e=urlparse(sys.argv[1]); ipaddress.ip_address(e.hostname); assert e.scheme=="udp" and e.port and not (e.path or e.query or e.fragment or "%" in e.hostname) and e.username is None and e.password is None' "$endpoint" || fail '--endpoint must be udp://IP:PORT'

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ ! -f "$root/upstream.json" || ! -f "$root/scripts/bootstrap.py" ]]; then
  destination="${CHROMIUM_OBSERVER_DIR:-$HOME/chromium-observer}"
  if [[ ! -e "$destination" ]]; then
    git clone --depth=1 https://github.com/MIkleRustRepin/chromium-observer.git "$destination"
  fi
  [[ -f "$destination/build-mac.sh" && -f "$destination/upstream.json" ]] || fail "Destination already exists and is not Chromium Observer: $destination"
  if [[ "$original_argc" -gt 0 ]]; then
    exec /bin/bash "$destination/build-mac.sh" "${original_args[@]}"
  else
    # Empty array expansion under nounset differs in the macOS Bash 3.2.
    exec /bin/bash "$destination/build-mac.sh"
  fi
fi
[[ ! "$root" =~ [[:space:]] ]] || fail 'Chromium requires a checkout path without whitespace; move this project and rerun.'
cd "$root"

device="$(df -Pk "$root" | awk 'NR==2 {print $1}')"
filesystem="$(diskutil info -plist "$device" | python3 -c 'import plistlib,sys; print(plistlib.loads(sys.stdin.buffer.read()).get("FilesystemType", "unknown"))')"
[[ "$filesystem" == apfs ]] || fail "Chromium needs an APFS volume; detected $filesystem."
free_gib="$(df -Pk "$root" | awk 'NR==2 {printf "%d", $4/1048576}')"
minimum=100
[[ ! -d chromium/src/.git ]] || minimum=15
[[ "$free_gib" -ge "$minimum" ]] || fail "Need at least $minimum GiB free here; available: $free_gib GiB."

if [[ -z "$jobs" ]]; then
  memory_gib=$(( $(sysctl -n hw.memsize) / 1073741824 ))
  cpu_count="$(sysctl -n hw.logicalcpu)"
  jobs=$(( (memory_gib - 8) / 3 ))
  [[ "$jobs" -ge 1 ]] || jobs=1
  [[ "$jobs" -le "$cpu_count" ]] || jobs="$cpu_count"
fi

lock="$root/.mac-build.lock"
if ! mkdir "$lock" 2>/dev/null; then
  previous_pid="$(cat "$lock/pid" 2>/dev/null || true)"
  if [[ "$previous_pid" =~ ^[1-9][0-9]*$ ]] && ! kill -0 "$previous_pid" 2>/dev/null; then
    rm -f "$lock/pid"
    rmdir "$lock" && mkdir "$lock" || fail "Could not recover the stale build lock: $lock"
  else
    fail "A build lock already exists: $lock. If no build is running, remove that directory and rerun."
  fi
fi
printf '%s\n' "$$" > "$lock/pid"
caffeine_pid=''
stage='initialization'
logfile=''
cleanup() {
  result=$?
  if [[ -n "$caffeine_pid" ]]; then kill "$caffeine_pid" 2>/dev/null || true; fi
  rm -f "$lock/pid"
  rmdir "$lock" 2>/dev/null || true
  if [[ "$result" -ne 0 ]]; then
    printf '\nBuild stopped during %s (exit %s). Fix the error and rerun the same command.\nLog: %s\n' "$stage" "$result" "$logfile" >&2
  fi
  return "$result"
}
trap cleanup EXIT
mkdir -p logs
logfile="$root/logs/build-mac-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$logfile") 2>&1
if command -v caffeinate >/dev/null; then
  caffeinate -is -w "$$" >/dev/null 2>&1 &
  caffeine_pid=$!
fi
export DEPOT_TOOLS_UPDATE=0
export PATH="$root/tools/depot_tools:$PATH"
printf 'Chromium Observer macOS arm64 build\nSDK: %s\nParallel jobs: %s\nFree disk: %s GiB\nLog: %s\n' "$sdk_version" "$jobs" "$free_gib" "$logfile"

stage='download Chromium and native dependencies'
python3 scripts/bootstrap.py
python="$root/tools/depot_tools/python-bin/python3"
[[ -x "$python" ]] || fail 'depot_tools did not provide its pinned Python interpreter.'

stage='Chromium build hooks'
(cd chromium/src && gclient runhooks)
stage='apply observer patch'
"$python" scripts/apply_patch.py
if [[ "$clean" -eq 1 && -f chromium/src/out/Observer/build.ninja ]]; then
  stage='clean previous build artifacts'
  (cd chromium/src && gn clean out/Observer)
fi
stage='compile Chromium.app'
printf 'Frame width/height scale: %s\n' "$scale"
build_args=(--jobs "$jobs" --endpoint "$endpoint" --scale "$scale")
if [[ "$tests" -eq 1 ]]; then build_args+=(--tests); fi
"$python" scripts/build.py "${build_args[@]}"
[[ -x chromium/src/out/Observer/Chromium.app/Contents/MacOS/Chromium ]] || fail 'Build finished without a runnable Chromium.app.'
stage='package Chromium.app'
"$python" scripts/package.py
printf '\nBuild complete.\nApp: %s\nArchive: %s\n' \
  "$root/chromium/src/out/Observer/Chromium.app" \
  "$root/artifacts/chromium-observer-macos-arm64.tar.gz"
printf '\nStart the local viewer server:\n  %q -m server.server\n' "$python"
