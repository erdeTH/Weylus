#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
weylus_binary="${WEYLUS_BINARY:-$project_dir/target/release/weylus}"
if [[ ! -x "$weylus_binary" ]]; then
    echo "Weylus executable not found at $weylus_binary" >&2
    echo "Build/download the Linux server first, or set WEYLUS_BINARY to its path." >&2
    exit 1
fi
python3 "$project_dir/usb/bridge.py" &
relay_pid=$!
trap 'kill "$relay_pid" 2>/dev/null || true; wait "$relay_pid" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$weylus_binary" --bind-address 127.0.0.1 --web-port 1701 --auto-start "$@"
