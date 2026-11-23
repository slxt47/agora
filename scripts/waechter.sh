#!/bin/sh
# Agora-Wächter: prüft /api/gesund?tief=1 und /api/metriken und gibt EINE Zeile Status aus – für das Server-Monitoring.
#   Exit 0 = OK, 1 = Warnung, 2 = Fehler (App, Datenbank, LiveKit, Platte oder Hintergrund).
# Aufruf auf dem Server:  sh scripts/waechter.sh
# Umgebung: AGORA_URL (Standard http://127.0.0.1:8110), AGORA_METRIKEN_TOKEN (nur nötig, wenn nicht von 127.0.0.1),
#           AGORA_WAECHTER_STAND (Datei für den letzten Zählerstand, Standard ~/.cache/agora-waechter.stand)
# Warnungen aus Zähler-Differenzen seit dem letzten Lauf: fehlgeschlagene Mails, LiveKit-Fehler (> 5),
# fehlgeschlagene Anmeldungen (> 50), Fehler in der Hintergrund-Schleife.
# Braucht: curl, jq, awk.
set -u
URL=${AGORA_URL:-http://127.0.0.1:8110}
STAND=${AGORA_WAECHTER_STAND:-${HOME:-/tmp}/.cache/agora-waechter.stand}
TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT

holen() {   # holen <pfad> <datei> → HTTP-Status
  if [ -n "${AGORA_METRIKEN_TOKEN:-}" ]; then
    curl -s -m 10 -o "$2" -w '%{http_code}' -H "Authorization: Bearer $AGORA_METRIKEN_TOKEN" "$URL$1"
  else
    curl -s -m 10 -o "$2" -w '%{http_code}' "$URL$1"
  fi
}

code=$(holen '/api/gesund?tief=1' "$TMP/gesund") || code=000
if [ "$code" = 000 ]; then echo "agora FEHLER – App antwortet nicht ($URL)"; exit 2; fi
if [ "$code" = 404 ]; then echo "agora FEHLER – kein Zugriff auf die Prüfung (nicht lokal? AGORA_METRIKEN_TOKEN setzen)"; exit 2; fi
if ! jq -e . "$TMP/gesund" >/dev/null 2>&1; then echo "agora FEHLER – /api/gesund?tief=1 liefert HTTP $code ohne JSON"; exit 2; fi

j() { jq -r "$1 // \"-\"" "$TMP/gesund"; }
fehlt=$(jq -r '[.pruefungen | to_entries[] | select(.value.ok != true) | .key] | join(", ")' "$TMP/gesund")
frei_gb=$(jq -r '(.pruefungen.platte.frei_bytes // 0) / 1073741824 | floor' "$TMP/gesund")

mcode=$(holen '/api/metriken' "$TMP/metriken") || mcode=000
wert() {   # wert <metrik mit Labels, z. B. agora_mails_total{ergebnis="fehler"}> → Zahl (0 wenn fehlt)
  awk -v n="$1" '$1 == n { print $2; f = 1 } END { if (!f) print 0 }' "$TMP/metriken" | head -1 | awk '{ printf "%d", $1 }'
}
if [ "$mcode" = 200 ]; then
  meetings=$(wert agora_meetings_aktiv)
  verbunden=$(wert agora_teilnehmer_verbunden)
  mail_ok=$(wert 'agora_mails_total{ergebnis="ok"}')
  mail_fehler=$(wert 'agora_mails_total{ergebnis="fehler"}')
  login_fehler=$(wert 'agora_anmeldungen_total{ergebnis="fehlgeschlagen"}')
  lk_fehler=$(awk '/^agora_livekit_fehler_total/ { s += $2 } END { printf "%d", s }' "$TMP/metriken")
  hg_fehler=$(wert agora_hintergrund_fehler_total)
  start=$(wert agora_start_zeitstempel_sekunden)
else
  meetings='?' verbunden='?' mail_ok=0 mail_fehler=0 login_fehler=0 lk_fehler=0 hg_fehler=0 start=0
fi

# Differenzen zum letzten Lauf (nach einem Neustart der App beginnen die Zähler bei 0)
d_mail=0 d_lk=0 d_login=0 d_hg=0
if [ -r "$STAND" ]; then
  # shellcheck disable=SC1090
  . "$STAND"
  if [ "${alt_start:-0}" = "$start" ]; then
    d_mail=$((mail_fehler - ${alt_mail:-0})); d_lk=$((lk_fehler - ${alt_lk:-0}))
    d_login=$((login_fehler - ${alt_login:-0})); d_hg=$((hg_fehler - ${alt_hg:-0}))
  fi
fi
mkdir -p "$(dirname "$STAND")" 2>/dev/null
printf 'alt_start=%s\nalt_mail=%s\nalt_lk=%s\nalt_login=%s\nalt_hg=%s\n' "$start" "$mail_fehler" "$lk_fehler" "$login_fehler" "$hg_fehler" > "$STAND" 2>/dev/null

details="$meetings Meetings, $verbunden verbunden, LiveKit $(j .pruefungen.livekit.ms) ms, DB $(j .pruefungen.datenbank.ms) ms, \
Platte ${frei_gb} GB frei ($(j .pruefungen.platte.frei_prozent) %), Hintergrund vor $(j .pruefungen.hintergrund.alter_s) s, \
Mails ${mail_ok} ok/${mail_fehler} Fehler, LiveKit-Fehler ${lk_fehler}"

if [ "$code" != 200 ] || [ -n "$fehlt" ]; then
  echo "agora FEHLER – ${fehlt:-HTTP $code} | $details"; exit 2
fi
warn=''
[ "$mcode" != 200 ] && warn="$warn Metriken HTTP $mcode;"
[ "$d_mail" -gt 0 ] && warn="$warn $d_mail Mail(s) fehlgeschlagen;"
[ "$d_lk" -gt 5 ] && warn="$warn $d_lk LiveKit-Fehler;"
[ "$d_login" -gt 50 ] && warn="$warn $d_login fehlgeschlagene Anmeldungen;"
[ "$d_hg" -gt 0 ] && warn="$warn $d_hg Hintergrund-Fehler;"
if [ -n "$warn" ]; then echo "agora WARNUNG –${warn%;} | $details"; exit 1; fi
echo "agora OK – $details"
exit 0
