#!/bin/sh
# E2E: Testinstanz (echte LiveKit + App im Testmodus) bauen, Playwright-Ablauf mit zwei Browsern, aufräumen.
# Aufruf aus dem Projektordner: sh tests/e2e/lauf.sh   (Bildschirmfotos landen in tests/e2e/bilder/)
set -e
cd "$(dirname "$0")"
mkdir -p bilder
docker compose -f docker-compose.test.yml up -d --build
trap 'docker compose -f docker-compose.test.yml down -v >/dev/null 2>&1' EXIT
i=0; until curl -sf http://127.0.0.1:8111/api/gesund >/dev/null; do i=$((i+1)); [ $i -gt 60 ] && { echo "App startet nicht"; exit 1; }; sleep 1; done
docker build -q -t agora-playwright:1.64.1 -f Dockerfile.playwright . >/dev/null
docker run --rm --network host --ipc=host -v "$PWD:/e2e" -e BASIS=http://127.0.0.1:8111 -e AUSGABE=/e2e/bilder/ \
  agora-playwright:1.64.1 python3 /e2e/browser_e2e.py
