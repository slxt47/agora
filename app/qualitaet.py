"""Verbindungsqualität im Meeting (Header X-Teilnahme).

Der Browser liest alle 10 s die WebRTC-Statistik seiner Verbindungen (getStats) und schickt etwa alle 30 s eine
kleine Zusammenfassung: Laufzeit (RTT), Paketverlust, Jitter, Bitrate rein/raus, Kandidat (udp/tcp/relay),
Auflösung/fps der eigenen Kamera. Der Server bewertet sie (gut/mittel/schlecht), summiert sie je Meeting
(Tabelle `qualitaet`, ohne IPs, ohne Namen) und merkt sich die letzten Werte je Teilnahme für die Host-Ansicht
(`qualitaet_person`, wird beim Meeting-Ende gelöscht).
"""
import json
import re

from fastapi import APIRouter, Request

from . import db
from .kern import fehler, grenze, jetzt, json_body
from .meeting import steuernd, teilnahme
from .raeume import BEIM_ENDE

router = APIRouter()
KANDIDATEN = ('udp', 'tcp', 'relay')
AUFLOESUNG_RE = re.compile(r'^\d{1,5}x\d{1,5}$')
# Grenzen der Werte (alles darüber ist Unsinn oder Absicht)
ZAHLEN = {'rtt': 60000, 'verlust': 100, 'jitter': 10000, 'ein': 1_000_000, 'aus': 1_000_000, 'fps': 240}
PERSON_FRISCH_S = 90          # Host-Ansicht: ältere Werte gelten als veraltet


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
    return {'ok': True, 'bewertung': w['bewertung'] if w else None}


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
