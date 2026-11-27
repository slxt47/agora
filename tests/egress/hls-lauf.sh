#!/bin/sh
# HLS-Test: Wegwerf-Stapel (Redis + LiveKit + Egress + App mit AGORA_HLS=1) bauen; ein Playwright-Host startet im
# Webinar „Öffentliche Übertragung (HLS)“ (720p + 360p), der Test prüft Playlists/Segmente und lässt die Zuschauerseite
# /h/<code> in Chromium abspielen (currentTime muss weiterlaufen), beendet und räumt auf.
# Aufruf aus dem Projektordner: sh tests/egress/hls-lauf.sh   (baut agora-playwright:1.64.1 aus tests/e2e bei Bedarf)
# Ports 7850–7854 und 8251. Bilder: tests/egress/bilder/hls-*.png, Messwerte (CPU/RAM des Egress) am Ende.
set -e
cd "$(dirname "$0")"
export HLS_AUSGABE="${HLS_AUSGABE:-$(mktemp -d)}"
chmod 2770 "$HLS_AUSGABE"            # Egress schreibt als 1001:1000, die App (1000) liest und löscht
mkdir -p bilder
C="docker compose -f docker-compose.hls-test.yml"
$C up -d --build
# Egress legt Dateien als 1001 an (und nach einem Abbruch evtl. auch Ordner) → als root im Wegwerf-Container löschen
trap '$C down -v >/dev/null 2>&1; docker run --rm --user 0:0 -v "$HLS_AUSGABE":/h --entrypoint sh agora:test-hls -c "rm -rf /h/*"; rm -rf "$HLS_AUSGABE"' EXIT
i=0; until curl -sf http://127.0.0.1:8251/api/gesund >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { echo "App startet nicht"; exit 1; }; sleep 1; done
docker build -q -t agora-playwright:1.64.1 -f ../e2e/Dockerfile.playwright ../e2e >/dev/null
( while sleep 5; do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' agora-hls-test-egress-test-1 2>/dev/null; done ) > bilder/hls-stats.txt &
STATS=$!
set +e
docker run --rm --network host --ipc=host -v "$PWD:/eg" -e BASIS=http://127.0.0.1:8251 -e AUSGABE=/eg/bilder/ -e HLS_ORDNER=/hls \
  -v "$HLS_AUSGABE:/hls:ro" agora-playwright:1.64.1 python3 /eg/hls_e2e.py
ERG=$?
kill $STATS 2>/dev/null
echo "Egress-Container (CPU, RAM) während des Tests – Spitze:"
sort -k2 -t' ' -n -r bilder/hls-stats.txt | head -1
awk '{print $3}' bilder/hls-stats.txt | sort -h | tail -1 | sed 's/^/RAM max: /'
exit $ERG
