#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f server.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source server.env
  set +a
fi

exec python3 -m src.server "$@"
