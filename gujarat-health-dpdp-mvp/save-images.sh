#!/usr/bin/env bash
# Builds the demo images and saves them to images/gj-health-dpdp-images.tar.gz so the demo can start with NO internet
# (conference Wi-Fi, client site). Run this once while you are online. ./start.sh loads the bundle automatically.
set -u
cd "$(dirname "$0")" || exit 1
command -v docker >/dev/null 2>&1 || { echo "Docker is not installed" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running - open Docker Desktop first" >&2; exit 1; }
export DB_PASSWORD="${DB_PASSWORD:-x}" TENANT_KEY="${TENANT_KEY:-x}" LEDGER_KEY="${LEDGER_KEY:-x}"
CODE_HASH="$({ find app tests -type f ! -name '*.pyc' ! -path '*/__pycache__/*' | LC_ALL=C sort | while read -r f; do printf '%s\n' "$f"; cat "$f"; done; cat docker-compose.yml; } | { if command -v shasum >/dev/null 2>&1; then shasum -a 256; else sha256sum; fi; } | cut -c1-16)"; export CODE_HASH
echo "==> Building / pulling images..."
docker compose build || exit 1
docker pull postgres:16-alpine || exit 1
mkdir -p images
echo "==> Saving to images/gj-health-dpdp-images.tar.gz (about 150-250 MB)..."
docker save gj-health-dpdp-app:1.0 postgres:16-alpine | gzip > images/gj-health-dpdp-images.tar.gz || exit 1
ls -lh images/gj-health-dpdp-images.tar.gz
echo "Done. Copy the whole project folder (including images/) to any Apple-silicon Mac with Docker and run ./start.sh"
