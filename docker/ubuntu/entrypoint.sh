#!/bin/bash
set -euo pipefail

# Only initialize a dedicated empty volume or reuse a volume created by us.
marker=/workspace/.chromium-observer-ubuntu
expected='chromium-observer Ubuntu 24.04 amd64 cache v1'
if [[ -f "$marker" ]]; then
  [[ "$(cat "$marker")" == "$expected" ]] || { echo 'Unknown cache volume format.' >&2; exit 1; }
elif [[ -n "$(find /workspace -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo 'Refusing to use a nonempty volume that was not created by this builder.' >&2
  exit 1
else
  printf '%s\n' "$expected" > "$marker"
fi
chown builder:builder /workspace
exec runuser -u builder -- /usr/local/lib/chromium-observer/build.sh /workspace "$@"
