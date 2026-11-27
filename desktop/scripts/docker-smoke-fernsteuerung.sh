#!/bin/sh
# Smoke-Test der Fernsteuerung (Linux, X11 unter xvfb in Docker): startet die gebaute App mit dem Selbsttest
# src/selbsttest-fernsteuerung.js und ein fremdes Zielfenster (test/ziel.py, Tk). Der Test geht den echten Weg
# Seite → Brücke → Hauptprozess → Eingabe-Adapter → X-Server und beobachtet die Wirkung mit xdotool und dem Zielfenster.
# Braucht keine Agora-Instanz (eine leere Seite aus python3 -m http.server im Container genügt).
# Aufruf aus dem Projektordner:  sh desktop/scripts/docker-smoke-fernsteuerung.sh          (Adapter robotjs)
#                                EINGABE=xdotool sh desktop/scripts/docker-smoke-fernsteuerung.sh
# Fehlt dist/linux-<arch>-unpacked, wird nur dieses Verzeichnis gebaut (electron-builder --dir, ~1 min).
set -e
cd "$(dirname "$0")/.."
case "$(uname -m)" in aarch64|arm64) ARCH=arm64 ;; *) ARCH=x64 ;; esac
APP="dist/linux-$ARCH-unpacked"
[ "$ARCH" = x64 ] && APP=dist/linux-unpacked
BILD=node:22.23.5-bookworm-slim
if [ ! -x "$APP/agora" ] || [ -n "$NEU_BAUEN" ]; then
  mkdir -p .cache
  docker run --rm -v "$PWD":/d -w /d -e HOME=/d/.cache/home -e ELECTRON_CACHE=/d/.cache/electron \
    -e ELECTRON_BUILDER_CACHE=/d/.cache/builder -e "UID_GID=$(id -u):$(id -g)" "$BILD" sh -ec '
      # robotjs prüft beim Installieren sein Prebuild (lädt libXtst) – sonst versucht es einen Neubau
      apt-get update -qq && apt-get install -y -qq --no-install-recommends libxtst6 libx11-6 libpng16-16 >/dev/null
      npm ci --no-audit --no-fund >/dev/null
      npx electron-builder --linux dir --'"$ARCH"' --publish never >/dev/null
      chown -R "$UID_GID" /d/dist /d/node_modules /d/.cache'
fi
docker run --rm --shm-size=1g -v "$PWD/$APP":/app:ro -v "$PWD/test/ziel.py":/ziel.py:ro -e EINGABE="${EINGABE:-}" "$BILD" sh -ec '
  apt-get update -qq && apt-get install -y -qq --no-install-recommends xvfb xauth libgtk-3-0 libnss3 libasound2 libgbm1 libxss1 \
    libxtst6 libatspi2.0-0 libdrm2 libxkbcommon0 libsecret-1-0 libnotify4 dbus-x11 xdotool python3 python3-tk x11-xkb-utils xkb-data >/dev/null
  mkdir -p /tmp/seite && echo "<!doctype html><title>Agora-Test</title><p>Fernsteuerung" > /tmp/seite/index.html
  (cd /tmp/seite && python3 -m http.server 8253 --bind 127.0.0.1 >/dev/null 2>&1 &)
  Xvfb :99 -screen 0 1280x900x24 -nolisten tcp >/dev/null 2>&1 &
  export DISPLAY=:99
  sleep 1
  setxkbmap de || true            # deutsches Layout: ä, ö, ü haben eigene Tasten (wie auf einem echten Desktop)
  AGORA_ZIEL_LOG=/tmp/ziel.log python3 /ziel.py &
  sleep 1
  # --no-sandbox nur hier: im Container fehlt der SUID-Helfer/Namespaces für die Chromium-Sandbox
  AGORA_DESKTOP_TEST=1 AGORA_DESKTOP_SELBSTTEST=1 AGORA_DESKTOP_SELBSTTEST_ART=fernsteuerung AGORA_DESKTOP_TEST_QUELLE=erste \
  AGORA_DESKTOP_TEST_ZUSTIMMUNG=ja AGORA_DESKTOP_PROFIL=/tmp/profil AGORA_ZIEL_LOG=/tmp/ziel.log AGORA_EINGABE="$EINGABE" \
  AGORA_ADRESSE=http://127.0.0.1:8253/ timeout 180 dbus-launch --exit-with-session /app/agora --no-sandbox 2>&1 \
    | grep -v -E "^\[[0-9:/.]+ (WARNING|ERROR|INFO):|dbus|Gtk-WARNING|libva" | grep -E "AGORA_(SELBSTTEST|TEST eingabe|TEST fernsteuerung-ende)" | tee /tmp/aus.txt
  echo "Zielfenster-Log:"; cat /tmp/ziel.log
  grep -q "AGORA_SELBSTTEST {\"schritte\".*\"ok\":true" /tmp/aus.txt
'
