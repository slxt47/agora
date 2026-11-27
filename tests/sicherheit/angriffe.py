"""Sicherheits-Regressionstest für Agora (Black-Box gegen eine Wegwerf-Instanz – NIE gegen agora.slxt.dev).

Start über tests/sicherheit/lauf.sh (baut App + LiveKit im Testmodus mit Begrenzern, ohne lokale Webhook-Ziele).
Jeder Angriff ist eine Prüfung „sicher ja/nein“. Exit-Code 1, sobald ein Angriff klappt (oder ein Test selbst
abbricht) – so bleibt das Skript als Regressionstest stehen. Nur Standardbibliothek + Playwright (Image
agora-playwright:1.64.1); livekit-client kommt als UMD-Datei (LK_JS) aus web/node_modules.

Annahmen der Testinstanz (tests/e2e/docker-compose.test.yml): AGORA_TEST=1 (Mails unter /api/test/posteingang),
Cookie „agora“ ohne Secure, Admin host@example.com, LiveKit-Schlüssel testschluessel/… Die App vertraut wie in
Produktion CF-Connecting-IP (dort setzt es nur Cloudflare) – damit simuliert der Test verschiedene Angreifer-IPs.
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import struct
import sys
import time
import traceback
import urllib.parse

B = os.environ.get('BASIS', 'http://127.0.0.1:8195').rstrip('/')
_U = urllib.parse.urlsplit(B)
HOST, PORT = _U.hostname, _U.port or 80
LK_URL = os.environ.get('LK_URL', 'ws://127.0.0.1:7790')
LK_API = os.environ.get('LK_API', 'http://127.0.0.1:7790')
LK_KEY = os.environ.get('LK_KEY', 'testschluessel')
LK_SECRET = os.environ.get('LK_SECRET', 'testgeheimnis-testgeheimnis-testgeheimnis-123')
LK_JS = os.environ.get('LK_JS', '/lk.js')
FS_JS = os.environ.get('FS_JS', '/fs.js')          # desktop/src/fernsteuerung.js (Prüfregeln der Desktop-App)
COOKIE = os.environ.get('AGORA_COOKIE', 'agora')
NUR = set(filter(None, os.environ.get('NUR', '').split(',')))      # einzelne Bereiche: NUR=xss,ssrf

ERGEBNISSE = []          # (bereich, name, sicher, info)
BEREICH = ['?']


def pruefen(name, sicher, info=''):
    ERGEBNISSE.append((BEREICH[0], name, bool(sicher), str(info)[:300]))
    print(('  ok  ' if sicher else '  !!  ') + name + ('' if sicher or not info else f'  → {str(info)[:300]}'), flush=True)
    return bool(sicher)


def zufalls_ip():
    return f'198.18.{secrets.randbelow(255)}.{secrets.randbelow(254) + 1}'


# ---------------------------------------------------------------- HTTP (http.client: volle Kontrolle über Kopfzeilen)
class Antwort:
    def __init__(self, status, kopf, body):
        self.status, self.kopf, self.body = status, kopf, body

    @property
    def text(self):
        return self.body.decode('utf-8', 'replace')

    def json(self):
        try:
            return json.loads(self.body or b'null')
        except ValueError:
            return None

    def k(self, name):
        return self.kopf.get(name.lower(), [''])[-1]

    def __repr__(self):
        return f'<{self.status} {self.text[:120]!r}>'


def roh(methode, pfad, body=None, kopf=None, timeout=20, host=HOST, port=PORT):
    c = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        c.request(methode, pfad, body=body, headers=kopf or {})
        r = c.getresponse()
        k = {}
        for n, v in r.getheaders():
            k.setdefault(n.lower(), []).append(v)
        return Antwort(r.status, k, r.read())
    finally:
        c.close()


class Sitzung:
    """Ein Browser/Client: eigene IP (CF-Connecting-IP), eigenes Cookie, X-Agora an (wie das Frontend)."""
    def __init__(self, ip=None, agora=True):
        self.ip, self.agora, self.cookie = ip or zufalls_ip(), agora, None
        self.email = self.pw = None

    def __call__(self, methode, pfad, j=None, kopf=None, body=None, agora=None, ip=None, timeout=20):
        h = {'CF-Connecting-IP': ip or self.ip}
        if self.agora if agora is None else agora:
            h['X-Agora'] = '1'
        if self.cookie:
            h['Cookie'] = f'{COOKIE}={self.cookie}'
        if j is not None:
            body = json.dumps(j).encode()
            h['Content-Type'] = 'application/json'
        h.update(kopf or {})
        r = roh(methode, pfad, body, h, timeout=timeout)
        for v in r.kopf.get('set-cookie', []):
            m = re.match(rf'{COOKIE}=([^;]*)', v)
            if m:
                self.cookie = m.group(1).strip('"') or None
        return r

    def get(self, pfad, **kw):
        return self('GET', pfad, **kw)

    def post(self, pfad, j=None, **kw):
        return self('POST', pfad, j if j is not None or 'body' in kw else {}, **kw)

    def patch(self, pfad, j=None, **kw):
        return self('PATCH', pfad, j if j is not None else {}, **kw)

    def put(self, pfad, j=None, **kw):
        return self('PUT', pfad, j if j is not None else {}, **kw)

    def delete(self, pfad, j=None, **kw):
        return self('DELETE', pfad, j, **kw)


def posteingang():
    return roh('GET', '/api/test/posteingang').json()['mails']


def konto_anlegen(name='Ada Angriff', email=None, pw=None):
    s = Sitzung()
    s.email = email or f'u{secrets.token_hex(5)}@example.org'
    s.pw = pw or 'Passwort-' + secrets.token_hex(6)
    r = s.post('/api/konto/registrieren', {'name': name, 'email': s.email, 'passwort': s.pw})
    assert r.status == 200, f'Registrieren: {r}'
    link = [m for m in posteingang() if m['an'] == s.email][-1]['text']
    pfad = re.search(r'(/api/konto/bestaetigen\?t=[\w\-.~]+)', link).group(1)
    r = s.get(pfad)
    assert r.status == 303 and s.cookie, f'Bestätigen: {r}'
    s.id = s.get('/api/konto/ich').json()['ich']['id']
    return s


def raum(s, **kw):
    r = s.post('/api/raeume', kw)
    assert r.status == 200, f'Raum anlegen: {r}'
    return r.json()['raum']


def zugang(s, code, **kw):
    r = s.post(f'/api/raeume/{code}/beitreten', kw)
    assert r.status == 200 and r.json()['status'] == 'zugelassen', f'Beitreten: {r}'
    return r.json()['zugang']


def T(z):
    return {'X-Teilnahme': z['teilnahme']}


def jwt_teile(token):
    teil = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(teil + '=' * (-len(teil) % 4)))


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def lk_admin_jwt(raumname):
    jetzt = int(time.time())
    kopf = b64u(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    last = b64u(json.dumps({'iss': LK_KEY, 'nbf': jetzt - 10, 'exp': jetzt + 600,
                            'video': {'roomAdmin': True, 'roomList': True, 'room': raumname}}).encode())
    sig = b64u(hmac.new(LK_SECRET.encode(), f'{kopf}.{last}'.encode(), hashlib.sha256).digest())
    return f'{kopf}.{last}.{sig}'


def lk_teilnehmer(raumname):
    """Wahrheit vom Medienserver (Twirp ListParticipants) – nicht vom angreifenden Client."""
    u = urllib.parse.urlsplit(LK_API)
    r = roh('POST', '/twirp/livekit.RoomService/ListParticipants', json.dumps({'room': raumname}).encode(),
            {'Authorization': 'Bearer ' + lk_admin_jwt(raumname), 'Content-Type': 'application/json'}, host=u.hostname, port=u.port)
    assert r.status == 200, f'LiveKit ListParticipants: {r}'
    return r.json().get('participants', [])


def lk_raum_metadaten(raumname):
    u = urllib.parse.urlsplit(LK_API)
    r = roh('POST', '/twirp/livekit.RoomService/ListRooms', json.dumps({'names': [raumname]}).encode(),
            {'Authorization': 'Bearer ' + lk_admin_jwt(raumname), 'Content-Type': 'application/json'}, host=u.hostname, port=u.port)
    raeume = (r.json() or {}).get('rooms', [])
    return raeume[0].get('metadata', '') if raeume else ''


def totp(geheim, versatz=0):
    k = base64.b32decode(geheim + '=' * (-len(geheim) % 8))
    d = hmac.new(k, struct.pack('>Q', int(time.time() // 30) + versatz), hashlib.sha1).digest()
    o = d[-1] & 15
    return str((struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def abgelehnt(r, erlaubt=(400, 401, 403, 404, 405, 409, 413, 415, 422, 429)):
    return r.status in erlaubt


# ================================================================= 1. Kopfzeilen, Cookies, CORS
def kopfzeilen():
    r = roh('GET', '/')
    csp = r.k('content-security-policy')
    pruefen('CSP: script-src nur self (kein unsafe-inline/eval)', "script-src 'self'" in csp and 'unsafe-inline' not in csp.split('style-src')[0]
            and "'unsafe-eval'" not in csp, csp)
    pruefen("CSP: object-src 'none', base-uri 'none', frame-ancestors 'none'",
            all(x in csp for x in ("object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'")), csp)
    pruefen('X-Frame-Options DENY', r.k('x-frame-options') == 'DENY', r.k('x-frame-options'))
    pruefen('X-Content-Type-Options nosniff', r.k('x-content-type-options') == 'nosniff')
    pruefen('Referrer-Policy no-referrer', r.k('referrer-policy') == 'no-referrer')
    pruefen('COOP same-origin', r.k('cross-origin-opener-policy') == 'same-origin')
    api = roh('GET', '/api/einstellungen')
    pruefen('API-Antworten: Cache-Control no-store', 'no-store' in api.k('cache-control'), api.k('cache-control'))
    if B.startswith('https://'):
        pruefen('HSTS gesetzt', 'max-age=' in r.k('strict-transport-security'), r.k('strict-transport-security'))
    # CORS: fremde Herkunft bekommt nie Access-Control-Allow-*
    for m, p in (('GET', '/api/konto/ich'), ('OPTIONS', '/api/raeume')):
        x = roh(m, p, kopf={'Origin': 'https://boese.example', 'Access-Control-Request-Method': 'POST',
                            'Access-Control-Request-Headers': 'x-agora,content-type'})
        pruefen(f'CORS {m} {p}: kein Access-Control-Allow-Origin', not x.k('access-control-allow-origin'), x.kopf)
    # Cookie-Flags (Secure ist in der Testinstanz absichtlich aus – AGORA_COOKIE_SECURE=1 ist Standard)
    s = konto_anlegen()
    r = s.post('/api/konto/anmelden', {'email': s.email, 'passwort': s.pw})
    sc = ';'.join(r.kopf.get('set-cookie', [])).lower()
    pruefen('Sitzungs-Cookie HttpOnly + SameSite=Lax + Path=/', 'httponly' in sc and 'samesite=lax' in sc and 'path=/' in sc, sc)


# ================================================================= 2. CSRF
def csrf():
    s = konto_anlegen()
    for m, p, j in (('POST', '/api/raeume', {'titel': 'csrf'}), ('PATCH', '/api/konto/ich', {'name': 'csrf'}),
                    ('DELETE', '/api/konto', {'passwort': s.pw}), ('POST', '/api/konto/api-schluessel', {'name': 'x', 'rechte': ['webhooks']})):
        r = s(m, p, j, agora=False)
        pruefen(f'CSRF ohne X-Agora: {m} {p} abgelehnt', r.status == 403, r)
    for typ, body in (('text/plain', b'{"titel":"csrf"}'), ('application/x-www-form-urlencoded', b'titel=csrf')):
        r = s('POST', '/api/raeume', body=body, kopf={'Content-Type': typ}, agora=False)
        pruefen(f'CSRF als einfaches Formular ({typ}) abgelehnt', r.status == 403, r)
    r = s('POST', '/api/raeume', {'titel': 'csrf'}, kopf={'X-Agora': '0'}, agora=False)
    pruefen('CSRF mit X-Agora: 0 abgelehnt', r.status == 403, r)
    # Ausnahme /api/v1/ darf nicht über Pfad-Tricks für Cookie-Endpunkte gelten
    for p in ('/api/v1/../raeume', '/api/v1/%2e%2e/raeume', '/api/v1%2F..%2Fraeume', '/api/v1/..%2Fraeume'):
        r = s('POST', p, {'titel': 'csrf'}, agora=False)
        pruefen(f'CSRF-Ausnahme /api/v1 nicht per Pfad-Trick ({p})', r.status != 200, r)
    r = s.get('/api/v1/meetings')
    pruefen('/api/v1 akzeptiert kein Sitzungs-Cookie', r.status == 401, r)
    vorher = len(s.get('/api/raeume').json()['geplant'])
    pruefen('kein Raum durch CSRF-Versuche entstanden', vorher == 0, vorher)


# ================================================================= 3. Anmeldung, Sitzungen, Admin
def anmeldung():
    anonym = Sitzung()
    for p in ('/api/raeume', '/api/aufnahmen', '/api/konto/export', '/api/konto/sicherheit', '/api/verlauf', '/api/vorlagen',
              '/api/konto/api-schluessel', '/api/konto/webhooks', '/api/qualitaet', '/api/konto/2fa', '/api/konto/kalender',
              '/api/konto/blockiert', '/api/konto/erinnerung'):
        r = anonym.get(p)
        pruefen(f'ohne Anmeldung: GET {p} → 401', r.status == 401, r)
    s = konto_anlegen()
    for p in ('/admin', '/admin.html', '/api/admin/uebersicht', '/api/admin/benutzer', '/api/admin/meldungen', '/api/admin/protokoll',
              '/api/admin/meetings'):
        r = s.get(p)
        pruefen(f'Nicht-Admin: GET {p} → 404', r.status == 404, r)
    r = s.post(f'/api/admin/benutzer/{s.id}/sperren', {'gesperrt': False})
    pruefen('Nicht-Admin: Konto entsperren → 404', r.status == 404, r)
    for p in ('/api/metriken', '/api/gesund?tief=1'):
        r = roh('GET', p, kopf={'CF-Connecting-IP': '203.0.113.9'})
        pruefen(f'{p} über den Tunnel (CF-Connecting-IP) → 404', r.status == 404, r)
    # Sitzungs-Fixierung: ein vorher gesetztes Cookie wird nie zur Sitzung
    f = Sitzung()
    f.cookie = 'angreifer-gewaehlt-' + secrets.token_hex(8)
    r = f.post('/api/konto/anmelden', {'email': s.email, 'passwort': s.pw})
    pruefen('Sitzungs-Fixierung: Anmeldung setzt neues Token', r.status == 200 and not f.cookie.startswith('angreifer-'), f.cookie)
    # Abmelden macht das alte Cookie ungültig
    alt = f.cookie
    f.post('/api/konto/abmelden')
    f.cookie = alt
    r = f.get('/api/konto/export')
    pruefen('nach dem Abmelden: altes Cookie ungültig', r.status == 401, r)
    # Passwort ändern meldet andere Geräte ab
    g = Sitzung()
    g.post('/api/konto/anmelden', {'email': s.email, 'passwort': s.pw})
    r = s.post('/api/konto/passwort-aendern', {'alt': s.pw, 'neu': s.pw + '-neu'})
    s.pw += '-neu'
    pruefen('Passwort ändern meldet andere Sitzungen ab', r.status == 200 and g.get('/api/konto/ich').json()['angemeldet'] is False, r)
    for wert in ('x' * 99, '"; DROP TABLE sitzungen; --', 'ä' * 20):
        x = Sitzung()
        x.cookie = urllib.parse.quote(wert)
        pruefen(f'gefälschtes Cookie {wert[:12]!r} → nicht angemeldet', x.get('/api/konto/ich').json() == {'angemeldet': False})
    # Datenexport ohne Geheimnisse
    exp = s.get('/api/konto/export').text
    pruefen('Datenexport ohne Passwort-Hash/2FA-Geheimnis', 'argon2' not in exp and 'passwort_hash' not in exp and 'totp_geheim' not in exp)
    # Konto-Aufzählung: Registrieren/Passwort vergessen antworten gleich
    r1 = Sitzung().post('/api/konto/registrieren', {'name': 'X', 'email': s.email, 'passwort': 'egal-egal-egal'})
    r2 = Sitzung().post('/api/konto/registrieren', {'name': 'X', 'email': f'neu{secrets.token_hex(4)}@example.org', 'passwort': 'egal-egal-egal'})
    pruefen('Registrieren verrät nicht, ob es die Adresse gibt', (r1.status, r1.body) == (r2.status, r2.body), (r1, r2))
    r1 = Sitzung().post('/api/konto/passwort-vergessen', {'email': s.email})
    r2 = Sitzung().post('/api/konto/passwort-vergessen', {'email': 'gibtsnicht@example.org'})
    pruefen('Passwort vergessen verrät nicht, ob es die Adresse gibt', (r1.status, r1.body) == (r2.status, r2.body), (r1, r2))
    return s


# ================================================================= 4. IDOR zwischen Konten / Räumen
def idor():
    a, b = konto_anlegen('Alice'), konto_anlegen('Bob')
    ra = raum(a, titel='Alice privat', chat_speichern=True, passcode='geheim-1234')
    code = ra['code']
    a.patch(f'/api/raeume/{code}/transkript', {'speichern': True})
    za = zugang(a, code)
    a.post(f'/api/raeume/{code}/chat', {'text': 'vertraulich'}, kopf=T(za))
    a.post(f'/api/raeume/{code}/untertitel', {'text': 'vertraulich gesagt', 'final': True}, kopf=T(za))
    a.post(f'/api/raeume/{code}/steuerung', {'aktion': 'beenden'}, kopf=T(za))
    rec = a('POST', '/api/aufnahmen', body=b'\x1aE\xdf\xa3' + b'\0' * 2000, kopf={'Content-Type': 'video/webm', 'X-Titel': 'privat'})
    assert rec.status == 200, rec
    aid = rec.json()['aufnahme']['id']
    vid = a.get('/api/verlauf').json()['meetings'][0]['id']
    vorlage = a.post('/api/vorlagen', {'name': 'privat', 'einstellungen': {}}).json()['vorlage']['id']
    schl_a = a.post('/api/konto/api-schluessel', {'name': 'a', 'rechte': ['meetings:lesen', 'aufnahmen:lesen', 'webhooks'],
                                                   'gueltig_tage': 30}).json()
    wh = a.post('/api/konto/webhooks', {'url': 'https://93.184.215.14/hook', 'ereignisse': ['meeting.beendet']})
    whid = wh.json()['webhook']['id'] if wh.status == 200 else 0
    webinar_code = raum(a, titel='Webinar')['code']
    a.patch(f'/api/raeume/{webinar_code}/webinar', {'an': True, 'registrierung': True})
    Sitzung().post(f'/api/webinare/{webinar_code}/anmelden', {'name': 'Zoe', 'email': 'zoe@example.org'})

    versuche = [
        ('PATCH', f'/api/raeume/{code}', {'titel': 'übernommen'}), ('DELETE', f'/api/raeume/{code}', None),
        ('GET', f'/api/raeume/{code}/verlauf', None), ('GET', f'/api/raeume/{code}/transkripte', None),
        ('GET', f'/api/raeume/{code}/transkript', None), ('DELETE', f'/api/raeume/{code}/transkript', None),
        ('PATCH', f'/api/raeume/{code}/transkript', {'speichern': False}), ('GET', f'/api/raeume/{code}/qualitaet', None),
        ('GET', f'/api/raeume/{code}/umfragen/verlauf', None), ('PUT', f'/api/raeume/{code}/einbetten', {'erlaubt': True, 'herkuenfte': ['https://b.example']}),
        ('POST', f'/api/raeume/{code}/einladen', {'emails': ['opfer@example.org']}), ('DELETE', f'/api/raeume/{code}/dauer-chat', None),
        ('GET', f'/api/raeume/{webinar_code}/webinar', None), ('GET', f'/api/raeume/{webinar_code}/webinar/anmeldungen', None),
        ('GET', f'/api/raeume/{webinar_code}/webinar/anmeldungen.csv', None), ('POST', f'/api/raeume/{webinar_code}/webinar/panel', {'name': 'x'}),
        ('PATCH', f'/api/raeume/{webinar_code}/webinar', {'registrierung': False}),
        ('GET', f'/api/aufnahmen/{aid}/datei', None), ('DELETE', f'/api/aufnahmen/{aid}', None),
        ('POST', f'/api/aufnahmen/{aid}/freigabe', {'tage': 1}), ('DELETE', f'/api/aufnahmen/{aid}/freigabe', None),
        ('GET', f'/api/aufnahmen/{aid}/transkript', None),
        ('GET', f'/api/verlauf/{vid}/inhalt', None), ('DELETE', f'/api/verlauf/{vid}', None),
        ('PUT', f'/api/vorlagen/{vorlage}', {'name': 'übernommen'}), ('DELETE', f'/api/vorlagen/{vorlage}', None),
        ('POST', f'/api/vorlagen/{vorlage}/raum', {}), ('POST', '/api/vorlagen', {'name': 'x', 'aus_raum': code}),
        ('DELETE', f'/api/konto/api-schluessel/{schl_a["schluessel"]["id"]}', None),
        ('PATCH', f'/api/konto/webhooks/{whid}', {'aktiv': False}), ('DELETE', f'/api/konto/webhooks/{whid}', None),
        ('GET', f'/api/konto/webhooks/{whid}/zustellungen', None), ('POST', f'/api/konto/webhooks/{whid}/geheimnis', None),
        ('POST', f'/api/konto/webhooks/{whid}/test', None),
    ]
    for m, p, j in versuche:
        r = b(m, p, j)
        pruefen(f'Bob → {m} {p.replace(code, "<A>").replace(aid, "<aufnahme>")}', abgelehnt(r), r)
    # Alice selbst darf weiter (Gegenprobe)
    pruefen('Gegenprobe: Alice sieht ihre Aufnahme', a.get(f'/api/aufnahmen/{aid}/datei').status == 200)
    pruefen('Gegenprobe: Alice sieht ihr Transkript', a.get(f'/api/raeume/{code}/transkript').status == 200)
    # Öffentliche API: Bobs Schlüssel auf Alices Daten
    schl_b = b.post('/api/konto/api-schluessel', {'name': 'b', 'rechte': ['meetings:lesen', 'meetings:schreiben', 'aufnahmen:lesen', 'webhooks'],
                                                   'gueltig_tage': 30}).json()['token']
    bear = {'Authorization': 'Bearer ' + schl_b}
    for m, p in (('GET', f'/api/v1/meetings/{code}'), ('GET', f'/api/v1/meetings/{code}/teilnehmer'), ('GET', f'/api/v1/meetings/{code}/link'),
                 ('PATCH', f'/api/v1/meetings/{code}'), ('DELETE', f'/api/v1/meetings/{code}'), ('GET', f'/api/v1/aufnahmen/{aid}'),
                 ('GET', f'/api/v1/aufnahmen/{aid}/download'), ('DELETE', f'/api/v1/webhooks/{whid}')):
        r = roh(m, p, b'{}' if m in ('PATCH',) else None, {**bear, 'Content-Type': 'application/json', 'CF-Connecting-IP': zufalls_ip()})
        pruefen(f'API v1 (Bob) → {m} {p.replace(code, "<A>").replace(aid, "<aufnahme>")}', r.status in (403, 404), r)
    r = roh('GET', '/api/v1/meetings', kopf=bear)
    pruefen('API v1: Bobs Liste enthält keine fremden Meetings', all(m['code'] != code for m in r.json()['meetings']), r)
    # Signierter Download-Link: manipuliert → 403
    link = roh('GET', f'/api/v1/aufnahmen/{aid}/download', kopf={'Authorization': 'Bearer ' + schl_a['token']}).json()['url']
    pfad = urllib.parse.urlsplit(link)
    q = dict(urllib.parse.parse_qsl(pfad.query))
    pruefen('Gegenprobe: signierter Download-Link funktioniert', roh('GET', pfad.path + '?' + pfad.query).status == 200)
    for name, qq in (('bis verlängert', {**q, 'bis': str(int(q['bis']) + 1)}), ('Signatur geändert', {**q, 'sig': '0' * 64}),
                     ('ohne Signatur', {'bis': q['bis']})):
        r = roh('GET', pfad.path + '?' + urllib.parse.urlencode(qq))
        pruefen(f'Download-Link {name} → 403', r.status == 403, r)
    # Teilnahme-Geheimnis gilt nur im eigenen Raum
    rb = raum(b, titel='Bobs Raum')
    zb = zugang(Sitzung(), rb['code'], name='Gast')
    ra2 = raum(a, titel='Alices zweiter Raum')
    zugang(a, ra2['code'])
    for m, p, j in (('GET', f'/api/raeume/{ra2["code"]}/chat', None), ('POST', f'/api/raeume/{ra2["code"]}/chat', {'text': 'x'}),
                    ('GET', f'/api/raeume/{ra2["code"]}/zustand', None), ('GET', f'/api/raeume/{ra2["code"]}/dateien', None),
                    ('GET', f'/api/raeume/{ra2["code"]}/notizen', None)):
        r = Sitzung()(m, p, j, kopf=T(zb))
        pruefen(f'X-Teilnahme aus fremdem Raum → {m} {p.split("/", 4)[-1]}', r.status == 403, r)
    # Freigabe-Link ohne/mit falschem Token
    for t in ('A' * 22, '../' * 7 + 'x', ''):
        r = roh('GET', f'/api/freigaben/{urllib.parse.quote(t, safe="")}/datei')
        pruefen(f'Freigabe mit erfundenem Token {t[:10]!r}', r.status in (404, 405), r)
    return a, b


# ================================================================= 5. Rollen im Meeting
def rollen():
    a = konto_anlegen('Host')
    r = raum(a, titel='Rollen')
    code = r['code']
    a.patch(f'/api/raeume/{code}', {})
    za = zugang(a, code)
    a.post(f'/api/raeume/{code}/steuerung', {'aktion': 'rechte', 'bildschirm': False, 'umbenennen': False}, kopf=T(za))
    g = Sitzung()
    zg = zugang(g, code, name='Gast')
    host_id = za['identity']
    for name, m, p, j in (
            ('Host stummschalten', 'POST', 'steuerung', {'aktion': 'stumm', 'ziel': host_id}),
            ('Host entfernen', 'POST', 'steuerung', {'aktion': 'entfernen', 'ziel': host_id}),
            ('Rechte ändern', 'POST', 'steuerung', {'aktion': 'rechte', 'bildschirm': True}),
            ('Meeting sperren', 'POST', 'steuerung', {'aktion': 'sperren', 'an': True}),
            ('Meeting beenden', 'POST', 'steuerung', {'aktion': 'beenden'}),
            ('sich zum Co-Host machen', 'POST', 'steuerung', {'aktion': 'rolle', 'ziel': zg['identity'], 'rolle': 'cohost'}),
            ('Host übernehmen', 'POST', 'steuerung', {'aktion': 'host_uebertragen', 'ziel': zg['identity']}),
            ('Host umbenennen', 'POST', 'steuerung', {'aktion': 'umbenennen', 'ziel': host_id, 'name': 'Ich bin der Host'}),
            ('sich umbenennen (Recht aus)', 'POST', 'steuerung', {'aktion': 'umbenennen', 'ziel': zg['identity'], 'name': 'Host'}),
            ('Warteraum lesen', 'GET', 'warteraum', None),
            ('Umfrage anlegen', 'POST', 'umfragen', {'frage': 'x', 'optionen': ['a', 'b']}),
            ('Whiteboard sperren', 'POST', 'whiteboard/sperren', {'an': True}),
            ('Whiteboard leeren', 'POST', 'whiteboard/leeren', {}),
            ('Breakout anlegen', 'POST', 'breakout', {'anzahl': 2}),
            ('Aufnahme-Anzeige setzen', 'POST', 'aufnahme', {'aktiv': True}),
            ('Statistik aller lesen', 'GET', 'statistik', None),
            ('Server-Aufnahme starten', 'POST', 'egress/aufnahme', {'aktion': 'start'}),
            ('Livestream starten', 'POST', 'egress/stream', {'aktion': 'start', 'url': 'rtmp://127.0.0.1/live', 'schluessel': 'x'}),
            ('Anmerkungen sperren (teilt nicht)', 'POST', 'anmerkungen', {'erlaubt': False}),
            ('Breakout-Rundruf', 'POST', 'breakout/nachricht', {'text': 'x'})):
        x = g(m, f'/api/raeume/{code}/{p}', j, kopf=T(zg))
        pruefen(f'Gast: {name}', abgelehnt(x, (400, 403, 404, 409)), x)
    # Webinar: Zuschauer und Registrierung
    w = raum(a, titel='Webinar')['code']
    a.patch(f'/api/raeume/{w}/webinar', {'an': True, 'registrierung': True})
    x = Sitzung().post(f'/api/raeume/{w}/beitreten', {'name': 'Ohne Anmeldung'})
    pruefen('Webinar mit Anmeldung: Beitritt ohne persönlichen Link', x.status == 403, x)
    x = Sitzung().post(f'/api/raeume/{w}/beitreten', {'name': 'Panel?', 'panel': 'f' * 40})
    pruefen('Webinar: erfundener Panel-Link', x.status == 403, x)
    x = Sitzung().post(f'/api/raeume/{w}/beitreten', {'name': 'Anmeldung?', 'anmeldung': 'f' * 32})
    pruefen('Webinar: erfundener Anmelde-Link', x.status == 403, x)
    a.patch(f'/api/raeume/{w}/webinar', {'registrierung': False})
    zz = zugang(Sitzung(), w, name='Zuschauer')
    pruefen('Webinar: Gast wird Zuschauer', zz['rolle'] == 'zuschauer', zz['rolle'])
    x = Sitzung().post(f'/api/raeume/{w}/untertitel', {'text': 'x', 'final': True}, kopf=T(zz))
    pruefen('Zuschauer: Untertitel senden', x.status == 403, x)
    x = Sitzung().post(f'/api/raeume/{w}/webinar/rolle', {'rolle': 'panelist', 'ziel': zz['identity']}, kopf=T(zz))
    pruefen('Zuschauer: sich selbst aufs Podium holen', x.status == 403, x)
    return {'code': code, 'gast': zg, 'host': za, 'webinar': w, 'zuschauer': zz, 'besitzer': a}


# ================================================================= 6. LiveKit-Token (Grants, Rollen, Attribute)
def token_grants(ctx):
    for name, z, rolle, quellen in (('Gast (Bildschirm-Recht aus)', ctx['gast'], 'gast', {'camera', 'microphone'}),
                                    ('Zuschauer', ctx['zuschauer'], 'zuschauer', set()),
                                    ('Host', ctx['host'], 'host', {'camera', 'microphone', 'screen_share', 'screen_share_audio'})):
        j = jwt_teile(z['token'])
        v = j.get('video', {})
        pruefen(f'{name}: Token nur für genau diesen Raum', v.get('room') in (ctx['code'], ctx['webinar']) and v.get('roomJoin') is True, v)
        pruefen(f'{name}: keine Admin-Grants (roomAdmin/Create/List/Record/hidden/agent)',
                not any(v.get(k) for k in ('roomAdmin', 'roomCreate', 'roomList', 'roomRecord', 'hidden', 'recorder', 'agent', 'ingressAdmin')), v)
        pruefen(f'{name}: erlaubte Quellen {sorted(quellen) or "keine"}', set(v.get('canPublishSources', [])) == quellen
                and bool(v.get('canPublish')) == bool(quellen), v)
        pruefen(f'{name}: kein canUpdateOwnMetadata (Name/Attribute nur über den Server)', not v.get('canUpdateOwnMetadata'), v)
        pruefen(f'{name}: Token kurzlebig (≤ 15 min)', j['exp'] - j.get('nbf', j['exp'] - 900) <= 15 * 60 + 5, j)
        pruefen(f'{name}: identity = vom Server vergeben', j['sub'] == z['identity'], j['sub'])
        if rolle:
            pruefen(f'{name}: Attribut rolle={rolle}', j.get('attributes', {}).get('rolle') == rolle, j.get('attributes'))
    # Rolle per Anfrage erzwingen?
    x = zugang(Sitzung(), ctx['code'], name='Spieler', rolle='host', identity='u1-admin', attributes={'rolle': 'host'})
    j = jwt_teile(x['token'])
    pruefen('Beitritt mit rolle/identity im Body: Server ignoriert das', x['rolle'] in ('gast', 'teilnehmer') and j['sub'].startswith('g-')
            and j['attributes']['rolle'] == x['rolle'], j)


# ================================================================= 7. SSRF (Webhooks)
def ssrf():
    a = konto_anlegen('SSRF')
    ziele = ['http://93.184.215.14/', 'https://127.0.0.1/', 'https://localhost/', 'https://[::1]/', 'https://169.254.169.254/latest/meta-data/',
             'https://2130706433/', 'https://0x7f000001/', 'https://127.1/', 'https://0177.0.0.1/', 'https://0/', 'https://0.0.0.0/',
             'https://[::ffff:127.0.0.1]/', 'https://[::ffff:7f00:1]/', 'https://10.0.0.1/', 'https://172.16.0.1/', 'https://192.168.1.1/',
             'https://100.64.0.1/', 'https://[fd00::1]/', 'https://[fe80::1]/', 'https://[64:ff9b::7f00:1]/', 'https://[64:ff9b::a9fe:a9fe]/',
             'https://[::127.0.0.1]/', 'https://[2002:7f00:1::1]/', 'https://localtest.me/', 'https://127.0.0.1.nip.io/',
             'https://user:pw@93.184.215.14/', 'https://93.184.215.14/#frag', 'file:///etc/passwd', 'gopher://127.0.0.1:6379/_x',
             'https://127.0.0.1:7790/twirp/livekit.RoomService/ListRooms', f'https://{HOST}:{PORT}/api/gesund?tief=1']
    for i, u in enumerate(ziele):
        if i and i % 15 == 0:
            a = konto_anlegen('SSRF')              # Grenze: 20 neue Webhooks je Konto und Stunde
        r = a.post('/api/konto/webhooks', {'url': u, 'ereignisse': ['meeting.beendet']})
        pruefen(f'Webhook-Ziel {u} abgelehnt', r.status == 400, r)
    a = konto_anlegen('SSRF')
    ok = a.post('/api/konto/webhooks', {'url': 'https://93.184.215.14/hook', 'ereignisse': ['meeting.beendet']})
    pruefen('Gegenprobe: öffentliche IP wird angenommen', ok.status == 200, ok)
    if ok.status == 200:
        wid = ok.json()['webhook']['id']
        for u in ('https://127.0.0.1/', 'https://[64:ff9b::7f00:1]/'):
            r = a.patch(f'/api/konto/webhooks/{wid}', {'url': u})
            pruefen(f'Webhook ändern auf {u} abgelehnt', r.status == 400, r)
    # öffentliche API ebenso
    tok = a.post('/api/konto/api-schluessel', {'name': 'w', 'rechte': ['webhooks'], 'gueltig_tage': 30}).json()['token']
    r = roh('POST', '/api/v1/webhooks', json.dumps({'url': 'https://169.254.169.254/', 'ereignisse': ['meeting.beendet']}).encode(),
            {'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
    pruefen('API v1: Webhook auf Metadaten-Dienst abgelehnt', r.status == 400, r)


# ================================================================= 8. Pfade, Range
def pfade(ctx_a):
    a = ctx_a
    pfade_ = ['/api/aufnahmen/..%2F..%2Fagora.db/datei', '/api/aufnahmen/..%2f..%2fagora.db', '/assets/..%2F..%2F..%2Fetc%2Fpasswd',
              '/..%2f..%2f..%2fetc/passwd', '/%2e%2e/%2e%2e/%2e%2e/etc/passwd', '/vendor/..%2F..%2F..%2Fsrv%2Fapp%2Fmain.py',
              '/kalender/..%2F..%2Fagora.db', '/kalender/%2e%2e%2fagora.db.ics', '/a/..%2F..%2F..%2Fetc%2Fpasswd',
              '/api/freigaben/..%2F..%2Fagora.db/datei', '/icons/..%2F..%2Fapp%2Fmain.py', '/e/..%2F..%2Fetc%2Fpasswd',
              '//etc/passwd', '/static/../../etc/passwd', '/raum.html/..%2F..%2Fetc%2Fpasswd', '/.env', '/app.env', '/daten/agora.db',
              '/.git/config', '/sw.js/..%2f..%2fapp%2fkern.py']
    for p in pfade_:
        r = a.get(p)
        geheim = any(x in r.body for x in (b'root:x:', b'SQLite format', b'import os', b'AGORA_GEHEIM', b'[core]'))
        pruefen(f'Pfad {p}', not geheim and r.status != 500, r)
    code = a.get('/api/raeume').json()['geplant'][0]['code'] if a.get('/api/raeume').json()['geplant'] else raum(a)['code']
    z = zugang(a, code)
    for name in ('../../agora.db.pdf', '..\\..\\x.pdf', 'a/../../b.pdf', '%2e%2e%2fx.pdf', '.htaccess.pdf', 'x.pdf\0.html', 'x.svg', 'x.html',
                 'x.PDF.exe'):
        r = a('POST', f'/api/raeume/{code}/dateien', body=b'%PDF-1.4 test', kopf={**T(z), 'Content-Type': 'application/octet-stream',
                                                                                 'X-Dateiname': urllib.parse.quote(name)})
        if r.status == 200:
            d = r.json()['datei']
            gefaehrlich_inline = False
            x = a.get(f'/api/raeume/{code}/dateien/{d["id"]}?vorschau=1', kopf=T(z))
            cd = x.k('content-disposition')
            gefaehrlich_inline = 'inline' in cd and d['name'].rsplit('.', 1)[-1].lower() not in ('png', 'jpg', 'jpeg', 'gif', 'webp')
            pruefen(f'Datei-Upload {name!r}: Name ohne Pfad, sicher ausgeliefert', '/' not in d['name'] and '\\' not in d['name']
                    and not gefaehrlich_inline and 'sandbox' in x.k('content-security-policy') and x.k('x-content-type-options') == 'nosniff',
                    (d['name'], cd, x.k('content-type')))
        else:
            pruefen(f'Datei-Upload {name!r} abgelehnt', abgelehnt(r), r)
    # Range-Anfragen (viele Bereiche) auf eine Aufnahme: kein 500, schnell
    rec = a('POST', '/api/aufnahmen', body=b'\x1aE\xdf\xa3' + os.urandom(200000), kopf={'Content-Type': 'video/webm', 'X-Titel': 'range'})
    aid = rec.json()['aufnahme']['id']
    for rng in ('bytes=0-1', 'bytes=' + ','.join(f'{i}-{i}' for i in range(0, 4000, 2)), 'bytes=-0', 'bytes=999999999-', 'bytes=5-1',
                'bytes=0-1,' * 500 + '0-1', 'bytes=' + '9' * 400 + '-'):
        t0 = time.time()
        r = a.get(f'/api/aufnahmen/{aid}/datei', kopf={'Range': rng})
        pruefen(f'Range {rng[:30]}…: kein Fehler, schnell', r.status in (200, 206, 400, 416) and time.time() - t0 < 3, (r.status, time.time() - t0))


# ================================================================= 9. Größen, kaputte Eingaben
def groessen():
    # Content-Length riesig, Body klein: der Server darf nicht auf 100 MB warten (sonst puffert er sie im Speicher)
    code = raum(konto_anlegen('Gross'))['code']
    for pfad in ('/api/konto/anmelden', '/api/konto/registrieren', f'/api/raeume/{code}/beitreten', '/api/konto/passwort-vergessen',
                 f'/api/webinare/{code}/anmelden', '/api/konto/anmelden/code'):
        s = socket.create_connection((HOST, PORT), timeout=5)
        try:
            s.sendall(f'POST {pfad} HTTP/1.1\r\nHost: x\r\nX-Agora: 1\r\nContent-Type: application/json\r\n'
                      f'CF-Connecting-IP: {zufalls_ip()}\r\nContent-Length: 100000000\r\n\r\n{{"email":'.encode())
            try:
                antwort = s.recv(200)
            except socket.timeout:
                antwort = b'(keine Antwort nach 5 s - Server wartet auf 100 MB)'
        finally:
            s.close()
        pruefen(f'{pfad.replace(code, "<code>")}: Content-Length 100 MB sofort abgelehnt', antwort[:12] in (b'HTTP/1.1 413', b'HTTP/1.1 404'),
                antwort[:60])
    # Chunked ohne Länge: Server bricht nach der Grenze ab
    s = socket.create_connection((HOST, PORT), timeout=10)
    try:
        s.sendall(f'POST /api/konto/anmelden HTTP/1.1\r\nHost: x\r\nX-Agora: 1\r\nContent-Type: application/json\r\n'
                  f'CF-Connecting-IP: {zufalls_ip()}\r\nTransfer-Encoding: chunked\r\n\r\n'.encode())
        stueck = b' ' * 65536
        antwort = b''
        for _ in range(64):                      # bis 4 MB – die Antwort soll lange vorher kommen
            try:
                s.sendall(b'%x\r\n' % len(stueck) + stueck + b'\r\n')
            except OSError:
                break
            s.settimeout(0.05)
            try:
                antwort = s.recv(200)
                if antwort:
                    break
            except (socket.timeout, BlockingIOError):
                pass
            s.settimeout(10)
        if not antwort:
            s.settimeout(10)
            s.sendall(b'0\r\n\r\n')
            antwort = s.recv(200)
    finally:
        s.close()
    pruefen('chunked 4 MB an /api/konto/anmelden: 413', antwort.startswith(b'HTTP/1.1 413'), antwort[:60])
    # tief verschachteltes JSON (innerhalb der Größengrenze) → 400, nicht 500
    for body in (b'[' * 15000, b'{"a":' * 3000 + b'1' + b'}' * 3000, b'\xff\xfe', b'{"email": "\\ud800"}', b'null', b'1e999999'):
        r = roh('POST', '/api/konto/anmelden', body, {'X-Agora': '1', 'Content-Type': 'application/json', 'CF-Connecting-IP': zufalls_ip()})
        pruefen(f'kaputtes JSON {body[:12]!r}… → 4xx statt 500', 400 <= r.status < 500, r)
    a = konto_anlegen('Gross')
    code = raum(a)['code']
    z = zugang(a, code)
    s = socket.create_connection((HOST, PORT), timeout=5)
    try:
        s.sendall(f'POST /api/raeume/{code}/dateien HTTP/1.1\r\nHost: x\r\nX-Agora: 1\r\nX-Teilnahme: {z["teilnahme"]}\r\n'
                  f'X-Dateiname: a.pdf\r\nCF-Connecting-IP: {a.ip}\r\nContent-Length: {30 * 1024 ** 2}\r\n\r\nxx'.encode())
        try:
            antwort = s.recv(100)
        except socket.timeout:
            antwort = b'(keine Antwort)'
    finally:
        s.close()
    pruefen('Datei über 25 MB (laut Content-Length) sofort 413', antwort.startswith(b'HTTP/1.1 413'), antwort[:60])
    r = a('POST', '/api/aufnahmen', body=b'<html><script>alert(1)</script>', kopf={'Content-Type': 'text/html'})
    pruefen('Aufnahme als text/html → 415', r.status == 415, r)
    # unauthentifizierter Upload
    r = Sitzung()('POST', '/api/aufnahmen', body=b'x' * 100, kopf={'Content-Type': 'video/webm'})
    pruefen('Aufnahme hochladen ohne Anmeldung → 401', r.status == 401, r)


# ================================================================= 10. Brute Force / Begrenzer
def brute():
    s = konto_anlegen('Brute')
    ip = zufalls_ip()
    st = [Sitzung(ip=ip).post('/api/konto/anmelden', {'email': s.email, 'passwort': f'falsch-{i}-xxxx'}).status for i in range(11)]
    pruefen('Login: 11. Fehlversuch derselben IP → 429', st[-1] == 429, st)
    # verteilte Versuche (wechselnde IPv4) gegen dasselbe Konto
    st = [Sitzung().post('/api/konto/anmelden', {'email': s.email, 'passwort': f'falsch-{i}-yyyy'}).status for i in range(40)]
    pruefen('Login: 40 Versuche von wechselnden IPs gegen ein Konto → irgendwann 429', 429 in st, st[-5:])
    # IPv6: eine /64 hat 2^64 Adressen – der Begrenzer muss sie zusammenfassen
    t = konto_anlegen('Brute6')
    netz = f'2001:db8:{secrets.randbelow(65535):x}:{secrets.randbelow(65535):x}'
    st = [Sitzung(ip=f'{netz}:{secrets.randbelow(65535):x}:{secrets.randbelow(65535):x}:{secrets.randbelow(65535):x}:{i:x}')
          .post('/api/konto/anmelden', {'email': t.email, 'passwort': f'falsch-{i}-zzzz'}).status for i in range(12)]
    pruefen('Login: 12 Versuche aus einer IPv6-/64 (wechselnde Adressen) → 429', st[-1] == 429, st)
    # Passcode
    a = konto_anlegen('Passcode')
    code = raum(a, passcode='1234')['code']
    g = Sitzung()
    st = [g.post(f'/api/raeume/{code}/beitreten', {'name': 'x', 'passcode': f'{i:04d}'}).status for i in range(5000, 5011)]
    pruefen('Passcode: 11. Fehlversuch → 429', st[-1] == 429, st)
    # 2FA
    z = konto_anlegen('Zweifaktor')
    geheim = z.post('/api/konto/2fa/einrichten', {'passwort': z.pw}).json()['geheim']
    r = z.post('/api/konto/2fa/bestaetigen', {'code': totp(geheim)})
    assert r.status == 200, r
    schritt = Sitzung().post('/api/konto/anmelden', {'email': z.email, 'passwort': z.pw}).json()
    pruefen('2FA: Passwort allein gibt keine Sitzung', schritt.get('zwei_faktor') is True and 'ich' not in schritt, schritt)
    x = Sitzung()
    st = [x.post('/api/konto/anmelden/code', {'schritt': schritt['schritt'], 'code': f'{i:06d}'}).status for i in range(5)]
    richtig = x.post('/api/konto/anmelden/code', {'schritt': schritt['schritt'], 'code': totp(geheim)})
    pruefen('2FA: nach 5 falschen Codes ist der Schritt verbraucht', richtig.status == 400 and not x.cookie, (st, richtig))
    for name, wert in (('erfundener Schritt', secrets.token_urlsafe(32)), ('Schritt als Zahl', 12345), ('Schritt als Liste', ['x'])):
        r = Sitzung().post('/api/konto/anmelden/code', {'schritt': wert, 'code': totp(geheim)})
        pruefen(f'2FA: {name} → 400', r.status == 400, r)
    r = z.post('/api/konto/2fa/ausschalten', {'passwort': z.pw, 'code': '000000'})
    pruefen('2FA ausschalten ohne gültigen Code → 400', r.status == 400, r)
    # API-Schlüssel raten
    ip = zufalls_ip()
    st = [roh('GET', '/api/v1/meetings', kopf={'Authorization': f'Bearer agora_{secrets.token_urlsafe(32)}', 'CF-Connecting-IP': ip}).status
          for _ in range(31)]
    pruefen('API-Schlüssel raten: 31. Versuch → 429', st[-1] == 429, st[-3:])
    # Passwort-Reset-Token raten
    ip = zufalls_ip()
    st = [Sitzung(ip=ip).post('/api/konto/passwort-setzen', {'token': secrets.token_urlsafe(32), 'passwort': 'neues-passwort-1'}).status
          for _ in range(21)]
    pruefen('Reset-Token raten: 21. Versuch → 429', st[-1] == 429, st[-3:])
    r = Sitzung().get('/api/konto/bestaetigen?t=' + secrets.token_urlsafe(32))
    pruefen('erfundener Bestätigungslink → keine Sitzung', r.status == 303 and 'bestaetigt=0' in r.k('location') and 'set-cookie' not in r.kopf, r)
    # Freigabe-Passwort raten
    rec = a('POST', '/api/aufnahmen', body=b'\x1aE\xdf\xa3' + b'\0' * 100, kopf={'Content-Type': 'video/webm'}).json()['aufnahme']['id']
    link = a.post(f'/api/aufnahmen/{rec}/freigabe', {'tage': 1, 'passwort': 'richtig-1'}).json()['link']
    tok = link.rsplit('/', 1)[-1]
    ip = zufalls_ip()
    st = [roh('GET', f'/api/freigaben/{tok}/datei?p=falsch{i}', kopf={'CF-Connecting-IP': ip}).status for i in range(21)]
    pruefen('Freigabe-Passwort raten: 21. Versuch → 429', st[-1] == 429, st[-3:])
    # Warteraum-Anfrage mit falschem Geheimnis
    w = raum(a, warteraum=True)['code']
    an = Sitzung().post(f'/api/raeume/{w}/beitreten', {'name': 'Wartend'}).json()
    r = Sitzung().get(f'/api/raeume/{w}/warten/{an["anfrage"]}?g=falsch')
    pruefen('Warteraum: fremde Anfrage ohne Geheimnis → 404', r.status == 404, r)


# ================================================================= 11. SQL-Injection, Admin-Suche
def sqli():
    for body in ({'email': "' OR '1'='1' --@x.de", 'passwort': 'x'}, {'email': 'a@b.de', 'passwort': "' OR 1=1 --"},
                 {'email': ["a"], 'passwort': {'$ne': 1}}):
        r = Sitzung().post('/api/konto/anmelden', body)
        pruefen(f'Login mit SQL-Nutzlast {str(body)[:40]} → 400', r.status == 400, r)
    for p in ("/api/raeume/abc-defg-hij' OR '1'='1", '/api/raeume/abc-defg-hij%27%20OR%201=1--', '/api/raeume/%27%3B%20DROP%20TABLE%20raeume--'):
        r = Sitzung().get(p.replace(' ', '%20').replace("'", '%27'))
        pruefen(f'Raum-Code mit SQL-Nutzlast → 404', r.status == 404, r)
    s = konto_anlegen()
    tok = s.post('/api/konto/api-schluessel', {'name': 'q', 'rechte': ['meetings:lesen'], 'gueltig_tage': 30}).json()['token']
    r = roh('GET', '/api/v1/meetings?art=' + urllib.parse.quote("sofort' OR '1'='1"), kopf={'Authorization': 'Bearer ' + tok})
    pruefen('API v1 art mit SQL-Nutzlast → 400', r.status == 400, r)
    r = s.patch('/api/konto/ich', {'name': "x', email='boese@example.org", "zeitzone = 'x' --": 'y'})
    pruefen('PATCH ich: fremde Felder/Spalten werden ignoriert', r.status == 200 and r.json()['ich']['email'] == s.email, r)
    # Admin (host@example.com aus AGORA_ADMIN_MAILS) sucht mit Nutzlast – darf nicht alle finden
    try:
        adm = konto_anlegen('Admin', email='host@example.com', pw='Admin-Passwort-1')
    except AssertionError:
        adm = Sitzung()
        adm.post('/api/konto/anmelden', {'email': 'host@example.com', 'passwort': 'Admin-Passwort-1'})
    if adm.cookie:
        for q in ("' OR '1'='1", '%', '_', "\\' OR 1=1 --"):
            r = adm.get('/api/admin/benutzer?q=' + urllib.parse.quote(q))
            pruefen(f'Admin-Suche {q!r}: findet nicht alle Konten', r.status == 200 and r.json()['gesamt'] == 0, (r.status, (r.json() or {}).get('gesamt')))
        r = adm.get('/api/admin/benutzer?seite=' + urllib.parse.quote('1; DROP TABLE benutzer'))
        pruefen('Admin-Seite mit Nutzlast → 200 ohne Fehler', r.status == 200, r)
    return adm


# ================================================================= 12. Einbetten
def einbetten():
    a = konto_anlegen('Einbetten')
    code = raum(a)['code']
    r = roh('GET', f'/e/{code}')
    pruefen('/e/<code> ohne Freigabe → 404', r.status == 404, r)
    for h in ("https://a.example; script-src *", 'https://*.example', 'javascript://x', 'https://a.example/pfad', 'http://a.example',
              "https://a.example 'unsafe-inline'", 'https://a.example\nX-Frame-Options: ALLOW', 'https://user@a.example', 'data:text/html,x'):
        x = a.put(f'/api/raeume/{code}/einbetten', {'erlaubt': True, 'herkuenfte': [h]})
        pruefen(f'Einbetten: Herkunft {h!r} abgelehnt', x.status == 400, x)
    a.put(f'/api/raeume/{code}/einbetten', {'erlaubt': True, 'herkuenfte': ['https://partner.example']})
    r = roh('GET', f'/e/{code}')
    csp = r.k('content-security-policy')
    pruefen('/e/<code>: frame-ancestors nur die erlaubte Herkunft', 'frame-ancestors https://partner.example' in csp and not r.k('x-frame-options'), csp)
    r = roh('GET', f'/r/{code}')
    pruefen('/r/<code> bleibt nicht einbettbar', r.k('x-frame-options') == 'DENY' and "frame-ancestors 'none'" in r.k('content-security-policy'))


# ================================================================= 13. Browser: Veröffentlichen mit fremden Rechten, XSS, Umleitung
XSS_KURZ = '<img src=x onerror=window.__xss=1>'          # passt in 60 Zeichen (Namen)
XSS = ('"><img src=x onerror="window.__xss=1"><svg/onload=window.__xss=1><script>window.__xss=1</script>'
       '<a href="javascript:window.__xss=1">j</a><iframe srcdoc="<script>parent.__xss=1</script>"></iframe>')
XSS_MD = ('**' + XSS_KURZ + '** `<svg onload=window.__xss=1>`\n- [ ] <b onmouseover=window.__xss=1>x</b>\n'
          '[link](javascript:window.__xss=1) https://x.example/"><img src=x onerror=window.__xss=1>\n# <script>window.__xss=1</script>')


def browser(ctx, adm):
    from playwright.sync_api import sync_playwright
    lk_js = open(LK_JS, encoding='utf-8').read()
    with sync_playwright() as p:
        br = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'])
        try:
            BEREICH[0] = 'livekit'
            veroeffentlichen(br, ctx, lk_js)
            BEREICH[0] = 'xss'
            xss(br, lk_js, adm)
            BEREICH[0] = 'umleitung'
            umleitung(br)
            BEREICH[0] = 'fernsteuerung'
            fernsteuerung(br, lk_js)
        finally:
            br.close()


def lk_seite(br, lk_js):
    c = br.new_context(bypass_csp=True, permissions=['camera', 'microphone'])
    s = c.new_page()
    s.goto(B + '/offline.html')
    s.add_script_tag(content=lk_js)
    return c, s


LK_VERBINDEN = """async ({url, token}) => {
  const L = window.LivekitClient;
  const room = new L.Room();
  window.__raum = room;
  await room.connect(url, token);
  const res = {};
  const versuch = async (name, f) => { try { await Promise.race([f(), new Promise((_, n) => setTimeout(() => n(new Error('Zeitüberschreitung')), 6000))]); res[name] = 'ok'; } catch (e) { res[name] = 'fehler: ' + (e && e.message || e); } };
  await versuch('kamera', async () => { const s = await navigator.mediaDevices.getUserMedia({video: true}); await room.localParticipant.publishTrack(s.getVideoTracks()[0], {source: L.Track.Source.Camera}); });
  await versuch('mikrofon', async () => { const s = await navigator.mediaDevices.getUserMedia({audio: true}); await room.localParticipant.publishTrack(s.getAudioTracks()[0], {source: L.Track.Source.Microphone}); });
  await versuch('bildschirm', async () => { const c = document.createElement('canvas'); c.width = 320; c.height = 240; const x = c.getContext('2d'); setInterval(() => { x.fillStyle = '#' + Math.floor(Math.random() * 16777215).toString(16).padStart(6, '0'); x.fillRect(0, 0, 320, 240); }, 100); await room.localParticipant.publishTrack(c.captureStream(10).getVideoTracks()[0], {source: L.Track.Source.ScreenShare}); });
  await versuch('name', () => room.localParticipant.setName('Host (offiziell)'));
  await versuch('attribute', () => room.localParticipant.setAttributes({rolle: 'host', farbe: '#000000'}));
  await versuch('metadaten', () => room.localParticipant.setMetadata('{"rolle":"host"}'));
  return res;
}"""


def veroeffentlichen(br, ctx, lk_js):
    """Mit dem echten LiveKit-Client und dem ausgegebenen Token versuchen, mehr zu senden als erlaubt.
    Was wirklich ankam, sagt der Medienserver selbst (ListParticipants), nicht der Client."""
    faelle = (('Zuschauer (Webinar)', ctx['zuschauer'], ctx['webinar'], set()),
              ('Gast ohne Bildschirm-Recht', ctx['gast'], ctx['code'], {'CAMERA', 'MICROPHONE'}))
    for name, z, raumname, erlaubt in faelle:
        c, s = lk_seite(br, lk_js)
        try:
            versuche = s.evaluate(LK_VERBINDEN, {'url': LK_URL, 'token': z['token']})
            time.sleep(1.5)
            ich = [x for x in lk_teilnehmer(raumname) if x['identity'] == z['identity']]
            assert ich, f'{name} nicht verbunden: {versuche}'
            ich = ich[0]
            quellen = {t.get('source', 'CAMERA') for t in ich.get('tracks', [])}
            pruefen(f'{name}: sendet nur erlaubte Quellen {sorted(erlaubt) or "(keine)"}', quellen <= erlaubt, (quellen, versuche))
            pruefen(f'{name}: kann den eigenen Namen nicht selbst ändern', ich.get('name') == z['name'], (ich.get('name'), versuche.get('name')))
            attr = ich.get('attributes', {})
            pruefen(f'{name}: kann Attribut rolle nicht fälschen', attr.get('rolle') == z['rolle'] and attr.get('farbe') != '#000000',
                    (attr, versuche.get('attribute')))
            pruefen(f'{name}: kann Metadaten nicht setzen', not ich.get('metadata'), (ich.get('metadata'), versuche.get('metadaten')))
            r = Sitzung().post(f'/api/raeume/{raumname}/hand', {'an': True}, kopf=T(z))
            time.sleep(0.8)
            ich = [x for x in lk_teilnehmer(raumname) if x['identity'] == z['identity']][0]
            pruefen(f'{name}: Hand heben über den Server kommt an (Gegenprobe)', r.status == 200 and ich.get('attributes', {}).get('hand') == '1',
                    (r, ich.get('attributes')))
            s.evaluate('() => window.__raum.disconnect()')
        finally:
            c.close()


def xss(br, lk_js, adm):
    """Gespeicherte Nutzlasten überall, wo Nutzertext landet – dann die Seiten rendern (CSP absichtlich aus, damit nur
    das Escaping zählt). Erfolg wäre window.__xss oder ein Dialog."""
    a = konto_anlegen(XSS_KURZ[:60])
    g_konto = konto_anlegen('Gast ' + XSS_KURZ[:50])
    r = raum(a, titel=XSS[:120], chat_speichern=True, geplant={'start': '2030-01-01T10:00', 'zeitzone': 'Europe/Berlin',
                                                                'beschreibung': XSS_MD[:2000], 'dauer_min': 30})
    code = r['code']
    a.post('/api/vorlagen', {'name': XSS_KURZ, 'einstellungen': {'titel': XSS_KURZ, 'beschreibung': XSS}})
    a('POST', '/api/aufnahmen', body=b'\x1aE\xdf\xa3' + b'\0' * 100, kopf={'Content-Type': 'video/webm', 'X-Titel': urllib.parse.quote(XSS)})
    a.post('/api/konto/api-schluessel', {'name': XSS_KURZ, 'rechte': ['webhooks'], 'gueltig_tage': 30})
    # Webinar-Registrierung mit Nutzlasten (Besitzer sieht die Liste)
    w = raum(a, titel=XSS[:120])['code']
    a.patch(f'/api/raeume/{w}/webinar', {'an': True, 'registrierung': True, 'fragen': [XSS[:200]]})
    Sitzung().post(f'/api/webinare/{w}/anmelden', {'name': XSS_KURZ, 'email': f'x{secrets.token_hex(3)}@example.org', 'antworten': [XSS]})
    # Im Meeting: zweite Host-Sitzung per API (wird Co-Host, sobald der Browser-Host beitritt)
    zcoh = zugang(a, code, name=XSS_KURZ)
    zg = zugang(g_konto, code, name=XSS_KURZ)
    T_g, T_c = T(zg), T(zcoh)
    dialoge = []
    c_host = br.new_context(permissions=['camera', 'microphone'], bypass_csp=True, viewport={'width': 1400, 'height': 900})
    c_host.add_cookies([{'name': COOKIE, 'value': a.cookie, 'url': B}])
    hp = c_host.new_page()
    hp.on('dialog', lambda d: (dialoge.append(d.message), d.dismiss()))
    c_g, gp = lk_seite(br, lk_js)
    try:
        # Konto-Seite (Raumliste, Vorlagen, Aufnahmen, Entwickler, Webinar-Anmeldungen)
        hp.goto(B + '/konto')
        hp.wait_for_selector('#liste-geplant li .name', timeout=15000)
        for tab in ('#tab-aufnahmen', '#tab-profil', '#tab-entwickler', '#tab-meetings'):
            hp.click(tab)
            time.sleep(0.4)
        sichtbar = 'onerror' in hp.inner_text('body')
        pruefen('Konto-Seite: Nutzlasten als Text sichtbar, kein Skript', sichtbar and not hp.evaluate('window.__xss') and not dialoge,
                (sichtbar, hp.evaluate('window.__xss'), dialoge))
        # Meeting: Gast verbindet sich (erscheint in der Liste), Chat, Q&A, Dateiname, Notizen, Umfrage
        gp.evaluate("""async ({url, token}) => { const r = new window.LivekitClient.Room(); window.__raum = r; await r.connect(url, token); }""",
                    {'url': LK_URL, 'token': zg['token']})
        hp.goto(f'{B}/r/{code}')
        hp.wait_for_selector('#v-lobby:not([hidden])', timeout=15000)
        hp.click('#beitreten-knopf')
        hp.wait_for_selector('#v-meeting:not([hidden])', timeout=20000)
        g = Sitzung()
        g.post(f'/api/raeume/{code}/chat', {'text': XSS}, kopf=T_g)
        g.post(f'/api/raeume/{code}/chat', {'text': XSS_MD}, kopf=T_c)
        g.post(f'/api/raeume/{code}/fragen', {'text': XSS}, kopf=T_g)
        g('POST', f'/api/raeume/{code}/dateien', body=b'%PDF-1.4', kopf={**T_g, 'X-Dateiname': urllib.parse.quote(XSS[:140] + '.pdf')})
        g.put(f'/api/raeume/{code}/notizen', {'text': XSS_MD, 'version': 0}, kopf=T_g)
        u = g.post(f'/api/raeume/{code}/umfragen', {'frage': XSS[:300], 'optionen': [XSS_KURZ, XSS[:200]]}, kopf=T_c)
        if u.status == 200:
            g.post(f'/api/raeume/{code}/umfragen/{u.json()["umfrage"]["id"]}/starten', kopf=T_c)
        g.post(f'/api/raeume/{code}/whiteboard/ops', {'ops': [{'art': 'neu', 'objekt': {'id': 'x' * 12, 'art': 'text', 'farbe': '#ffffff', 'breite': 2,
                                                                                        'x': 10, 'y': 10, 'text': XSS}}]}, kopf=T_g)
        g.post(f'/api/raeume/{code}/untertitel', {'text': XSS[:500], 'final': True}, kopf=T_g)
        time.sleep(2)
        for tab in ('chat', 'leute', 'umfragen', 'fragen', 'dateien', 'notizen', 'chat'):
            hp.evaluate(f"() => document.getElementById('tab-{tab}')?.click()")
            time.sleep(0.6)
            if tab == 'notizen':
                hp.evaluate("() => document.querySelector('.notizen-fuss .btn')?.click()")
                time.sleep(0.4)
        text = hp.inner_text('body')
        pruefen('Meeting: Nutzlasten in Chat/Teilnehmern/Q&A/Dateien/Notizen/Umfrage als Text, kein Skript',
                'onerror' in text and not hp.evaluate('window.__xss') and not dialoge, (text.count('onerror'), hp.evaluate('window.__xss'), dialoge))
        links = hp.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(h => /^\\s*(javascript|data|vbscript):/i.test(h))")
        pruefen('Meeting: keine javascript:/data:-Links', not links, links)
        # Gast meldet (Admin sieht die Meldung), dann Meeting beenden → Verlauf mit gespeichertem Chat
        g.post(f'/api/raeume/{code}/melden', {'ziel': zcoh['identity'], 'grund': 'sonstiges', 'text': XSS}, kopf=T_g)
        hp.goto(B + '/konto')
        hp.wait_for_selector('#liste-geplant li .name', timeout=15000)
        for knopf in hp.query_selector_all('[data-verlauf], .verlauf button'):
            try:
                knopf.click(timeout=2000)
                time.sleep(0.5)
            except Exception:   # noqa: BLE001
                pass
        pruefen('Konto nach dem Meeting: kein Skript', not hp.evaluate('window.__xss') and not dialoge, dialoge)
        # Fehler-Monitoring: Nutzlast als Fehlermeldung, Stack und Seite (ohne Anmeldung gemeldet, Admin sieht sie)
        Sitzung().post('/api/fehlerbericht', {'art': 'fehler', 'nachricht': XSS[:400], 'stapel': XSS_MD[:2000], 'seite': '/' + XSS_KURZ})
        # Admin-Ansicht (Meldungen, Konten, Protokoll, Meetings, Fehler)
        if adm and adm.cookie:
            c_adm = br.new_context(bypass_csp=True)
            c_adm.add_cookies([{'name': COOKIE, 'value': adm.cookie, 'url': B}])
            ap = c_adm.new_page()
            ap.on('dialog', lambda d: (dialoge.append(d.message), d.dismiss()))
            ap.goto(B + '/admin')
            ap.wait_for_selector('#tab-meldungen', timeout=15000)
            for tab in ('#tab-meldungen', '#tab-konten', '#tab-meetings', '#tab-protokoll', '#tab-uebersicht'):
                ap.click(tab)
                time.sleep(0.6)
            ap.click('#tab-fehler')
            ap.wait_for_selector('#liste-fehler li[data-fehler]', timeout=10000)
            for knopf in ap.query_selector_all('#liste-fehler [data-aktion=details]')[:5]:
                knopf.click()
                time.sleep(0.5)
                ap.keyboard.press('Escape')
                time.sleep(0.2)
            text = ap.inner_text('#p-fehler')
            pruefen('Admin „Fehler“: Nutzlast aus Fehlerbericht als Text, kein Skript', 'onerror' in text and not ap.evaluate('window.__xss') and not dialoge,
                    (text.count('onerror'), ap.evaluate('window.__xss'), dialoge))
            ap.click('#tab-meldungen')
            time.sleep(0.6)
            text = ap.inner_text('body')
            pruefen('Admin: Meldung/Konten mit Nutzlast als Text, kein Skript', 'onerror' in text and not ap.evaluate('window.__xss') and not dialoge,
                    (text.count('onerror'), ap.evaluate('window.__xss'), dialoge))
            c_adm.close()
        # Öffentliche Webinar-Anmeldeseite und Lobby (anonym)
        c_anon = br.new_context(bypass_csp=True, permissions=['camera', 'microphone'])
        an = c_anon.new_page()
        an.on('dialog', lambda d: (dialoge.append(d.message), d.dismiss()))
        an.goto(f'{B}/w/{w}')
        time.sleep(2)
        an.goto(f'{B}/r/{code}')
        an.wait_for_selector('#v-lobby:not([hidden])', timeout=15000)
        time.sleep(1)
        pruefen('Webinar-Anmeldeseite und Lobby (anonym): kein Skript', not an.evaluate('window.__xss') and not dialoge, dialoge)
        c_anon.close()
    finally:
        try:
            gp.evaluate('() => window.__raum && window.__raum.disconnect()')
        except Exception:   # noqa: BLE001
            pass
        c_g.close()
        c_host.close()


def umleitung(br):
    s = konto_anlegen('Umleitung')
    c = br.new_context()
    c.add_cookies([{'name': COOKIE, 'value': s.cookie, 'url': B}])
    p = c.new_page()
    try:
        for ziel in ('//boese.example/x', '/\\boese.example', 'https://boese.example', 'javascript:alert(1)', '%2F%2Fboese.example',
                     '/%2F%2Fboese.example', '\\\\boese.example', 'http:boese.example', '/ /boese.example', '//boese.example@127.0.0.1'):
            p.goto(f'{B}/?weiter={urllib.parse.quote(ziel, safe="")}')
            time.sleep(0.8)
            host = urllib.parse.urlsplit(p.url).hostname
            pruefen(f'?weiter={ziel} bleibt auf der eigenen Seite', host == HOST, p.url)
        r = Sitzung().get('/api/konto/bestaetigen?t=x&weiter=https://boese.example')
        pruefen('Bestätigungslink leitet nur intern weiter', r.k('location').startswith('/'), r.k('location'))
    finally:
        c.close()


# ================================================================= 13. Organisationen (Autorisierung über Organisationen hinweg)
def orgs():
    """Alice (owner von A), Mia (mitglied von A), Bob (owner von B): Bob kommt an nichts von A heran – weder über A's
    Kurznamen noch über seine eigene Organisation mit A's IDs; Mia darf in A nur lesen."""
    alice, mia, bob = konto_anlegen('Alice Org'), konto_anlegen('Mia Org'), konto_anlegen('Bob Org')
    a = alice.post('/api/orgs', {'name': 'Angriff A ' + secrets.token_hex(3)}).json()['org']['slug']
    b = bob.post('/api/orgs', {'name': 'Angriff B ' + secrets.token_hex(3)}).json()['org']['slug']
    alice.post(f'/api/orgs/{a}/einladungen', {'emails': [mia.email]})
    eid = mia.get('/api/orgs').json()['einladungen'][0]['id']
    pruefen('Gegenprobe: Mia nimmt ihre Einladung an', mia.post(f'/api/org-einladungen/{eid}/annehmen').status == 200)
    code = alice.post(f'/api/orgs/{a}/raeume', {'titel': 'Vertraulich A', 'chat_speichern': True}).json()['raum']['code']
    vid = alice.post(f'/api/orgs/{a}/vorlagen', {'name': 'Vorlage A'}).json()['vorlage']['id']
    rec = alice('POST', '/api/aufnahmen', body=b'\x1aE\xdf\xa3' + b'\0' * 1000, kopf={'Content-Type': 'video/webm', 'X-Raum': code})
    aid = rec.json()['aufnahme']['id'] if rec.status == 200 else 'A' * 22
    pruefen('Gegenprobe: Aufnahme landet in der Bibliothek von A', rec.status == 200 and
            [x['id'] for x in mia.get(f'/api/orgs/{a}/aufnahmen').json()['aufnahmen']] == [aid])
    alice.post(f'/api/orgs/{a}/einladungen', {'emails': ['opfer@example.org']})
    offen = alice.get(f'/api/orgs/{a}/einladungen').json()['einladungen'][0]['id']
    mia_id, alice_id = mia.get('/api/konto/ich').json()['ich']['id'], alice.id

    # Bob (fremde Organisation): alles unter /api/orgs/<A> → 404, A's Raum nicht verwaltbar
    versuche = [('GET', f'/api/orgs/{a}', None), ('PATCH', f'/api/orgs/{a}', {'name': 'x'}), ('DELETE', f'/api/orgs/{a}', {'bestaetigung': a}),
                ('GET', f'/api/orgs/{a}/raeume', None), ('POST', f'/api/orgs/{a}/raeume', {'titel': 'x'}),
                ('GET', f'/api/orgs/{a}/vorlagen', None), ('PUT', f'/api/orgs/{a}/vorlagen/{vid}', {'name': 'x'}),
                ('POST', f'/api/orgs/{a}/vorlagen/{vid}/raum', {'ziel': 'konto'}), ('GET', f'/api/orgs/{a}/aufnahmen', None),
                ('GET', f'/api/orgs/{a}/aufnahmen/{aid}/datei', None), ('DELETE', f'/api/orgs/{a}/aufnahmen/{aid}', None),
                ('GET', f'/api/orgs/{a}/einladungen', None), ('POST', f'/api/orgs/{a}/einladungen', {'emails': ['x@example.org']}),
                ('DELETE', f'/api/orgs/{a}/einladungen/{offen}', None), ('PATCH', f'/api/orgs/{a}/mitglieder/{bob.id}', {'rolle': 'owner'}),
                ('DELETE', f'/api/orgs/{a}/mitglieder/{mia_id}', None), ('POST', f'/api/orgs/{a}/beitreten', None),
                ('POST', f'/api/orgs/{a}/verlassen', None),
                # A's IDs über Bobs eigene Organisation
                ('PUT', f'/api/orgs/{b}/vorlagen/{vid}', {'name': 'x'}), ('DELETE', f'/api/orgs/{b}/vorlagen/{vid}', None),
                ('POST', f'/api/orgs/{b}/vorlagen/{vid}/raum', {}), ('GET', f'/api/orgs/{b}/aufnahmen/{aid}/datei', None),
                ('DELETE', f'/api/orgs/{b}/aufnahmen/{aid}', None), ('DELETE', f'/api/orgs/{b}/einladungen/{offen}', None),
                ('PATCH', f'/api/orgs/{b}/mitglieder/{mia_id}', {'rolle': 'mitglied'}), ('DELETE', f'/api/orgs/{b}/mitglieder/{alice_id}', None),
                ('POST', f'/api/orgs/{b}/vorlagen', {'name': 'x', 'aus_raum': code}),
                # A's Raum direkt
                ('PATCH', f'/api/raeume/{code}', {'titel': 'übernommen'}), ('DELETE', f'/api/raeume/{code}', None),
                ('GET', f'/api/raeume/{code}/verlauf', None), ('POST', f'/api/raeume/{code}/einladen', {'emails': ['x@example.org']}),
                ('PUT', f'/api/raeume/{code}/einbetten', {'erlaubt': True, 'herkuenfte': ['https://b.example']}),
                ('GET', f'/api/aufnahmen/{aid}/datei', None), ('POST', '/api/vorlagen', {'name': 'x', 'aus_raum': code}),
                ('POST', f'/api/org-einladungen/{offen}/annehmen', None), ('POST', f'/api/org-einladungen/{offen}/ablehnen', None)]
    for m, p, j in versuche:
        r = bob(m, p, j)
        pruefen(f'Bob (Org B) → {m} {p.replace(a, "<A>").replace(b, "<B>").replace(code, "<raum>").replace(aid, "<aufn>")}', abgelehnt(r), r)
    pruefen('Bob ist im Raum von A kein Host', not bob.get(f'/api/raeume/{code}').json()['raum']['ist_host'])
    r = bob.post('/api/konto/api-schluessel', {'name': 'x', 'rechte': ['meetings:lesen'], 'gueltig_tage': 30, 'org': a})
    pruefen('Bob: API-Schlüssel für Organisation A → 404', r.status == 404, r)
    tok_b = bob.post('/api/konto/api-schluessel', {'name': 'b', 'rechte': ['meetings:lesen', 'meetings:schreiben', 'aufnahmen:lesen'],
                                                   'gueltig_tage': 30, 'org': b}).json()['token']
    for m, p in (('GET', f'/api/v1/meetings/{code}'), ('PATCH', f'/api/v1/meetings/{code}'), ('DELETE', f'/api/v1/meetings/{code}'),
                 ('GET', f'/api/v1/aufnahmen/{aid}'), ('GET', f'/api/v1/aufnahmen/{aid}/download')):
        r = roh(m, p, b'{}' if m == 'PATCH' else None, {'Authorization': 'Bearer ' + tok_b, 'Content-Type': 'application/json',
                                                         'CF-Connecting-IP': zufalls_ip()})
        pruefen(f'API v1 (Schlüssel von Org B) → {m} {p.replace(code, "<A>").replace(aid, "<aufn>")}', r.status in (403, 404), r)
    r = roh('GET', '/api/v1/meetings', kopf={'Authorization': 'Bearer ' + tok_b})
    pruefen('API v1 (Org B): Liste ohne Meetings von A', r.status == 200 and all(x['code'] != code for x in r.json()['meetings']), r)
    r = bob.post('/api/konto/api-schluessel', {'name': 'w', 'rechte': ['webhooks'], 'gueltig_tage': 30, 'org': b})
    pruefen('Schlüssel einer Organisation ohne Webhooks', r.status == 400, r)

    # Mia (mitglied von A): lesen ja, verwalten nein
    pruefen('Gegenprobe: Mia sieht die Meetings von A', [x['code'] for x in mia.get(f'/api/orgs/{a}/raeume').json()['geplant']] == [code])
    for m, p, j in (('PATCH', f'/api/orgs/{a}', {'einstellungen': {'warteraum_extern': False}}), ('DELETE', f'/api/orgs/{a}', {'bestaetigung': a}),
                    ('POST', f'/api/orgs/{a}/raeume', {'titel': 'x'}), ('POST', f'/api/orgs/{a}/einladungen', {'emails': ['x@example.org']}),
                    ('GET', f'/api/orgs/{a}/einladungen', None), ('PATCH', f'/api/orgs/{a}/mitglieder/{mia_id}', {'rolle': 'owner'}),
                    ('DELETE', f'/api/orgs/{a}/mitglieder/{alice_id}', None), ('DELETE', f'/api/orgs/{a}/aufnahmen/{aid}', None),
                    ('POST', f'/api/orgs/{a}/vorlagen', {'name': 'x'}), ('DELETE', f'/api/orgs/{a}/vorlagen/{vid}', None),
                    ('POST', f'/api/orgs/{a}/vorlagen/{vid}/raum', {'ziel': 'org'}), ('PATCH', f'/api/raeume/{code}', {'titel': 'x'}),
                    ('DELETE', f'/api/raeume/{code}', None), ('GET', f'/api/raeume/{code}/verlauf', None),
                    ('POST', '/api/konto/api-schluessel', {'name': 'x', 'rechte': ['meetings:lesen'], 'gueltig_tage': 30, 'org': a})):
        r = mia(m, p, j)
        pruefen(f'Mia (mitglied) → {m} {p.replace(a, "<A>").replace(code, "<raum>").replace(aid, "<aufn>")}', abgelehnt(r), r)
    pruefen('Mia: E-Mail-Adressen anderer Mitglieder verborgen', all('email' not in x for x in mia.get(f'/api/orgs/{a}').json()['org']['mitglieder']))
    # Einladungs-Link einer anderen Adresse und erfundene Token
    for t in ('A' * 43, '../' * 10, ''):
        r = bob.post('/api/org-einladungen/link', {'token': t})
        pruefen(f'Einladungs-Link mit erfundenem Token {t[:8]!r} → abgelehnt', r.status in (400, 403, 404, 429), r)
    # Domain: fremde Domain lässt sich nicht beanspruchen (sonst „Selbst-Beitritt“ für fremde Firmen)
    for dom in ('slxt.dev', 'gmail.com', 'example.org.evil.test'):
        r = bob.patch(f'/api/orgs/{b}', {'einstellungen': {'domain': dom}})
        pruefen(f'Domain {dom} ohne passende eigene Adresse → 400', r.status == 400, r)
    pruefen('Gegenprobe: Alice verwaltet weiter', alice.patch(f'/api/raeume/{code}', {'titel': 'Bleibt'}).status == 200)


# ================================================================= 14. Status-Seite (öffentlich, ohne Interna)
def status_seite():
    r = Sitzung().get('/api/status')
    pruefen('/api/status ohne Anmeldung erreichbar', r.status == 200, r)
    text = r.text.lower()
    for x in ('livekit', '127.0.0.1', 'localhost', 'smtp', 'version', 'sqlite', ':78', 'host'):
        pruefen(f'/api/status enthält nicht {x!r}', x not in text, text[:200])
    s = konto_anlegen('Kein Admin')
    for m, p, j in (('GET', '/api/admin/status/meldungen', None), ('POST', '/api/admin/status/meldungen', {'titel': 'Fake', 'art': 'stoerung'}),
                    ('PATCH', '/api/admin/status/meldungen/1', {'titel': 'x'}), ('DELETE', '/api/admin/status/meldungen/1', None)):
        r = s(m, p, j)
        pruefen(f'Nicht-Admin → {m} {p} → 404', r.status == 404, r)
        r = Sitzung()(m, p, j)
        pruefen(f'Abgemeldet → {m} {p} → 404', r.status == 404, r)
    c = Sitzung()
    stati = [c.get('/api/status').status for _ in range(125)]
    pruefen('/api/status begrenzt (120/min je IP)', 429 in stati, stati[-5:])


# ================================================================= Fehler-Monitoring (/api/fehlerbericht, ohne Anmeldung)
def fehlerbericht():
    gut = {'art': 'fehler', 'nachricht': 'Sicherheitstest', 'seite': '/konto', 'version': '1.0.0'}
    s = Sitzung()
    r = s.post('/api/fehlerbericht', gut)
    pruefen('Fehlerbericht ohne Anmeldung angenommen (Gegenprobe)', r.status == 200, r)
    r = s.post('/api/fehlerbericht', gut, agora=False)
    pruefen('Fehlerbericht ohne X-Agora → 403 (CSRF)', r.status == 403, r)
    for groesse in (20_000, 2_000_000):
        r = Sitzung().post('/api/fehlerbericht', {**gut, 'stapel': 'x' * groesse})
        pruefen(f'Fehlerbericht {groesse // 1000} KB → 413', r.status == 413, r)
    sock = socket.create_connection((HOST, PORT), timeout=5)
    try:
        sock.sendall(f'POST /api/fehlerbericht HTTP/1.1\r\nHost: x\r\nX-Agora: 1\r\nContent-Type: application/json\r\n'
                     f'CF-Connecting-IP: {zufalls_ip()}\r\nContent-Length: 100000000\r\n\r\n{{"art":'.encode())
        try:
            antwort = sock.recv(200)
        except socket.timeout:
            antwort = b'(keine Antwort nach 5 s)'
    finally:
        sock.close()
    pruefen('Fehlerbericht: Content-Length 100 MB sofort abgelehnt', antwort[:12] == b'HTTP/1.1 413', antwort[:60])
    for body in ({'art': 'boese'}, {'art': 'fehler', 'nachricht': {'$gt': ''}}, {'art': ['fehler']}, {'art': 'fehler', 'stapel': 7}):
        r = Sitzung().post('/api/fehlerbericht', body)
        pruefen(f'Fehlerbericht mit falschen Typen {str(body)[:40]} → 400', r.status == 400, r)
    r = Sitzung()('POST', '/api/fehlerbericht', body=b'[' * 100000, kopf={'Content-Type': 'application/json'})
    pruefen('Fehlerbericht: tief verschachteltes JSON → 4xx', 400 <= r.status < 500, r)
    # Flut von einer IP (und einer IPv6-/64 mit wechselnden Adressen): spätestens nach 30 Berichten 429
    f = Sitzung()
    stati = [f.post('/api/fehlerbericht', {**gut, 'nachricht': f'Flut {i}'}).status for i in range(40)]
    pruefen('Fehlerbericht-Flut einer IP begrenzt (≤ 30 angenommen)', stati.count(200) <= 30 and 429 in stati, stati[-5:])
    netz = f'2001:db8:{secrets.randbelow(65535):x}:{secrets.randbelow(65535):x}'
    stati = [Sitzung(ip=f'{netz}::{i + 1:x}').post('/api/fehlerbericht', {**gut, 'nachricht': f'v6 {i}'}).status for i in range(40)]
    pruefen('Fehlerbericht-Flut aus einer IPv6-/64 begrenzt', stati.count(200) <= 30 and 429 in stati, stati[-5:])
    # Fehlerberichte sind für Nicht-Admins nicht lesbar
    k = konto_anlegen('Kein Admin Fehler')
    for m, p, j in (('GET', '/api/admin/fehler', None), ('GET', '/api/admin/fehler/1', None), ('POST', '/api/admin/fehler/1', {'erledigt': True}),
                    ('DELETE', '/api/admin/fehler/1', None)):
        pruefen(f'Nicht-Admin → {m} {p} → 404', k(m, p, j).status == 404, k(m, p, j))
        pruefen(f'Abgemeldet → {m} {p} → 404', Sitzung()(m, p, j).status == 404, Sitzung()(m, p, j))
    # E2EE-Schlüssel aus dem Fragment und Personendaten landen nicht in der Gruppe (Admin liest nach)
    geheim = 'SICHERHEIT' + secrets.token_hex(8)
    Sitzung().post('/api/fehlerbericht', {'art': 'promise', 'nachricht': f'x https://h/r/abc-defg-hij#k={geheim} ada@example.org',
                                          'stapel': f'at a (/assets/a.js#k={geheim}:1:1)', 'seite': f'/r/abc-defg-hij#k={geheim}'})
    adm = Sitzung()
    adm.post('/api/konto/anmelden', {'email': 'host@example.com', 'passwort': 'Admin-Passwort-1'})
    if adm.cookie:
        alle = adm.get('/api/admin/fehler?status=alle').json() or {}
        roh_text = json.dumps([adm.get(f'/api/admin/fehler/{g["id"]}').json() for g in alle.get('gruppen', [])[:50]])
        pruefen('Fehlerbericht: #k=-Schlüssel, Meeting-Code und E-Mail nicht gespeichert',
                geheim not in roh_text and 'abc-defg-hij' not in roh_text and 'ada@example.org' not in roh_text and alle.get('gruppen'), roh_text[:200])


# ================================================================= 15. Öffentliche Übertragung (HLS): Rechte und Pfade
def hls():
    a = konto_anlegen('HLS Host')
    code = raum(a, titel='HLS')['code']
    r = a.patch(f'/api/raeume/{code}/webinar', {'an': True})
    assert r.status == 200, f'Webinar: {r}'
    z = zugang(a, code)
    g = Sitzung()
    gz = zugang(g, code, name='Zuschauer')
    assert a.patch(f'/api/raeume/{code}/webinar', {'registrierung': True}).status == 200
    r = g.post(f'/api/raeume/{code}/egress/hls', {'aktion': 'start'}, kopf=T(gz))
    pruefen('HLS: Zuschauer kann keine Übertragung starten', r.status == 403, r)
    r = Sitzung().post(f'/api/raeume/{code}/egress/hls', {'aktion': 'start'})
    pruefen('HLS: ohne Teilnahme kein Start', abgelehnt(r), r)
    r = Sitzung().post(f'/api/raeume/{code}/egress/hls', {'aktion': 'start'}, agora=False, kopf=T(z))
    pruefen('HLS: Start ohne X-Agora (CSRF) abgelehnt', r.status == 403, r)
    r = a.post(f'/api/raeume/{code}/egress/hls', {'aktion': 'start', 'zugang': 'anmeldung'}, kopf=T(z), timeout=60)   # ohne Egress-Dienst
    pruefen('HLS: Start ohne Egress-Dienst sauber abgelehnt (kein 500)', r.status in (502, 503), r)
    i = Sitzung().get(f'/api/hls/{code}')
    pruefen('HLS: Info verrät keine Quelle, solange nichts läuft', i.status == 200 and 'quelle' not in i.text and '/hls/' not in i.text, i)
    for tok in ('x' * 32, "' OR 1=1 --", '../' * 10):
        r = Sitzung().post(f'/api/hls/{code}/zugang', {'anmeldung': tok})
        pruefen(f'HLS: Zugang mit falschem Token {tok[:12]!r} → keine Quelle', abgelehnt(r) and 'quelle' not in r.text, r)
    kennung = 'A' * 22
    pfade_ = [f'/hls/{code}/{kennung}/..%2F..%2F..%2Fagora.sqlite3', f'/hls/{code}/{kennung}/%2e%2e%2f%2e%2e%2fagora.sqlite3',
              f'/hls/{code}/..%2F..%2F/index.m3u8', f'/hls/..%2F..%2F..%2Fetc/{kennung}/passwd', f'/hls/{code}/{kennung}/../../../../etc/passwd',
              f'/hls/{code}/{kennung}/720p_00000.ts%00.html', f'/hls/{code}/{kennung}/..\\..\\agora.sqlite3', '/hls/%2e%2e/%2e%2e/%2e%2e/etc/passwd',
              f'/hls/{code}/%2e%2e/%2e%2e/agora.sqlite3', '/hls/../app.env', f'/hls/{code}/{kennung}/720p.m3u8', '/h/..%2F..%2Fetc%2Fpasswd',
              f'/hls/{code}/{kennung}/index.m3u8']
    for p in pfade_:
        r = Sitzung().get(p)
        geheim = any(x in r.body for x in (b'root:x:', b'SQLite format', b'import os', b'AGORA_GEHEIM', b'#EXTM3U'))
        pruefen(f'HLS-Pfad {p}', not geheim and r.status in (400, 404) or (p.startswith('/h/') and r.status == 200 and b'<!doctype' in r.body.lower()), r)
    r = Sitzung().post(f'/api/hls/{code}/da', {'id': '../../' + 'a' * 20})
    pruefen('HLS: Zuschauer-Meldung mit Pfad-Zeichen abgelehnt', r.status == 400, r)


# ================================================================= 16. Fernsteuerung: nur die freigegebene identity
FS_SEITE = """async ({url, token, kennung, ich}) => {
  const L = window.LivekitClient, F = window.AgoraFernsteuerung;
  const room = new L.Room();
  window.__raum = room;
  window.__fs = [];
  room.on(L.RoomEvent.DataReceived, (daten, p, _k, topic, verschl) => {
    if (topic !== F.TOPIC) return;
    const g = F.freigabeAusMetadaten(room.metadata, ich);
    const z = { freigabe: { aktiv: true, art: 'bildschirm' }, zustimmung: g ? { identity: g.an, kennung } : null, freigabeServer: g, ich, e2ee: false };
    const ok = F.darfSteuern(z, p ? p.identity : undefined, !!verschl);
    window.__fs.push({ von: p ? p.identity : null, ok: ok.ok, grund: ok.grund || '', ev: F.ereignisLesen(daten) });
  });
  await room.connect(url, token);
  const c = document.createElement('canvas'); c.width = 320; c.height = 240; const x = c.getContext('2d');
  setInterval(() => { x.fillStyle = '#' + Math.floor(Math.random() * 16777215).toString(16).padStart(6, '0'); x.fillRect(0, 0, 320, 240); }, 100);
  await room.localParticipant.publishTrack(c.captureStream(10).getVideoTracks()[0], { source: L.Track.Source.ScreenShare });
  return room.localParticipant.identity;
}"""
FS_VERBINDEN = """async ({url, token}) => { const room = new window.LivekitClient.Room(); window.__r = room; await room.connect(url, token); }"""
FS_SENDEN = """async ({ziel, pakete}) => {
  for (const p of pakete) await window.__r.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(p)), { reliable: true, topic: 'fernsteuerung', destinationIdentities: [ziel] });
  await new Promise((r) => setTimeout(r, 800));
  await window.__r.disconnect();
}"""


def fs_senden(br, lk_js, s, z, ziel, pakete):
    """Verbinden, warten bis die Empfängerin (Seite s) uns als Teilnehmerin kennt – wie im Meeting –, dann senden."""
    c2, s2 = lk_seite(br, lk_js)
    try:
        s2.evaluate(FS_VERBINDEN, {'url': LK_URL, 'token': z['token']})
        s.wait_for_function('(id) => !!window.__raum.getParticipantByIdentity(id)', arg=z['identity'], timeout=15000)
        s2.evaluate(FS_SENDEN, {'ziel': ziel, 'pakete': pakete})
    finally:
        c2.close()


def fernsteuerung(br, lk_js):
    """Teilende Person P gibt A die Steuerung (über den Server). B schickt Datenpakete – auch mit gefälschtem Absender im
    Inhalt. Die Prüfregeln der Desktop-App (desktop/src/fernsteuerung.js) laufen im Browser von P gegen den Absender,
    den LiveKit selbst meldet: nur Pakete von A dürfen durch."""
    fs_js = open(FS_JS, encoding='utf-8').read()
    p_konto = konto_anlegen('Paula Präsentiert')
    code = raum(p_konto, titel='Fernsteuerung')['code']
    pz = zugang(p_konto, code)
    a, b = Sitzung(), Sitzung()
    az, bz = zugang(a, code, name='Anna Erlaubt'), zugang(b, code, name='Boris Böse')
    kennung = 'Sich_' + secrets.token_urlsafe(24)
    c, s = lk_seite(br, lk_js)
    try:
        s.add_script_tag(content=fs_js)
        s.evaluate(FS_SEITE, {'url': LK_URL, 'token': pz['token'], 'kennung': kennung, 'ich': pz['identity']})
        time.sleep(1.5)
        r = p_konto.post(f'/api/raeume/{code}/fernsteuerung', {'aktion': 'bereit', 'an': True}, kopf=T(pz))
        assert r.status == 200, f'bereit: {r}'
        # Server: B kann sich nichts selbst geben, nichts für P erteilen, Annas Freigabe nicht beenden
        r = b.post(f'/api/raeume/{code}/fernsteuerung', {'aktion': 'erteilen', 'an': bz['identity'], 'kennung': kennung}, kopf=T(bz))
        pruefen('Fernsteuerung: B kann sich nicht selbst die Steuerung erteilen', r.status == 403, r)
        r = b.post(f'/api/raeume/{code}/fernsteuerung', {'aktion': 'bereit', 'an': True}, kopf=T(bz))
        md = json.loads(lk_raum_metadaten(code) or '{}')
        pruefen('Fernsteuerung: „bereit“ ohne eigene Bildschirmspur wirkungslos', r.status == 200 and r.json().get('ok') is False
                and bz['identity'] not in md.get('fernsteuerung_bereit', []), (r, md))
        r = p_konto.post(f'/api/raeume/{code}/fernsteuerung', {'aktion': 'erteilen', 'an': az['identity'], 'kennung': kennung}, kopf=T(pz))
        assert r.status == 200, f'erteilen: {r}'
        r = b.post(f'/api/raeume/{code}/fernsteuerung', {'aktion': 'beenden', 'ziel': pz['identity']}, kopf=T(bz))
        pruefen('Fernsteuerung: B kann die Steuerung von A nicht beenden', r.status == 404, r)
        r = Sitzung().post(f'/api/raeume/{code}/fernsteuerung', {'aktion': 'erteilen', 'an': bz['identity'], 'kennung': kennung}, agora=False, kopf=T(pz))
        pruefen('Fernsteuerung: erteilen ohne X-Agora (CSRF) abgelehnt', r.status == 403, r)
        time.sleep(1.5)
        # B schickt Pakete (auch mit Annas identity im Inhalt), A schickt eins
        boese = [{'t': 'bewegen', 'x': 0.5, 'y': 0.5}, {'t': 'klick', 'x': 0.1, 'y': 0.1, 'knopf': 'left', 'absender': az['identity'], 'identity': az['identity']},
                 {'t': 'taste', 'taste': 'Enter', 'mod': [], 'von': az['identity']}]
        for z, pak in ((bz, boese), (az, [{'t': 'bewegen', 'x': 0.25, 'y': 0.75}])):
            fs_senden(br, lk_js, s, z, pz['identity'], pak)
        time.sleep(1)
        erg = s.evaluate('window.__fs')
        von_b = [e for e in erg if e['von'] == bz['identity']]
        von_a = [e for e in erg if e['von'] == az['identity']]
        pruefen('Fernsteuerung: alle Pakete von B (nicht freigegeben) verworfen – Absender kommt von LiveKit',
                len(von_b) == 3 and all(not e['ok'] and e['grund'] == 'falscher-absender' for e in von_b), erg)
        pruefen('Fernsteuerung: Paket von A (freigegeben) angenommen (Gegenprobe)', len(von_a) == 1 and von_a[0]['ok'] and von_a[0]['ev']['x'] == 0.25, erg)
        # Nach dem Beenden durch A: auch A wird verworfen
        a.post(f'/api/raeume/{code}/fernsteuerung', {'aktion': 'beenden'}, kopf=T(az))
        time.sleep(1.5)
        time.sleep(1)                       # A ist eben gegangen – P soll die alte Verbindung vergessen haben
        fs_senden(br, lk_js, s, az, pz['identity'], [{'t': 'bewegen', 'x': 0.3, 'y': 0.3}])
        time.sleep(1)
        erg = s.evaluate('window.__fs')
        pruefen('Fernsteuerung: nach dem Beenden auch A verworfen', len(erg) == 5 and not erg[-1]['ok'] and erg[-1]['von'] == az['identity'], erg[-1])
        s.evaluate('() => window.__raum.disconnect()')
    finally:
        c.close()


# ================================================================= Ablauf
def main():
    print(f'Sicherheitstest gegen {B} (LiveKit {LK_URL})', flush=True)
    assert roh('GET', '/api/gesund').status == 200, 'Instanz nicht erreichbar'
    assert roh('GET', '/api/test/posteingang').status == 200, 'Nur gegen eine Testinstanz (AGORA_TEST=1) laufen lassen'
    t0 = time.time()
    ctx = adm = None
    schritte = [('kopfzeilen', kopfzeilen), ('csrf', csrf), ('anmeldung', anmeldung), ('idor', idor), ('rollen', rollen),
                ('token', None), ('ssrf', ssrf), ('pfade', None), ('groessen', groessen), ('brute', brute), ('sqli', sqli),
                ('einbetten', einbetten), ('orgs', orgs), ('status', status_seite), ('fehlerbericht', None), ('hls', hls), ('browser', None)]
    abbrueche = []
    noetig = set(NUR)
    for n in list(NUR):
        noetig |= {'token': {'rollen'}, 'browser': {'rollen', 'sqli'}, 'fehlerbericht': {'sqli'}}.get(n, set())
    for name, f in schritte:
        if NUR and name not in noetig:
            continue
        BEREICH[0] = name
        print(f'\n== {name}', flush=True)
        try:
            if name == 'rollen':
                ctx = rollen()
            elif name == 'token':
                token_grants(ctx)
            elif name == 'pfade':
                pfade(konto_anlegen('Pfade'))
            elif name == 'sqli':
                adm = sqli()
            elif name == 'fehlerbericht':
                fehlerbericht()
            elif name == 'browser':
                browser(ctx, adm)
            else:
                f()
        except Exception as e:   # noqa: BLE001
            traceback.print_exc()
            abbrueche.append(f'{name}: {type(e).__name__}: {e}')
    erfolgreich = [x for x in ERGEBNISSE if not x[2]]
    print(f'\n{len(ERGEBNISSE)} Prüfungen in {time.time() - t0:.0f} s, {len(erfolgreich)} Angriffe erfolgreich, {len(abbrueche)} Abbrüche')
    for bereich, name, _s, info in erfolgreich:
        print(f'  ANGRIFF ERFOLGREICH [{bereich}] {name}: {info}')
    for x in abbrueche:
        print(f'  TEST ABGEBROCHEN {x}')
    sys.exit(1 if erfolgreich or abbrueche else 0)


if __name__ == '__main__':
    main()
