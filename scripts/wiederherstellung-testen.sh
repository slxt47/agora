#!/bin/sh
# Wiederherstellung testen: die letzte Sicherung (daten/sicherung/agora.sqlite3, alle 6 h) in einer Wegwerf-App öffnen
# und prüfen, dass sie startet und die Daten lesbar sind. Ändert nichts an der laufenden Instanz.
#   sh scripts/wiederherstellung-testen.sh [pfad/zur/sicherung.sqlite3]
set -e
cd "$(dirname "$0")/.."
QUELLE=${1:-daten/sicherung/agora.sqlite3}
[ -f "$QUELLE" ] || { echo "✗ keine Sicherung unter $QUELLE"; exit 1; }
TMP=$(mktemp -d); trap 'docker rm -f agora-wiederherstellung >/dev/null 2>&1; rm -rf "$TMP"' EXIT
cp "$QUELLE" "$TMP/agora.sqlite3"; chmod -R a+rwX "$TMP"
docker run -d --name agora-wiederherstellung --network none -v "$TMP:/daten" -e AGORA_TEST=1 -e AGORA_DATEN=/daten \
  --entrypoint python agora:1.0.0 -c "import time; time.sleep(120)" >/dev/null
docker exec -i agora-wiederherstellung python - <<'PY'
import sqlite3
c = sqlite3.connect('/daten/agora.sqlite3')
assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Datenbank beschädigt'
v = c.execute('SELECT * FROM schema_version').fetchone()[0]
z = {t: c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('benutzer', 'raeume', 'aufnahmen')}
print(f'✓ Sicherung lesbar: Schema {v}, {z}')
import app.main  # Schema-Migration auf der Kopie, App lädt
print('✓ App startet mit der Sicherung')
PY
