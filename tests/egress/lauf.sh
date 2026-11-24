#!/bin/sh
# Egress-Test: Wegwerf-Stapel (Redis + LiveKit + Egress + App) bauen, Playwright-Host zeichnet auf dem Server auf
# und startet einen Livestream (zu einem nicht erreichbaren Ziel → Fehler muss ankommen, Schlüssel darf nirgends im
# Log stehen), aufräumen. Aufruf aus dem Projektordner: sh tests/egress/lauf.sh   (braucht agora-playwright:1.64.1
# aus tests/e2e/lauf.sh). Bilder: tests/egress/bilder/, Messwerte (RAM/CPU) am Ende der Ausgabe.
set -e
cd "$(dirname "$0")"
export EGRESS_AUSGABE="${EGRESS_AUSGABE:-$(mktemp -d)}"
chmod 2770 "$EGRESS_AUSGABE"          # Egress schreibt als 1001:1000, die App (1000) verschiebt
mkdir -p bilder
docker compose -f docker-compose.egress-test.yml up -d --build
trap 'docker compose -f docker-compose.egress-test.yml down -v >/dev/null 2>&1; rm -rf "$EGRESS_AUSGABE"' EXIT
i=0; until curl -sf http://127.0.0.1:8172/api/gesund >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { echo "App startet nicht"; exit 1; }; sleep 1; done
docker build -q -t agora-playwright:1.64.1 -f ../e2e/Dockerfile.playwright ../e2e >/dev/null
( while sleep 5; do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' agora-egress-test-egress-test-1 2>/dev/null; done ) > bilder/stats.txt &
STATS=$!
set +e
docker run --rm --network host --ipc=host -v "$PWD:/eg" -e BASIS=http://127.0.0.1:8172 -e AUSGABE=/eg/bilder/ \
  agora-playwright:1.64.1 python3 /eg/egress_e2e.py
ERG=$?
kill $STATS 2>/dev/null
echo "Egress-Container (CPU, RAM) während des Tests – Spitze:"
sort -k2 -t' ' -n -r bilder/stats.txt | head -1
awk '{print $3}' bilder/stats.txt | sort -h | tail -1 | sed 's/^/RAM max: /'
if docker compose -f docker-compose.egress-test.yml logs 2>&1 | grep -q 'sk_test_GEHEIM'; then echo "FEHLER: Stream-Schlüssel im Log"; ERG=1; fi
exit $ERG
