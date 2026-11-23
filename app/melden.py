"""Melden und Blockieren.

Melden (X-Teilnahme, auch Gäste): Jede Person im Meeting kann eine andere melden (Kategorie + optionaler Text).
Die Meldung wird gespeichert, geht per Mail an die Admins (AGORA_ADMIN_MAILS, nur bestätigte Konten) und Host/Co-Hosts
bekommen im Meeting einen Hinweis (Ereignis `meldung`, ohne den Namen der meldenden Person).

Blockieren (angemeldet): Ein Konto blockiert ein anderes. Das blockierte Konto kann Meetings in Räumen des
Blockierenden nicht mehr betreten und ihm im Chat keine privaten Nachrichten schicken. Verwalten im Konto.
"""
from fastapi import APIRouter, Request

from . import admins, db, mail, meeting, raeume
from . import livekit_dienst as lk
from .kern import SEITE, benutzer_aus_sitzung, fehler, grenze, iso, jetzt, json_body, text_feld
from .meeting import teilnahme, ziel_laden

router = APIRouter()
GRUENDE = {
    'belaestigung': 'Belästigung oder Mobbing',
    'hass': 'Hassrede oder Diskriminierung',
    'unangemessen': 'Unangemessene oder sexuelle Inhalte',
    'stoerung': 'Stört das Meeting',
    'spam': 'Spam oder Werbung',
    'sonstiges': 'Sonstiges',
}
AUFBEWAHRUNG_S = 365 * 86400


def meldung_json(m):
    return {'id': m['id'], 'zeit': iso(m['zeit']), 'raum_code': m['raum_code'], 'raum_titel': m['raum_titel'],
            'von_name': m['von_name'], 'von_benutzer': m['von_benutzer'], 'ziel_name': m['ziel_name'],
            'ziel_benutzer': m['ziel_benutzer'], 'grund': m['grund'], 'grund_text': GRUENDE.get(m['grund'], m['grund']),
            'text': m['text'], 'status': m['status'], 'notiz': m['notiz'] or '', 'bearbeitet': iso(m['bearbeitet'])}


def blockiert(von_id, ziel_id):
    """Hat Konto von_id das Konto ziel_id blockiert?"""
    if not von_id or not ziel_id:
        return False
    return bool(db.verbindung().execute('SELECT 1 FROM blockiert WHERE benutzer_id = ? AND blockiert_id = ?',
                                        (von_id, ziel_id)).fetchone())


# ---------------------------------------------------------------- Haken in Beitreten und Chat
def beitritt_pruefen(r, b):
    if b and blockiert(r['besitzer_id'], b['id']):
        fehler(403, 'Du kannst diesem Meeting nicht beitreten.')


def chat_pruefen(t, z):
    if z and blockiert(z['benutzer_id'], t['benutzer_id']):
        fehler(403, 'Diese Person möchte keine privaten Nachrichten von dir.')


raeume.BEITRITT_PRUEFEN.append(beitritt_pruefen)
meeting.CHAT_PRUEFEN.append(chat_pruefen)


# ---------------------------------------------------------------- Melden
def meldung_mail(an, m, link):
    zeilen = [('Meeting', f'{m["raum_titel"]} ({m["raum_code"]})'), ('Gemeldet', m['ziel_name'] + (' (Konto)' if m['ziel_benutzer'] else ' (Gast)')),
              ('Grund', GRUENDE.get(m['grund'], m['grund'])), ('Von', m['von_name'])]
    if m['text']:
        zeilen.append(('Text', m['text']))
    satz = 'Im Meeting wurde jemand gemeldet. Bitte sieh dir die Meldung im Admin-Bereich an.'
    text = satz + '\n\n' + '\n'.join(f'{k}: {v}' for k, v in zeilen) + f'\n\nAdmin-Bereich: {link}\n'
    mail.senden(an, f'Agora: Meldung im Meeting {m["raum_code"]}', text,
                mail.html_mail('Neue Meldung', [satz], knopf=('Meldung ansehen', link), details=zeilen, vorschau='Neue Meldung im Meeting'))


@router.post('/api/raeume/{code}/melden')
async def melden(code: str, req: Request):
    r, t = teilnahme(req, code)
    d = await json_body(req)
    z = ziel_laden(r, d.get('ziel'))
    if z['identity'] == t['identity']:
        fehler(400, 'Das geht nicht bei dir selbst.')
    if d.get('grund') not in GRUENDE:
        fehler(400, 'Bitte einen Grund auswählen.')
    text = text_feld(d, 'text', 1000)
    grenze.pruefen('melden:' + t['identity'], 5, 600)
    c = db.verbindung()
    if c.execute('SELECT 1 FROM meldungen WHERE raum_code = ? AND sitzung = ? AND von_identity = ? AND ziel_identity = ?',
                 (r['code'], r['sitzung'], t['identity'], z['identity'])).fetchone():
        fehler(409, 'Du hast diese Person in diesem Meeting schon gemeldet.')
    mid = c.execute('INSERT INTO meldungen (zeit, raum_code, raum_titel, sitzung, von_name, von_identity, von_benutzer, ziel_name, '
                    'ziel_identity, ziel_benutzer, grund, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (jetzt(), r['code'], r['titel'], r['sitzung'], t['name'], t['identity'], t['benutzer_id'], z['name'],
                     z['identity'], z['benutzer_id'], d['grund'], text)).lastrowid
    m = c.execute('SELECT * FROM meldungen WHERE id = ?', (mid,)).fetchone()
    db.protokollieren(f'Meldung #{mid} abgegeben ({d["grund"]})', t['benutzer_id'], r['code'])
    for a in admins.bestaetigte():
        if grenze.ok('meldemail:' + a['email'].lower(), 30, 3600):
            mail.im_hintergrund(meldung_mail, a['email'], m, f'{SEITE}/admin#meldungen')
    hosts = [x['identity'] for x in raeume.teilnahmen(r, ('host', 'cohost')) if x['identity'] not in (t['identity'], z['identity'])]
    await lk.senden(r['code'], {'typ': 'meldung', 'ziel': z['identity'], 'ziel_name': z['name'], 'grund': d['grund']}, an=hosts)
    return {'ok': True}


# ---------------------------------------------------------------- Blockieren
@router.post('/api/raeume/{code}/blockieren')
async def blockieren(code: str, req: Request):
    b = benutzer_aus_sitzung(req)
    r, t = teilnahme(req, code)
    if t['benutzer_id'] != b['id']:
        fehler(403, 'Bitte im Meeting mit deinem Konto angemeldet sein.')
    d = await json_body(req)
    z = ziel_laden(r, d.get('ziel'))
    if not z['benutzer_id']:
        fehler(400, 'Gäste ohne Konto kann man nicht blockieren – melde sie oder bitte den Host, sie zu entfernen.')
    if z['benutzer_id'] == b['id']:
        fehler(400, 'Das geht nicht bei dir selbst.')
    grenze.pruefen(f'blockieren:{b["id"]}', 30, 3600)
    db.verbindung().execute('INSERT OR IGNORE INTO blockiert VALUES (?, ?, ?, ?)', (b['id'], z['benutzer_id'], z['name'], jetzt()))
    db.protokollieren('Konto blockiert', b['id'], r['code'])
    return {'ok': True, 'name': z['name']}


@router.get('/api/konto/blockiert')
def blockiert_liste(req: Request):
    b = benutzer_aus_sitzung(req)
    rows = db.verbindung().execute('SELECT * FROM blockiert WHERE benutzer_id = ? ORDER BY zeit DESC', (b['id'],)).fetchall()
    return {'blockiert': [{'id': x['blockiert_id'], 'name': x['name'], 'seit': iso(x['zeit'])} for x in rows]}


@router.delete('/api/konto/blockiert/{ziel_id}')
def entblocken(ziel_id: int, req: Request):
    b = benutzer_aus_sitzung(req)
    if not db.verbindung().execute('DELETE FROM blockiert WHERE benutzer_id = ? AND blockiert_id = ?', (b['id'], ziel_id)).rowcount:
        fehler(404, 'Nicht gefunden')
    db.protokollieren('Blockierung aufgehoben', b['id'])
    return {'ok': True}


def aufraeumen(t):
    db.verbindung().execute("DELETE FROM meldungen WHERE status = 'erledigt' AND zeit < ?", (t - AUFBEWAHRUNG_S,))
