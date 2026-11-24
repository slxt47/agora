"""Webhooks: Agora ruft bei Ereignissen eigene HTTPS-Adressen des Kontos auf (siehe docs/API-V1.md#webhooks).

- Je Konto höchstens 5 Endpunkte; nur https://, Ziel muss eine öffentliche IP haben (alle Adressen aus DNS werden
  geprüft, verbunden wird genau mit der geprüften Adresse – kein zweites Auflösen, kein DNS-Rebinding). Weiterleitungen
  werden nie verfolgt (3xx = Fehlschlag).
- Ereignisse: meeting.gestartet, meeting.beendet, teilnehmer.beigetreten, teilnehmer.gegangen, aufnahme.fertig,
  webinar.anmeldung (nur für Räume/Aufnahmen des Kontos). Nutzlast `{id, ereignis, erstellt, daten}`.
- Signatur `X-Agora-Signatur: t=<unix>,v1=<hex HMAC-SHA256(geheimnis, "<t>.<body>")>`. Das Geheimnis (`whsec_…`) wird
  einmal gezeigt und verschlüsselt gespeichert (Schlüssel aus AGORA_GEHEIM) – zum Signieren braucht es den Klartext.
- Zustellung im Hintergrund (eigener Thread, 4 parallel), bis zu 5 Versuche mit wachsendem Abstand (sofort, 1 min,
  5 min, 30 min, 2 h). Nach 3 endgültig fehlgeschlagenen Zustellungen in Folge wird der Endpunkt abgeschaltet und der
  Besitzer per Mail informiert. Protokoll je Endpunkt: die letzten 50 Versuche, höchstens 14 Tage.
- „teilnehmer.beigetreten“ entsteht, wenn der Server den Zugang ausgibt; „teilnehmer.gegangen“, wenn die Person laut
  LiveKit nicht mehr verbunden ist (minütliche Prüfung) oder das Meeting endet. Dafür merkt sich die Tabelle
  webhook_anwesend identity und Namen – nur bei Konten mit passendem Webhook und nur bis zum Ende des Meetings.
"""
import hashlib
import hmac
import http.client
import json
import os
import secrets
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from . import aufnahmen, db, konto, mail, raeume, webinar
from .api_schluessel import pruefen
from .kern import SEITE, TEST, benutzer_aus_sitzung, fehler, grenze, iso, jetzt, json_body
from .netz import oeffentlich
from .zwei_faktor import entschluesseln, verschluesseln

router = APIRouter()
EREIGNISSE = ('meeting.gestartet', 'meeting.beendet', 'teilnehmer.beigetreten', 'teilnehmer.gegangen', 'aufnahme.fertig',
              'webinar.anmeldung')
MAX_JE_KONTO = 5
VERSUCHE = 5
ABSTAND_S = (60, 300, 1800, 7200)          # Wartezeit nach dem 1., 2., 3., 4. Fehlschlag
FEHL_GRENZE = 3                            # endgültig fehlgeschlagene Zustellungen in Folge → abschalten
LOG_MAX = 50
LOG_TAGE = 14
TIMEOUT_S = 10
PARALLEL = 4
# Nur für Tests: http:// und Adressen im eigenen Netz erlauben (lokaler Fake-Empfänger). Ohne AGORA_TEST wirkungslos.
LOKAL_ERLAUBT = TEST and os.environ.get('AGORA_WEBHOOK_LOKAL') == '1'


class ZielFehler(Exception):
    pass


# ---------------------------------------------------------------- Ziel prüfen + senden (ohne Weiterleitungen)
def ziel_pruefen(url):
    """→ (teile, [IP-Adressen]). Wirft ZielFehler mit deutschem Text."""
    if not isinstance(url, str) or len(url) > 500:
        raise ZielFehler('Die Adresse ist zu lang (höchstens 500 Zeichen).')
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError:
        raise ZielFehler('Ungültige Adresse.') from None
    if u.scheme != 'https' and not (LOKAL_ERLAUBT and u.scheme == 'http'):
        raise ZielFehler('Die Adresse muss mit https:// beginnen.')
    if not u.hostname or u.username or u.password or u.fragment:
        raise ZielFehler('Ungültige Adresse (ohne Benutzername, Passwort und #Fragment).')
    try:
        adressen = list(dict.fromkeys(a[4][0] for a in socket.getaddrinfo(
            u.hostname, port or (443 if u.scheme == 'https' else 80), proto=socket.IPPROTO_TCP)))
    except (socket.gaierror, UnicodeError, OSError):
        raise ZielFehler('Der Name lässt sich nicht auflösen.') from None
    if not adressen:
        raise ZielFehler('Der Name lässt sich nicht auflösen.')
    for a in adressen:
        if not oeffentlich(a) and not LOKAL_ERLAUBT:
            raise ZielFehler('Adressen im eigenen Netz (localhost, private IPs) sind nicht erlaubt.')
    return u, adressen


class _Https(http.client.HTTPSConnection):
    """HTTPS zu genau der geprüften IP; Zertifikat und SNI für den Hostnamen."""
    def __init__(self, host, ip, port, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        roh = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(roh, server_hostname=self.host)


class _Http(http.client.HTTPConnection):     # nur mit LOKAL_ERLAUBT (Tests)
    def __init__(self, host, ip, port, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def signatur(geheim, t, body):
    return hmac.new(geheim.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest()


def senden(url, geheim, ereignis, zustellung, body):
    """Ein Versuch → (http_status | None, fehlertext | None, dauer_ms). Erfolg = 2xx."""
    start = time.monotonic()
    try:
        u, adressen = ziel_pruefen(url)
        t = int(time.time())
        kopf = {'Content-Type': 'application/json', 'User-Agent': 'Agora-Webhooks/1', 'X-Agora-Ereignis': ereignis,
                'X-Agora-Zustellung': zustellung, 'X-Agora-Signatur': f't={t},v1={signatur(geheim, t, body)}'}
        art = _Https if u.scheme == 'https' else _Http
        extra = {'context': ssl.create_default_context()} if u.scheme == 'https' else {}
        conn = art(u.hostname, adressen[0], u.port or (443 if u.scheme == 'https' else 80), timeout=TIMEOUT_S, **extra)
        try:
            conn.request('POST', (u.path or '/') + (f'?{u.query}' if u.query else ''), body=body, headers=kopf)
            antwort = conn.getresponse()
            antwort.read(4096)            # Antwort wird nicht gespeichert
            status = antwort.status
        finally:
            conn.close()
    except ZielFehler as e:
        return None, str(e), int((time.monotonic() - start) * 1000)
    except ssl.SSLError:
        return None, 'TLS-Fehler (Zertifikat?)', int((time.monotonic() - start) * 1000)
    except (TimeoutError, socket.timeout):
        return None, f'Keine Antwort nach {TIMEOUT_S} s', int((time.monotonic() - start) * 1000)
    except (OSError, http.client.HTTPException) as e:
        return None, f'Verbindung fehlgeschlagen ({type(e).__name__})', int((time.monotonic() - start) * 1000)
    ms = int((time.monotonic() - start) * 1000)
    if 300 <= status < 400:
        return status, 'Weiterleitung – wird nicht verfolgt', ms
    return status, None if 200 <= status < 300 else f'HTTP {status}', ms


# ---------------------------------------------------------------- Ereignisse auslösen
def _abonnenten(benutzer_id, ereignis):
    rows = db.verbindung().execute('SELECT * FROM webhooks WHERE benutzer_id = ? AND aktiv = 1', (benutzer_id,)).fetchall()
    return [w for w in rows if ereignis in json.loads(w['ereignisse'])]


def ausloesen(benutzer_id, ereignis, daten):
    """Ereignis für alle passenden Endpunkte des Kontos in die Warteschlange legen (Zustellung im Hintergrund)."""
    ziele = _abonnenten(benutzer_id, ereignis) if benutzer_id else []
    if not ziele:
        return None
    t = jetzt()
    eid = 'evt_' + secrets.token_urlsafe(12)
    nutzlast = json.dumps({'id': eid, 'ereignis': ereignis, 'erstellt': iso(t), 'daten': daten}, ensure_ascii=False)
    c = db.verbindung()
    for w in ziele:
        c.execute('INSERT INTO webhook_zustellungen (id, webhook_id, ereignis, nutzlast, versuch, naechster, erstellt) '
                  'VALUES (?, ?, ?, ?, 0, ?, ?)', ('zst_' + secrets.token_urlsafe(12), w['id'], ereignis, nutzlast, t, t))
    _wecken.set()
    return eid


def _meeting(r):
    return {'code': r['code'], 'titel': r['titel'], 'link': raeume.link(r['code'])}


def _gestartet(r):
    ausloesen(r['besitzer_id'], 'meeting.gestartet', {'meeting': _meeting(r), 'sitzung': r['sitzung'], 'gestartet': iso(r['gestartet'])})


def _beendet(r):
    ausloesen(r['besitzer_id'], 'meeting.beendet', {'meeting': _meeting(r), 'sitzung': r['sitzung'],
                                                    'gestartet': iso(r['gestartet']), 'beendet': iso(jetzt())})


def _teilnehmer_daten(r, identity, name, rolle, grund=None):
    d = {'meeting': _meeting(r), 'sitzung': r['sitzung'], 'teilnehmer': {'identity': identity, 'name': name, 'rolle': rolle}}
    return d | ({'grund': grund} if grund else {})


def _nach_zugang(r, identity, name, rolle):
    if not (_abonnenten(r['besitzer_id'], 'teilnehmer.beigetreten') or _abonnenten(r['besitzer_id'], 'teilnehmer.gegangen')):
        return
    db.verbindung().execute('INSERT OR REPLACE INTO webhook_anwesend VALUES (?, ?, ?, ?, ?, 0, ?)',
                            (r['id'], r['sitzung'], identity, name, rolle, jetzt()))
    ausloesen(r['besitzer_id'], 'teilnehmer.beigetreten', _teilnehmer_daten(r, identity, name, rolle))


def _gegangen(r, a, grund):
    db.verbindung().execute('DELETE FROM webhook_anwesend WHERE raum_id = ? AND identity = ?', (r['id'], a['identity']))
    ausloesen(r['besitzer_id'], 'teilnehmer.gegangen', _teilnehmer_daten(r, a['identity'], a['name'], a['rolle'], grund))


def _bei_liste(r, liste):
    """Minütlich: wer laut LiveKit nicht mehr da ist (oder sich 5 min nach dem Zugang nie verbunden hat) → gegangen."""
    c = db.verbindung()
    da = {p['identity'] for p in liste}
    for a in c.execute('SELECT * FROM webhook_anwesend WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung'])).fetchall():
        if a['identity'] in da:
            if not a['verbunden']:
                c.execute('UPDATE webhook_anwesend SET verbunden = 1 WHERE raum_id = ? AND identity = ?', (r['id'], a['identity']))
        elif a['verbunden'] or a['zeit'] < jetzt() - 300:
            _gegangen(r, a, 'verlassen')


def _vor_ende(r):
    c = db.verbindung()
    for a in c.execute('SELECT * FROM webhook_anwesend WHERE raum_id = ?', (r['id'],)).fetchall():
        _gegangen(r, a, 'meeting_beendet')


def _aufnahme(a):
    ausloesen(a['benutzer_id'], 'aufnahme.fertig', {'aufnahme': {'id': a['id'], 'titel': a['titel'], 'raum_code': a['raum_code'],
                                                                 'groesse': a['groesse'], 'typ': a['typ'], 'dauer': a['dauer'],
                                                                 'erstellt': iso(a['erstellt']), 'ablauf': iso(a['ablauf'])}})


def _anmeldung(r, name, email, antworten):
    ausloesen(r['besitzer_id'], 'webinar.anmeldung', {'meeting': _meeting(r), 'anmeldung': {'name': name, 'email': email,
                                                                                         'antworten': antworten, 'zeit': iso(jetzt())}})


raeume.BEIM_START.append(_gestartet)
raeume.VOR_ENDE.append(_vor_ende)
raeume.BEIM_ENDE.append(_beendet)
raeume.NACH_ZUGANG.append(_nach_zugang)
raeume.BEI_LISTE.append(_bei_liste)
aufnahmen.NACH_NEU.append(_aufnahme)
webinar.NACH_ANMELDUNG.append(_anmeldung)


# ---------------------------------------------------------------- Zustellung
def _log(w_id, zustellung, ereignis, versuch, status, fehlertext, ms, t=None):
    c = db.verbindung()
    c.execute('INSERT INTO webhook_log (webhook_id, zustellung, ereignis, zeit, versuch, status, ok, dauer_ms, fehler) '
              'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (w_id, zustellung, ereignis, t or jetzt(), versuch, status, int(fehlertext is None), ms,
                                                    (fehlertext or '')[:200] or None))
    c.execute('DELETE FROM webhook_log WHERE webhook_id = ? AND id NOT IN (SELECT id FROM webhook_log WHERE webhook_id = ? '
              'ORDER BY id DESC LIMIT ?)', (w_id, w_id, LOG_MAX))


def zustellen(z):
    """Einen fälligen Versuch ausführen und Ergebnis verbuchen."""
    c = db.verbindung()
    w = c.execute('SELECT * FROM webhooks WHERE id = ?', (z['webhook_id'],)).fetchone()
    if not w or not w['aktiv']:
        c.execute('DELETE FROM webhook_zustellungen WHERE id = ?', (z['id'],))
        return
    geheim = entschluesseln(w['geheim']) or ''
    versuch = z['versuch'] + 1
    status, text, ms = senden(w['url'], geheim, z['ereignis'], z['id'], z['nutzlast'].encode())
    _log(w['id'], z['id'], z['ereignis'], versuch, status, text, ms)
    if text is None:
        c.execute('DELETE FROM webhook_zustellungen WHERE id = ?', (z['id'],))
        c.execute('UPDATE webhooks SET fehler_folge = 0, letzter_erfolg = ? WHERE id = ?', (jetzt(), w['id']))
    elif versuch < VERSUCHE:
        c.execute('UPDATE webhook_zustellungen SET versuch = ?, naechster = ? WHERE id = ?', (versuch, jetzt() + ABSTAND_S[versuch - 1], z['id']))
    else:
        c.execute('DELETE FROM webhook_zustellungen WHERE id = ?', (z['id'],))
        c.execute('UPDATE webhooks SET fehler_folge = fehler_folge + 1 WHERE id = ?', (w['id'],))
        if w['fehler_folge'] + 1 >= FEHL_GRENZE:
            abschalten(w, text)


def abschalten(w, grund):
    c = db.verbindung()
    c.execute('UPDATE webhooks SET aktiv = 0, deaktiviert_grund = ? WHERE id = ?', (f'Zu viele Fehlschläge: {grund}'[:200], w['id']))
    c.execute('DELETE FROM webhook_zustellungen WHERE webhook_id = ?', (w['id'],))
    db.protokollieren(f'Webhook abgeschaltet (#{w["id"]})', w['benutzer_id'])
    b = c.execute('SELECT * FROM benutzer WHERE id = ?', (w['benutzer_id'],)).fetchone()
    if b and grenze.ok('mail:' + b['email'].lower(), 3, 3600):
        mail.im_hintergrund(abgeschaltet_mail, b['email'], b['name'], w['url'], grund, b['sprache'])


_MAIL = {     # Mail an den Besitzer in seiner Konto-Sprache (ADR 0006)
    'de': {'betreff': 'Webhook abgeschaltet', 'hallo': 'Hallo {name},',
           'satz': 'dein Webhook an {url} hat {n} Ereignisse in Folge nicht angenommen (je {v} Versuche). '
                   'Wir haben ihn deshalb abgeschaltet – es gehen keine Ereignisse mehr dorthin.',
           'tun': 'Prüfe den Empfänger und schalte den Webhook im Konto unter „Entwickler“ wieder ein.',
           'fehler': 'Letzter Fehler', 'adresse': 'Adresse', 'knopf': 'Zu den Webhooks', 'vorschau': 'Ein Webhook wurde wegen Fehlern abgeschaltet'},
    'en': {'betreff': 'Webhook disabled', 'hallo': 'Hello {name},',
           'satz': 'your webhook to {url} did not accept {n} events in a row ({v} attempts each). '
                   'We have therefore disabled it – no more events are sent there.',
           'tun': 'Check the receiver and enable the webhook again in your account under “Developers”.',
           'fehler': 'Last error', 'adresse': 'Address', 'knopf': 'Go to webhooks', 'vorschau': 'A webhook was disabled because of errors'},
}


def abgeschaltet_mail(an, name, url, grund, sprache='de'):
    m = _MAIL.get(sprache, _MAIL['de'])
    saetze = [m['hallo'].format(name=name), m['satz'].format(url=url, n=FEHL_GRENZE, v=VERSUCHE), m['tun']]
    link = f'{SEITE}/konto#entwickler'
    text = '\n\n'.join(saetze) + f'\n\n{m["fehler"]}: {grund}\n\n{link}\n'
    mail.senden(an, f'{mail.MARKE}: {m["betreff"]}', text,
                mail.html_mail(m['betreff'], saetze, knopf=(m['knopf'], link), details=[(m['adresse'], url), (m['fehler'], grund)],
                               vorschau=m['vorschau'], sprache=sprache), sprache=sprache)


def faellige(t=None, maximal=50):
    """Fällige Zustellungen holen und als „in Arbeit“ markieren (naechster + 10 min, falls der Prozess abbricht)."""
    t = t or jetzt()
    c = db.verbindung()
    rows = c.execute('SELECT * FROM webhook_zustellungen WHERE naechster <= ? ORDER BY naechster LIMIT ?', (t, maximal)).fetchall()
    for z in rows:
        c.execute('UPDATE webhook_zustellungen SET naechster = ? WHERE id = ?', (t + 600, z['id']))
    return rows


def faellige_zustellen(t=None):
    """Alle fälligen nacheinander (Tests, und als Rückfall)."""
    for z in faellige(t):
        zustellen(z)


_wecken = threading.Event()


def starten():
    """Hintergrund-Thread: alle 5 s (oder sofort nach einem neuen Ereignis) fällige Zustellungen, 4 parallel."""
    pool = ThreadPoolExecutor(PARALLEL, thread_name_prefix='webhook')

    def lauf():
        while True:
            try:
                list(pool.map(zustellen, faellige()))
            except Exception as e:   # noqa: BLE001
                print(f'webhook-fehler {type(e).__name__}', flush=True)
            _wecken.wait(5)
            _wecken.clear()
    threading.Thread(target=lauf, daemon=True, name='webhooks').start()


def aufraeumen(t):
    c = db.verbindung()
    c.execute('DELETE FROM webhook_log WHERE zeit < ?', (t - LOG_TAGE * 86400,))
    c.execute('DELETE FROM webhook_zustellungen WHERE erstellt < ?', (t - 2 * 86400,))     # Sicherheitsnetz
    c.execute('DELETE FROM webhook_anwesend WHERE zeit < ? AND raum_id NOT IN (SELECT id FROM raeume WHERE aktiv = 1)', (t - 86400,))


# ---------------------------------------------------------------- Verwaltung (Konto + /api/v1)
def webhook_json(w):
    return {'id': w['id'], 'url': w['url'], 'ereignisse': json.loads(w['ereignisse']), 'aktiv': bool(w['aktiv']),
            'erstellt': iso(w['erstellt']), 'letzter_erfolg': iso(w['letzter_erfolg']), 'fehler_folge': w['fehler_folge'],
            'deaktiviert_grund': w['deaktiviert_grund']}


def log_json(x):
    return {'zustellung': x['zustellung'], 'ereignis': x['ereignis'], 'zeit': iso(x['zeit']), 'versuch': x['versuch'],
            'status': x['status'], 'ok': bool(x['ok']), 'dauer_ms': x['dauer_ms'], 'fehler': x['fehler']}


def _ereignisse(d, pflicht):
    e = d.get('ereignisse')
    if e is None and not pflicht:
        return None
    if not isinstance(e, list) or not e or any(x not in EREIGNISSE for x in e):
        fehler(400, 'Bitte mindestens ein gültiges Ereignis wählen: ' + ', '.join(EREIGNISSE) + '.')
    return [x for x in EREIGNISSE if x in e]


async def _url(d):
    try:
        u, _a = await run_in_threadpool(ziel_pruefen, d.get('url'))
    except ZielFehler as e:
        fehler(400, str(e))
    return u.geturl()


def _eigener(b, i):
    w = db.verbindung().execute('SELECT * FROM webhooks WHERE id = ? AND benutzer_id = ?', (i, b['id'])).fetchone()
    if not w:
        fehler(404, 'Webhook nicht gefunden')
    return w


def liste(b):
    rows = db.verbindung().execute('SELECT * FROM webhooks WHERE benutzer_id = ? ORDER BY id', (b['id'],)).fetchall()
    return {'webhooks': [webhook_json(w) for w in rows], 'ereignisse': list(EREIGNISSE), 'maximal': MAX_JE_KONTO}


async def anlegen(b, d):
    grenze.pruefen(f'webhook-neu:{b["id"]}', 20, 3600)
    url = await _url(d)
    ereignisse = _ereignisse(d, True)
    c = db.verbindung()
    if c.execute('SELECT COUNT(*) FROM webhooks WHERE benutzer_id = ?', (b['id'],)).fetchone()[0] >= MAX_JE_KONTO:
        fehler(409, f'Höchstens {MAX_JE_KONTO} Webhooks je Konto.')
    geheim = 'whsec_' + secrets.token_urlsafe(32)
    i = c.execute('INSERT INTO webhooks (benutzer_id, url, ereignisse, geheim, erstellt) VALUES (?, ?, ?, ?, ?)',
                  (b['id'], url, json.dumps(ereignisse), verschluesseln(geheim), jetzt())).lastrowid
    db.protokollieren(f'Webhook angelegt (#{i})', b['id'])
    return {'webhook': webhook_json(c.execute('SELECT * FROM webhooks WHERE id = ?', (i,)).fetchone()), 'geheimnis': geheim}


async def aendern(b, i, d):
    w = _eigener(b, i)
    c = db.verbindung()
    if 'url' in d:
        c.execute('UPDATE webhooks SET url = ? WHERE id = ?', (await _url(d), w['id']))
    e = _ereignisse(d, False)
    if e:
        c.execute('UPDATE webhooks SET ereignisse = ? WHERE id = ?', (json.dumps(e), w['id']))
    if 'aktiv' in d:
        if not isinstance(d['aktiv'], bool):
            fehler(400, 'aktiv: true oder false.')
        c.execute('UPDATE webhooks SET aktiv = ?, fehler_folge = 0, deaktiviert_grund = NULL WHERE id = ?', (int(d['aktiv']), w['id']))
        if not d['aktiv']:
            c.execute('DELETE FROM webhook_zustellungen WHERE webhook_id = ?', (w['id'],))
    return {'webhook': webhook_json(c.execute('SELECT * FROM webhooks WHERE id = ?', (w['id'],)).fetchone())}


def loeschen(b, i):
    w = _eigener(b, i)
    db.verbindung().execute('DELETE FROM webhooks WHERE id = ?', (w['id'],))       # Zustellungen + Log per CASCADE
    db.protokollieren(f'Webhook gelöscht (#{w["id"]})', b['id'])
    return {'ok': True}


def neues_geheimnis(b, i):
    w = _eigener(b, i)
    geheim = 'whsec_' + secrets.token_urlsafe(32)
    db.verbindung().execute('UPDATE webhooks SET geheim = ? WHERE id = ?', (verschluesseln(geheim), w['id']))
    db.protokollieren(f'Webhook: neues Geheimnis (#{w["id"]})', b['id'])
    return {'geheimnis': geheim}


async def testen(b, i):
    """„Test senden“: sofort ein Ereignis `test` zustellen (ein Versuch, zählt nicht zu den Fehlschlägen)."""
    w = _eigener(b, i)
    grenze.pruefen(f'webhook-test:{b["id"]}', 10, 60)
    z = 'zst_' + secrets.token_urlsafe(12)
    body = json.dumps({'id': 'evt_' + secrets.token_urlsafe(12), 'ereignis': 'test', 'erstellt': iso(jetzt()),
                       'daten': {'nachricht': 'Test von Agora', 'webhook': w['id']}}, ensure_ascii=False).encode()
    status, text, ms = await run_in_threadpool(senden, w['url'], entschluesseln(w['geheim']) or '', 'test', z, body)
    _log(w['id'], z, 'test', 1, status, text, ms)
    return {'ok': text is None, 'status': status, 'fehler': text, 'dauer_ms': ms}


def protokoll(b, i):
    w = _eigener(b, i)
    rows = db.verbindung().execute('SELECT * FROM webhook_log WHERE webhook_id = ? AND zeit >= ? ORDER BY id DESC LIMIT ?',
                                   (w['id'], jetzt() - LOG_TAGE * 86400, LOG_MAX)).fetchall()
    return {'zustellungen': [log_json(x) for x in rows]}


def _konto(req):
    return benutzer_aus_sitzung(req)


def _api(req):
    return pruefen(req, 'webhooks')[0]


# Gleiche Endpunkte zweimal: im Konto (Cookie + X-Agora) und in der öffentlichen API (Bearer, Recht „webhooks“)
for basis, wer in (('/api/konto/webhooks', _konto), ('/api/v1/webhooks', _api)):
    def _routen(basis=basis, wer=wer):
        @router.get(basis, name=f'{basis}:liste')
        def _liste(req: Request):
            return liste(wer(req))

        @router.post(basis, name=f'{basis}:neu', status_code=201 if basis.startswith('/api/v1') else 200)
        async def _neu(req: Request):
            b = wer(req)
            return await anlegen(b, await json_body(req))

        @router.patch(basis + '/{webhook_id}', name=f'{basis}:aendern')
        async def _aendern(webhook_id: int, req: Request):
            b = wer(req)
            return await aendern(b, webhook_id, await json_body(req))

        @router.delete(basis + '/{webhook_id}', name=f'{basis}:loeschen')
        def _loeschen(webhook_id: int, req: Request):
            return loeschen(wer(req), webhook_id)

        @router.post(basis + '/{webhook_id}/test', name=f'{basis}:test')
        async def _test(webhook_id: int, req: Request):
            return await testen(wer(req), webhook_id)

        @router.post(basis + '/{webhook_id}/geheimnis', name=f'{basis}:geheimnis')
        def _geheimnis(webhook_id: int, req: Request):
            return neues_geheimnis(wer(req), webhook_id)

        @router.get(basis + '/{webhook_id}/zustellungen', name=f'{basis}:zustellungen')
        def _protokoll(webhook_id: int, req: Request):
            return protokoll(wer(req), webhook_id)
    _routen()


def _export(b):
    c = db.verbindung()
    return {'webhooks': [webhook_json(w) | {'zustellungen': [log_json(x) for x in c.execute(
        'SELECT * FROM webhook_log WHERE webhook_id = ? ORDER BY id', (w['id'],)).fetchall()]}
        for w in c.execute('SELECT * FROM webhooks WHERE benutzer_id = ?', (b['id'],)).fetchall()]}


konto.EXPORT.append(_export)
