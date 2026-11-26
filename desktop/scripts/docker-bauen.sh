#!/bin/sh
# Linux-Pakete der Desktop-App in Docker bauen (ohne lokales Node/Electron): AppImage + deb für arm64 und x64.
# Aufruf aus dem Projektordner: sh desktop/scripts/docker-bauen.sh   → desktop/dist/
# Caches (Electron-Zips, AppImage-Werkzeuge) landen in desktop/.cache/ und lassen sich danach löschen.
set -e
cd "$(dirname "$0")/.."
BILD=node:22.23.5-bookworm-slim
mkdir -p .cache
docker run --rm -v "$PWD":/d -w /d -e HOME=/d/.cache/home \
  -e ELECTRON_CACHE=/d/.cache/electron -e ELECTRON_BUILDER_CACHE=/d/.cache/builder \
  -e USE_SYSTEM_FPM=true -e "UID_GID=$(id -u):$(id -g)" "$BILD" sh -ec '
    # deb braucht fpm (Ruby); das mitgelieferte fpm von electron-builder gibt es nur für x86_64
    apt-get update -qq && apt-get install -y -qq --no-install-recommends ruby binutils xz-utils rpm ca-certificates >/dev/null
    gem install --no-document -q fpm -v 1.18.0 >/dev/null
    npm ci --no-audit --no-fund
    npm test
    npx electron-builder --linux AppImage deb --arm64 --x64 --publish never
    chown -R "$UID_GID" /d/dist /d/node_modules /d/.cache
  '
ls -la dist/*.AppImage dist/*.deb
