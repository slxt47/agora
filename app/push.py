"""Push-Benachrichtigungen (Web Push) – opt-in je Gerät.

- Schlüssel (VAPID, RFC 8292): `AGORA_VAPID_PRIVAT` (32 Byte, Base64url) oder beim ersten Bedarf erzeugt und in
  `DATEN/push-vapid.json` (0600) abgelegt. Ändert er sich, müssen alle Geräte Push neu einschalten.
- Ein Gerät schaltet im Konto ein: Der Browser fragt nach Erlaubnis, abonniert beim Push-Dienst seines Herstellers
  (Google FCM, Mozilla, Apple, Microsoft) und schickt Adresse + Schlüssel hierher. Gespeichert je Konto; nur Adressen bei
  bekannten Push-Diensten (`AGORA_PUSH_DIENSTE`), verbunden wird wie bei Webhooks nur mit öffentlichen IPs.
- Inhalt ist Ende-zu-Ende verschlüsselt (RFC 8291, app/webpush.py): Der Push-Dienst sieht nur Größe und Zeitpunkt.
- Ereignisse (je Konto abwählbar): `erinnerung` (Termine eigener geplanter Meetings, Zeitpunkt wie die Erinnerungs-Mail,
  ohne Mail-Einstellung 10 min vorher), `warteraum` (jemand wartet, während weder Host noch Co-Host im Meeting ist),
  `einladung` (ein anderes Konto lädt die eigene Adresse ein), `fertig` (Server-Aufnahme, Transkript, Zusammenfassung).
- Zustellung im Hintergrund (2 Threads), ohne Wiederholung. 404/410 vom Dienst → Gerät gelöscht; nach 10 Fehlschlägen
  in Folge ebenfalls.
"""
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from fastapi import APIRouter, Request

from . import db, egress, erinnerungen, konto, metriken, raeume, texte, untertitel, webhooks, webpush
from . import livekit_dienst as lk
from .kern import SEITE, TEST, benutzer_aus_sitzung, fehler, grenze, iso, jetzt, json_body, text_feld

router = APIRouter()
EREIGNISSE = ('erinnerung', 'warteraum', 'einladung', 'fertig')
MAX_GERAETE = 10
FEHL_GRENZE = 10
TIMEOUT_S = 10
STANDARD_MIN = 10              # Erinnerung, wenn im Konto keine Mail-Erinnerung eingestellt ist
SUBJEKT = os.environ.get('AGORA_VAPID_SUBJEKT', '').strip() or SEITE
DIENSTE = tuple(x.strip().lower().lstrip('.') for x in os.environ.get(
    'AGORA_PUSH_DIENSTE', 'fcm.googleapis.com,android.googleapis.com,push.services.mozilla.com,push.apple.com,notify.windows.com'
).split(',') if x.strip())
_pool = ThreadPoolExecutor(2, thread_name_prefix='push')
_offen = []                    # nur im Testmodus: laufende Zustellungen (warten())
_lock = threading.Lock()
_vapid = None
metriken.HILFE['agora_push_total'] = ('counter', 'Push-Zustellungen nach Ergebnis (ok, weg, fehler)')


# ---------------------------------------------------------------- Schlüssel
def vapid():
    """(privater Schlüssel, öffentlicher Schlüssel als Base64url)."""
    global _vapid
    with _lock:
        if _vapid:
            return _vapid
        roh = os.environ.get('AGORA_VAPID_PRIVAT', '').strip()
        if roh:
            privat = webpush.privat_aus_roh(webpush.unb64(roh))
        else:
            datei = os.path.join(db.DATEN, 'push-vapid.json')
            try:
                with open(datei, encoding='utf-8') as f:
                    privat = webpush.privat_aus_roh(webpush.unb64(json.load(f)['privat']))
            except FileNotFoundError:
                privat = webpush.neuer_schluessel()
                fd = os.open(datei, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump({'privat': webpush.b64(webpush.privat_roh(privat)),
                               'oeffentlich': webpush.b64(webpush.oeffentlich_roh(privat))}, f)
        _vapid = (privat, webpush.b64(webpush.oeffentlich_roh(privat)))
        return _vapid


# ---------------------------------------------------------------- Einstellungen
def ereignisse(benutzer_id):
    e = db.verbindung().execute('SELECT ereignisse FROM push_einstellung WHERE benutzer_id = ?', (benutzer_id,)).fetchone()
    return [x for x in json.loads(e['ereignisse']) if x in EREIGNISSE] if e else list(EREIGNISSE)


def kennung(endpunkt):
    """Kurzer Hash der Adresse: Der Browser erkennt daran „dieses Gerät“, ohne dass die Adresse zurückkommt."""
    return hashlib.sha256(endpunkt.encode()).hexdigest()[:16]


def geraet_json(a):
    return {'id': a['id'], 'geraet': a['geraet'], 'kennung': kennung(a['endpunkt']), 'erstellt': iso(a['erstellt']),
            'benutzt': iso(a['benutzt'])}


def dienst_erlaubt(host):
    return any(host == d or host.endswith('.' + d) for d in DIENSTE)


def endpunkt_pruefen(url):
    if not isinstance(url, str) or not 12 <= len(url) <= 1000:
        fehler(400, 'Ungültige Push-Adresse.')
    try:
        u = urlsplit(url)
        _ = u.port
    except ValueError:
        fehler(400, 'Ungültige Push-Adresse.')
    lokal = webhooks.LOKAL_ERLAUBT
    if (u.scheme != 'https' and not (lokal and u.scheme == 'http')) or not u.hostname or u.username or u.password or u.fragment:
        fehler(400, 'Ungültige Push-Adresse.')
    if not lokal and not dienst_erlaubt(u.hostname.lower()):
        fehler(400, 'Dieser Push-Dienst ist nicht freigegeben.')
    return url


# ---------------------------------------------------------------- Zustellen
def _zustellen(abo, nutzlast, ttl, dringend, thema):
    """Ein Versuch an ein Gerät → HTTP-Status oder None."""
    status = None
    try:
        privat, _ = vapid()
        body = webpush.verschluesseln(nutzlast, abo['p256dh'], abo['auth'])
        u, adressen = webhooks.ziel_pruefen(abo['endpunkt'])
        kopf = {'TTL': str(ttl), 'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream',
                'Urgency': 'high' if dringend else 'normal', 'Authorization': webpush.vapid_kopf(abo['endpunkt'], privat, SUBJEKT)}
        if thema:
            kopf['Topic'] = thema
        art = webhooks._Https if u.scheme == 'https' else webhooks._Http     # noqa: SLF001 – gleiche Verbindungsregeln
        extra = {'context': ssl.create_default_context()} if u.scheme == 'https' else {}
        conn = art(u.hostname, adressen[0], u.port or (443 if u.scheme == 'https' else 80), timeout=TIMEOUT_S, **extra)
        try:
            conn.request('POST', (u.path or '/') + (f'?{u.query}' if u.query else ''), body=body, headers=kopf)
            antwort = conn.getresponse()
            antwort.read(4096)
            status = antwort.status
        finally:
            conn.close()
    except (webhooks.ZielFehler, OSError, ssl.SSLError, socket.timeout, http.client.HTTPException, ValueError) as e:
        print(f'push-fehler {type(e).__name__}', flush=True)
    c = db.verbindung()
    if status in (404, 410):            # Abo beim Dienst abgelaufen oder abgemeldet
        c.execute('DELETE FROM push_abos WHERE id = ?', (abo['id'],))
        metriken.zaehlen('agora_push_total', ergebnis='weg')
    elif status is not None and 200 <= status < 300:
        c.execute('UPDATE push_abos SET benutzt = ?, fehler_folge = 0 WHERE id = ?', (jetzt(), abo['id']))
        metriken.zaehlen('agora_push_total', ergebnis='ok')
    else:
        c.execute('UPDATE push_abos SET fehler_folge = fehler_folge + 1 WHERE id = ?', (abo['id'],))
        c.execute('DELETE FROM push_abos WHERE id = ? AND fehler_folge >= ?', (abo['id'], FEHL_GRENZE))
        metriken.zaehlen('agora_push_total', ergebnis='fehler')
    return status


def an_konto(benutzer_id, ereignis, titel, text, url='/', tag='', dringend=False, ttl=86400):
    """Benachrichtigung an alle Geräte des Kontos (wenn das Ereignis gewählt ist) → Anzahl Geräte."""
    if ereignis != 'test' and ereignis not in ereignisse(benutzer_id):
        return 0
    abos = db.verbindung().execute('SELECT * FROM push_abos WHERE benutzer_id = ?', (benutzer_id,)).fetchall()
    if not abos:
        return 0
    nutzlast = json.dumps({'titel': titel[:120], 'text': text[:300], 'url': url, 'tag': tag[:64]}, ensure_ascii=False).encode()
    thema = re.sub(r'[^A-Za-z0-9_-]', '', tag)[:32]
    for a in abos:
        f = _pool.submit(_zustellen, dict(a), nutzlast, ttl, dringend, thema)
        if TEST:
            _offen.append(f)
    return len(abos)


def warten():
    """Nur Tests: alle laufenden Zustellungen abwarten → Liste der Status."""
    erg = [f.result(timeout=30) for f in list(_offen)]
    _offen.clear()
    return erg


def _sprache(benutzer_id):
    b = db.verbindung().execute('SELECT sprache FROM benutzer WHERE id = ?', (benutzer_id,)).fetchone()
    return texte.gueltig(b['sprache'] if b else 'de')


def senden(benutzer_id, ereignis, schluessel, url='/', tag='', dringend=False, ttl=86400, **werte):
    """Texte push.<schluessel>.titel/.text in der Sprache des Kontos."""
    s = _sprache(benutzer_id)
    return an_konto(benutzer_id, ereignis, texte.tx(s, f'push.{schluessel}.titel', **werte), texte.tx(s, f'push.{schluessel}.text', **werte),
                    url, tag, dringend, ttl)


def hat_abos(benutzer_id):
    return bool(db.verbindung().execute('SELECT 1 FROM push_abos WHERE benutzer_id = ? LIMIT 1', (benutzer_id,)).fetchone())


# ---------------------------------------------------------------- Ereignisse
async def _neue_anfrage(r, name):
    """Warteraum: nur wenn weder Host noch Co-Host verbunden ist; höchstens alle 2 min je Raum."""
    if not hat_abos(r['besitzer_id']) or 'warteraum' not in ereignisse(r['besitzer_id']):
        return
    mods = {t['identity'] for t in raeume.teilnahmen(r, ('host', 'cohost'))}
    liste = await lk.teilnehmer(r['code'])
    if liste is not None and any(p['identity'] in mods for p in liste):
        return
    if not grenze.ok(f'push-warteraum:{r["id"]}', 1, 120):
        return
    senden(r['besitzer_id'], 'warteraum', 'warteraum', url=f'/r/{r["code"]}', tag=f'warteraum-{r["code"]}', dringend=True, ttl=1800,
           titel=r['titel'], name=name)


def _einladung(r, b, adressen):
    c = db.verbindung()
    for a in adressen:
        z = c.execute('SELECT id FROM benutzer WHERE email = ? AND bestaetigt = 1 AND gesperrt = 0', (a,)).fetchone()
        if z and z['id'] != b['id']:
            senden(z['id'], 'einladung', 'einladung', url=f'/r/{r["code"]}', tag=f'einladung-{r["code"]}', titel=r['titel'], name=b['name'])


def _server_aufnahme(a):
    senden(a['benutzer_id'], 'fertig', 'aufnahme', url='/konto#aufnahmen', tag=f'aufnahme-{a["id"][:12]}', titel=a['titel'])


def _transkript(a):
    senden(a['benutzer_id'], 'fertig', 'transkript', url='/konto#aufnahmen', tag=f'transkript-{a["id"][:12]}', titel=a['titel'])


raeume.NEUE_ANFRAGE.append(_neue_anfrage)
raeume.NACH_EINLADUNG.append(_einladung)


def erinnerungen_pruefen(t=None):
    """Hintergrund (jede Minute): Termine eigener geplanter Meetings – gleiches Fenster wie die Erinnerungs-Mail."""
    t = t or jetzt()
    c = db.verbindung()
    n_ges = 0
    for b in c.execute('SELECT DISTINCT b.* FROM push_abos a JOIN benutzer b ON b.id = a.benutzer_id '
                       'WHERE b.bestaetigt = 1 AND b.gesperrt = 0').fetchall():
        if 'erinnerung' not in ereignisse(b['id']):
            continue
        minuten = erinnerungen.konto_minuten(b) or STANDARD_MIN
        for r in c.execute("SELECT * FROM raeume WHERE besitzer_id = ? AND art = 'geplant'", (b['id'],)).fetchall():
            n = raeume.termin(r, t)
            if n is None or not erinnerungen.faellig(n, minuten, r['erstellt'], t):
                continue
            s = texte.gueltig(b['sprache'])
            if erinnerungen._einmal(f'push:{r["id"]}:{n}:{b["id"]}', t, lambda b=b, r=r, n=n, s=s, m=minuten: senden(   # noqa: SLF001
                    b['id'], 'erinnerung', 'erinnerung', url=f'/r/{r["code"]}', tag=f'erinnerung-{r["code"]}', ttl=m * 60,
                    titel=r['titel'], wann=erinnerungen.wann_text(n, b['zeitzone'], s), n=m)) == 'gesendet':
                n_ges += 1
    return n_ges


# ---------------------------------------------------------------- API (Konto)
@router.get('/api/push')
def lesen(req: Request):
    b = benutzer_aus_sitzung(req)
    rows = db.verbindung().execute('SELECT * FROM push_abos WHERE benutzer_id = ? ORDER BY id', (b['id'],)).fetchall()
    return {'schluessel': vapid()[1], 'ereignisse': ereignisse(b['id']), 'alle_ereignisse': list(EREIGNISSE),
            'geraete': [geraet_json(a) for a in rows]}


@router.post('/api/push/abo')
async def abonnieren(req: Request):
    """Body wie PushSubscription.toJSON(): {endpoint, keys:{p256dh, auth}}, dazu geraet (Bezeichnung) und optional alt
    (alte Adresse nach pushsubscriptionchange)."""
    b = benutzer_aus_sitzung(req)
    d = await json_body(req)
    grenze.pruefen(f'push-abo:{b["id"]}', 30, 3600)
    endpunkt = endpunkt_pruefen(d.get('endpoint'))
    keys = d.get('keys') if isinstance(d.get('keys'), dict) else {}
    try:
        webpush.ua_schluessel_pruefen(keys.get('p256dh'), keys.get('auth'))
    except (ValueError, TypeError):
        fehler(400, 'Ungültige Push-Schlüssel.')
    geraet = text_feld(d, 'geraet', 60).replace('\n', ' ')
    c = db.verbindung()
    if isinstance(d.get('alt'), str) and d['alt'] != endpunkt:
        alt = c.execute('SELECT geraet FROM push_abos WHERE endpunkt = ? AND benutzer_id = ?', (d['alt'], b['id'])).fetchone()
        geraet = geraet or (alt['geraet'] if alt else '')          # Bezeichnung des erneuerten Abos behalten
        c.execute('DELETE FROM push_abos WHERE endpunkt = ? AND benutzer_id = ?', (d['alt'], b['id']))
    c.execute('DELETE FROM push_abos WHERE endpunkt = ?', (endpunkt,))     # gleiches Gerät (ggf. vorher anderes Konto)
    if c.execute('SELECT COUNT(*) FROM push_abos WHERE benutzer_id = ?', (b['id'],)).fetchone()[0] >= MAX_GERAETE:
        fehler(400, 'Höchstens 10 Geräte je Konto – entferne zuerst ein altes.')
    i = c.execute('INSERT INTO push_abos (benutzer_id, endpunkt, p256dh, auth, geraet, erstellt) VALUES (?, ?, ?, ?, ?, ?)',
                  (b['id'], endpunkt, keys['p256dh'], keys['auth'], geraet, jetzt())).lastrowid
    db.protokollieren('Push eingeschaltet (Gerät)', b['id'])
    return {'geraet': geraet_json(c.execute('SELECT * FROM push_abos WHERE id = ?', (i,)).fetchone())}


@router.delete('/api/push/abo/{aid}')
def abmelden(aid: int, req: Request):
    b = benutzer_aus_sitzung(req)
    if not db.verbindung().execute('DELETE FROM push_abos WHERE id = ? AND benutzer_id = ?', (aid, b['id'])).rowcount:
        fehler(404, 'Gerät nicht gefunden')
    db.protokollieren('Push ausgeschaltet (Gerät)', b['id'])
    return {'ok': True}


@router.put('/api/push/ereignisse')
async def ereignisse_setzen(req: Request):
    b = benutzer_aus_sitzung(req)
    e = (await json_body(req)).get('ereignisse')
    if not isinstance(e, list) or any(x not in EREIGNISSE for x in e):
        fehler(400, 'Ereignisse: erinnerung, warteraum, einladung, fertig.')
    liste = [x for x in EREIGNISSE if x in e]
    db.verbindung().execute('INSERT OR REPLACE INTO push_einstellung VALUES (?, ?)', (b['id'], json.dumps(liste)))
    return {'ereignisse': liste}


@router.post('/api/push/test')
def testen(req: Request):
    b = benutzer_aus_sitzung(req)
    grenze.pruefen(f'push-test:{b["id"]}', 10, 3600)
    n = senden(b['id'], 'test', 'test', url='/konto#profil', tag='test')
    if not n:
        fehler(400, 'Auf keinem Gerät eingeschaltet.')
    return {'geraete': n}


konto.EXPORT.append(lambda b: {'push': {'ereignisse': ereignisse(b['id']), 'geraete': [
    {k: v for k, v in geraet_json(a).items() if k != 'kennung'}
    for a in db.verbindung().execute('SELECT * FROM push_abos WHERE benutzer_id = ?', (b['id'],)).fetchall()]}})
egress.NACH_SERVER_AUFNAHME.append(_server_aufnahme)
untertitel.NACH_TRANSKRIPT.append(_transkript)
