#!/usr/bin/env python3
"""Cloudflare-Cache-Regel für die HLS-Auslieferung von Agora (27.11.2026): agora.slxt.dev/hls/* zwischenspeichern,
Edge-TTL folgt den Cache-Control-Kopfzeilen der App (Segmente immutable, Playlists 2–5 s, 404 no-store).
Ergänzt die bestehende Zonen-Regelliste (Phase http_request_cache_settings), ersetzt nichts; Sicherung vorher.
    sudo python3 scripts/cloudflare-hls-cache.py            # einrichten
    sudo python3 scripts/cloudflare-hls-cache.py --zurueck  # wieder entfernen
"""
import json, os, sys, time
sys.path.insert(0, os.path.expanduser('~fneubauer/compose/date-admin'))
from cf import cf  # noqa: E402

HOST, NAME = 'agora.slxt.dev', 'Agora HLS zwischenspeichern'
zid = cf('GET', '/zones?name=slxt.dev')['result'][0]['id']
r = cf('GET', f'/zones/{zid}/rulesets/phases/http_request_cache_settings/entrypoint')
regeln = ((r.get('result') or {}).get('rules') or []) if r.get('success') else []
p = f"/home/fneubauer/backups/cloudflare-cache-regeln-vor-agora-hls-{time.strftime('%Y-%m-%d-%H%M')}.json"
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps(regeln, indent=1).encode()); os.close(fd); os.chown(p, 1000, 1000)
print('Sicherung:', p, '–', len(regeln), 'Regeln vorher')
behalten = [{k: v for k, v in x.items() if k in ('action', 'action_parameters', 'expression', 'description', 'enabled')}
            for x in regeln if x.get('description') != NAME]
if '--zurueck' not in sys.argv:
    behalten.append({'description': NAME, 'enabled': True, 'action': 'set_cache_settings',
                     'expression': f'(http.host eq "{HOST}" and starts_with(http.request.uri.path, "/hls/"))',
                     'action_parameters': {'cache': True, 'edge_ttl': {'mode': 'respect_origin'},
                                           'browser_ttl': {'mode': 'respect_origin'}}})
erg = cf('PUT', f'/zones/{zid}/rulesets/phases/http_request_cache_settings/entrypoint', {'rules': behalten})
print('Ergebnis:', 'ok' if erg.get('success') else erg.get('errors'))
print('Regeln jetzt:', [x.get('description') for x in
                        cf('GET', f'/zones/{zid}/rulesets/phases/http_request_cache_settings/entrypoint')['result']['rules']])
