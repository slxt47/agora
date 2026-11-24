#!/bin/bash
# Startpunkt des Egress-Containers (statt /entrypoint.sh des Images, das bei read_only an tmpfs-Mountpunkten scheitert):
# baut die Egress-Konfiguration aus livekit.env (LIVEKIT_KEYS="<key>: <secret>", REDIS_HOST, REDIS_PASSWORD) – so liegt
# kein Geheimnis in einer zweiten Datei –, startet PulseAudio im Benutzermodus und dann Egress.
set -euo pipefail
schluessel="${LIVEKIT_KEYS%%:*}"
geheim="${LIVEKIT_KEYS#*:}"
geheim="${geheim# }"
export EGRESS_CONFIG_BODY="api_key: \"${schluessel}\"
api_secret: \"${geheim}\"
ws_url: \"${EGRESS_WS_URL:-ws://127.0.0.1:7880}\"
redis:
  address: \"${REDIS_HOST}\"
  password: \"${REDIS_PASSWORD}\"
template_port: ${EGRESS_TEMPLATE_PORT:-7884}
log_level: info"
unset LIVEKIT_KEYS REDIS_PASSWORD
find /home/egress/tmp -mindepth 1 -delete 2>/dev/null || true
mkdir -p -m 0700 "${XDG_RUNTIME_DIR:-/home/egress/.cache/xdgr}"
ulimit -n 65536 || true
pulseaudio -D --exit-idle-time=-1 --disallow-exit >/dev/null 2>&1
exec egress
