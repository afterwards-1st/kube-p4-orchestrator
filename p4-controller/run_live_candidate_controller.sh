#!/usr/bin/env bash
# Starts the candidate as the ONLY live P4 writer.  The old controller must
# already be paused and a pre-live snapshot must exist.
set -euo pipefail
if [[ "${P4_LIVE_CONFIRM:-}" != "YES" ]]; then
  echo 'Refusing live P4 writes. Re-run with P4_LIVE_CONFIRM=YES.' >&2
  exit 2
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="${P4_CONTROL_TOKEN_FILE:-$HERE/p4-control-token}"
if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "Refusing live P4 writes: token file is not readable: $TOKEN_FILE" >&2
  exit 2
fi
export P4_CONTROL_TOKEN="$(<"$TOKEN_FILE")"
exec python3 "$HERE/controller_global_9backend.py" \
  --control-mode external \
  --api-ip 0.0.0.0 --api-port 18100 \
  --listen-ip 127.0.0.1 --listen-port 19999