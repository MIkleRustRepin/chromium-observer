#!/bin/bash
# Build a Linux amd64 release in Ubuntu, using Docker Desktop on macOS.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./build-ubuntu-on-mac.sh [--jobs N] [--endpoint URL] [--scale FACTOR]
                               [--tests] [--clean] [--volume NAME]

Builds Linux x86_64 Chromium in an Ubuntu 24.04 Docker container on a Mac.
Apple Silicon uses amd64 emulation. Requires running Docker Desktop with
at least 8 GiB RAM and 100 GiB free in its Linux disk (150+ recommended).

  --jobs N       Parallel jobs (default chosen from Docker RAM/CPU allocation)
  --endpoint URL UDP destination (default udp://127.0.0.1:8766)
  --scale FACTOR Frame width/height scale (default 0.75)
  --tests        Also build browser_tests; does not run them
  --clean        Clear only the Linux out/Observer artifacts before compiling
  --volume NAME  Persistent Docker cache (default chromium-observer-ubuntu-amd64)
  -h, --help     Show help

Result: artifacts/chromium-observer-linux-x64.tar.gz on the Mac.
Sources, tools and intermediate files stay in the named Docker volume.
The macOS build and its out/Observer directory are not used by this script.
EOF
}
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

jobs=''
endpoint='udp://127.0.0.1:8766'
scale='0.75'
volume='chromium-observer-ubuntu-amd64'
extra=()
original_argc=$#
original_args=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs|--endpoint|--scale|--volume)
      [[ $# -ge 2 ]] || fail "$1 needs a value"
      case "$1" in
        --jobs) jobs="$2" ;;
        --endpoint) endpoint="$2" ;;
        --scale) scale="$2" ;;
        --volume) volume="$2" ;;
      esac
      shift 2 ;;
    --tests|--clean) extra+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown argument: $1 (see --help)" ;;
  esac
done
[[ -z "$jobs" || "$jobs" =~ ^[1-9][0-9]*$ ]] || fail '--jobs must be a positive integer'
[[ "$volume" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || fail 'Invalid Docker volume name'
[[ "$(uname -s)" == Darwin ]] || fail 'Run this script on macOS.'
case "$(uname -m)" in arm64|x86_64) ;; *) fail 'Unsupported Mac architecture.' ;; esac
command -v git >/dev/null || fail 'Git is required (install Xcode command-line tools).'
command -v docker >/dev/null || fail 'Install and start Docker Desktop for Mac.'

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ ! -f "$root/upstream.json" || ! -f "$root/docker/ubuntu/Dockerfile" ]]; then
  destination="${CHROMIUM_OBSERVER_DIR:-$HOME/chromium-observer}"
  if [[ ! -e "$destination" ]]; then
    git clone --depth=1 https://github.com/MIkleRustRepin/chromium-observer.git "$destination"
  fi
  [[ -f "$destination/build-ubuntu-on-mac.sh" ]] || fail "Not a Chromium Observer checkout: $destination"
  if [[ "$original_argc" -gt 0 ]]; then
    exec /bin/bash "$destination/build-ubuntu-on-mac.sh" "${original_args[@]}"
  else
    exec /bin/bash "$destination/build-ubuntu-on-mac.sh"
  fi
fi

resources="$(docker info --format '{{.OSType}} {{.MemTotal}} {{.NCPU}}')" || fail 'Start Docker Desktop and rerun.'
read -r docker_os memory_bytes cpu_count <<< "$resources"
[[ "$docker_os" == linux && "$memory_bytes" =~ ^[0-9]+$ && "$cpu_count" =~ ^[1-9][0-9]*$ ]] || fail 'Docker must provide a Linux engine with RAM/CPU information.'
memory_gib=$(( memory_bytes / 1073741824 ))
[[ "$memory_gib" -ge 8 ]] || fail 'Allocate at least 8 GiB RAM to Docker Desktop (Settings > Resources).'
if [[ -z "$jobs" ]]; then
  jobs=$(( (memory_gib - 3) / 3 ))
  [[ "$jobs" -ge 1 ]] || jobs=1
  [[ "$jobs" -le "$cpu_count" ]] || jobs="$cpu_count"
fi
revision="$(sed -n 's/.*"revision": "\([0-9a-f]*\)".*/\1/p' "$root/upstream.json")"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || fail 'Missing pinned Chromium revision in upstream.json.'
image='chromium-observer-ubuntu-builder:24.04-amd64'
container=''
snapshot=''
stage='initialization'
cleanup() {
  result=$?
  if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
  if [[ -n "$snapshot" ]]; then rm -f "$snapshot"; fi
  if [[ "$result" -ne 0 ]]; then
    printf '\nStopped during %s. Cached work is preserved in Docker volume %s.\n' "$stage" "$volume" >&2
  fi
  return "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Target: Ubuntu 24.04 / Linux x86_64\nDocker RAM: %s GiB\nJobs: %s\nCache volume: %s\n' "$memory_gib" "$jobs" "$volume"
if [[ "$(uname -m)" == arm64 ]]; then
  printf 'This Apple Silicon Mac will use amd64 emulation for the Linux build.\n'
fi
stage='snapshot project sources'
snapshot="$(mktemp "${TMPDIR:-/tmp}/chromium-observer-ubuntu.XXXXXX")"
git -C "$root" ls-files -z | tar -C "$root" --null -T - -cf "$snapshot"
stage='prepare Ubuntu build image and system dependencies'
docker build --platform linux/amd64 --build-arg "CHROMIUM_REVISION=$revision" \
  --tag "$image" "$root/docker/ubuntu"
stage='create Ubuntu build container'
build_args=(--jobs "$jobs" --endpoint "$endpoint" --scale "$scale")
if [[ "${#extra[@]}" -gt 0 ]]; then build_args+=("${extra[@]}"); fi
container="$(docker create --interactive --init --platform linux/amd64 \
  --mount "type=volume,source=$volume,target=/workspace" "$image" "${build_args[@]}")"
stage='download, patch, compile and package Linux Chromium'
if ! docker start --attach --interactive "$container" < "$snapshot"; then
  mkdir -p "$root/artifacts"
  docker cp "$container:/workspace/project/logs" "$root/artifacts/ubuntu-build-logs" >/dev/null 2>&1 || true
  fail 'Ubuntu build failed; see terminal output and artifacts/ubuntu-build-logs.'
fi
stage='export Linux archive to Mac'
mkdir -p "$root/artifacts"
docker cp "$container:/workspace/project/artifacts/chromium-observer-linux-x64.tar.gz" "$root/artifacts/"
printf '\nLinux build complete: %s/artifacts/chromium-observer-linux-x64.tar.gz\n' "$root"
