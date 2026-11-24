"""Gemeinsames Whiteboard im laufenden Meeting (Header X-Teilnahme).

Der Server führt ein Operations-Log je Meeting (raum_id + sitzung) und verteilt jede angenommene Operation mit
laufender Nummer `seq` über den LiveKit-Datenkanal (Topic `whiteboard`, nur vom Server). Wer später kommt oder eine
Lücke in `seq` bemerkt, holt den Stand per GET. Operationen:

  {art: "neu",      objekt}   – Objekt anlegen (id vom Client, eindeutig)
  {art: "aendern",  objekt}   – Objekt ersetzen (verschieben, Text ändern); unbekannte id wird still übergangen
  {art: "loeschen", id}       – Objekt entfernen (Radierer); unbekannte id wird still übergangen

Objekt: {id, art: stift|linie|pfeil|rechteck|ellipse|text|notiz, farbe "#rrggbb", breite 1–40, und je nach Art
punkte [x0, y0, x1, y1, …] (stift, ≤ 2000 Punkte) oder x, y, w, h (alle anderen; Linie/Pfeil: von x,y nach x+w,y+h),
text (text/notiz, ≤ 2000 Zeichen)}. Rückgängig/Wiederholen ist Sache des Clients (je Person eigene Schritte, als
Gegen-Operation geschickt).

Grenzen: höchstens 3000 Objekte und 3 MB Stand je Meeting, 20 Operationen bzw. 64 KB je Anfrage. Wächst das Log über
4000 Einträge, wird es zu einem Schnappschuss (ein „neu“ je Objekt) verdichtet.

Rechte: alle zeichnen; Host/Co-Host können das Whiteboard sperren (dann nur Moderation), leeren und für alle öffnen.
Mit dem Meeting gelöscht, außer der Raum hat „chat_speichern“ (dann im Meeting-Verlauf des Besitzers).
"""
import json
import math
import re

from fastapi import APIRouter, Request

from . import db, verlauf
from . import livekit_dienst as lk
from .kern import fehler, grenze, jetzt, json_body
from .meeting import protokoll, steuernd, teilnahme
from .raeume import BEIM_ENDE

router = APIRouter()
MOD = ('host', 'cohost')
ARTEN = ('stift', 'linie', 'pfeil', 'rechteck', 'ellipse', 'text', 'notiz')
ID_RE = re.compile(r'^[A-Za-z0-9_-]{6,40}$')
FARBE_RE = re.compile(r'^#[0-9a-f]{6}$')
MAX_OBJEKTE = 3000
MAX_STAND = 3 * 1024 ** 2          # Bytes (JSON) aller Objekte
MAX_OPS_JE_ANFRAGE = 20
MAX_PUNKTE = 2000
MAX_KOORD = 100000
VERDICHTEN_AB = 4000
MAX_PAKET = 14000                  # größer → Clients laden den Stand neu (Datenkanal-Pakete klein halten)
_cache = {}                        # (raum_id, sitzung) → (seq, {id: objekt}) – ein uvicorn-Worker


def _beim_ende(r):
    c = db.verbindung()
    c.execute('DELETE FROM whiteboard WHERE raum_id = ?', (r['id'],))
    if not r['chat_speichern']:
        c.execute('DELETE FROM whiteboard_ops WHERE raum_id = ?', (r['id'],))
    for k in [k for k in _cache if k[0] == r['id']]:
        del _cache[k]


BEIM_ENDE.append(_beim_ende)


# ---------------------------------------------------------------- Prüfen
def _zahl(v, lo=-MAX_KOORD, hi=MAX_KOORD):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        fehler(400, 'Ungültiges Whiteboard-Objekt')
    return round(float(v), 1)


def objekt_pruefen(o):
    """Objekt säubern (nur bekannte Felder, Zahlen gerundet) oder 400."""
    if not isinstance(o, dict) or not ID_RE.match(str(o.get('id', ''))) or o.get('art') not in ARTEN:
        fehler(400, 'Ungültiges Whiteboard-Objekt')
    farbe = o.get('farbe')
    if not isinstance(farbe, str) or not FARBE_RE.match(farbe):
        fehler(400, 'Ungültige Farbe')
    neu = {'id': o['id'], 'art': o['art'], 'farbe': farbe, 'breite': _zahl(o.get('breite', 3), 1, 40)}
    if o['art'] == 'stift':
        p = o.get('punkte')
        if not isinstance(p, list) or len(p) < 2 or len(p) % 2 or len(p) > 2 * MAX_PUNKTE:
            fehler(400, 'Ungültiger Strich')
        neu['punkte'] = [_zahl(v) for v in p]
    else:
        for k in ('x', 'y'):
            neu[k] = _zahl(o.get(k))
        for k in ('w', 'h'):
            neu[k] = _zahl(o.get(k), -20000, 20000)
    if o['art'] in ('text', 'notiz'):
        t = o.get('text', '')
        if not isinstance(t, str) or len(t) > 2000:
            fehler(400, 'Text zu lang (höchstens 2000 Zeichen).')
        neu['text'] = ''.join(ch for ch in t if ch in '\n\t' or ord(ch) >= 32)
    return neu


def op_pruefen(op):
    if not isinstance(op, dict) or op.get('art') not in ('neu', 'aendern', 'loeschen'):
        fehler(400, 'Ungültige Operation')
    if op['art'] == 'loeschen':
        if not isinstance(op.get('id'), str) or not ID_RE.match(op['id']):
            fehler(400, 'Ungültige Operation')
        return {'art': 'loeschen', 'id': op['id']}
    return {'art': op['art'], 'objekt': objekt_pruefen(op.get('objekt'))}


def anwenden(objekte, op):
    """Operation auf den Stand anwenden; False, wenn sie nichts ändert (unbekannte id)."""
    if op['art'] == 'loeschen':
        return objekte.pop(op['id'], None) is not None
    o = op['objekt']
    if op['art'] == 'aendern' and o['id'] not in objekte:
        return False
    objekte[o['id']] = o
    return True


# ---------------------------------------------------------------- Stand
def _zeile(r):
    c = db.verbindung()
    w = c.execute('SELECT * FROM whiteboard WHERE raum_id = ?', (r['id'],)).fetchone()
    if not w or w['sitzung'] != r['sitzung']:
        c.execute('INSERT OR REPLACE INTO whiteboard (raum_id, sitzung, seq, gesperrt) VALUES (?, ?, 0, 0)', (r['id'], r['sitzung']))
        w = c.execute('SELECT * FROM whiteboard WHERE raum_id = ?', (r['id'],)).fetchone()
    return w


def objekte_laden(raum_id, sitzung):
    objekte = {}
    for row in db.verbindung().execute('SELECT op FROM whiteboard_ops WHERE raum_id = ? AND sitzung = ? ORDER BY id', (raum_id, sitzung)):
        anwenden(objekte, json.loads(row['op']))
    return objekte


def stand(r, w=None):
    """(seq, {id: objekt}) des laufenden Meetings, aus dem Zwischenspeicher oder neu aus dem Log."""
    w = w or _zeile(r)
    k = (r['id'], r['sitzung'])
    if k in _cache and _cache[k][0] == w['seq']:
        return _cache[k]
    _cache[k] = (w['seq'], objekte_laden(r['id'], r['sitzung']))
    return _cache[k]


def _verdichten(r, seq, objekte):
    c = db.verbindung()
    c.execute('DELETE FROM whiteboard_ops WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung']))
    c.executemany('INSERT INTO whiteboard_ops (raum_id, sitzung, seq, identity, op, zeit) VALUES (?, ?, ?, ?, ?, ?)',
                  [(r['id'], r['sitzung'], seq, '', json.dumps({'art': 'neu', 'objekt': o}, separators=(',', ':')), jetzt())
                   for o in objekte.values()])


def darf_zeichnen(w, t):
    return t['rolle'] in MOD or not w['gesperrt']


async def senden(r, daten):
    await lk.senden(r['code'], daten, topic='whiteboard')


# ---------------------------------------------------------------- Endpunkte
@router.get('/api/raeume/{code}/whiteboard')
def lesen(code: str, req: Request):
    r, t = teilnahme(req, code)
    w = _zeile(r)
    seq, objekte = stand(r, w)
    return {'seq': seq, 'objekte': list(objekte.values()), 'gesperrt': bool(w['gesperrt']), 'darf_zeichnen': darf_zeichnen(w, t)}


@router.post('/api/raeume/{code}/whiteboard/ops')
async def ops(code: str, req: Request):
    r, t = teilnahme(req, code)
    grenze.pruefen('whiteboard:' + t['identity'], 80, 10)
    d = await json_body(req, 64 * 1024)
    liste = d.get('ops')
    if not isinstance(liste, list) or not 1 <= len(liste) <= MAX_OPS_JE_ANFRAGE:
        fehler(400, f'1 bis {MAX_OPS_JE_ANFRAGE} Operationen je Anfrage.')
    neue = [op_pruefen(op) for op in liste]
    c = db.verbindung()
    c.execute('BEGIN IMMEDIATE')
    try:
        w = _zeile(r)
        if not darf_zeichnen(w, t):
            fehler(403, 'Das Whiteboard ist gesperrt – nur Host und Co-Hosts zeichnen.')
        seq, objekte = stand(r, w)
        objekte = dict(objekte)         # Kopie: erst nach allen Prüfungen übernehmen
        angenommen = []
        for op in neue:
            if op['art'] == 'neu' and op['objekt']['id'] in objekte:
                fehler(409, 'Dieses Objekt gibt es schon.')
            if anwenden(objekte, op):
                angenommen.append(op)
        if len(objekte) > MAX_OBJEKTE:
            fehler(413, f'Das Whiteboard ist voll (höchstens {MAX_OBJEKTE} Objekte). Der Host kann es leeren.')
        if sum(len(json.dumps(o, separators=(',', ':'))) for o in objekte.values()) > MAX_STAND:
            fehler(413, 'Das Whiteboard ist voll (3 MB). Der Host kann es leeren.')
        von = seq + 1
        for op in angenommen:
            seq += 1
            c.execute('INSERT INTO whiteboard_ops (raum_id, sitzung, seq, identity, op, zeit) VALUES (?, ?, ?, ?, ?, ?)',
                      (r['id'], r['sitzung'], seq, t['identity'], json.dumps(op, separators=(',', ':')), jetzt()))
        c.execute('UPDATE whiteboard SET seq = ? WHERE raum_id = ?', (seq, r['id']))
        if c.execute('SELECT COUNT(*) FROM whiteboard_ops WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung'])).fetchone()[0] > VERDICHTEN_AB:
            _verdichten(r, seq, objekte)
        c.execute('COMMIT')
    except BaseException:
        c.execute('ROLLBACK')
        raise
    _cache[(r['id'], r['sitzung'])] = (seq, objekte)
    if angenommen:
        paket = {'typ': 'ops', 'von_seq': von, 'seq': seq, 'ops': angenommen, 'von': t['identity'], 'von_name': t['name']}
        if len(json.dumps(paket, separators=(',', ':'))) > MAX_PAKET:
            paket = {'typ': 'neu_laden', 'seq': seq, 'von': t['identity'], 'von_name': t['name']}
        await senden(r, paket)
    return {'seq': seq, 'angenommen': len(angenommen)}


@router.post('/api/raeume/{code}/whiteboard/sperren')
async def sperren(code: str, req: Request):
    r, t = steuernd(req, code)
    d = await json_body(req)
    if not isinstance(d.get('an'), bool):
        fehler(400, 'Bitte „an“ (true/false) angeben.')
    _zeile(r)
    db.verbindung().execute('UPDATE whiteboard SET gesperrt = ? WHERE raum_id = ?', (int(d['an']), r['id']))
    await senden(r, {'typ': 'gesperrt', 'an': d['an'], 'von_name': t['name']})
    protokoll(t, r, 'Whiteboard gesperrt' if d['an'] else 'Whiteboard entsperrt')
    return {'ok': True, 'gesperrt': d['an']}


@router.post('/api/raeume/{code}/whiteboard/leeren')
async def leeren(code: str, req: Request):
    r, t = steuernd(req, code)
    c = db.verbindung()
    w = _zeile(r)
    seq = w['seq'] + 1
    c.execute('DELETE FROM whiteboard_ops WHERE raum_id = ? AND sitzung = ?', (r['id'], r['sitzung']))
    c.execute('UPDATE whiteboard SET seq = ? WHERE raum_id = ?', (seq, r['id']))
    _cache[(r['id'], r['sitzung'])] = (seq, {})
    await senden(r, {'typ': 'geleert', 'seq': seq, 'von_name': t['name']})
    protokoll(t, r, 'Whiteboard geleert')
    return {'ok': True, 'seq': seq}


@router.post('/api/raeume/{code}/whiteboard/zeigen')
async def zeigen(code: str, req: Request):
    """Host/Co-Host öffnet das Whiteboard bei allen (jede Person kann es danach selbst wieder schließen)."""
    r, t = steuernd(req, code)
    grenze.pruefen('whiteboard-zeigen:' + t['identity'], 10, 60)
    await senden(r, {'typ': 'zeigen', 'von': t['identity'], 'von_name': t['name']})
    return {'ok': True}


def verlauf_inhalt(raum_id, sitzung):
    """Für den Meeting-Verlauf (chat_speichern): Objekte des gespeicherten Whiteboards."""
    return {'whiteboard': list(objekte_laden(raum_id, sitzung).values())}


verlauf.INHALT.append(verlauf_inhalt)
