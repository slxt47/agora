"""Live-Untertitel und Transkripte.

Live-Untertitel: Jede sprechende Person transkribiert im eigenen Browser (Web Speech API – in Chrome geht der Ton an
Google, deshalb nur nach ausdrücklicher Zustimmung, nie automatisch) und schickt erkannte Abschnitte hierher. Der Server
prüft die Teilnahme, setzt Name/identity selbst (nicht fälschbar) und verteilt über den Datenkanal (Topic `untertitel`,
nur vom Server). Gespeichert werden nur fertige Abschnitte und nur, wenn der Besitzer „Transkript speichern“ für den
Raum eingeschaltet hat – dann steht das auch in den Raum-Metadaten (`transkript: true`), alle sehen einen Hinweis.
Der Besitzer lädt das Transkript eines Meetings als .txt oder .vtt; Transkripte bleiben 90 Tage (wie Aufnahmen).

Transkript aus Aufnahme: Ist AGORA_WHISPER_URL gesetzt (OpenAI-kompatibler Endpunkt /v1/audio/transcriptions, z. B.
whisper.cpp-Server mit --convert), erstellt ein Hintergrund-Job aus einer Aufnahme der Bibliothek eine WebVTT-Datei
neben der Aufnahme (/daten/aufnahmen/<id>.vtt). Immer nur ein Job gleichzeitig.
"""
import json
import os
import re
import secrets
import threading
import time
import urllib.request

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import aufnahmen, db, meeting, raeume
from . import livekit_dienst as lk
from .kern import benutzer_aus_sitzung, fehler, grenze, iso, jetzt, json_body, text_feld
from .raeume import besitzer_raum

router = APIRouter()
AUFBEWAHRUNG_S = 90 * 86400
WHISPER_URL = os.environ.get('AGORA_WHISPER_URL', '').strip()
WHISPER_MODELL = os.environ.get('AGORA_WHISPER_MODELL', 'whisper-1')
WHISPER_TIMEOUT_S = int(os.environ.get('AGORA_WHISPER_TIMEOUT', '3600'))
_whisper_lock = threading.Lock()
NACH_TRANSKRIPT = []          # f(aufnahme_zeile) – Whisper-Transkript fertig (Push)


def _meta(r):
    return {'transkript': True} if r['transkript_speichern'] else {}


raeume.META_EXTRA.append(_meta)
raeume.RAUM_JSON_EXTRA.append(lambda r, b: {'transkript_speichern': bool(r['transkript_speichern'])})


def aufraeumen(t=None):
    db.verbindung().execute('DELETE FROM transkript WHERE zeit < ?', ((t or jetzt()) - AUFBEWAHRUNG_S,))


# ---------------------------------------------------------------- Live-Untertitel
@router.post('/api/raeume/{code}/untertitel')
async def senden(code: str, req: Request):
    r, t = meeting.teilnahme(req, code)
    if t['rolle'] == 'zuschauer':
        fehler(403, 'Zuschauer senden keinen Ton – also auch keine Untertitel.')
    grenze.pruefen('untertitel:' + t['identity'], 60, 30)
    d = await json_body(req, 4096)
    text = text_feld(d, 'text', 500).replace('\n', ' ')
    if not text:
        fehler(400, 'Leerer Text')
    final = d.get('final') is True
    dauer = d.get('dauer_ms')
    dauer = dauer if isinstance(dauer, int) and not isinstance(dauer, bool) and 0 <= dauer <= 120000 else 0
    jetzt_ms = int(time.time() * 1000)
    seg = {'identity': t['identity'], 'name': t['name'], 'text': text, 'final': final, 'zeit': jetzt_ms}
    await lk.senden(r['code'], seg, topic='untertitel')
    if final and r['transkript_speichern']:
        db.verbindung().execute('INSERT INTO transkript (raum_id, sitzung, identity, name, text, beginn_ms, ende_ms, zeit) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                                (r['id'], r['sitzung'], t['identity'], t['name'], text, jetzt_ms - dauer, jetzt_ms, jetzt()))
    return {'ok': True}


# ---------------------------------------------------------------- Transkript (Besitzer)
@router.patch('/api/raeume/{code}/transkript')
async def einstellen(code: str, req: Request):
    b, r = besitzer_raum(req, code)
    d = await json_body(req)
    if not isinstance(d.get('speichern'), bool):
        fehler(400, 'Bitte „speichern“ (true/false) angeben.')
    db.verbindung().execute('UPDATE raeume SET transkript_speichern = ? WHERE id = ?', (int(d['speichern']), r['id']))
    r = raeume.neu_laden(r)
    if r['aktiv']:
        await lk.metadaten(r['code'], raeume.meta(r))
        await lk.senden(r['code'], {'typ': 'transkript', 'an': d['speichern']})
    db.protokollieren(f'Transkript speichern: {"an" if d["speichern"] else "aus"}', b['id'], code)
    return {'speichern': d['speichern']}


@router.get('/api/raeume/{code}/transkripte')
def liste(code: str, req: Request):
    _b, r = besitzer_raum(req, code)
    rows = db.verbindung().execute('SELECT sitzung, COUNT(*) n, MIN(beginn_ms) b, MAX(ende_ms) e FROM transkript WHERE raum_id = ? '
                                   'GROUP BY sitzung ORDER BY sitzung DESC', (r['id'],)).fetchall()
    return {'speichern': bool(r['transkript_speichern']), 'aktuelle_sitzung': r['sitzung'] if r['aktiv'] else None,
            'transkripte': [{'sitzung': x['sitzung'], 'abschnitte': x['n'], 'beginn': iso(x['b'] // 1000), 'ende': iso(x['e'] // 1000)} for x in rows]}


def vtt_zeit(ms):
    ms = max(0, ms)
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}'


def vtt_text(s):
    return s.replace('-->', '→').replace('\n', ' ')


@router.get('/api/raeume/{code}/transkript')
def herunterladen(code: str, req: Request, sitzung: int = 0, format: str = 'txt'):
    _b, r = besitzer_raum(req, code)
    if format not in ('txt', 'vtt'):
        fehler(400, 'Format: txt oder vtt.')
    c = db.verbindung()
    if not sitzung:
        x = c.execute('SELECT MAX(sitzung) FROM transkript WHERE raum_id = ?', (r['id'],)).fetchone()[0]
        sitzung = x or 0
    rows = c.execute('SELECT * FROM transkript WHERE raum_id = ? AND sitzung = ? ORDER BY beginn_ms, id', (r['id'], sitzung)).fetchall()
    if not rows:
        fehler(404, 'Für dieses Meeting gibt es kein Transkript.')
    start = rows[0]['beginn_ms']
    if format == 'vtt':
        teile = ['WEBVTT', '']
        for i, x in enumerate(rows, 1):
            ende = max(x['ende_ms'], x['beginn_ms'] + 800)
            teile += [str(i), f'{vtt_zeit(x["beginn_ms"] - start)} --> {vtt_zeit(ende - start)}', f'<v {vtt_text(x["name"])}>{vtt_text(x["text"])}', '']
        inhalt, typ = '\n'.join(teile), 'text/vtt; charset=utf-8'
    else:
        kopf = f'Transkript: {r["titel"]} ({r["code"]}), Meeting {sitzung}, Beginn {iso(start // 1000)}\n\n'
        inhalt = kopf + '\n'.join(f'[{vtt_zeit(x["beginn_ms"] - start)[:8]}] {x["name"]}: {x["text"]}' for x in rows) + '\n'
        typ = 'text/plain; charset=utf-8'
    return Response(inhalt, media_type=typ, headers={'Content-Disposition': f'attachment; filename="transkript-{code}-{sitzung}.{format}"'})


@router.delete('/api/raeume/{code}/transkript')
def loeschen(code: str, req: Request, sitzung: int = 0):
    b, r = besitzer_raum(req, code)
    c = db.verbindung()
    if sitzung:
        c.execute('DELETE FROM transkript WHERE raum_id = ? AND sitzung = ?', (r['id'], sitzung))
    else:
        c.execute('DELETE FROM transkript WHERE raum_id = ?', (r['id'],))
    db.protokollieren('Transkript gelöscht', b['id'], code)
    return {'ok': True}


# ---------------------------------------------------------------- Transkript aus Aufnahme (Whisper)
def vtt_pfad(aufnahme_id):
    return aufnahmen.pfad(aufnahme_id) + '.vtt'


def status(aufnahme_id):
    """None (nichts) oder {status: laeuft|fertig|fehler, fehler}."""
    x = db.verbindung().execute('SELECT * FROM aufnahme_transkript WHERE aufnahme_id = ?', (aufnahme_id,)).fetchone()
    return {'status': x['status'], 'fehler': x['fehler']} if x else None


def _multipart(felder, datei_feld, dateiname, typ, pfad):
    """multipart/form-data als Generator (Datei wird gestreamt, nicht ganz in den Speicher geladen)."""
    grenze_ = 'agora' + secrets.token_hex(12)
    vorn = b''.join(f'--{grenze_}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in felder.items())
    vorn += f'--{grenze_}\r\nContent-Disposition: form-data; name="{datei_feld}"; filename="{dateiname}"\r\nContent-Type: {typ}\r\n\r\n'.encode()
    hinten = f'\r\n--{grenze_}--\r\n'.encode()
    laenge = len(vorn) + os.path.getsize(pfad) + len(hinten)

    def teile():
        yield vorn
        with open(pfad, 'rb') as f:
            while block := f.read(1 << 20):
                yield block
        yield hinten
    return grenze_, laenge, teile()


def whisper_anfrage(pfad, typ):
    """Datei an AGORA_WHISPER_URL schicken → WebVTT-Text. Eigene Funktion, damit Tests sie ersetzen können."""
    endung = 'mp4' if 'mp4' in typ else 'webm'
    grenze_, laenge, koerper = _multipart({'model': WHISPER_MODELL, 'response_format': 'vtt'}, 'file', f'aufnahme.{endung}', typ, pfad)
    anfrage = urllib.request.Request(WHISPER_URL, data=koerper, method='POST', headers={
        'Content-Type': f'multipart/form-data; boundary={grenze_}', 'Content-Length': str(laenge)})
    schluessel = os.environ.get('AGORA_WHISPER_SCHLUESSEL')
    if schluessel:
        anfrage.add_header('Authorization', f'Bearer {schluessel}')
    with urllib.request.urlopen(anfrage, timeout=WHISPER_TIMEOUT_S) as antwort:     # noqa: S310 (Adresse vom Betreiber)
        text = antwort.read(20 * 1024 * 1024).decode('utf-8', 'replace')
    if text.lstrip().startswith('{'):            # manche Server antworten trotz response_format mit JSON
        d = json.loads(text)
        text = d.get('vtt') or d.get('text') or ''
        if not text.lstrip().startswith('WEBVTT'):
            text = 'WEBVTT\n\n1\n00:00:00.000 --> 99:00:00.000\n' + text.strip() + '\n'
    if not text.lstrip().startswith('WEBVTT'):
        raise ValueError('Antwort ist kein WebVTT')
    return text


def _job(aufnahme_id, pfad, typ):
    c = db.verbindung()
    with _whisper_lock:                           # ein Job nach dem anderen (Whisper ist rechenintensiv)
        try:
            vtt = whisper_anfrage(pfad, typ)
            tmp = vtt_pfad(aufnahme_id) + '.neu'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(vtt)
            os.replace(tmp, vtt_pfad(aufnahme_id))
            c.execute("UPDATE aufnahme_transkript SET status = 'fertig', fehler = NULL, fertig = ? WHERE aufnahme_id = ?", (jetzt(), aufnahme_id))
            a = c.execute('SELECT * FROM aufnahmen WHERE id = ?', (aufnahme_id,)).fetchone()
            for f in NACH_TRANSKRIPT:
                if a:
                    f(a)
        except Exception as e:   # noqa: BLE001
            print(f'whisper-fehler {type(e).__name__}', flush=True)
            c.execute("UPDATE aufnahme_transkript SET status = 'fehler', fehler = ? WHERE aufnahme_id = ?",
                      (f'{type(e).__name__}: {str(e)[:150]}', aufnahme_id))


@router.post('/api/aufnahmen/{i}/transkript')
def aufnahme_transkript(i: str, req: Request):
    if not WHISPER_URL:
        fehler(404, 'Transkripte aus Aufnahmen sind nicht eingerichtet.')
    b, a = aufnahmen.eigene(req, i)
    grenze.pruefen(f'whisper:{b["id"]}', 10, 3600)
    s = status(a['id'])
    if s and s['status'] == 'laeuft':
        fehler(409, 'Das Transkript wird schon erstellt.')
    db.verbindung().execute('INSERT OR REPLACE INTO aufnahme_transkript (aufnahme_id, status, fehler, erstellt) VALUES (?, ?, NULL, ?)',
                            (a['id'], 'laeuft', jetzt()))
    threading.Thread(target=_job, args=(a['id'], aufnahmen.pfad(a['id']), a['typ']), daemon=True).start()
    db.protokollieren('Transkript aus Aufnahme angefordert', b['id'])
    return {'transkript': {'status': 'laeuft', 'fehler': None}}


@router.get('/api/aufnahmen/{i}/transkript')
def aufnahme_transkript_lesen(i: str, req: Request, format: str = ''):
    _b, a = aufnahmen.eigene(req, i)
    if not format:
        return {'transkript': status(a['id']), 'verfuegbar': bool(WHISPER_URL)}
    if format not in ('vtt', 'txt') or not os.path.isfile(vtt_pfad(a['id'])):
        fehler(404, 'Kein Transkript vorhanden.')
    with open(vtt_pfad(a['id']), encoding='utf-8') as f:
        vtt = f.read()
    name = re.sub(r'[^A-Za-z0-9 ._-]', '_', a['titel'])[:80] or 'aufnahme'
    if format == 'txt':
        zeilen = [z for z in vtt.splitlines() if z.strip() and z.strip() != 'WEBVTT' and '-->' not in z and not z.strip().isdigit()]
        return Response('\n'.join(re.sub(r'<[^>]+>', '', z) for z in zeilen) + '\n', media_type='text/plain; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{name}.txt"'})
    return Response(vtt, media_type='text/vtt; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{name}.vtt"'})


def _aufnahme_entfernt(i):
    try:
        os.remove(vtt_pfad(i))
    except FileNotFoundError:
        pass


aufnahmen.BEIM_ENTFERNEN.append(_aufnahme_entfernt)
aufnahmen.JSON_EXTRA.append(lambda a: {'transkript': status(a['id']), 'transkript_moeglich': bool(WHISPER_URL)})
