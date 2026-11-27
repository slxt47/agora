#!/bin/sh
# Sicherheits-Regressionstest (Black-Box gegen eine Wegwerf-Instanz, nie gegen agora.slxt.dev):
# baut App + LiveKit wie die E2E-Tests (tests/e2e/docker-compose.test.yml), aber mit Begrenzern und ohne lokale
# Webhook-Ziele, und lässt tests/sicherheit/angriffe.py im Playwright-Container laufen. Exit ≠ 0 = ein Angriff klappte.
#   sh tests/sicherheit/lauf.sh                     Ports 8195 (App) und 7790–7792 (LiveKit)
#   APP_PORT=8196 LK_PORT=7800 sh tests/sicherheit/lauf.sh
#   BEHALTEN=1 sh tests/sicherheit/lauf.sh          Instanz danach nicht abbauen (Abbau steht in der Ausgabe)
# Braucht web/node_modules (livekit-client für den Veröffentlichungs-Test): einmal `npm ci` in web/.
set -e
cd "$(dirname "$0")"
export APP_PORT=${APP_PORT:-8195} LK_PORT=${LK_PORT:-7790}
export IDP_PORT=${IDP_PORT:-$((APP_PORT+9))}          # nachgebauter OIDC-Anbieter (startet angriffe.py)
export COMPOSE_PROJECT_NAME="agora-sicherheit-$APP_PORT"
export LK_CONFIG="/tmp/agora-livekit-sicherheit-$APP_PORT.yaml"
LKJS="$(cd ../../web && pwd)/node_modules/livekit-client/dist/livekit-client.umd.js"
[ -f "$LKJS" ] || { echo "livekit-client fehlt – erst in web/ 'npm ci' ausführen"; exit 2; }
sed -e "s/^port: 7980/port: $LK_PORT/; s/tcp_port: 7981/tcp_port: $((LK_PORT+1))/; s/udp_port: 7982/udp_port: $((LK_PORT+2))/" \
  ../e2e/livekit-test.yaml > "$LK_CONFIG"
chmod 644 "$LK_CONFIG"
C="docker compose -f ../e2e/docker-compose.test.yml -f docker-compose.sicherheit.yml"
$C up -d --build
if [ -z "$BEHALTEN" ]; then
  trap '$C down -v >/dev/null 2>&1; rm -f "$LK_CONFIG"' EXIT
else
  echo "Instanz bleibt: abbauen mit  cd tests/sicherheit && COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME $C down -v"
fi
i=0; until curl -sf "http://127.0.0.1:$APP_PORT/api/gesund" >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { echo "App startet nicht"; exit 1; }; sleep 1; done
docker build -q -t agora-playwright:1.64.1 -f ../e2e/Dockerfile.playwright ../e2e >/dev/null
FSJS="$(cd ../../desktop/src && pwd)/fernsteuerung.js"     # Prüfregeln der Desktop-App (Fernsteuerung)
docker run --rm --network host --ipc=host -v "$PWD:/s:ro" -v "$LKJS:/lk.js:ro" -v "$FSJS:/fs.js:ro" -e FS_JS=/fs.js \
  -e "BASIS=http://127.0.0.1:$APP_PORT" -e "LK_URL=ws://127.0.0.1:$LK_PORT" -e "LK_API=http://127.0.0.1:$LK_PORT" \
  -e "IDP_URL=http://127.0.0.1:$IDP_PORT" -e LK_KEY=testschluessel -e LK_SECRET=testgeheimnis-testgeheimnis-testgeheimnis-123 -e LK_JS=/lk.js \
  agora-playwright:1.64.1 python3 /s/angriffe.py
