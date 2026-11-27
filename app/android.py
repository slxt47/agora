"""Android-App (Trusted Web Activity, mobil/android/): Digital Asset Links unter /.well-known/assetlinks.json.

Chrome zeigt die TWA nur ohne Adressleiste, wenn diese Datei den Paketnamen und den SHA-256-Fingerabdruck des
Signatur-Zertifikats nennt. Ausgeliefert wird sie nur, wenn `AGORA_ANDROID_SHA256` gesetzt ist (sonst 404):
- `AGORA_ANDROID_SHA256`: ein oder mehrere Fingerabdrücke, Komma-getrennt, Form `AB:CD:…` (32 Byte, Hex mit Doppelpunkt;
  ohne Doppelpunkte geht auch). Z. B. Upload-Schlüssel und Play-App-Signatur.
- `AGORA_ANDROID_PAKET`: Paketname, Vorgabe `dev.slxt.agora.twa`.
Ungültige Werte → 404 und eine Zeile im Log (lieber keine Datei als eine falsche).
"""
import os
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

router = APIRouter()
PAKET_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$')
_gemeldet = set()


def fingerabdruck(text):
    """'ab:cd:…' oder 'abcd…' (64 Hex) → 'AB:CD:…' oder None."""
    roh = text.strip().replace(':', '').upper()
    if not re.fullmatch(r'[0-9A-F]{64}', roh):
        return None
    return ':'.join(roh[i:i + 2] for i in range(0, 64, 2))


def asset_links():
    """Inhalt der Datei oder None (nicht eingerichtet/ungültig)."""
    roh = os.environ.get('AGORA_ANDROID_SHA256', '').strip()
    if not roh:
        return None
    paket = os.environ.get('AGORA_ANDROID_PAKET', 'dev.slxt.agora.twa').strip()
    abdruecke = [fingerabdruck(t) for t in roh.split(',') if t.strip()]
    if not PAKET_RE.match(paket) or len(paket) > 150 or not abdruecke or None in abdruecke or len(abdruecke) > 5:
        if roh not in _gemeldet:
            _gemeldet.add(roh)
            print('android: AGORA_ANDROID_SHA256/AGORA_ANDROID_PAKET ungültig – assetlinks.json bleibt aus', flush=True)
        return None
    return [{
        'relation': ['delegate_permission/common.handle_all_urls'],
        'target': {'namespace': 'android_app', 'package_name': paket, 'sha256_cert_fingerprints': list(dict.fromkeys(abdruecke))},
    }]


@router.api_route('/.well-known/assetlinks.json', methods=['GET', 'HEAD'])
def assetlinks():
    inhalt = asset_links()
    if inhalt is None:
        return Response(status_code=404)
    return JSONResponse(inhalt)
