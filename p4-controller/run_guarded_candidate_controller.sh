#!/usr/bin/env bash
# Candidate only: --dry-run validates requests without writing BMv2 tables.
# It uses new API/report ports and does not replace the existing 114 service.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/controller_global_9backend.py" \
  --control-mode external --dry-run \
  --api-ip 0.0.0.0 --api-port 18100 \
  --listen-ip 127.0.0.1 --listen-port 19999