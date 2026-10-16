#!/usr/bin/env bash
# Export the platform's images to images/ghip-images.tar.gz so ./start.sh can run with no internet (air-gapped venue).
set -eu
cd "$(dirname "$0")"
mkdir -p images
docker save ghip/py-services:1.0 ghip/dpdp-compliance:1.0 ghip/gateway:1.0 pgvector/pgvector:pg16 | gzip > images/ghip-images.tar.gz
ls -lh images/ghip-images.tar.gz
