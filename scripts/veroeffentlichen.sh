#!/bin/sh
# Nach jedem Feature: Tests, gitleaks, Push nach GitHub (öffentlich) und ins private Forgejo.
#   sh scripts/veroeffentlichen.sh            Tests + Push
#   sh scripts/veroeffentlichen.sh --ohne-e2e ohne Browser-E2E
set -e
cd "$(dirname "$0")/.."
[ -z "$(git status --porcelain)" ] || { echo "✗ Nicht committete Änderungen – erst committen"; git status --short; exit 1; }
docker build -q --target test -t agora:test . >/dev/null && docker run --rm agora:test | tail -1
[ "${1:-}" = --ohne-e2e ] || sh tests/e2e/lauf.sh | tail -2
docker run --rm -v "$PWD:/repo:ro" zricethezav/gitleaks:v8.30.0 git /repo --no-banner >/dev/null 2>&1 || { echo "✗ gitleaks hat etwas gefunden"; exit 1; }
echo "✓ gitleaks sauber"
git push -q github main && echo "✓ GitHub"
[ -x "$HOME/bin/forgejo-sync.py" ] || [ -f "$HOME/bin/forgejo-sync.py" ] && python3 "$HOME/bin/forgejo-sync.py" --nur agora | grep -E "ok|Fehler" && echo "✓ Forgejo"
