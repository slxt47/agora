"""Zusammenfassung, Kapitelmarken und Aufgaben für gespeicherte Transkripte.

Quellen: das Transkript der Live-Untertitel eines Meetings (Raum + Sitzung) oder das Whisper-Transkript einer Aufnahme
(`<aufnahme>.vtt`). Nur wer den Raum verwalten darf (bzw. die Aufnahme besitzt) löst es aus, immer ausdrücklich.

- **KI** (nur mit `AGORA_KI_URL` + `AGORA_KI_MODELL`, optional `AGORA_KI_SCHLUESSEL`): OpenAI-kompatibles
  `/v1/chat/completions`. Das Transkript (mit Namen) geht an genau diesen Endpunkt – die Oberfläche sagt das vorher, die
  API verlangt `zustimmung: true`. Lange Transkripte werden in Teile geschnitten (`AGORA_KI_ZEICHEN`, Standard 12000
  Zeichen); jeder Teil liefert Zusammenfassung, Kapitel und Aufgaben, ein letzter Aufruf fasst die Teil-Zusammenfassungen
  zusammen. Hintergrund-Thread, ein Auftrag nach dem anderen.
- **Stichwort-Kapitel** (ohne KI, immer verfügbar): Kapitelgrenzen an langen Pausen und Sprecherwechseln, Titel aus den
  Wörtern, die im Abschnitt häufig und im Rest selten sind. Keine Zusammenfassung, keine Aufgaben – ehrlich so beschriftet.

Ein Ergebnis je Quelle (neu erzeugen ersetzt), gelöscht mit dem Transkript bzw. der Aufnahme. Kapitel gibt es als
WebVTT (`kind="chapters"`) für den Spieler.
"""
import json
import math
import os
import re
import threading
import urllib.error
import urllib.request
from collections import Counter
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import aufnahmen, db, push, texte, untertitel
from .kern import fehler, grenze, iso, jetzt, json_body
from .raeume import besitzer_raum

router = APIRouter()
KI_URL = os.environ.get('AGORA_KI_URL', '').strip()
KI_MODELL = os.environ.get('AGORA_KI_MODELL', '').strip()
KI_TIMEOUT_S = int(os.environ.get('AGORA_KI_TIMEOUT', '300'))
KI_ZEICHEN = max(2000, int(os.environ.get('AGORA_KI_ZEICHEN', '12000')))
ARTEN = ('ki', 'stichwort')
_lock = threading.Lock()


def ki_an():
    return bool(KI_URL and KI_MODELL)


def ki_endpunkt():
    u = KI_URL.rstrip('/')
    if u.endswith('/chat/completions'):
        return u
    return u + ('/chat/completions' if u.endswith('/v1') else '/v1/chat/completions')


def ki_ziel():
    """Nur Host[:Port] (ohne Pfad/Schlüssel) – für den Hinweis vor dem Senden."""
    return urlsplit(ki_endpunkt()).netloc if ki_an() else None


# ---------------------------------------------------------------- Abschnitte aus den Quellen
def abschnitte_raum(r, sitzung):
    rows = db.verbindung().execute('SELECT name, text, beginn_ms, ende_ms FROM transkript WHERE raum_id = ? AND sitzung = ? '
                                   'ORDER BY beginn_ms, id', (r['id'], sitzung)).fetchall()
    if not rows:
        return []
    start = rows[0]['beginn_ms']
    return [{'ms': x['beginn_ms'] - start, 'ende_ms': max(x['ende_ms'], x['beginn_ms'] + 800) - start, 'name': x['name'], 'text': x['text']}
            for x in rows]


_ZEIT = re.compile(r'(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?')


def zeit_ms(text):
    """'01:02:03.500', '02:03' oder '2:03,5' → Millisekunden (oder None)."""
    m = _ZEIT.fullmatch(str(text).strip())
    if not m:
        return None
    h, mi, s = (int(m.group(i) or 0) for i in range(1, 4))
    return ((h * 60 + mi) * 60 + s) * 1000 + int((m.group(4) or '0').ljust(3, '0')[:3])


def abschnitte_vtt(vtt):
    """WebVTT (Whisper) → Abschnitte; Sprecher aus <v Name>, sonst leer."""
    aus = []
    for b in re.split(r'\r?\n\r?\n', vtt.replace('﻿', '')):
        zeilen_ = [z for z in b.splitlines() if z.strip()]
        for i, z in enumerate(zeilen_):
            if '-->' not in z:
                continue
            von, _, bis = z.partition('-->')
            a, e = zeit_ms(von), zeit_ms(bis.split()[0] if bis.split() else '')
            text = ' '.join(zeilen_[i + 1:])
            m = re.match(r'<v(?:\.[^ >]*)? ([^>]+)>', text)
            name = m.group(1).strip() if m else ''
            text = re.sub(r'<[^>]+>', '', text).strip()
            if a is not None and text:
                aus.append({'ms': a, 'ende_ms': e if e is not None and e >= a else a + 800, 'name': name, 'text': text})
            break
    return aus


def zeit_text(ms):
    s = max(0, ms) // 1000
    return f'{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}' if s >= 3600 else f'{s // 60:02d}:{s % 60:02d}'


def zeilen(abschnitte):
    return [f'[{zeit_text(a["ms"])}] {a["name"] + ": " if a["name"] else ""}{a["text"]}' for a in abschnitte]


# ---------------------------------------------------------------- Stichwort-Kapitel (ohne KI)
STOPP = set('''
aber alle allem allen aller alles also auch auf aus bei beim bin bis bist dann darf darum das dass dein deine dem den denn der des
dich die dies diese diesem diesen dieser dieses doch dort durch eben ein eine einem einen einer eines einfach einmal erst etwa etwas
euch euer für gar gibt ganz geht gerade gestern gut habe haben hast hat hatte heute hier hin ich ihm ihn ihr ihre ihrem ihren ihrer
immer ins ist jetzt kann kannst kein keine können könnte machen mal man mehr mein meine mich mir mit morgen muss müssen nach
nein nicht nichts noch nun nur oder okay schon sehr sein seine sich sie sind soll sollen sondern sonst über uns unser unter
viel vom von vor war wäre waren warum was weil weiter welche wenn wer werden wie wieder wir wird wirklich wollen würde zum zur
zwar zwei genau eigentlich vielleicht natürlich gesagt sagen sage glaube denke damit dabei dafür danach daran darauf
about after again also and any are because been before being both but can could did does doing done down each even every for
from get gets getting going gonna good have having here just know like look make many more most much need now okay only other
our out over really right said same say should some still such sure than that the their them then there these they thing things
think this those through very want was way well were what when where which while will with would yeah yes you your
'''.split())
_WORT = re.compile(r'[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]{3,}')


def _woerter(text):
    return [w for w in _WORT.findall(text) if w.lower() not in STOPP]


def stichwort_kapitel(abschnitte, pause_ms=20000, wechsel_ms=180000, max_ms=600000, min_ms=60000):
    """Kapitelgrenzen: Pause ≥ pause_ms, Sprecherwechsel nach ≥ wechsel_ms im Kapitel oder spätestens nach max_ms.
    Kapitel unter min_ms gehen im vorigen auf (außer nach einer Pause ≥ 3 × pause_ms). Titel: die drei Wörter mit dem höchsten tf·idf im Kapitel."""
    gruppen = []
    for a in abschnitte:
        g = gruppen[-1] if gruppen else None
        if g is None or a['ms'] - g['ende'] >= pause_ms or a['ms'] - g['start'] >= max_ms or (
                a['name'] and g['letzter'] and a['name'] != g['letzter'] and a['ms'] - g['start'] >= wechsel_ms):
            g = {'start': a['ms'], 'ende': a['ende_ms'], 'teile': [], 'sprecher': Counter(), 'letzter': '',
                 'luecke': a['ms'] - gruppen[-1]['ende'] if gruppen else 0}
            gruppen.append(g)
        g['teile'].append(a['text'])
        g['ende'] = max(g['ende'], a['ende_ms'])
        if a['name']:
            g['sprecher'][a['name']] += 1
            g['letzter'] = a['name']
    zusammen = []
    for g in gruppen:
        if zusammen and g['ende'] - g['start'] < min_ms and g['luecke'] < 3 * pause_ms:   # nach langer Pause bleibt es ein Kapitel
            z = zusammen[-1]
            z['teile'] += g['teile']
            z['ende'] = max(z['ende'], g['ende'])
            z['sprecher'] += g['sprecher']
        else:
            zusammen.append(g)
    form = {}
    zaehler = []
    for g in zusammen:
        c = Counter()
        for t in g['teile']:
            for w in _woerter(t):
                c[w.lower()] += 1
                form.setdefault(w.lower(), Counter())[w] += 1
        zaehler.append(c)
    df = Counter(w for c in zaehler for w in c)
    n = len(zusammen)
    kapitel = []
    for i, (g, c) in enumerate(zip(zusammen, zaehler), 1):
        rang = sorted(c, key=lambda w: (-(c[w] * (1 + math.log((1 + n) / (1 + df[w])))), w))
        titel = ', '.join(form[w].most_common(1)[0][0] for w in rang[:3])
        kapitel.append({'start_ms': g['start'], 'titel': titel or f'#{i}', 'sprecher': [s for s, _ in g['sprecher'].most_common(3)]})
    return kapitel


# ---------------------------------------------------------------- KI (OpenAI-kompatibel)
PROMPT = {
    'de': ('Du fasst Transkripte von Besprechungen sachlich zusammen. Antworte ausschließlich mit einem JSON-Objekt der Form '
           '{"zusammenfassung": "…", "kapitel": [{"zeit": "MM:SS", "titel": "…"}], "aufgaben": [{"text": "…", "wer": "…"}]}. '
           'Zusammenfassung: höchstens 8 Sätze auf Deutsch. Kapitel: Themenwechsel mit der Startzeit aus den Zeitstempeln in eckigen '
           'Klammern, Titel höchstens 8 Wörter. Aufgaben: konkrete vereinbarte nächste Schritte, „wer“ nur wenn genannt, sonst leer. '
           'Erfinde nichts, was nicht im Transkript steht.'),
    'en': ('You summarize meeting transcripts factually. Reply only with a JSON object of the form '
           '{"zusammenfassung": "…", "kapitel": [{"zeit": "MM:SS", "titel": "…"}], "aufgaben": [{"text": "…", "wer": "…"}]}. '
           'Summary ("zusammenfassung"): at most 8 sentences in English. Chapters ("kapitel"): topic changes with the start time '
           'from the timestamps in square brackets, titles at most 8 words. Action items ("aufgaben"): concrete agreed next steps, '
           '"wer" (who) only if named, else empty. Do not invent anything that is not in the transcript.'),
}
ZUSAMMEN = {
    'de': 'Hier sind Zusammenfassungen aufeinanderfolgender Teile einer Besprechung. Fasse sie zu einer Zusammenfassung '
          '(höchstens 8 Sätze, Deutsch) zusammen. Antworte nur mit {"zusammenfassung": "…"}.',
    'en': 'Here are summaries of consecutive parts of one meeting. Combine them into one summary (at most 8 sentences, English). '
          'Reply only with {"zusammenfassung": "…"}.',
}


def ki_anfrage(nachrichten):
    """Ein Aufruf → Text der Antwort."""
    body = json.dumps({'model': KI_MODELL, 'messages': nachrichten, 'temperature': 0.2}).encode()
    anfrage = urllib.request.Request(ki_endpunkt(), data=body, method='POST', headers={'Content-Type': 'application/json'})
    schluessel = os.environ.get('AGORA_KI_SCHLUESSEL')
    if schluessel:
        anfrage.add_header('Authorization', f'Bearer {schluessel}')
    with urllib.request.urlopen(anfrage, timeout=KI_TIMEOUT_S) as antwort:     # noqa: S310 (Adresse vom Betreiber)
        d = json.loads(antwort.read(4 * 1024 * 1024))
    return d['choices'][0]['message']['content']


def json_aus(text):
    """JSON-Objekt aus einer Modell-Antwort (auch mit ```json …``` oder Text drumherum)."""
    a, e = text.find('{'), text.rfind('}')
    if a < 0 or e <= a:
        raise ValueError('Antwort ohne JSON')
    d = json.loads(text[a:e + 1])
    if not isinstance(d, dict):
        raise ValueError('Antwort ohne JSON-Objekt')
    return d


def teilen(liste, max_zeichen):
    """Zeilen zu Teilen von höchstens max_zeichen (eine einzelne zu lange Zeile wird gekürzt)."""
    teile, akt, n = [], [], 0
    for z in liste:
        z = z[:max_zeichen]
        if akt and n + len(z) + 1 > max_zeichen:
            teile.append(akt)
            akt, n = [], 0
        akt.append(z)
        n += len(z) + 1
    if akt:
        teile.append(akt)
    return teile


def _text(v, n):
    return re.sub(r'\s+', ' ', v).strip()[:n] if isinstance(v, str) else ''


def ki_zusammenfassen(abschnitte, sprache):
    s = texte.gueltig(sprache)
    teile = teilen(zeilen(abschnitte), KI_ZEICHEN)
    ende = abschnitte[-1]['ende_ms'] if abschnitte else 0
    summen, kapitel, aufgaben = [], [], []
    for i, t in enumerate(teile, 1):
        if len(teile) > 1:
            kopf = f'Teil {i}/{len(teile)} des Transkripts:' if s == 'de' else f'Part {i}/{len(teile)} of the transcript:'
        else:
            kopf = 'Transkript:' if s == 'de' else 'Transcript:'
        d = json_aus(ki_anfrage([{'role': 'system', 'content': PROMPT[s]}, {'role': 'user', 'content': kopf + '\n' + '\n'.join(t)}]))
        if _text(d.get('zusammenfassung'), 4000):
            summen.append(_text(d.get('zusammenfassung'), 4000))
        for k in d.get('kapitel') or []:
            if not isinstance(k, dict):
                continue
            z = k.get('zeit')
            ms = int(z * 1000) if isinstance(z, (int, float)) and not isinstance(z, bool) else zeit_ms(z or '')
            if ms is not None and 0 <= ms <= ende + 1000 and _text(k.get('titel'), 120):
                kapitel.append({'start_ms': ms, 'titel': _text(k.get('titel'), 120)})
        for a in d.get('aufgaben') or []:
            if isinstance(a, dict) and _text(a.get('text'), 300):
                aufgaben.append({'text': _text(a.get('text'), 300), 'wer': _text(a.get('wer'), 80)})
            elif _text(a, 300):
                aufgaben.append({'text': _text(a, 300), 'wer': ''})
    if len(summen) > 1:
        d = json_aus(ki_anfrage([{'role': 'system', 'content': ZUSAMMEN[s]}, {'role': 'user', 'content': '\n\n'.join(summen)}]))
        summen = [_text(d.get('zusammenfassung'), 4000) or ' '.join(summen)[:4000]]
    kap, gesehen = [], set()
    for k in sorted(kapitel, key=lambda k: k['start_ms']):
        if k['start_ms'] // 1000 not in gesehen:
            gesehen.add(k['start_ms'] // 1000)
            kap.append(k)
    auf, ges = [], set()
    for a in aufgaben:
        if a['text'].lower() not in ges:
            ges.add(a['text'].lower())
            auf.append(a)
    return {'zusammenfassung': summen[0] if summen else '', 'kapitel': kap[:50], 'aufgaben': auf[:30], 'teile': len(teile)}


# ---------------------------------------------------------------- Speicher + Aufträge
def _zeile(raum_id=None, sitzung=None, aufnahme_id=None):
    c = db.verbindung()
    if aufnahme_id:
        return c.execute('SELECT * FROM zusammenfassungen WHERE aufnahme_id = ?', (aufnahme_id,)).fetchone()
    return c.execute('SELECT * FROM zusammenfassungen WHERE raum_id = ? AND sitzung = ?', (raum_id, sitzung)).fetchone()


def zusammenfassung_json(z):
    if not z:
        return None
    return {'art': z['art'], 'status': z['status'], 'fehler': z['fehler'], 'modell': z['modell'], 'erstellt': iso(z['erstellt']),
            'fertig': iso(z['fertig']), 'ergebnis': json.loads(z['ergebnis']) if z['ergebnis'] else None}


def _job(zid, abschnitte, sprache, benutzer_id, titel):
    c = db.verbindung()
    with _lock:
        try:
            erg = ki_zusammenfassen(abschnitte, sprache)
            c.execute("UPDATE zusammenfassungen SET status = 'fertig', ergebnis = ?, fertig = ? WHERE id = ?",
                      (json.dumps(erg, ensure_ascii=False), jetzt(), zid))
            push.senden(benutzer_id, 'fertig', 'zusammenfassung', url='/konto', tag=f'zusammenfassung-{zid}', titel=titel)
        except Exception as e:   # noqa: BLE001
            text = f'HTTP {e.code}' if isinstance(e, urllib.error.HTTPError) else f'{type(e).__name__}: {str(e)[:150]}'
            print(f'ki-fehler {type(e).__name__}', flush=True)
            c.execute("UPDATE zusammenfassungen SET status = 'fehler', fehler = ?, fertig = ? WHERE id = ?", (text, jetzt(), zid))


def _antwort(z):
    return {'zusammenfassung': zusammenfassung_json(z), 'ki_verfuegbar': ki_an(), 'ki_ziel': ki_ziel()}


async def _starten(req, b, abschnitte, titel, raum_id=None, sitzung=None, aufnahme_id=None):
    d = await json_body(req, 1024)
    art = d.get('art')
    if art not in ARTEN:
        fehler(400, 'Art: ki oder stichwort.')
    if not abschnitte:
        fehler(404, 'Kein Transkript vorhanden.')
    alt = _zeile(raum_id, sitzung, aufnahme_id)
    if alt and alt['status'] == 'laeuft':
        fehler(409, 'Die Zusammenfassung wird schon erstellt.')
    if art == 'ki':
        if not ki_an():
            fehler(404, 'Die KI-Zusammenfassung ist nicht eingerichtet.')
        if d.get('zustimmung') is not True:
            fehler(400, 'Bitte bestätige, dass das Transkript an den KI-Dienst geschickt wird.')
        grenze.pruefen(f'ki:{b["id"]}', 20, 3600)
    c = db.verbindung()
    if alt:
        c.execute('DELETE FROM zusammenfassungen WHERE id = ?', (alt['id'],))
    if art == 'stichwort':
        erg = {'zusammenfassung': None, 'kapitel': stichwort_kapitel(abschnitte), 'aufgaben': []}
        zid = c.execute("INSERT INTO zusammenfassungen (raum_id, sitzung, aufnahme_id, benutzer_id, art, status, ergebnis, erstellt, fertig) "
                        "VALUES (?, ?, ?, ?, 'stichwort', 'fertig', ?, ?, ?)",
                        (raum_id, sitzung, aufnahme_id, b['id'], json.dumps(erg, ensure_ascii=False), jetzt(), jetzt())).lastrowid
    else:
        zid = c.execute("INSERT INTO zusammenfassungen (raum_id, sitzung, aufnahme_id, benutzer_id, art, status, modell, erstellt) "
                        "VALUES (?, ?, ?, ?, 'ki', 'laeuft', ?, ?)", (raum_id, sitzung, aufnahme_id, b['id'], KI_MODELL[:80], jetzt())).lastrowid
        db.protokollieren(f'KI-Zusammenfassung angefordert ({ki_ziel()})', b['id'])
        threading.Thread(target=_job, args=(zid, abschnitte, b['sprache'], b['id'], titel), daemon=True).start()
    return _antwort(c.execute('SELECT * FROM zusammenfassungen WHERE id = ?', (zid,)).fetchone())


def kapitel_vtt(z, ende_ms, name):
    erg = json.loads(z['ergebnis']) if z and z['status'] == 'fertig' and z['ergebnis'] else None
    if not erg or not erg.get('kapitel'):
        fehler(404, 'Keine Kapitel vorhanden.')
    k = erg['kapitel']
    teile = ['WEBVTT', '']
    for i, x in enumerate(k):
        bis = k[i + 1]['start_ms'] if i + 1 < len(k) else max(ende_ms, x['start_ms'] + 1000)
        teile += [str(i + 1), f'{untertitel.vtt_zeit(x["start_ms"])} --> {untertitel.vtt_zeit(max(bis, x["start_ms"] + 1))}',
                  untertitel.vtt_text(x['titel']), '']
    return Response('\n'.join(teile), media_type='text/vtt; charset=utf-8', headers={'Content-Disposition': f'inline; filename="{name}"'})


def _sitzung(r, sitzung):
    if sitzung:
        return sitzung
    return db.verbindung().execute('SELECT MAX(sitzung) FROM transkript WHERE raum_id = ?', (r['id'],)).fetchone()[0] or 0


# ---------------------------------------------------------------- API: Transkript eines Meetings (wer den Raum verwalten darf)
@router.get('/api/raeume/{code}/zusammenfassung')
def raum_lesen(code: str, req: Request, sitzung: int = 0):
    _b, r = besitzer_raum(req, code)
    return _antwort(_zeile(r['id'], _sitzung(r, sitzung)))


@router.post('/api/raeume/{code}/zusammenfassung')
async def raum_starten(code: str, req: Request, sitzung: int = 0):
    b, r = besitzer_raum(req, code)
    s = _sitzung(r, sitzung)
    return await _starten(req, b, abschnitte_raum(r, s), r['titel'], raum_id=r['id'], sitzung=s)


@router.delete('/api/raeume/{code}/zusammenfassung')
def raum_loeschen(code: str, req: Request, sitzung: int = 0):
    _b, r = besitzer_raum(req, code)
    db.verbindung().execute('DELETE FROM zusammenfassungen WHERE raum_id = ? AND sitzung = ?', (r['id'], _sitzung(r, sitzung)))
    return {'ok': True}


@router.get('/api/raeume/{code}/kapitel.vtt')
def raum_kapitel(code: str, req: Request, sitzung: int = 0):
    _b, r = besitzer_raum(req, code)
    s = _sitzung(r, sitzung)
    a = abschnitte_raum(r, s)
    return kapitel_vtt(_zeile(r['id'], s), a[-1]['ende_ms'] if a else 0, f'kapitel-{code}-{s}.vtt')


# ---------------------------------------------------------------- API: Whisper-Transkript einer Aufnahme (Besitzer)
def _aufnahme_abschnitte(a):
    try:
        with open(untertitel.vtt_pfad(a['id']), encoding='utf-8') as f:
            return abschnitte_vtt(f.read(20 * 1024 * 1024))
    except FileNotFoundError:
        return []


@router.get('/api/aufnahmen/{i}/zusammenfassung')
def aufnahme_lesen(i: str, req: Request):
    _b, a = aufnahmen.eigene(req, i)
    return _antwort(_zeile(aufnahme_id=a['id']))


@router.post('/api/aufnahmen/{i}/zusammenfassung')
async def aufnahme_starten(i: str, req: Request):
    b, a = aufnahmen.eigene(req, i)
    return await _starten(req, b, _aufnahme_abschnitte(a), a['titel'], aufnahme_id=a['id'])


@router.delete('/api/aufnahmen/{i}/zusammenfassung')
def aufnahme_loeschen(i: str, req: Request):
    _b, a = aufnahmen.eigene(req, i)
    db.verbindung().execute('DELETE FROM zusammenfassungen WHERE aufnahme_id = ?', (a['id'],))
    return {'ok': True}


@router.get('/api/aufnahmen/{i}/kapitel.vtt')
def aufnahme_kapitel(i: str, req: Request):
    _b, a = aufnahmen.eigene(req, i)
    abschn = _aufnahme_abschnitte(a)
    return kapitel_vtt(_zeile(aufnahme_id=a['id']), max(abschn[-1]['ende_ms'] if abschn else 0, (a['dauer'] or 0) * 1000), 'kapitel.vtt')


# ---------------------------------------------------------------- Aufräumen, Haken
def aufraeumen(_t=None):
    """Zusammenfassungen ohne Transkript (gelöscht oder nach 90 Tagen abgelaufen) entfernen; hängengebliebene Aufträge beenden."""
    c = db.verbindung()
    c.execute('DELETE FROM zusammenfassungen WHERE raum_id IS NOT NULL AND NOT EXISTS '
              '(SELECT 1 FROM transkript x WHERE x.raum_id = zusammenfassungen.raum_id AND x.sitzung = zusammenfassungen.sitzung)')
    for z in c.execute('SELECT id, aufnahme_id FROM zusammenfassungen WHERE aufnahme_id IS NOT NULL').fetchall():
        if not os.path.isfile(untertitel.vtt_pfad(z['aufnahme_id'])):
            c.execute('DELETE FROM zusammenfassungen WHERE id = ?', (z['id'],))
    c.execute("UPDATE zusammenfassungen SET status = 'fehler', fehler = 'Abgebrochen (Neustart)' WHERE status = 'laeuft' AND erstellt < ?",
              (jetzt() - 6 * 3600,))


def _neues_transkript(a):
    db.verbindung().execute('DELETE FROM zusammenfassungen WHERE aufnahme_id = ?', (a['id'],))   # altes passt nicht mehr


untertitel.BEIM_LOESCHEN.append(aufraeumen)        # auch beim minütlichen Aufräumen der Transkripte (90 Tage)
untertitel.NACH_TRANSKRIPT.insert(0, _neues_transkript)
aufnahmen.JSON_EXTRA.append(lambda a: {'zusammenfassung': (lambda z: {'art': z['art'], 'status': z['status']} if z else None)(
    _zeile(aufnahme_id=a['id']))})
