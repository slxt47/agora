"""Verbindungsqualität im Meeting (Header X-Teilnahme).

Der Browser liest alle 10 s die WebRTC-Statistik seiner Verbindungen (getStats) und schickt etwa alle 30 s eine
kleine Zusammenfassung: Laufzeit (RTT), Paketverlust, Jitter, Bitrate rein/raus, Kandidat (udp/tcp/relay),
Auflösung/fps der eigenen Kamera. Der Server bewertet sie (gut/mittel/schlecht), summiert sie je Meeting
(Tabelle `qualitaet`, ohne IPs, ohne Namen) und merkt sich die letzten Werte je Teilnahme für die Host-Ansicht
(`qualitaet_person`, wird beim Meeting-Ende gelöscht).

Rückmeldung nach dem Meeting: Die Statistik-Antwort enthält ein Rückmelde-Token (HMAC über Raum, Sitzung, identity
und Zeit, 24 h gültig). Damit bewertet jede Teilnahme das Meeting einmal (1–5 Sterne, Probleme, Text) – auch nachdem
das Meeting beendet und die Teilnahme gelöscht ist. Der Besitzer sieht im Konto nur Schnitt, Zählungen und Texte
ohne Namen (GET /api/qualitaet, GET /api/raeume/{code}/qualitaet).
"""
import hashlib
import hmac
import json
import re

from fastapi import APIRouter, Request

from . import db
from .kern import GEHEIM, benutzer_aus_sitzung, client_ip, fehler, grenze, iso, jetzt, json_body, text_feld
from .meeting import steuernd, teilnahme
from .raeume import BEIM_ENDE, besitzer_raum, raum_laden

router = APIRouter()
KANDIDATEN = ('udp', 'tcp', 'relay')
AUFLOESUNG_RE = re.compile(r'^\d{1,5}x\d{1,5}$')
# Grenzen der Werte (alles darüber ist Unsinn oder Absicht)
ZAHLEN = {'rtt': 60000, 'verlust': 100, 'jitter': 10000, 'ein': 1_000_000, 'aus': 1_000_000, 'fps': 240}
PERSON_FRISCH_S = 90          # Host-Ansicht: ältere Werte gelten als veraltet
PROBLEME = ('ton', 'echo', 'video', 'verbindung', 'bildschirm', 'bedienung')
FEEDBACK_S = 24 * 3600        # so lange nach der letzten Statistik-Meldung darf bewertet werden


def bewerten(rtt, verlust, jitter):
    """Gleiche Schwellen wie im Browser (web/src/raum/statistik-rechnen.js)."""
    if verlust >= 5 or rtt >= 400 or jitter >= 50:
        return 'schlecht'
    if verlust >= 2 or rtt >= 200 or jitter >= 30:
        return 'mittel'
    return 'gut'


def werte_lesen(d):
    """Zusammenfassung prüfen → dict oder None (nur Anmeldung ohne Messwerte)."""
    w = {}
    for k, grenze_ in ZAHLEN.items():
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 0 <= v <= grenze_:
            fehler(400, f'Ungültiger Wert: {k}')
        w[k] = round(float(v), 1)
    if not all(k in w for k in ('rtt', 'verlust', 'jitter')):
        if w:
            fehler(400, 'rtt, verlust und jitter gehören zusammen.')
        return None
    kandidat = d.get('kandidat') or ''
    if kandidat not in (*KANDIDATEN, ''):
        fehler(400, 'Ungültiger Wert: kandidat')
    aufloesung = d.get('aufloesung') or ''
    if not isinstance(aufloesung, str) or (aufloesung and not AUFLOESUNG_RE.match(aufloesung)):
        fehler(400, 'Ungültiger Wert: aufloesung')
    w.update(kandidat=kandidat, aufloesung=aufloesung, bewertung=bewerten(w['rtt'], w['verlust'], w['jitter']))
    for k in ('ein', 'aus', 'fps'):
        w.setdefault(k, 0.0)
    return w


def speichern(r, identity, w):
    c = db.verbindung()
    t = jetzt()
    neu = not c.execute('SELECT 1 FROM qualitaet_person WHERE raum_id = ? AND identity = ? AND sitzung = ?',
                        (r['id'], identity, r['sitzung'])).fetchone()
    c.execute('INSERT INTO qualitaet_person VALUES (?, ?, ?, ?, ?) ON CONFLICT (raum_id, identity) DO UPDATE SET '
              'sitzung = excluded.sitzung, zeit = excluded.zeit, werte = excluded.werte',
              (r['id'], r['sitzung'], identity, t, json.dumps(w)))
    c.execute('INSERT OR IGNORE INTO qualitaet (raum_id, sitzung, beginn, aktualisiert) VALUES (?, ?, ?, ?)',
              (r['id'], r['sitzung'], t, t))
    k = w['kandidat']
    c.execute('UPDATE qualitaet SET proben = proben + 1, teilnehmer = teilnehmer + ?, rtt_summe = rtt_summe + ?, '
              'verlust_summe = verlust_summe + ?, jitter_summe = jitter_summe + ?, ein_summe = ein_summe + ?, '
              'aus_summe = aus_summe + ?, rtt_max = MAX(rtt_max, ?), verlust_max = MAX(verlust_max, ?), '
              'schlecht = schlecht + ?, mittel = mittel + ?, udp = udp + ?, tcp = tcp + ?, relay = relay + ?, '
              'aktualisiert = ? WHERE raum_id = ? AND sitzung = ?',
              (int(neu), w['rtt'], w['verlust'], w['jitter'], w['ein'], w['aus'], w['rtt'], w['verlust'],
               int(w['bewertung'] == 'schlecht'), int(w['bewertung'] == 'mittel'),
               int(k == 'udp'), int(k == 'tcp'), int(k == 'relay'), t, r['id'], r['sitzung']))


def zusammenfassung(q):
    """Zeile aus `qualitaet` → Durchschnittswerte (oder None)."""
    if not q or not q['proben']:
        return None
    n = q['proben']
    kandidaten = q['udp'] + q['tcp'] + q['relay']
    return {'proben': n, 'teilnehmer': q['teilnehmer'], 'rtt': round(q['rtt_summe'] / n), 'verlust': round(q['verlust_summe'] / n, 1),
            'jitter': round(q['jitter_summe'] / n), 'ein': round(q['ein_summe'] / n), 'aus': round(q['aus_summe'] / n),
            'rtt_max': round(q['rtt_max']), 'verlust_max': round(q['verlust_max'], 1),
            'schlecht_anteil': round(q['schlecht'] / n * 100), 'mittel_anteil': round(q['mittel'] / n * 100),
            'kandidaten': {k: round(q[k] / kandidaten * 100) if kandidaten else 0 for k in KANDIDATEN}}


def _beim_ende(r):
    db.verbindung().execute('DELETE FROM qualitaet_person WHERE raum_id = ?', (r['id'],))


BEIM_ENDE.append(_beim_ende)


@router.post('/api/raeume/{code}/statistik')
async def statistik_melden(code: str, req: Request):
    r, t = teilnahme(req, code)
    d = await json_body(req, 4096)
    grenze.pruefen('statistik:' + t['identity'], 12, 60)
    w = werte_lesen(d)
    if w:
        speichern(r, t['identity'], w)
    return {'ok': True, 'bewertung': w['bewertung'] if w else None, 'feedback': feedback_token(r['id'], r['sitzung'], t['identity'])}


@router.get('/api/raeume/{code}/statistik')
def statistik_lesen(code: str, req: Request):
    """Host/Co-Host: letzte Werte je Teilnahme (nur frische) und der Schnitt des Meetings."""
    r, _t = steuernd(req, code)
    c = db.verbindung()
    t = jetzt()
    leute = {}
    for p in c.execute('SELECT * FROM qualitaet_person WHERE raum_id = ? AND sitzung = ? AND zeit > ?',
                       (r['id'], r['sitzung'], t - PERSON_FRISCH_S)).fetchall():
        leute[p['identity']] = json.loads(p['werte']) | {'alter_s': t - p['zeit']}
    q = c.execute('SELECT * FROM qualitaet WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung'])).fetchone()
    return {'teilnehmer': leute, 'meeting': zusammenfassung(q)}


# ---------------------------------------------------------------- Rückmeldung nach dem Meeting
def _signatur(raum_id, sitzung, identity, zeit):
    return hmac.new(GEHEIM, f'feedback:{raum_id}:{sitzung}:{identity}:{zeit}'.encode(), hashlib.sha256).hexdigest()[:32]


def feedback_token(raum_id, sitzung, identity, zeit=None):
    zeit = zeit or jetzt()
    return f'{sitzung}.{identity}.{zeit}.{_signatur(raum_id, sitzung, identity, zeit)}'


def feedback_token_pruefen(r, token):
    """→ (sitzung, identity) oder 403."""
    try:
        sitzung, identity, zeit, sig = str(token).split('.')
        sitzung, zeit = int(sitzung), int(zeit)
    except ValueError:
        fehler(403, 'Rückmeldung nicht möglich.')
    if len(token) > 200 or not hmac.compare_digest(sig, _signatur(r['id'], sitzung, identity, zeit)) or not 0 <= jetzt() - zeit <= FEEDBACK_S:
        fehler(403, 'Rückmeldung nicht möglich.')
    return sitzung, identity


def statistik_lesen_feedback(d):
    """Optionaler eigener Schnitt aus dem Browser – nur bekannte Zahlen in sinnvollen Grenzen."""
    s = d.get('statistik')
    if s is None:
        return None
    if not isinstance(s, dict):
        fehler(400, 'Ungültige Statistik.')
    grenzen = {'proben': 100000, 'rtt': 60000, 'verlust': 100, 'jitter': 10000, 'ein': 1_000_000, 'aus': 1_000_000, 'schlecht_anteil': 100}
    aus = {}
    for k, g in grenzen.items():
        v = s.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 0 <= v <= g:
            fehler(400, 'Ungültige Statistik.')
        aus[k] = round(float(v), 1)
    return aus or None


@router.post('/api/raeume/{code}/feedback')
async def feedback_senden(code: str, req: Request):
    r = raum_laden(code)
    d = await json_body(req, 8192)
    grenze.pruefen('feedback:' + client_ip(req), 20, 600)
    sitzung, identity = feedback_token_pruefen(r, d.get('token', ''))
    sterne = d.get('sterne')
    if isinstance(sterne, bool) or not isinstance(sterne, int) or not 1 <= sterne <= 5:
        fehler(400, 'Bitte 1 bis 5 Sterne vergeben.')
    probleme = d.get('probleme') or []
    if not isinstance(probleme, list) or len(probleme) > len(PROBLEME) or any(p not in PROBLEME for p in probleme):
        fehler(400, 'Unbekanntes Problem.')
    text = text_feld(d, 'text', 1000)
    db.verbindung().execute(
        'INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (raum_id, sitzung, identity) DO UPDATE SET '
        'sterne = excluded.sterne, probleme = excluded.probleme, text = excluded.text, statistik = excluded.statistik, zeit = excluded.zeit',
        (r['id'], sitzung, identity, sterne, json.dumps(sorted(set(probleme))), text,
         json.dumps(statistik_lesen_feedback(d)) if d.get('statistik') is not None else None, jetzt()))
    return {'ok': True}


def feedback_schnitt(rows):
    if not rows:
        return None
    probleme = dict.fromkeys(PROBLEME, 0)
    for f in rows:
        for p in json.loads(f['probleme']):
            if p in probleme:
                probleme[p] += 1
    return {'anzahl': len(rows), 'sterne': round(sum(f['sterne'] for f in rows) / len(rows), 1), 'probleme': probleme,
            'kommentare': [f['text'] for f in rows if f['text']]}


@router.get('/api/qualitaet')
def qualitaet_uebersicht(req: Request):
    """Besitzer: je eigenem Raum Schnitt aller Rückmeldungen und Verbindungswerte (für die Meeting-Liste im Konto)."""
    b = benutzer_aus_sitzung(req)
    c = db.verbindung()
    aus = {}
    for z in c.execute('SELECT r.code, COUNT(f.sterne) AS n, AVG(f.sterne) AS sterne FROM raeume r JOIN feedback f ON f.raum_id = r.id '
                       'WHERE r.besitzer_id = ? GROUP BY r.id', (b['id'],)).fetchall():
        aus[z['code']] = {'bewertungen': z['n'], 'sterne': round(z['sterne'], 1)}
    for z in c.execute('SELECT r.code, COUNT(*) AS meetings, SUM(q.proben) AS proben, SUM(q.rtt_summe) AS rtt, SUM(q.verlust_summe) AS verlust '
                       'FROM raeume r JOIN qualitaet q ON q.raum_id = r.id WHERE r.besitzer_id = ? AND q.proben > 0 GROUP BY r.id',
                       (b['id'],)).fetchall():
        e = aus.setdefault(z['code'], {'bewertungen': 0, 'sterne': None})
        e.update(meetings=z['meetings'], rtt=round(z['rtt'] / z['proben']), verlust=round(z['verlust'] / z['proben'], 1))
    for e in aus.values():
        e.setdefault('meetings', 0)
        e.setdefault('rtt', None)
        e.setdefault('verlust', None)
    # Je Meeting (für „Vergangene Meetings“ = meeting_verlauf): Schlüssel "code:sitzung"
    meetings = {}
    for z in c.execute('SELECT r.code, f.sitzung, COUNT(*) AS n, AVG(f.sterne) AS sterne FROM raeume r JOIN feedback f ON f.raum_id = r.id '
                       'WHERE r.besitzer_id = ? GROUP BY r.id, f.sitzung', (b['id'],)).fetchall():
        meetings[f"{z['code']}:{z['sitzung']}"] = {'bewertungen': z['n'], 'sterne': round(z['sterne'], 1), 'rtt': None, 'verlust': None}
    for q in c.execute('SELECT r.code, q.* FROM raeume r JOIN qualitaet q ON q.raum_id = r.id WHERE r.besitzer_id = ? AND q.proben > 0',
                       (b['id'],)).fetchall():
        e = meetings.setdefault(f"{q['code']}:{q['sitzung']}", {'bewertungen': 0, 'sterne': None})
        e.update(rtt=round(q['rtt_summe'] / q['proben']), verlust=round(q['verlust_summe'] / q['proben'], 1))
    return {'raeume': aus, 'meetings': meetings}


@router.get('/api/raeume/{code}/qualitaet')
def qualitaet_raum(code: str, req: Request):
    """Besitzer: je Meeting (Sitzung, neueste zuerst, höchstens 30) Verbindungsschnitt und Rückmeldungen ohne Namen."""
    _b, r = besitzer_raum(req, code)
    c = db.verbindung()
    sitzungen = {}
    for q in c.execute('SELECT * FROM qualitaet WHERE raum_id = ?', (r['id'],)).fetchall():
        sitzungen.setdefault(q['sitzung'], {})['q'] = q
    for f in c.execute('SELECT * FROM feedback WHERE raum_id = ? ORDER BY zeit', (r['id'],)).fetchall():
        sitzungen.setdefault(f['sitzung'], {}).setdefault('f', []).append(f)
    meetings = []
    for s in sorted(sitzungen, reverse=True)[:30]:
        e = sitzungen[s]
        beginn = e['q']['beginn'] if 'q' in e else min(f['zeit'] for f in e['f'])
        meetings.append({'sitzung': s, 'beginn': iso(beginn), 'verbindung': zusammenfassung(e.get('q')),
                         'feedback': feedback_schnitt(e.get('f', []))})
    return {'meetings': meetings}
