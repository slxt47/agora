"""Webinar-Modus und Registrierung.

Ein Raum mit `webinar = 1` hat zwei Gruppen:
- Panel: Host, Co-Hosts und Panelisten (Rolle `panelist`, eingeladen über einen eigenen Link mit Token) senden Bild und Ton.
- Zuschauer (Rolle `zuschauer`): sehen und hören zu, LiveKit-Token mit can_publish = false. Chat je nach Einstellung
  (`alle` | `panel` = nur privat ans Panel | `aus`), Fragen (Q&A) je nach `fragen_an` – das Q&A-Modul liest das über
  `einstellungen(raum)`.
Host und Co-Hosts holen Zuschauer aufs Podium (`panelist`) und schicken sie zurück (`zuschauer`); LiveKit bekommt die
neuen Rechte sofort (UpdateParticipant), der Client veröffentlicht bzw. beendet seine Spuren selbst.

Registrierung (optional): öffentliche Seite /w/<code> mit Name, E-Mail und bis zu 5 Zusatzfragen, Kapazitätsgrenze.
Die Bestätigungsmail enthält einen persönlichen Beitrittslink (`/r/<code>?anmeldung=<token>`, Token nur als Hash
gespeichert). Ist die Registrierung an, kommen Zuschauer nur mit diesem Link hinein. Der Besitzer sieht die Liste und
lädt sie als CSV. Anmeldedaten werden 30 Tage nach dem Webinar gelöscht (`loeschen_ab`; beim Ende eines Meetings im
Raum auf höchstens +30 Tage gekürzt, ohne Termin spätestens 90 Tage nach der Anmeldung).
"""
import csv
import hashlib
import hmac
import io
import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import db, mail, texte
from . import livekit_dienst as lk
from . import meeting, raeume
from .kern import GEHEIM, SEITE, benutzer_aus_sitzung, client_ip, fehler, grenze, h, iso, jetzt, json_body, text_feld
from .raeume import besitzer_raum, raum_laden

router = APIRouter()
NACH_ANMELDUNG = []           # f(raum, name, email, antworten) nach jeder (erneuten) Anmeldung (Webhooks)
CHAT = ('alle', 'panel', 'aus')
STANDARD = {'chat': 'alle', 'fragen_an': True, 'registrierung': False, 'kapazitaet': None, 'fragen': []}
PANEL_ROLLEN = ('host', 'cohost', 'panelist')
MAX_PANEL_LINKS = 20
MAX_FRAGEN = 5
NACH_ENDE_S = 30 * 86400          # Anmeldungen: 30 Tage nach dem Webinar
OHNE_TERMIN_S = 90 * 86400        # Sicherheitsnetz, wenn der Raum keinen Termin hat


# ---------------------------------------------------------------- Hilfen
def einstellungen(r):
    """Webinar-Einstellungen eines Raums (auch für andere Module, z. B. Q&A: `fragen_an`)."""
    try:
        e = json.loads(r['webinar_einst'] or '{}')
    except ValueError:
        e = {}
    return STANDARD | {k: v for k, v in e.items() if k in STANDARD}


def ist_webinar(r):
    return bool(r['webinar'])


def panel_token(raum_id, salz):
    return hmac.new(GEHEIM, f'panel:{raum_id}:{salz}'.encode(), hashlib.sha256).hexdigest()[:40]


def panel_link(r, p):
    return f'{SEITE}/r/{r["code"]}?panel={panel_token(r["id"], p["salz"])}'


def anmelde_link(code):
    return f'{SEITE}/w/{code}'


def angemeldet_zahl(r):
    return db.verbindung().execute('SELECT COUNT(*) FROM webinar_anmeldungen WHERE raum_id = ?', (r['id'],)).fetchone()[0]


def loeschen_ab(r, t):
    termin = raeume.termin(r, t)
    if termin is not None:
        return termin + (r['dauer_min'] or 60) * 60 + NACH_ENDE_S
    return t + OHNE_TERMIN_S


def token_lesen(d, feld):
    v = d.get(feld)
    if not isinstance(v, str) or not 10 <= len(v) <= 100:
        return None
    return v


# ---------------------------------------------------------------- Haken in raeume/meeting
def _rolle_standard(r, podium=False):
    if not ist_webinar(r):
        return None
    return 'panelist' if podium else 'zuschauer'


def _beitritt_rolle(r, b, d):
    """Panel-Link → panelist (ohne Warteraum). Sonst Zuschauer; bei Registrierung nur mit persönlichem Link."""
    if not ist_webinar(r):
        return None
    c = db.verbindung()
    tok = token_lesen(d, 'panel')
    if tok:
        if c.execute('SELECT 1 FROM webinar_panel WHERE raum_id = ? AND token_hash = ?', (r['id'], h(tok))).fetchone():
            return {'rolle': 'panelist', 'warteraum_frei': True}
        fehler(403, 'Dieser Panel-Link ist nicht (mehr) gültig.')
    if einstellungen(r)['registrierung']:
        tok = token_lesen(d, 'anmeldung')
        a = c.execute('SELECT * FROM webinar_anmeldungen WHERE raum_id = ? AND token_hash = ?', (r['id'], h(tok))).fetchone() if tok else None
        if not a:
            fehler(403, 'Für dieses Webinar ist eine Anmeldung nötig. Nutze den Link aus deiner Bestätigungsmail.')
        if not a['beigetreten']:
            c.execute('UPDATE webinar_anmeldungen SET beigetreten = ? WHERE id = ?', (jetzt(), a['id']))
    return {'rolle': 'zuschauer'}


def _meta(r):
    return {'webinar': True} if ist_webinar(r) else {}


def _raum_json(r, b):
    if not ist_webinar(r):
        return {'webinar': False}
    e = einstellungen(r)
    return {'webinar': {'registrierung': e['registrierung'], 'anmelde_link': anmelde_link(r['code']) if e['registrierung'] else None}}


def _chat_pruefen(t, z):
    if t['rolle'] != 'zuschauer':
        return
    r = db.verbindung().execute('SELECT * FROM raeume WHERE id = ?', (t['raum_id'],)).fetchone()
    if not r or not ist_webinar(r):
        return
    art = einstellungen(r)['chat']
    if art == 'aus':
        fehler(403, 'Der Chat ist für Zuschauer ausgeschaltet.')
    if art == 'panel' and (z is None or z['rolle'] not in PANEL_ROLLEN):
        fehler(403, 'Zuschauer schreiben in diesem Webinar nur privat an Host und Panel.')


def _beim_ende(r):
    """Anmeldedaten 30 Tage nach dem Webinar löschen (früheren Termin behalten)."""
    db.verbindung().execute('UPDATE webinar_anmeldungen SET loeschen_ab = MIN(loeschen_ab, ?) WHERE raum_id = ?',
                            (jetzt() + NACH_ENDE_S, r['id']))


raeume.ROLLE_STANDARD.append(_rolle_standard)
raeume.BEITRITT_ROLLE.append(_beitritt_rolle)
raeume.META_EXTRA.append(_meta)
raeume.RAUM_JSON_EXTRA.append(_raum_json)
raeume.BEIM_ENDE.append(_beim_ende)
meeting.CHAT_PRUEFEN.append(_chat_pruefen)


def aufraeumen(t=None):
    db.verbindung().execute('DELETE FROM webinar_anmeldungen WHERE loeschen_ab < ?', (t or jetzt(),))


# ---------------------------------------------------------------- Besitzer: Einstellungen, Panel-Links
def webinar_json(r):
    c = db.verbindung()
    panel = c.execute('SELECT * FROM webinar_panel WHERE raum_id = ? ORDER BY id', (r['id'],)).fetchall()
    return {'an': ist_webinar(r), **einstellungen(r), 'anmelde_link': anmelde_link(r['code']), 'angemeldet': angemeldet_zahl(r),
            'panel': [{'id': p['id'], 'name': p['name'], 'link': panel_link(r, p), 'erstellt': iso(p['erstellt'])} for p in panel]}


@router.get('/api/raeume/{code}/webinar')
def webinar_lesen(code: str, req: Request):
    _b, r = besitzer_raum(req, code)
    return {'webinar': webinar_json(r)}


@router.patch('/api/raeume/{code}/webinar')
async def webinar_aendern(code: str, req: Request):
    b, r = besitzer_raum(req, code)
    d = await json_body(req)
    e = einstellungen(r)
    an = ist_webinar(r)
    if 'an' in d:
        if not isinstance(d['an'], bool):
            fehler(400, 'Ungültige Anfrage')
        if d['an'] != an and r['aktiv']:
            fehler(409, 'Während das Meeting läuft, lässt sich der Webinar-Modus nicht umschalten.')
        an = d['an']
    if 'chat' in d:
        if d['chat'] not in CHAT:
            fehler(400, 'Chat: alle, panel oder aus.')
        e['chat'] = d['chat']
    for k in ('fragen_an', 'registrierung'):
        if k in d:
            if not isinstance(d[k], bool):
                fehler(400, 'Ungültige Anfrage')
            e[k] = d[k]
    if 'kapazitaet' in d:
        k = d['kapazitaet']
        if k is not None and (not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 100000):
            fehler(400, 'Kapazität: 1 bis 100000 oder leer.')
        e['kapazitaet'] = k
    if 'fragen' in d:
        f = d['fragen']
        if not isinstance(f, list) or len(f) > MAX_FRAGEN:
            fehler(400, f'Höchstens {MAX_FRAGEN} Zusatzfragen.')
        e['fragen'] = [q for q in (text_feld({'q': q}, 'q', 200).replace('\n', ' ') for q in f) if q]
    db.verbindung().execute('UPDATE raeume SET webinar = ?, webinar_einst = ? WHERE id = ?', (int(an), json.dumps(e), r['id']))
    db.protokollieren('Webinar-Einstellungen geändert', b['id'], code)
    return {'webinar': webinar_json(raeume.neu_laden(r))}


@router.post('/api/raeume/{code}/webinar/panel')
async def panel_neu(code: str, req: Request):
    _b, r = besitzer_raum(req, code)
    d = await json_body(req)
    name = text_feld(d, 'name', 60).replace('\n', ' ') or 'Panel'
    c = db.verbindung()
    if c.execute('SELECT COUNT(*) FROM webinar_panel WHERE raum_id = ?', (r['id'],)).fetchone()[0] >= MAX_PANEL_LINKS:
        fehler(400, f'Höchstens {MAX_PANEL_LINKS} Panel-Links.')
    salz = secrets.token_hex(16)
    pid = c.execute('INSERT INTO webinar_panel (raum_id, name, salz, token_hash, erstellt) VALUES (?, ?, ?, ?, ?)',
                    (r['id'], name, salz, h(panel_token(r['id'], salz)), jetzt())).lastrowid
    p = c.execute('SELECT * FROM webinar_panel WHERE id = ?', (pid,)).fetchone()
    return {'panel': {'id': p['id'], 'name': p['name'], 'link': panel_link(r, p), 'erstellt': iso(p['erstellt'])}}


@router.delete('/api/raeume/{code}/webinar/panel/{pid}')
def panel_loeschen(code: str, pid: int, req: Request):
    _b, r = besitzer_raum(req, code)
    if not db.verbindung().execute('DELETE FROM webinar_panel WHERE id = ? AND raum_id = ?', (pid, r['id'])).rowcount:
        fehler(404, 'Nicht gefunden')
    return {'ok': True}


# ---------------------------------------------------------------- Besitzer: Anmeldungen
def anmeldung_json(a):
    return {'id': a['id'], 'name': a['name'], 'email': a['email'], 'antworten': json.loads(a['antworten'] or '[]'),
            'angemeldet': iso(a['erstellt']), 'beigetreten': iso(a['beigetreten'])}


def anmeldungen(r):
    return db.verbindung().execute('SELECT * FROM webinar_anmeldungen WHERE raum_id = ? ORDER BY erstellt', (r['id'],)).fetchall()


@router.get('/api/raeume/{code}/webinar/anmeldungen')
def anmeldungen_liste(code: str, req: Request):
    _b, r = besitzer_raum(req, code)
    e = einstellungen(r)
    return {'anmeldungen': [anmeldung_json(a) for a in anmeldungen(r)], 'fragen': e['fragen'], 'kapazitaet': e['kapazitaet']}


def csv_zelle(v):
    """Gegen Formel-Einschleusung in Tabellenprogrammen: führende = + - @ entschärfen."""
    v = '' if v is None else str(v)
    return "'" + v if v[:1] in ('=', '+', '-', '@', '\t', '\r') else v


@router.get('/api/raeume/{code}/webinar/anmeldungen.csv')
def anmeldungen_csv(code: str, req: Request):
    b, r = besitzer_raum(req, code)
    fragen = einstellungen(r)['fragen']
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=';')
    w.writerow(['Name', 'E-Mail', 'Angemeldet (UTC)', 'Beigetreten (UTC)', *fragen])
    for a in anmeldungen(r):
        antw = json.loads(a['antworten'] or '[]')
        w.writerow([csv_zelle(x) for x in (a['name'], a['email'], iso(a['erstellt']), iso(a['beigetreten']),
                                             *[antw[i] if i < len(antw) else '' for i in range(len(fragen))])])
    db.protokollieren('Anmeldungen exportiert', b['id'], code)
    return Response('﻿' + puffer.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="anmeldungen-{code}.csv"'})


@router.delete('/api/raeume/{code}/webinar/anmeldungen/{aid}')
def anmeldung_loeschen(code: str, aid: int, req: Request):
    _b, r = besitzer_raum(req, code)
    if not db.verbindung().execute('DELETE FROM webinar_anmeldungen WHERE id = ? AND raum_id = ?', (aid, r['id'])).rowcount:
        fehler(404, 'Nicht gefunden')
    return {'ok': True}


# ---------------------------------------------------------------- Öffentlich: Registrierungsseite /w/<code>
def oeffentlich(code):
    r = raum_laden(code)
    if not ist_webinar(r) or not einstellungen(r)['registrierung']:
        fehler(404, 'Für dieses Webinar gibt es keine Anmeldung.')
    return r


@router.get('/api/webinare/{code}')
def webinar_info(code: str, req: Request):
    grenze.pruefen('webinar-info:' + client_ip(req), 120, 60)
    r = oeffentlich(code)
    e = einstellungen(r)
    j = raeume.raum_json(r)
    frei = None if e['kapazitaet'] is None else max(0, e['kapazitaet'] - angemeldet_zahl(r))
    return {'code': r['code'], 'titel': r['titel'], 'besitzer_name': j['besitzer_name'], 'geplant': j['geplant'],
            'naechster_termin': j['naechster_termin'], 'kalender': j['kalender'], 'fragen': e['fragen'], 'frei': frei,
            'offen': not r['beendet'] and frei != 0}


@router.post('/api/webinare/{code}/anmelden')
async def anmelden(code: str, req: Request):
    r = oeffentlich(code)
    d = await json_body(req)
    ip = client_ip(req)
    grenze.pruefen('webinar-anmelden:' + ip, 10, 3600)
    from .konto import zustellbar
    name = text_feld(d, 'name', 60, pflicht=True).replace('\n', ' ')
    email = text_feld(d, 'email', 254, pflicht=True)
    if not zustellbar(email):
        fehler(400, 'Bitte eine gültige E-Mail-Adresse eingeben.')
    e = einstellungen(r)
    antworten = d.get('antworten') or []
    if not isinstance(antworten, list) or len(antworten) > len(e['fragen']):
        fehler(400, 'Ungültige Antworten.')
    antworten = [text_feld({'a': a}, 'a', 1000) for a in antworten]
    if r['beendet']:
        fehler(409, 'Dieses Webinar ist beendet.')
    c = db.verbindung()
    t = jetzt()
    tok = secrets.token_urlsafe(24)
    c.execute('BEGIN IMMEDIATE')
    try:
        alt = c.execute('SELECT id FROM webinar_anmeldungen WHERE raum_id = ? AND email = ?', (r['id'], email)).fetchone()
        if alt:                         # erneut angemeldet: neuer Link (der alte gilt nicht mehr), Angaben aktualisieren
            c.execute('UPDATE webinar_anmeldungen SET name = ?, antworten = ?, token_hash = ? WHERE id = ?',
                      (name, json.dumps(antworten), h(tok), alt['id']))
        else:
            if e['kapazitaet'] is not None and angemeldet_zahl(r) >= e['kapazitaet']:
                fehler(409, 'Das Webinar ist leider ausgebucht.')
            c.execute('INSERT INTO webinar_anmeldungen (raum_id, name, email, antworten, token_hash, erstellt, loeschen_ab) '
                      'VALUES (?, ?, ?, ?, ?, ?, ?)', (r['id'], name, email, json.dumps(antworten), h(tok), t, loeschen_ab(r, t)))
        c.execute('COMMIT')
    except BaseException:
        c.execute('ROLLBACK')
        raise
    if grenze.ok('mail:' + email.lower(), 3, 3600):
        details = raeume.termin_details(r, None)
        ics_daten = raeume.ics_text(r) if r['art'] == 'geplant' else None
        mail.im_hintergrund(anmelde_mail, email, name, r['titel'], f'{SEITE}/r/{r["code"]}?anmeldung={tok}', details, ics_daten,
                            f'{anmelde_link(r["code"])}?abmelden={tok}', texte.sprache())
    db.protokollieren('Webinar-Anmeldung', None, code)
    for f in NACH_ANMELDUNG:
        f(r, name, email, antworten)
    return {'ok': True}


@router.post('/api/webinare/{code}/abmelden')
async def abmelden(code: str, req: Request):
    """Teilnehmende löschen ihre Anmeldung selbst (Link aus der Mail)."""
    r = raum_laden(code)
    d = await json_body(req)
    grenze.pruefen('webinar-abmelden:' + client_ip(req), 20, 3600)
    tok = token_lesen(d, 'token')
    if not tok or not db.verbindung().execute('DELETE FROM webinar_anmeldungen WHERE raum_id = ? AND token_hash = ?',
                                              (r['id'], h(tok))).rowcount:
        fehler(404, 'Diese Anmeldung gibt es nicht (mehr).')
    return {'ok': True}


def anmelde_mail(an, name, titel, link, details, ics_daten, abmelde_link, sprache='de'):
    """Bestätigung in der Sprache der Anmeldeseite (Accept-Language bei der Anmeldung)."""
    s = sprache
    saetze = [texte.tx(s, 'hallo', name=name), texte.tx(s, 'webinar.satz', titel=titel)]
    hinweis = texte.tx(s, 'webinar.hinweis', link=abmelde_link)
    text = '\n'.join(saetze) + '\n\n' + '\n'.join(f'{k}: {v}' for k, v in details) + f'\n\n{texte.tx(s, "webinar.teilnehmen")}: {link}\n\n{hinweis}\n'
    anhaenge = [('webinar.ics', 'text', 'calendar', ics_daten.encode(), {'method': 'PUBLISH', 'charset': 'utf-8'})] if ics_daten else []
    mail.senden(an, texte.tx(s, 'webinar.betreff', titel=titel), text,
                mail.html_mail(texte.tx(s, 'webinar.titel'), saetze, knopf=(texte.tx(s, 'webinar.knopf'), link), details=details,
                               hinweis=hinweis, vorschau=texte.tx(s, 'webinar.vorschau', titel=titel), sprache=s), anhaenge, sprache=s)


# ---------------------------------------------------------------- Im Meeting: Podium, Zustand
@router.post('/api/raeume/{code}/webinar/rolle')
async def rolle(code: str, req: Request):
    """Host/Co-Host: Zuschauer aufs Podium holen (panelist) oder zurückschicken (zuschauer)."""
    r, t = meeting.steuernd(req, code)
    if not ist_webinar(r):
        fehler(400, 'Das ist kein Webinar.')
    d = await json_body(req)
    if d.get('rolle') not in ('panelist', 'zuschauer'):
        fehler(400, 'Rolle: panelist oder zuschauer.')
    z = meeting.ziel_laden(r, d.get('ziel'))
    meeting.rang_pruefen(t, z)
    if z['rolle'] not in ('panelist', 'zuschauer'):
        fehler(400, 'Nur Panelisten und Zuschauer lassen sich so verschieben.')
    if z['rolle'] != d['rolle']:
        await meeting.rolle_setzen(r, z, d['rolle'])
        meeting.protokoll(t, r, f'Webinar: {"aufs Podium" if d["rolle"] == "panelist" else "zu den Zuschauern"}')
    return {'ok': True}


@router.get('/api/raeume/{code}/webinar/zustand')
async def zustand(code: str, req: Request):
    r, t = meeting.teilnahme(req, code)
    if not ist_webinar(r):
        return {'webinar': False}
    e = einstellungen(r)
    out = {'webinar': True, 'chat': e['chat'], 'fragen_an': e['fragen_an']}
    if t['rolle'] in PANEL_ROLLEN:
        rollen = {x['identity']: x['rolle'] for x in raeume.teilnahmen(r)}
        verbunden = await lk.teilnehmer(r['code'])
        if verbunden is not None:
            ids = [p['identity'] for p in verbunden]
            out['zuschauer'] = sum(1 for i in ids if rollen.get(i) == 'zuschauer')
            out['panel'] = sum(1 for i in ids if rollen.get(i) in PANEL_ROLLEN)
        if e['registrierung']:
            out['angemeldet'] = angemeldet_zahl(r)
    return out
