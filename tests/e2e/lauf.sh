#!/bin/sh
# E2E: Testinstanz (echte LiveKit + App im Testmodus) bauen, Playwright-Ablauf mit zwei Browsern, aufräumen.
# Aufruf aus dem Projektordner: sh tests/e2e/lauf.sh   (Bildschirmfotos landen in tests/e2e/bilder/)
# Mehrere Läufe gleichzeitig: APP_PORT=8121 LK_PORT=7990 sh tests/e2e/lauf.sh  (LiveKit nutzt LK_PORT, +1 TCP, +2 UDP)
# Browser-Matrix: BROWSER=firefox|webkit|chromium sh tests/e2e/lauf.sh → browser_matrix.py (Auswahl der Abläufe je Browser,
# Ergebnis in bilder/matrix-<browser>.json, siehe docs/BROWSER.md). Ohne BROWSER: der volle Chromium-Ablauf wie bisher.
set -e
cd "$(dirname "$0")"
export APP_PORT=${APP_PORT:-8111} LK_PORT=${LK_PORT:-7980}
export COMPOSE_PROJECT_NAME="agora-test-$APP_PORT"
export LK_CONFIG="/tmp/agora-livekit-test-$APP_PORT.yaml"
sed -e "s/^port: 7980/port: $LK_PORT/; s/tcp_port: 7981/tcp_port: $((LK_PORT+1))/; s/udp_port: 7982/udp_port: $((LK_PORT+2))/" livekit-test.yaml > "$LK_CONFIG"
chmod 644 "$LK_CONFIG"
mkdir -p bilder
docker compose -f docker-compose.test.yml up -d --build
trap 'docker compose -f docker-compose.test.yml down -v >/dev/null 2>&1; rm -f "$LK_CONFIG"' EXIT
i=0; until curl -sf "http://127.0.0.1:$APP_PORT/api/gesund" >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { echo "App startet nicht"; exit 1; }; sleep 1; done
docker build -q -t agora-playwright:1.64.1 -f Dockerfile.playwright . >/dev/null
if [ -n "$BROWSER" ]; then
  case "$BROWSER" in chromium|firefox|webkit) ;; *) echo "BROWSER muss chromium, firefox oder webkit sein"; exit 2 ;; esac
  SKRIPT=browser_matrix.py
else
  SKRIPT=browser_e2e.py
fi
docker run --rm --network host --ipc=host -v "$PWD:/e2e" -e "BASIS=http://127.0.0.1:$APP_PORT" -e AUSGABE=/e2e/bilder/ \
  -e "BROWSER=${BROWSER:-chromium}" agora-playwright:1.64.1 python3 -u "/e2e/$SKRIPT"
