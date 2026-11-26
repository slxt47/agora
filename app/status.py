"""Öffentliche Status-Seite `/status` und `/api/status` (ohne Anmeldung).

Ein eigener Hintergrund-Thread prüft jede Minute drei Dienste – App (Datenbank schreibt), Medien (LiveKit-Server-API
antwortet) und E-Mail (SMTP-Server nimmt eine Verbindung mit STARTTLS an; nur alle 5 Minuten, damit der Mail-Anbieter
nicht minütlich angefragt wird) – und zählt die Ergebnisse je Dienst in Fenstern zu 5 Minuten zusammen (Proben,
davon ok, Summe/Maximum der Antwortzeit). Daraus entstehen Verfügbarkeit (24 h / 7 Tage / 90 Tage), Balken und
Antwortzeiten. Fenster älter als 90 Tage löscht der Hintergrund.

Admins schreiben im Admin-Bereich Meldungen zu Störungen und Wartungen (Titel, Text, Art, Zustand, Zeiten, betroffene
Dienste); die Seite zeigt laufende und geplante sowie die der letzten 14 Tage. Nach außen gehen nur Zustände, Prozente
und Millisekunden – keine Hostnamen, IPs, Ports, Versionen oder Fehlertexte.
"""
import asyncio
import json
import os
import smtplib
import ssl
import threading
import time

from fastapi import APIRouter, Request

from . import db
from .kern import TEST, client_ip, fehler, grenze, iso, jetzt, json_body, text_feld

router = APIRouter()
FENSTER_S = 300
AUFBEWAHRUNG_S = 90 * 86400
MELDUNG_AUFBEWAHRUNG_S = 365 * 86400       # Meldungen: 365 Tage nach dem Ende
MELDUNG_SICHTBAR_S = 14 * 86400            # beendete Meldungen bleiben 14 Tage auf der Seite
MAIL_ALLE_S = 300
DIENSTE = ('app', 'medien', 'mail')
ARTEN = ('stoerung', 'wartung', 'hinweis')
ZUSTAENDE = {'stoerung': ('untersuchung', 'erkannt', 'beobachtung', 'behoben'),
             'wartung': ('geplant', 'laeuft', 'abgeschlossen'),
             'hinweis': ('aktiv', 'abgeschlossen')}
ERLEDIGT = ('behoben', 'abgeschlossen')
BALKEN = {'24h': (24, 3600), '7d': (28, 6 * 3600), '90d': (90, 86400)}     # Anzahl × Breite in Sekunden
LETZTE = {}               # dienst → (zeit, ok, ms) – letzte Probe im Arbeitsspeicher
_cache = {'zeit': 0.0, 'daten': None}
_lock = threading.Lock()


# ---------------------------------------------------------------- Prüfungen
def pruefe_app():
    from .metriken import db_schreibtest
    return db_schreibtest()


def pruefe_medien():
    from .metriken import livekit_lage
    ok, ms, _v, _r = asyncio.run(livekit_lage())
    if not ok:
        raise RuntimeError('livekit')
    return ms


def mail_eingerichtet():
    return TEST or bool(os.environ.get('SMTP_HOST'))


def pruefe_mail():
    """Verbindung + EHLO + STARTTLS + NOOP, ohne Anmeldung und ohne Mail. Im Testmodus immer ok."""
    if TEST:
        return 0.0
    t0 = time.perf_counter()
    with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.environ.get('SMTP_PORT', '587')), timeout=10) as s:
        s.ehlo()
        s.starttls(context=ssl.create_default_context())
        s.ehlo()
        code, _ = s.noop()
        if code != 250:
            raise RuntimeError('noop')
    return (time.perf_counter() - t0) * 1000


PRUEFUNGEN = {'app': pruefe_app, 'medien': pruefe_medien, 'mail': pruefe_mail}


def speichern(dienst, ok, ms, t=None):
    """Ein Ergebnis ins 5-Minuten-Fenster zählen."""
    t = t or jetzt()
    fenster = t - t % FENSTER_S
    ms = round(float(ms or 0), 1) if ok else 0.0
    db.verbindung().execute(
        'INSERT INTO status_proben (dienst, fenster, proben, ok, ms_summe, ms_max) VALUES (?, ?, 1, ?, ?, ?) '
        'ON CONFLICT (dienst, fenster) DO UPDATE SET proben = proben + 1, ok = ok + excluded.ok, '
        'ms_summe = ms_summe + excluded.ms_summe, ms_max = MAX(ms_max, excluded.ms_max)',
        (dienst, fenster, int(ok), ms, ms))
    LETZTE[dienst] = (t, bool(ok), ms)


def pruefen_und_speichern(t=None):
    """Alle fälligen Prüfungen einmal (Hintergrund jede Minute; Tests rufen es direkt)."""
    t = t or jetzt()
    for dienst, f in PRUEFUNGEN.items():
        if dienst == 'mail':
            if not mail_eingerichtet():
                continue
            letzte = LETZTE.get('mail')
            if letzte and t - letzte[0] < MAIL_ALLE_S - 5:
                continue
        try:
            ms = f()
            speichern(dienst, True, ms, t)
        except Exception as e:   # noqa: BLE001
            speichern(dienst, False, 0, t)
            print(f'status-pruefung {dienst} fehlgeschlagen: {type(e).__name__}', flush=True)
    with _lock:
        _cache['daten'] = None


def aufraeumen(t=None):
    t = t or jetzt()
    c = db.verbindung()
    c.execute('DELETE FROM status_proben WHERE fenster < ?', (t - AUFBEWAHRUNG_S,))
    c.execute('DELETE FROM status_meldungen WHERE ende IS NOT NULL AND ende < ?', (t - MELDUNG_AUFBEWAHRUNG_S,))


def _schleife():
    while True:
        try:
            pruefen_und_speichern()
        except Exception as e:   # noqa: BLE001
            print(f'status-fehler {type(e).__name__}', flush=True)
        time.sleep(60 - time.time() % 60 + 1)          # kurz nach jeder vollen Minute


def starten():
    threading.Thread(target=_schleife, daemon=True, name='status').start()


# ---------------------------------------------------------------- Auswertung
def _prozent(ok, proben):
    return round(ok / proben * 100, 3) if proben else None


def dienst_json(dienst, t):
    c = db.verbindung()
    rows = c.execute('SELECT fenster, proben, ok, ms_summe FROM status_proben WHERE dienst = ? AND fenster >= ? ORDER BY fenster',
                     (dienst, t - AUFBEWAHRUNG_S)).fetchall()
    uptime = {}
    for name, (n, breite) in BALKEN.items():
        seit = t - n * breite
        p = sum(r['proben'] for r in rows if r['fenster'] >= seit)
        o = sum(r['ok'] for r in rows if r['fenster'] >= seit)
        uptime[name] = _prozent(o, p)
    balken = {}
    for name, (n, breite) in BALKEN.items():
        ende = t - t % breite + breite             # Balken enden mit dem laufenden Abschnitt
        liste = []
        for i in range(n):
            von = ende - (n - i) * breite
            teil = [r for r in rows if von <= r['fenster'] < von + breite]
            p = sum(r['proben'] for r in teil)
            o = sum(r['ok'] for r in teil)
            ms = sum(r['ms_summe'] for r in teil)
            liste.append({'start': iso(von), 'uptime': _prozent(o, p), 'ms': round(ms / o, 1) if o else None, 'ausfaelle': p - o})
        balken[name] = liste
    tag = [r for r in rows if r['fenster'] >= t - 86400]
    ok_tag = sum(r['ok'] for r in tag)
    return {'id': dienst, 'zustand': zustand(dienst, t), 'ms': round(sum(r['ms_summe'] for r in tag) / ok_tag, 1) if ok_tag else None,
            'uptime': uptime, 'balken': balken}


def zustand(dienst, t):
    """ok | gestoert | ausfall | unbekannt – aus der letzten Probe, nach einem Neustart aus dem jüngsten Fenster."""
    grenze_s = (MAIL_ALLE_S if dienst == 'mail' else 60) * 3
    letzte = LETZTE.get(dienst)
    if letzte and t - letzte[0] <= grenze_s:
        if letzte[1]:
            return 'ok'
    r = db.verbindung().execute('SELECT fenster, proben, ok FROM status_proben WHERE dienst = ? ORDER BY fenster DESC LIMIT 1',
                                (dienst,)).fetchone()
    if not r or t - r['fenster'] > grenze_s + FENSTER_S:
        return 'unbekannt'
    if letzte and t - letzte[0] <= grenze_s and not letzte[1]:
        return 'ausfall' if r['ok'] == 0 else 'gestoert'
    if r['ok'] == r['proben']:
        return 'ok'
    return 'ausfall' if r['ok'] == 0 else 'gestoert'


def meldung_json(m, intern=False):
    j = {'id': m['id'], 'art': m['art'], 'titel': m['titel'], 'text': m['text'], 'zustand': m['zustand'],
         'dienste': json.loads(m['dienste'] or '[]'), 'beginn': iso(m['beginn']), 'ende': iso(m['ende']),
         'aktualisiert': iso(m['geaendert'])}
    if intern:
        j['erstellt'] = iso(m['erstellt'])
    return j


def oeffentliche_meldungen(t):
    rows = db.verbindung().execute('SELECT * FROM status_meldungen ORDER BY beginn DESC, id DESC LIMIT 200').fetchall()
    aktiv, vergangen = [], []
    for m in rows:
        erledigt = m['zustand'] in ERLEDIGT or (m['ende'] is not None and m['ende'] <= t)
        if not erledigt:
            aktiv.append(meldung_json(m))
        elif (m['ende'] or m['geaendert']) >= t - MELDUNG_SICHTBAR_S:
            vergangen.append(meldung_json(m))
    return aktiv, vergangen


def gesamt(dienste, aktiv, t):
    zustaende = [d['zustand'] for d in dienste]
    if 'ausfall' in zustaende or any(m['art'] == 'stoerung' for m in aktiv):
        return 'stoerung'
    if 'gestoert' in zustaende:
        return 'eingeschraenkt'
    if any(m['art'] == 'wartung' and m['zustand'] == 'laeuft' for m in aktiv):
        return 'wartung'
    if all(z == 'unbekannt' for z in zustaende):
        return 'unbekannt'
    return 'ok'


def status_daten(t=None):
    t = t or jetzt()
    dienste = [dienst_json(d, t) for d in DIENSTE if d != 'mail' or mail_eingerichtet()
               or db.verbindung().execute("SELECT 1 FROM status_proben WHERE dienst = 'mail' LIMIT 1").fetchone()]
    aktiv, vergangen = oeffentliche_meldungen(t)
    return {'stand': iso(t), 'gesamt': gesamt(dienste, aktiv, t), 'dienste': dienste, 'meldungen': aktiv, 'vergangen': vergangen,
            'fenster_s': FENSTER_S}


@router.get('/api/status')
def status_api(req: Request):
    grenze.pruefen('status:' + client_ip(req), 120, 60)
    with _lock:
        if _cache['daten'] is not None and time.monotonic() - _cache['zeit'] < 30:
            return _cache['daten']
    d = status_daten()
    with _lock:
        _cache.update(zeit=time.monotonic(), daten=d)
    return d


# ---------------------------------------------------------------- Meldungen (Admin)
def _zeit(d, feld, pflicht=False):
    from .raeume import zeit_lesen
    v = d.get(feld)
    if v in (None, ''):
        if pflicht:
            fehler(400, 'Bitte einen Beginn angeben.')
        return None
    return zeit_lesen(v, 'UTC', feld)


def meldung_lesen(d, alt=None):
    alt = dict(alt) if alt else {}
    art = d.get('art', alt.get('art', 'stoerung'))
    if art not in ARTEN:
        fehler(400, 'Art: stoerung, wartung oder hinweis.')
    zustand_neu = d.get('zustand', alt.get('zustand') if alt.get('art') == art else ZUSTAENDE[art][0])
    if zustand_neu not in ZUSTAENDE[art]:
        fehler(400, 'Ungültiger Zustand für diese Art.')
    titel = text_feld(d, 'titel', 140, standard=alt.get('titel', '')).replace('\n', ' ')
    if not titel:
        fehler(400, 'Bitte einen Titel eingeben.')
    text = text_feld(d, 'text', 4000, standard=alt.get('text', ''))
    dienste = d.get('dienste', json.loads(alt.get('dienste') or '[]'))
    if not isinstance(dienste, list) or any(x not in DIENSTE for x in dienste):
        fehler(400, 'Dienste: app, medien, mail.')
    beginn = _zeit(d, 'beginn') if 'beginn' in d else alt.get('beginn')
    beginn = beginn or jetzt()
    ende = _zeit(d, 'ende') if 'ende' in d else alt.get('ende')
    if ende is None and zustand_neu in ERLEDIGT:
        ende = jetzt()
    if ende is not None and ende < beginn:
        fehler(400, 'Das Ende liegt vor dem Beginn.')
    return {'art': art, 'titel': titel, 'text': text, 'zustand': zustand_neu, 'dienste': json.dumps(sorted(set(dienste), key=DIENSTE.index)),
            'beginn': beginn, 'ende': ende}


def _admin(req):
    from .admin import admin
    return admin(req)


@router.get('/api/admin/status/meldungen')
def admin_liste(req: Request):
    _admin(req)
    rows = db.verbindung().execute('SELECT * FROM status_meldungen ORDER BY beginn DESC, id DESC LIMIT 200').fetchall()
    return {'meldungen': [meldung_json(m, True) for m in rows], 'arten': ZUSTAENDE, 'dienste': list(DIENSTE)}


@router.post('/api/admin/status/meldungen')
async def admin_neu(req: Request):
    a = _admin(req)
    f = meldung_lesen(await json_body(req))
    c = db.verbindung()
    i = c.execute('INSERT INTO status_meldungen (art, titel, text, zustand, dienste, beginn, ende, erstellt, geaendert, von_id) '
                  'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (f['art'], f['titel'], f['text'], f['zustand'], f['dienste'], f['beginn'], f['ende'],
                                                            jetzt(), jetzt(), a['id'])).lastrowid
    db.protokollieren(f'Admin: Status-Meldung #{i} angelegt ({f["art"]}, {f["zustand"]})', a['id'])
    with _lock:
        _cache['daten'] = None
    return {'meldung': meldung_json(c.execute('SELECT * FROM status_meldungen WHERE id = ?', (i,)).fetchone(), True)}


def _laden(mid):
    m = db.verbindung().execute('SELECT * FROM status_meldungen WHERE id = ?', (mid,)).fetchone()
    if not m:
        fehler(404, 'Meldung nicht gefunden')
    return m


@router.patch('/api/admin/status/meldungen/{mid}')
async def admin_aendern(mid: int, req: Request):
    a = _admin(req)
    m = _laden(mid)
    f = meldung_lesen(await json_body(req), m)
    c = db.verbindung()
    c.execute('UPDATE status_meldungen SET art = ?, titel = ?, text = ?, zustand = ?, dienste = ?, beginn = ?, ende = ?, geaendert = ? '
              'WHERE id = ?', (f['art'], f['titel'], f['text'], f['zustand'], f['dienste'], f['beginn'], f['ende'], jetzt(), mid))
    db.protokollieren(f'Admin: Status-Meldung #{mid} geändert ({f["zustand"]})', a['id'])
    with _lock:
        _cache['daten'] = None
    return {'meldung': meldung_json(_laden(mid), True)}


@router.delete('/api/admin/status/meldungen/{mid}')
def admin_loeschen(mid: int, req: Request):
    a = _admin(req)
    _laden(mid)
    db.verbindung().execute('DELETE FROM status_meldungen WHERE id = ?', (mid,))
    db.protokollieren(f'Admin: Status-Meldung #{mid} gelöscht', a['id'])
    with _lock:
        _cache['daten'] = None
    return {'ok': True}
