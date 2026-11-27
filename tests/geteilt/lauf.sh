#!/bin/sh
# Integrationstest Redis (Begrenzer, Präsenz, zwei uvicorn-Worker): Wegwerf-Redis nur auf 127.0.0.1, gehärtet, ohne
# Persistenz, gleiches Image wie in docker-compose.yml – dann tests/test_geteilt.py im Host-Netz.
#   sh tests/geteilt/lauf.sh                 Redis auf 127.0.0.1:6391
#   REDIS_PORT=6392 sh tests/geteilt/lauf.sh
set -e
cd "$(dirname "$0")/../.."
REDIS_PORT=${REDIS_PORT:-6391}
NAME="agora-test-redis-$REDIS_PORT"
BILD="redis:8.10.3-alpine@sha256:21090af93f8324db2f0f0f4f38a7c60369d47d917e000c3423d89d2ae2883537"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "127.0.0.1:$REDIS_PORT:6379" --read-only --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges:true --memory 128m "$BILD" \
  redis-server --requirepass agoratest-geheim --save '' --appendonly no --maxmemory 64mb --maxmemory-policy noeviction >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
i=0; until docker exec "$NAME" redis-cli -a agoratest-geheim --no-auth-warning ping 2>/dev/null | grep -q PONG; do i=$((i+1)); [ $i -gt 30 ] && { echo "Redis startet nicht"; exit 1; }; sleep 1; done
docker run --rm --network host --user 1000:1000 -e HOME=/tmp -e "AGORA_TEST_REDIS=redis://:agoratest-geheim@127.0.0.1:$REDIS_PORT/3" \
  -v "$PWD":/src -w /src python:3.14.10-slim \
  sh -c "pip install -q --user -r requirements-test.txt 2>/dev/null; mkdir -p /tmp/d && AGORA_TEST=1 AGORA_DATEN=/tmp/d python -m pytest -q -p no:cacheprovider -rs tests/test_geteilt.py"
