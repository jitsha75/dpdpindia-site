#!/usr/bin/env bash
# Copy the shared helpers (common.py, gjref.py) into every Python service, so each stays independently buildable.
set -euo pipefail
cd "$(dirname "$0")/../services"
for d in */; do
  d=${d%/}; [ "$d" = _shared ] && continue; [ "$d" = dpdp-compliance-service ] && continue
  [ -f "$d/main.py" ] || continue
  cp _shared/common.py _shared/gjref.py "$d/"
done
echo "shared helpers synced"
