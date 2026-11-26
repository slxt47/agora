#!/bin/sh
# Smoke-Test der gebauten Linux-App (dist/linux-<arch>-unpacked) unter xvfb in Docker gegen eine laufende Testinstanz:
# startet die App, tritt einem Raum bei, löst die Kürzel über den Test-Haken aus, prüft Brücke, Navigation, Rechte und
# Bildschirmfreigabe (src/selbsttest.js). Vorher: Testinstanz starten (tests/e2e/docker-compose.test.yml) und einen
# Raum anlegen. Aufruf: ADRESSE=http://127.0.0.1:8221/r/abc-defg-hij sh desktop/scripts/docker-smoke.sh
set -e
cd "$(dirname "$0")/.."
: "${ADRESSE:?ADRESSE=http://127.0.0.1:<port>/r/<code> setzen}"
case "$(uname -m)" in aarch64|arm64) ARCH=arm64 ;; *) ARCH=x64 ;; esac
APP="dist/linux-$ARCH-unpacked"
[ "$ARCH" = x64 ] && APP=dist/linux-unpacked
[ -x "$APP/agora" ] || { echo "$APP/agora fehlt – erst sh desktop/scripts/docker-bauen.sh"; exit 1; }
docker run --rm --network host --shm-size=1g -v "$PWD/$APP":/app:ro node:22.23.5-bookworm-slim sh -ec '
  apt-get update -qq && apt-get install -y -qq --no-install-recommends xvfb xauth libgtk-3-0 libnss3 libasound2 libgbm1 libxss1 \
    libxtst6 libatspi2.0-0 libdrm2 libxkbcommon0 libsecret-1-0 libnotify4 dbus-x11 >/dev/null
  # --no-sandbox nur hier: im Container fehlt der SUID-Helfer/Namespaces für die Chromium-Sandbox
  AGORA_DESKTOP_TEST=1 AGORA_DESKTOP_SELBSTTEST=1 AGORA_DESKTOP_TEST_QUELLE=erste AGORA_DESKTOP_PROFIL=/tmp/profil \
  AGORA_ADRESSE="'"$ADRESSE"'" timeout 180 xvfb-run -a -s "-screen 0 1280x900x24" dbus-launch --exit-with-session \
    /app/agora --no-sandbox 2>&1 | grep -v -E "^\[[0-9:/.]+ (WARNING|ERROR|INFO):|dbus|Gtk-WARNING|libva" | tee /tmp/aus.txt
  grep -q "AGORA_SELBSTTEST {\"schritte\".*\"ok\":true" /tmp/aus.txt
'
