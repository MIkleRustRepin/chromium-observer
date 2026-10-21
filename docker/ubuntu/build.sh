#!/bin/bash
set -euo pipefail

workspace="${1:?Missing workspace directory}"
shift
jobs=1
endpoint='udp://127.0.0.1:8766'
scale='0.75'
tests=0
clean=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs|--endpoint|--scale)
      [[ $# -ge 2 ]] || { echo "$1 needs a value" >&2; exit 1; }
      case "$1" in --jobs) jobs="$2";; --endpoint) endpoint="$2";; --scale) scale="$2";; esac
      shift 2 ;;
    --tests) tests=1; shift ;;
    --clean) clean=1; shift ;;
    *) echo "Unknown build argument: $1" >&2; exit 1 ;;
  esac
done
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid --jobs' >&2; exit 1; }
python3 -c 'import math,sys; x=float(sys.argv[1]); assert math.isfinite(x) and 0<x<=1' "$scale"
python3 -c 'import ipaddress,sys; from urllib.parse import urlparse; e=urlparse(sys.argv[1]); ipaddress.ip_address(e.hostname); assert e.scheme=="udp" and e.port and not (e.path or e.query or e.fragment or "%" in e.hostname) and e.username is None and e.password is None' "$endpoint"

exec 9> "$workspace/.observer-build.lock"
flock -n 9 || { echo 'Another build is using this Docker volume.' >&2; exit 1; }
project="$workspace/project"
mkdir -p "$project"
snapshot="$(mktemp -d "$workspace/snapshot.XXXXXX")"
trap 'rm -rf "$snapshot"' EXIT
tar -xf - -C "$snapshot"
# Synchronize control files while retaining only Linux checkout/cache artifacts.
rsync -a --delete --exclude=/chromium/ --exclude=/tools/ --exclude=/artifacts/ \
  --exclude=/logs/ --exclude=/.git/ --exclude=/.codebase-memory/ "$snapshot/" "$project/"
cd "$project"
minimum=100
[[ ! -d chromium/src/.git ]] || minimum=15
free_gib="$(df -Pk . | awk 'NR==2 {printf "%d", $4/1048576}')"
[[ "$free_gib" -ge "$minimum" ]] || { echo "Docker's Linux disk needs $minimum GiB free; available: $free_gib GiB." >&2; exit 1; }
mkdir -p logs
logfile="logs/build-ubuntu-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$logfile") 2>&1
export DEPOT_TOOLS_UPDATE=0
export PATH="$project/tools/depot_tools:$PATH"
printf 'Ubuntu amd64 build: jobs=%s scale=%s endpoint=%s\n' "$jobs" "$scale" "$endpoint"
python3 scripts/bootstrap.py
python="$project/tools/depot_tools/python-bin/python3"
[[ -x "$python" ]] || { echo 'Missing pinned Python interpreter.' >&2; exit 1; }
(cd chromium/src && gclient runhooks)
"$python" scripts/apply_patch.py
if [[ "$clean" -eq 1 && -f chromium/src/out/Observer/build.ninja ]]; then
  (cd chromium/src && gn clean out/Observer)
fi
build_args=(--jobs "$jobs" --endpoint "$endpoint" --scale "$scale")
if [[ "$tests" -eq 1 ]]; then build_args+=(--tests); fi
"$python" scripts/build.py "${build_args[@]}"
[[ -x chromium/src/out/Observer/chrome ]] || { echo 'Missing Linux chrome binary after build.' >&2; exit 1; }
"$python" scripts/package.py
