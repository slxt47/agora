#!/bin/sh
# Beweis „Anmelden mit …“ gegen einen echten, lokalen Identity-Provider (Dex, Wegwerf-Container): App + LiveKit wie die
# E2E-Tests, dazu Dex auf 127.0.0.1:DEX_PORT; Playwright meldet sich über die Login-Seite von Dex an (sso_e2e.py).
#   APP_PORT=8291 LK_PORT=7900 sh tests/e2e/sso.sh           (DEX_PORT=5556 Standard; Exit ≠ 0 = Fehler)
set -e
cd "$(dirname "$0")"
export APP_PORT=${APP_PORT:-8291} LK_PORT=${LK_PORT:-7900} DEX_PORT=${DEX_PORT:-5556}
export KI_PORT=${KI_PORT:-$((APP_PORT+5))}
export COMPOSE_PROJECT_NAME="agora-sso-$APP_PORT"
export LK_CONFIG="/tmp/agora-livekit-sso-$APP_PORT.yaml" DEX_CONFIG="/tmp/agora-dex-sso-$APP_PORT.yaml"
sed -e "s/^port: 7980/port: $LK_PORT/; s/tcp_port: 7981/tcp_port: $((LK_PORT+1))/; s/udp_port: 7982/udp_port: $((LK_PORT+2))/" livekit-test.yaml > "$LK_CONFIG"
sed -e "s/__DEX__/$DEX_PORT/g; s/__APP__/$APP_PORT/g" dex-test.yaml > "$DEX_CONFIG"
chmod 644 "$LK_CONFIG" "$DEX_CONFIG"
mkdir -p bilder
C="docker compose -f docker-compose.test.yml -f docker-compose.sso.yml"
$C up -d --build
trap '$C down -v >/dev/null 2>&1; rm -f "$LK_CONFIG" "$DEX_CONFIG"' EXIT
i=0; until curl -sf "http://127.0.0.1:$APP_PORT/api/gesund" >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { echo "App startet nicht"; exit 1; }; sleep 1; done
i=0; until curl -sf "http://127.0.0.1:$DEX_PORT/dex/.well-known/openid-configuration" >/dev/null; do
  i=$((i+1)); [ $i -gt 30 ] && { echo "Dex startet nicht"; $C logs dex-test | tail -20; exit 1; }; sleep 1; done
docker build -q -t agora-playwright:1.64.1 -f Dockerfile.playwright . >/dev/null
docker run --rm --network host --ipc=host -v "$PWD:/e2e" -e "BASIS=http://127.0.0.1:$APP_PORT" -e "DEX=http://127.0.0.1:$DEX_PORT/dex" \
  -e AUSGABE=/e2e/bilder/ agora-playwright:1.64.1 python3 -u /e2e/sso_e2e.py
