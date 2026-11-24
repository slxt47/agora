"""Ende-zu-Ende-Verschlüsselung (E2EE) je Raum – der Server kennt den Schlüssel NIE.

Der Host erzeugt im Browser ein Geheimnis (32 Zufallsbytes, base64url) und verteilt es nur im Link-Fragment
(`/r/<code>#k=…`, Fragmente schickt der Browser nicht an den Server). Gespeichert wird hier nur:

- `e2ee` (an/aus), damit jede Lobby weiß, dass sie einen Schlüssel braucht, und
- `e2ee_pruef`: ein Prüfwert, den der Browser per HKDF-SHA-256 aus dem Geheimnis ableitet (eigenes `info`, getrennt
  vom Medienschlüssel). Damit erkennt die Lobby einen falschen Schlüssel sofort. Aus dem Prüfwert lässt sich weder
  das Geheimnis noch der Medienschlüssel berechnen (HKDF ist eine Einwegfunktion, das Geheimnis hat 256 Bit).

An- und Ausschalten geht nur, solange niemand verbunden ist – sonst säßen im selben Meeting Teilnehmende mit und
ohne Verschlüsselung (oder mit altem und neuem Schlüssel). Siehe docs/adr/0005-e2ee.md.
"""
import re

from . import livekit_dienst as lk
from .kern import fehler

PRUEF_RE = re.compile(r'^[0-9a-f]{32}$')


def felder_lesen(d):
    """Felder `e2ee` (bool) und `e2ee_pruef` (32 Hex-Zeichen, Pflicht beim Einschalten) → Spalten."""
    if 'e2ee' not in d:
        if 'e2ee_pruef' in d:
            fehler(400, 'Ungültige Anfrage')
        return {}
    an = d['e2ee']
    if not isinstance(an, bool):
        fehler(400, 'Ungültige Anfrage')
    if not an:
        return {'e2ee': 0, 'e2ee_pruef': None}
    pruef = d.get('e2ee_pruef')
    if not isinstance(pruef, str) or not PRUEF_RE.match(pruef):
        fehler(400, 'Für die Ende-zu-Ende-Verschlüsselung fehlt der Prüfwert des Schlüssels.')
    return {'e2ee': 1, 'e2ee_pruef': pruef}


async def aenderung_pruefen(r, f):
    """Schlüssel/Modus nur ändern, solange niemand im Meeting ist (LiveKit weiß, wer verbunden ist)."""
    if 'e2ee' not in f or (f['e2ee'] == r['e2ee'] and f['e2ee_pruef'] == r['e2ee_pruef']):
        return
    if r['aktiv']:
        liste = await lk.teilnehmer(r['code'])
        if liste is None or liste:
            fehler(409, 'Die Verschlüsselung lässt sich nur ändern, solange niemand im Meeting ist.')


def json_teil(r):
    return {'e2ee': bool(r['e2ee']), 'e2ee_pruef': r['e2ee_pruef'] if r['e2ee'] else None}
