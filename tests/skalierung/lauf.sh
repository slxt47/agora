#!/bin/sh
# Skalierungsbeweis (ADR 0007) auf einem Rechner: zwei LiveKit-Knoten (A, B) mit gemeinsamem Redis + App im Testmodus,
# Chromium-Gäste in mehreren Räumen. Abschnitte (skalierung_e2e.py):
#   1. Verteilung – beide Knoten in eu-central: LiveKit verteilt neue Räume auf beide Knoten, Medien laufen auf beiden
#   2. Regionen   – A eu-central, B eu-west: CF-IPCountry DE → Raum auf A, FR → Raum auf B; ein Gast mit Signal über A
#                   im Raum auf B (Weiterleitung über Redis)
#   3. Ausfall    – Knoten A wird hart beendet (docker kill): Raum auf B läuft weiter, neue Räume landen auf B,
#                   ein Raum von A wird beim Wiederbeitritt auf B neu angelegt, die App meldet Knoten A als ausgefallen
# Aufruf aus dem Projektordner: sh tests/skalierung/lauf.sh   (baut agora-playwright:1.64.1 aus tests/e2e bei Bedarf)
# Ports: 7860–7863 (A), 7870–7873 (B), Redis 6392, App 8261 – alles nur 127.0.0.1. Räumt am Ende alles ab.
set -e
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME=agora-skalierung
C="docker compose -f docker-compose.skalierung.yml"
mkdir -p bilder
S="$(mktemp -d)"                      # Abstimmung mit dem Playwright-Container (Dateien <abschnitt>.fertig/.weiter)
trap '$C down -v >/dev/null 2>&1; docker rm -f agora-skalierung-pw >/dev/null 2>&1; rm -rf "$S"' EXIT

app_bereit() {
  i=0; until curl -sf http://127.0.0.1:8261/api/gesund >/dev/null; do i=$((i+1)); [ $i -gt 90 ] && { echo "App startet nicht"; exit 1; }; sleep 1; done
}
knoten_bereit() {     # beide Knoten in Redis gemeldet (Prometheus antwortet erst nach dem Start des Knotens)
  for port in 7863 7873; do
    i=0; until curl -sf "http://127.0.0.1:$port/metrics" >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { echo "Knoten $port startet nicht"; exit 1; }; sleep 1; done
  done
  sleep 3
}
warten_auf() {        # bis der Playwright-Ablauf <abschnitt>.fertig meldet (oder endet)
  until [ -e "$S/$1.fertig" ]; do
    [ -e "$S/ende" ] && return 1
    docker inspect agora-skalierung-pw >/dev/null 2>&1 || return 1
    sleep 1
  done
}

echo "== Abschnitt 1: beide Knoten eu-central"
REGION_A=eu-central REGION_B=eu-central $C up -d --build
app_bereit; knoten_bereit
docker build -q -t agora-playwright:1.64.1 -f ../e2e/Dockerfile.playwright ../e2e >/dev/null
docker run -d --name agora-skalierung-pw --user "$(id -u):$(id -g)" -e HOME=/tmp --network host --ipc=host -v "$PWD:/sk:ro" -v "$PWD/bilder:/bilder" -v "$S:/steuer" \
  -e BASIS=http://127.0.0.1:8261 -e AUSGABE=/bilder/ -e STEUER=/steuer/ agora-playwright:1.64.1 python3 -u /sk/skalierung_e2e.py >/dev/null
docker logs -f agora-skalierung-pw &
LOGS=$!

if warten_auf verteilung; then
  echo "== Abschnitt 2: Knoten B nach eu-west (B und App neu)"
  REGION_A=eu-central REGION_B=eu-west $C up -d
  app_bereit; knoten_bereit
  docker exec agora-skalierung-lk-a-1 /livekit-server --config /etc/livekit.yaml list-nodes 2>/dev/null | grep -E 'ND_|Region' | cut -c1-60 || true
  touch "$S/verteilung.weiter"
  if warten_auf regionen; then
    echo "== Abschnitt 3: Knoten A hart beenden"
    docker kill agora-skalierung-lk-a-1 >/dev/null
    touch "$S/regionen.weiter"
  fi
fi
ERG=$(docker wait agora-skalierung-pw)
kill $LOGS 2>/dev/null || true
echo "== Speicher je Knoten am Ende:"; docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' agora-skalierung-lk-b-1 2>/dev/null || true
[ "$ERG" = 0 ] && [ "$(cat "$S/ende" 2>/dev/null)" = ok ]
