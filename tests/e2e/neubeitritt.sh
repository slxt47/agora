#!/bin/sh
# Test: automatischer Neubeitritt, wenn der LiveKit-Knoten ausfällt (ADR 0007). Aufruf aus dem Projektordner:
#   APP_PORT=8281 LK_PORT=7890 sh tests/e2e/neubeitritt.sh
set -e
cd "$(dirname "$0")"
export APP_PORT=${APP_PORT:-8281} LK_PORT=${LK_PORT:-7890}
export COMPOSE_PROJECT_NAME="agora-neu-$APP_PORT" LK_CONFIG="/tmp/agora-livekit-neu-$APP_PORT.yaml" KI_PORT=$((APP_PORT+5))
sed -e "s/^port: 7980/port: $LK_PORT/; s/tcp_port: 7981/tcp_port: $((LK_PORT+1))/; s/udp_port: 7982/udp_port: $((LK_PORT+2))/" livekit-test.yaml > "$LK_CONFIG"
chmod 644 "$LK_CONFIG"; mkdir -p bilder; rm -f bereit wieder
docker compose -f docker-compose.test.yml up -d --build >/dev/null 2>&1
trap 'docker compose -f docker-compose.test.yml down -v >/dev/null 2>&1; rm -f "$LK_CONFIG" bereit wieder' EXIT
i=0; until curl -sf "http://127.0.0.1:$APP_PORT/api/gesund" >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { echo "App startet nicht"; exit 1; }; sleep 1; done
LK=$(docker compose -f docker-compose.test.yml ps -q livekit-test)
(
  until [ -f bereit ]; do sleep 1; done
  echo "Knoten aus (45 s) …"; docker stop "$LK" >/dev/null; sleep 45; docker start "$LK" >/dev/null
  until curl -s -o /dev/null "http://127.0.0.1:$LK_PORT/"; do sleep 1; done; sleep 2; touch wieder
) &
docker run --rm --network host --ipc=host -v "$PWD:/e2e" -e "BASIS=http://127.0.0.1:$APP_PORT" agora-playwright:1.64.1 python3 /e2e/neubeitritt.py
