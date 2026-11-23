#!/bin/sh
# Lasttest und Netzsimulation gegen eine WEGWERF-Instanz (nie gegen Produktion). Ergebnisse: docs/LASTTEST.md.
#
#   sh scripts/lasttest.sh last [10 25 50]   LiveKit (Produktionsversion) + offizielles LiveKit-CLI `lk load-test`:
#                                            1 Raum × N Video-Publisher (Simulcast) + N Abonnenten, je 60 s;
#                                            misst CPU/RAM des LiveKit-Containers mit docker stats
#   sh scripts/lasttest.sh netz              eigenes Docker-Netz agora-lastnetz (10.250.77.0/28) mit LiveKit, App und
#                                            Playwright; tc/netem NUR auf eth0 des Playwright-Containers (NET_ADMIN)
#   sh scripts/lasttest.sh                   beides
#
# Ports: LiveKit 7710 (Signal), 7711 (ICE-TCP), 7712 (UDP) – im Lastteil nur auf 127.0.0.1, im Netzteil nur im
# eigenen Docker-Netz; App 8132 nur im eigenen Netz. Vorher prüft das Skript, dass die Ports frei sind.
# Ausgabe: ${AUSGABE:-/tmp/agora-lasttest}/ (Rohdaten, Bildschirmfotos, Zusammenfassung zusammenfassung.txt)
set -eu
cd "$(dirname "$0")/.."
LIVEKIT_IMAGE=livekit/livekit-server:v1.13.11@sha256:25cd902de7cacbd5f1f779f6a6ac1eae20ba8f56b0935934ce6b0ab80d1e3d20
LK_CLI_IMAGE=livekit/livekit-cli:v2.20.1@sha256:3955b4e52a4c73acc2cf2b039b7bc68d8a40e3fd4ffbdca6ec6a5cb93073eaed
KEY=lastschluessel
SECRET=lastgeheimnis-lastgeheimnis-lastgeheimnis-123
DAUER=${DAUER:-60}
AUSGABE=${AUSGABE:-/tmp/agora-lasttest}
NETZ=agora-lastnetz
mkdir -p "$AUSGABE"
ZF="$AUSGABE/zusammenfassung.txt"

ports_frei() {
  for p in "$@"; do
    if ss -tulnH | awk '{print $5}' | grep -qE "[:.]$p\$"; then echo "Port $p ist belegt – Abbruch"; exit 1; fi
  done
}

aufraeumen() {
  docker rm -f agora-last-livekit agora-last-app agora-last-pw agora-last-lk >/dev/null 2>&1 || true
  docker network rm "$NETZ" >/dev/null 2>&1 || true
}
trap aufraeumen EXIT

# ---------------------------------------------------------------- Lasttest
stats_messen() {   # stats_messen <container> <datei> – alle 2 s CPU% und RAM (MiB), bis die Datei .stopp existiert
  while [ ! -e "$2.stopp" ]; do
    docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' "$1" 2>/dev/null \
      | awk '{ cpu = $1; sub("%", "", cpu); m = $2; f = 1; if (m ~ /GiB/) f = 1024; if (m ~ /KiB/) f = 1/1024; sub(/[A-Za-z]+/, "", m); printf "%s %.1f\n", cpu, m * f }' >> "$2"
    sleep 1
  done
}

last() {
  ports_frei 7710 7711 7712
  echo "== Lasttest $(date -Iseconds) – $(nproc) Kerne, $(free -g | awk '/Mem/ {print $2}') GB RAM, $(uname -m)" | tee -a "$ZF"
  docker run -d --name agora-last-livekit --network host --user 1000:1000 --read-only --cap-drop ALL \
    -v "$PWD/tests/last/livekit-last.yaml:/etc/livekit.yaml:ro" "$LIVEKIT_IMAGE" --config /etc/livekit.yaml >/dev/null
  i=0; until curl -sf http://127.0.0.1:7710/ >/dev/null; do i=$((i+1)); [ $i -gt 30 ] && { echo "LiveKit startet nicht"; exit 1; }; sleep 1; done
  for n in "$@"; do
    roh="$AUSGABE/stats-$n.txt"; cli="$AUSGABE/stats-cli-$n.txt"; rm -f "$roh" "$roh.stopp" "$cli" "$cli.stopp"
    stats_messen agora-last-livekit "$roh" &
    sp=$!
    stats_messen agora-last-lk "$cli" &
    sp2=$!
    docker run --rm --name agora-last-lk --network host --user 1000:1000 "$LK_CLI_IMAGE" load-test --url ws://127.0.0.1:7710 --api-key "$KEY" --api-secret "$SECRET" \
      --room "last-$n" --video-publishers "$n" --subscribers "$n" --layout 3x3 --num-per-second 5 --duration "${DAUER}s" \
      > "$AUSGABE/lk-$n.txt" 2>&1 || echo "lk load-test $n: Exit $?" | tee -a "$ZF"
    touch "$roh.stopp" "$cli.stopp"; wait $sp $sp2 2>/dev/null || true
    awk -v n="$n" '{ c += $1; if ($1 > cmax) cmax = $1; if ($2 > mmax) mmax = $2; k++ } END {
      printf "N=%s Publisher+%s Abonnenten: CPU Schnitt %.0f %% (max %.0f %%, 100 %% = 1 Kern), RAM max %.0f MiB, %d Messpunkte\n", n, n, c / k, cmax, mmax, k }' "$roh" | tee -a "$ZF"
    awk '{ c += $1; if ($1 > cmax) cmax = $1 } END { if (k = NR) printf "    Last-Erzeuger (lk-CLI): CPU Schnitt %.0f %% (max %.0f %%)\n", c / k, cmax }' "$cli" | tee -a "$ZF"
    grep -E "│ Total" "$AUSGABE/lk-$n.txt" | sed 's/^/    lk: /' | tee -a "$ZF" || true
    echo "    Grundlast des Servers (load average): $(cut -d' ' -f1-3 /proc/loadavg)" | tee -a "$ZF"
    sleep 5
  done
  docker rm -f agora-last-livekit >/dev/null
}

# ---------------------------------------------------------------- Netzsimulation
netz() {
  ports_frei 8132
  echo "== Netzsimulation $(date -Iseconds) – netem: ${NETEM:-loss 5% delay 150ms rate 1mbit}" | tee -a "$ZF"
  docker network create --subnet 10.250.77.0/28 "$NETZ" >/dev/null
  docker run -d --name agora-last-livekit --network "$NETZ" --ip 10.250.77.10 --user 1000:1000 --read-only --cap-drop ALL \
    -v "$PWD/tests/last/livekit-netz.yaml:/etc/livekit.yaml:ro" "$LIVEKIT_IMAGE" --config /etc/livekit.yaml >/dev/null
  docker build -q --target laufzeit -t agora:lasttest . >/dev/null
  docker run -d --name agora-last-app --network "$NETZ" --ip 10.250.77.11 --user 1000:1000 --read-only --cap-drop ALL \
    --tmpfs /daten:uid=1000,gid=1000 --tmpfs /tmp \
    -e AGORA_TEST=1 -e AGORA_GRENZEN_AUS=1 -e AGORA_COOKIE=agora -e AGORA_COOKIE_SECURE=0 \
    -e AGORA_SEITE=http://127.0.0.1:8132 -e AGORA_LIVEKIT_URL=ws://10.250.77.10:7710 -e LIVEKIT_API_HOST=http://10.250.77.10:7710 \
    -e LIVEKIT_API_KEY="$KEY" -e LIVEKIT_API_SECRET="$SECRET" \
    agora:lasttest uvicorn app.main:app --host 0.0.0.0 --port 8132 --workers 1 --no-access-log >/dev/null
  docker build -q -t agora-playwright-netz:1.64.1 -f tests/last/Dockerfile.netz tests/last >/dev/null
  # Playwright läuft als root, weil tc root + NET_ADMIN braucht – beides gilt nur im eigenen Netz-Namespace des Containers
  docker run --rm --name agora-last-pw --network "$NETZ" --ip 10.250.77.12 --cap-add NET_ADMIN --ipc=host \
    -v "$PWD/tests/last:/last:ro" -v "$AUSGABE:/aus" -e APP=10.250.77.11:8132 -e AUSGABE=/aus/ \
    -e NETEM="${NETEM:-loss 5% delay 150ms rate 1mbit}" -e DAUER_S="$DAUER" \
    agora-playwright-netz:1.64.1 python3 /last/netz_e2e.py > "$AUSGABE/netz.txt" 2>&1 && st=0 || st=$?
  grep -v "^ERGEBNIS" "$AUSGABE/netz.txt" || true
  grep '^ERGEBNIS' "$AUSGABE/netz.txt" | tee -a "$ZF"
  return $st
}

was=${1:-alle}
[ $# -gt 0 ] && shift
case "$was" in
  last) if [ $# -gt 0 ]; then last "$@"; else last 10 25 50; fi ;;
  netz) netz ;;
  alle) last 10 25 50; netz ;;
  *) echo "Aufruf: sh scripts/lasttest.sh [last [N …] | netz]"; exit 2 ;;
esac
echo "Zusammenfassung: $ZF"
