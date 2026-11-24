#!/bin/sh
# Nach jedem Feature: Tests, gitleaks, Push nach GitHub (öffentlich) und ins private Forgejo.
#   sh scripts/veroeffentlichen.sh            Tests + Push
#   sh scripts/veroeffentlichen.sh --ohne-e2e ohne Browser-E2E
set -e
cd "$(dirname "$0")/.."
[ -z "$(git status --porcelain)" ] || { echo "✗ Nicht committete Änderungen – erst committen"; git status --short; exit 1; }
docker build -q --target test -t agora:test . >/dev/null
ERG=$(docker run --rm agora:test 2>&1) || { echo "$ERG" | tail -30; echo "✗ API-Tests fehlgeschlagen"; exit 1; }
echo "$ERG" | tail -1
if [ "${1:-}" != --ohne-e2e ]; then
  ERG=$(sh tests/e2e/lauf.sh 2>&1) || { echo "$ERG" | tail -30; echo "✗ Browser-E2E fehlgeschlagen"; exit 1; }
  echo "$ERG" | tail -2
  # Sicherheits-Regressionstest (Black-Box gegen Wegwerf-Instanz, ca. 1 min) – docs/SICHERHEIT.md
  ERG=$(sh tests/sicherheit/lauf.sh 2>&1) || { echo "$ERG" | grep -E '!!|ANGRIFF|ABGEBROCHEN' | tail -30; echo "✗ Sicherheitstest fehlgeschlagen"; exit 1; }
  echo "$ERG" | tail -1
fi
docker run --rm -v "$PWD:/repo:ro" zricethezav/gitleaks:v8.30.0 git /repo --no-banner >/dev/null 2>&1 || { echo "✗ gitleaks hat etwas gefunden"; exit 1; }
echo "✓ gitleaks sauber"
git push -q github main && echo "✓ GitHub"
[ -x "$HOME/bin/forgejo-sync.py" ] || [ -f "$HOME/bin/forgejo-sync.py" ] && python3 "$HOME/bin/forgejo-sync.py" --nur agora | grep -E "ok|Fehler" && echo "✓ Forgejo"
