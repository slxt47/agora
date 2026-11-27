#!/bin/sh
# Integrationstest Objektspeicher: Wegwerf-MinIO (nur 127.0.0.1, gehärtet, Daten im tmpfs) + tests/test_speicher.py.
#   sh tests/speicher/lauf.sh                         MinIO auf 127.0.0.1:9351 (API) / 9352 (Konsole)
#   S3_PORT=9361 sh tests/speicher/lauf.sh            andere Ports (Konsole = S3_PORT+1)
# Image: pgsty/minio (gepflegter Community-Build von MinIO; die offiziellen minio/minio-Images gibt es seit Ende 2025
# nicht mehr öffentlich) – Tag + Digest fest.
set -e
cd "$(dirname "$0")/../.."
S3_PORT=${S3_PORT:-9351}
NAME="agora-test-minio-$S3_PORT"
BILD="pgsty/minio:RELEASE.2026-08-04T00-00-00Z@sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "127.0.0.1:$S3_PORT:9000" -p "127.0.0.1:$((S3_PORT+1)):9001" \
  --read-only --tmpfs /data:uid=1000,gid=1000 --tmpfs /tmp --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges:true \
  --memory 512m -e MINIO_ROOT_USER=agoratest -e MINIO_ROOT_PASSWORD=agoratest-geheim-123 -e HOME=/tmp \
  "$BILD" server /data --console-address :9001 >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
i=0; until curl -sf "http://127.0.0.1:$S3_PORT/minio/health/live" >/dev/null; do i=$((i+1)); [ $i -gt 30 ] && { docker logs "$NAME"; echo "MinIO startet nicht"; exit 1; }; sleep 1; done
docker run --rm --network host --user 1000:1000 -e HOME=/tmp -e "AGORA_TEST_S3=http://127.0.0.1:$S3_PORT" -v "$PWD":/src -w /src \
  python:3.14.10-slim sh -c "pip install -q --user -r requirements-test.txt 2>/dev/null; mkdir -p /tmp/d && AGORA_TEST=1 AGORA_DATEN=/tmp/d python -m pytest -q -p no:cacheprovider -rs tests/test_speicher.py"
