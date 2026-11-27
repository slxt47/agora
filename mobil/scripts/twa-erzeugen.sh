#!/bin/sh
# mobil/android/ neu erzeugen (Bubblewrap-Kern 1.27.0 in Docker, ohne Android-SDK). Symbole kommen von ICONS_VON
# (Vorgabe: eine laufende Testinstanz auf 127.0.0.1:8221; sonst die öffentliche Seite).
# Aufruf aus dem Projektordner: ICONS_VON=http://127.0.0.1:8221 sh mobil/scripts/twa-erzeugen.sh
set -e
cd "$(dirname "$0")/../.."
ICONS_VON=${ICONS_VON:-http://127.0.0.1:8221}
docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp -e "ICONS_VON=$ICONS_VON" -v "$PWD":/p -w /p/mobil \
  node:22.23.5-bookworm-slim sh -ec '
    npm ci --no-audit --no-fund --ignore-scripts >/dev/null
    rm -rf android.neu && node scripts/twa-erzeugen.js android.neu ../web/public/manifest.webmanifest
    rm -rf android && mv android.neu android && rm -rf node_modules
  '
echo "mobil/android/ erzeugt"
