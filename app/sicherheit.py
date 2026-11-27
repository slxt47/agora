"""Sicherheitsereignisse des eigenen Kontos (Konto → Profil → Sicherheit): Anmeldungen, Fehlversuche, Passwort- und
Zwei-Faktor-Änderungen, Sperren durch Admins. Quelle ist das Protokoll (ohne Inhalte, 30 Tage)."""
from fastapi import APIRouter, Request

from . import db
from .kern import benutzer_aus_sitzung, iso

router = APIRouter()
MAX = 100

# Ereignistext im Protokoll → Art (für Symbol und Text im Browser). Präfixe, damit Zusätze wie „(3 übrig)“ passen.
ARTEN = [
    ('angemeldet (mit Wiederherstellungscode)', 'anmeldung_code'),
    ('angemeldet (mit 2FA)', 'anmeldung_2fa'),
    ('angemeldet über', 'anmeldung_extern'),
    ('angemeldet', 'anmeldung'),
    ('Anmeldung: falsches Passwort', 'fehlversuch_passwort'),
    ('Anmeldung: falscher Code', 'fehlversuch_code'),
    ('Anmeldung: Konto gesperrt', 'fehlversuch_gesperrt'),
    ('Passwort geändert', 'passwort_geaendert'),
    ('Passwort zurückgesetzt', 'passwort_zurueckgesetzt'),
    ('E-Mail bestätigt', 'email_bestaetigt'),
    ('2FA eingeschaltet', 'zwei_faktor_an'),
    ('2FA ausgeschaltet', 'zwei_faktor_aus'),
    ('2FA: neue Wiederherstellungscodes', 'zwei_faktor_codes'),
    ('2FA: Wiederherstellungscode benutzt', 'zwei_faktor_code_benutzt'),
    ('2FA: falscher Code', 'fehlversuch_code'),
    ('2FA zurückgesetzt (Admin)', 'zwei_faktor_admin'),
    ('Konto gesperrt (Admin)', 'gesperrt'),
    ('Konto entsperrt (Admin)', 'entsperrt'),
    ('API-Schlüssel erstellt', 'api_schluessel_neu'),
    ('API-Schlüssel widerrufen', 'api_schluessel_weg'),
    ('Anmeldung verknüpft', 'extern_verknuepft'),
    ('Anmeldung getrennt', 'extern_getrennt'),
]


def art(ereignis):
    for praefix, a in ARTEN:
        if ereignis == praefix or ereignis.startswith(praefix + ' '):
            return a
    return None


@router.get('/api/konto/sicherheit')
def sicherheit(req: Request):
    b = benutzer_aus_sitzung(req)
    rows = db.verbindung().execute('SELECT zeit, ereignis FROM protokoll WHERE benutzer_id = ? ORDER BY zeit DESC, rowid DESC LIMIT 1000',
                                   (b['id'],)).fetchall()
    out = []
    for r in rows:
        a = art(r['ereignis'])
        if a:
            out.append({'zeit': iso(r['zeit']), 'art': a, 'ereignis': r['ereignis']})
            if len(out) >= MAX:
                break
    return {'ereignisse': out, 'tage': 30}
