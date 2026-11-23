"""Zwei-Faktor-Anmeldung mit TOTP (RFC 6238: SHA-1, 6 Stellen, 30 s) – freiwillig, im Profil einzuschalten.

- Einrichten: Passwort bestätigen → neues Geheimnis (QR-Code zeichnet der Browser selbst) → erster Code schaltet ein
  und liefert 10 Wiederherstellungscodes (nur einmal sichtbar, gespeichert nur als HMAC).
- Anmelden: Passwort richtig → `{zwei_faktor:true, schritt}` statt Sitzung; POST …/anmelden/code mit TOTP- oder
  Wiederherstellungscode → Sitzung. Ein Schritt gilt 5 min und höchstens 5 Versuche; dazu Grenzen je Konto und IP.
- Toleranz ±1 Zeitschritt; jeder Schritt wird höchstens einmal angenommen (totp_schritt, atomar hochgezählt).
- Das Geheimnis liegt verschlüsselt in der Datenbank: Schlüssel aus AGORA_GEHEIM abgeleitet (HMAC-SHA256),
  Verschlüsselung als HMAC-SHA256-Schlüsselstrom (CTR) mit Zufalls-Nonce, danach HMAC-Prüfsumme (encrypt-then-MAC).
  Nur Standardbibliothek – keine zusätzliche Abhängigkeit.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import db
from .kern import GEHEIM, benutzer_aus_sitzung, client_ip, fehler, grenze, h, jetzt, json_body, pw_ok, sitzung_setzen

router = APIRouter()
SCHRITT_S = 30
STELLEN = 6
TOLERANZ = 1                    # ±1 Zeitschritt (Uhren laufen nie ganz gleich)
ANZAHL_CODES = 10
SCHRITT_GUELTIG_S = 300         # zweiter Anmeldeschritt
MAX_VERSUCHE = 5
AUSSTELLER = 'Agora'
CODE_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'     # ohne i/l/o/0/1


def _zeit():                    # eigene Funktion, damit Tests die Uhr verstellen können
    return time.time()


# ---------------------------------------------------------------- TOTP (RFC 4226/6238)
def hotp(schluessel: bytes, zaehler: int, stellen=STELLEN):
    d = hmac.new(schluessel, struct.pack('>Q', zaehler), hashlib.sha1).digest()
    o = d[-1] & 0x0F
    wert = struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF
    return str(wert % 10 ** stellen).zfill(stellen)


def b32_neu():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def b32_bytes(geheim):
    return base64.b32decode(geheim + '=' * (-len(geheim) % 8))


def code_pruefen(geheim_b32, code, letzter_schritt, t=None):
    """Passender Zeitschritt (> letzter_schritt) oder None."""
    if not isinstance(code, str) or len(code) != STELLEN or not code.isdigit():
        return None
    schritt = int((t if t is not None else _zeit()) // SCHRITT_S)
    k = b32_bytes(geheim_b32)
    for s in range(schritt - TOLERANZ, schritt + TOLERANZ + 1):
        if s > letzter_schritt and hmac.compare_digest(hotp(k, s), code):
            return s
    return None


def uri(geheim, email):
    return (f'otpauth://totp/{quote(AUSSTELLER)}:{quote(email)}?secret={geheim}&issuer={quote(AUSSTELLER)}'
            f'&algorithm=SHA1&digits={STELLEN}&period={SCHRITT_S}')


# ---------------------------------------------------------------- Verschlüsselung des Geheimnisses
def _schluessel(zweck):
    return hmac.new(GEHEIM, b'agora-totp-' + zweck, hashlib.sha256).digest()


def _strom(nonce, laenge):
    k, out, i = _schluessel(b'verschluesseln'), b'', 0
    while len(out) < laenge:
        out += hmac.new(k, nonce + struct.pack('>I', i), hashlib.sha256).digest()
        i += 1
    return out[:laenge]


def verschluesseln(text):
    nonce = secrets.token_bytes(16)
    roh = text.encode()
    chiffre = bytes(a ^ b for a, b in zip(roh, _strom(nonce, len(roh))))
    tag = hmac.new(_schluessel(b'pruefen'), b'v1' + nonce + chiffre, hashlib.sha256).digest()[:16]
    return 'v1.' + base64.urlsafe_b64encode(nonce + chiffre + tag).decode()


def entschluesseln(wert):
    """Klartext oder None (falsches AGORA_GEHEIM, beschädigt)."""
    try:
        art, rest = wert.split('.', 1)
        roh = base64.urlsafe_b64decode(rest)
    except (AttributeError, ValueError):
        return None
    if art != 'v1' or len(roh) < 33:
        return None
    nonce, chiffre, tag = roh[:16], roh[16:-16], roh[-16:]
    if not hmac.compare_digest(tag, hmac.new(_schluessel(b'pruefen'), b'v1' + nonce + chiffre, hashlib.sha256).digest()[:16]):
        return None
    return bytes(a ^ b for a, b in zip(chiffre, _strom(nonce, len(chiffre)))).decode()


# ---------------------------------------------------------------- Wiederherstellungscodes
def code_hash(code):
    return hmac.new(_schluessel(b'wiederherstellung'), code.encode(), hashlib.sha256).hexdigest()


def normalisieren(code):
    return ''.join(ch for ch in str(code or '').lower() if ch.isalnum())[:40]


def codes_neu(benutzer_id):
    c = db.verbindung()
    c.execute('DELETE FROM wiederherstellung WHERE benutzer_id = ?', (benutzer_id,))
    codes = []
    for _ in range(ANZAHL_CODES):
        z = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(10))
        codes.append(f'{z[:5]}-{z[5:]}')
        c.execute('INSERT INTO wiederherstellung VALUES (?, ?)', (benutzer_id, code_hash(normalisieren(codes[-1]))))
    return codes


def codes_uebrig(benutzer_id):
    return db.verbindung().execute('SELECT COUNT(*) FROM wiederherstellung WHERE benutzer_id = ?', (benutzer_id,)).fetchone()[0]


def zweiter_faktor_ok(b, code):
    """TOTP-Code (verbraucht seinen Zeitschritt) oder Wiederherstellungscode (wird gelöscht). → 'totp' | 'code' | None"""
    c = db.verbindung()
    roh = normalisieren(code)
    if len(roh) == STELLEN and roh.isdigit():
        geheim = entschluesseln(b['totp_geheim']) if b['totp_geheim'] else None
        s = code_pruefen(geheim, roh, b['totp_schritt']) if geheim else None
        # atomar: nur der erste, der diesen Schritt einlöst, gewinnt (Schutz gegen Wiederverwendung)
        if s is not None and c.execute('UPDATE benutzer SET totp_schritt = ? WHERE id = ? AND totp_schritt < ?',
                                       (s, b['id'], s)).rowcount == 1:
            return 'totp'
        return None
    if len(roh) == 10 and c.execute('DELETE FROM wiederherstellung WHERE benutzer_id = ? AND code_hash = ?',
                                    (b['id'], code_hash(roh))).rowcount == 1:
        db.protokollieren(f'2FA: Wiederherstellungscode benutzt ({codes_uebrig(b["id"])} übrig)', b['id'])
        return 'code'
    return None


def benutzer_neu(bid):
    return db.verbindung().execute('SELECT * FROM benutzer WHERE id = ?', (bid,)).fetchone()


async def passwort_pruefen(b, d):
    pw = d.get('passwort')
    if not isinstance(pw, str) or len(pw) > 200 or not await run_in_threadpool(pw_ok, b['passwort_hash'], pw):
        fehler(400, 'Passwort falsch.')


# ---------------------------------------------------------------- Anmelden, Schritt 2
def schritt_erstellen(b):
    """Nach richtigem Passwort: Token für den Code-Schritt (statt Sitzung)."""
    t = secrets.token_urlsafe(32)
    c = db.verbindung()
    c.execute('DELETE FROM anmeldeschritte WHERE gueltig_bis < ? OR benutzer_id = ?', (jetzt(), b['id']))
    c.execute('INSERT INTO anmeldeschritte VALUES (?, ?, ?, 0)', (h(t), b['id'], jetzt() + SCHRITT_GUELTIG_S))
    return t


@router.post('/api/konto/anmelden/code')
async def anmelden_code(req: Request):
    from .konto import ich_json        # spät importieren (konto importiert dieses Modul)
    d = await json_body(req)
    ip = client_ip(req)
    grenze.pruefen('code:' + ip, 30, 600)
    t = d.get('schritt')
    c = db.verbindung()
    s = c.execute('SELECT * FROM anmeldeschritte WHERE token_hash = ? AND gueltig_bis > ?',
                  (h(t), jetzt())).fetchone() if isinstance(t, str) and 20 <= len(t) <= 100 else None
    if not s:
        fehler(400, 'Die Anmeldung ist abgelaufen – bitte noch einmal mit Passwort anmelden.')
    grenze.pruefen(f'code:{s["benutzer_id"]}', 10, 600)
    c.execute('UPDATE anmeldeschritte SET versuche = versuche + 1 WHERE token_hash = ?', (s['token_hash'],))
    b = benutzer_neu(s['benutzer_id'])
    if not b or not b['totp_an'] or not b['bestaetigt'] or _gesperrt(b):
        c.execute('DELETE FROM anmeldeschritte WHERE token_hash = ?', (s['token_hash'],))
        fehler(400, 'Die Anmeldung ist abgelaufen – bitte noch einmal mit Passwort anmelden.')
    art = zweiter_faktor_ok(b, d.get('code'))
    if not art:
        db.protokollieren('Anmeldung: falscher Code', b['id'])
        if s['versuche'] + 1 >= MAX_VERSUCHE:
            c.execute('DELETE FROM anmeldeschritte WHERE token_hash = ?', (s['token_hash'],))
            fehler(400, 'Zu viele falsche Codes – bitte noch einmal mit Passwort anmelden.')
        fehler(400, 'Der Code stimmt nicht.')
    c.execute('DELETE FROM anmeldeschritte WHERE token_hash = ?', (s['token_hash'],))
    c.execute('UPDATE benutzer SET letzte_anmeldung = ? WHERE id = ?', (jetzt(), b['id']))
    db.protokollieren('angemeldet (mit 2FA)' if art == 'totp' else 'angemeldet (mit Wiederherstellungscode)', b['id'])
    resp = JSONResponse({'ok': True, 'ich': ich_json(benutzer_neu(b['id'])), 'codes_uebrig': codes_uebrig(b['id'])})
    sitzung_setzen(resp, b['id'])
    return resp


def _gesperrt(b):
    return 'gesperrt' in b.keys() and bool(b['gesperrt'])


# ---------------------------------------------------------------- Verwalten (angemeldet)
@router.get('/api/konto/2fa')
def zustand(req: Request):
    b = benutzer_aus_sitzung(req)
    return {'an': bool(b['totp_an']), 'codes_uebrig': codes_uebrig(b['id']) if b['totp_an'] else 0}


@router.post('/api/konto/2fa/einrichten')
async def einrichten(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'2fa:{b["id"]}', 10, 900)
    if b['totp_an']:
        fehler(409, 'Die Zwei-Faktor-Anmeldung ist schon eingeschaltet.')
    await passwort_pruefen(b, d)
    geheim = b32_neu()
    db.verbindung().execute('UPDATE benutzer SET totp_geheim = ?, totp_schritt = 0 WHERE id = ?', (verschluesseln(geheim), b['id']))
    return {'geheim': geheim, 'uri': uri(geheim, b['email'])}


@router.post('/api/konto/2fa/bestaetigen')
async def bestaetigen(req: Request):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'2fa:{b["id"]}', 10, 900)
    if b['totp_an']:
        fehler(409, 'Die Zwei-Faktor-Anmeldung ist schon eingeschaltet.')
    geheim = entschluesseln(b['totp_geheim']) if b['totp_geheim'] else None
    if not geheim:
        fehler(400, 'Bitte die Einrichtung neu beginnen.')
    s = code_pruefen(geheim, normalisieren(d.get('code')), b['totp_schritt'])
    if s is None:
        fehler(400, 'Der Code stimmt nicht. Stimmt die Uhrzeit auf dem Handy?')
    db.verbindung().execute('UPDATE benutzer SET totp_an = 1, totp_schritt = ? WHERE id = ?', (s, b['id']))
    db.protokollieren('2FA eingeschaltet', b['id'])
    return {'ok': True, 'wiederherstellungscodes': codes_neu(b['id'])}


async def _passwort_und_code(req):
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'2fa:{b["id"]}', 10, 900)
    if not b['totp_an']:
        fehler(409, 'Die Zwei-Faktor-Anmeldung ist nicht eingeschaltet.')
    await passwort_pruefen(b, d)
    if not zweiter_faktor_ok(b, d.get('code')):
        db.protokollieren('2FA: falscher Code', b['id'])
        fehler(400, 'Der Code stimmt nicht.')
    return b


@router.post('/api/konto/2fa/ausschalten')
async def ausschalten(req: Request):
    b = await _passwort_und_code(req)
    c = db.verbindung()
    c.execute('UPDATE benutzer SET totp_an = 0, totp_geheim = NULL, totp_schritt = 0 WHERE id = ?', (b['id'],))
    c.execute('DELETE FROM wiederherstellung WHERE benutzer_id = ?', (b['id'],))
    db.protokollieren('2FA ausgeschaltet', b['id'])
    return {'ok': True}


@router.post('/api/konto/2fa/codes')
async def codes_erneuern(req: Request):
    b = await _passwort_und_code(req)
    db.protokollieren('2FA: neue Wiederherstellungscodes', b['id'])
    return {'ok': True, 'wiederherstellungscodes': codes_neu(b['id'])}
