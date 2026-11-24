"""Erinnerungs-Mails vor geplanten Meetings (opt-in).

- Konto: Im Profil wählt man 10/15/30/60 Minuten (oder aus). Dann kommt vor jedem Termin eigener geplanter Meetings
  eine Mail – bei wiederkehrenden Meetings vor jedem Vorkommen.
- Eingeladene: Nur wenn der Host beim Einladen „Erinnerung schicken“ ankreuzt, merkt sich Agora die Adressen
  (Tabelle erinnerung_gaeste) – für genau den nächsten Termin. Nach dessen Ende (oder wenn kein Termin mehr kommt, das
  Meeting gelöscht wird) wird die Adresse gelöscht.

Der Hintergrund ruft `pruefen()` jede Minute auf. Jede Erinnerung geht höchstens einmal je Termin und Adresse raus:
Vor dem Versand wird ein Schlüssel (Hash aus Raum, Termin, Empfänger) in erinnerungen_gesendet eingetragen – das
übersteht auch einen Neustart. Schlägt der Versand fehl, wird der Schlüssel wieder entfernt (neuer Versuch in der
nächsten Minute, solange das Fenster offen ist). Liegt der Server während des Fensters still, entfällt die Erinnerung
(nie nach Beginn). Wer den Termin erst innerhalb des Fensters anlegt bzw. einlädt, bekommt keine Erinnerung.
"""
from datetime import datetime

from fastapi import APIRouter, Request

from . import db, ics, konto, mail, raeume, texte
from .kern import benutzer_aus_sitzung, fehler, h, jetzt, json_body

router = APIRouter()
MINUTEN = (10, 15, 30, 60)
GAST_MAX_S = 400 * 86400        # Sicherheitsnetz: vorgemerkte Adressen höchstens so lange


# ---------------------------------------------------------------- Einstellung im Konto
def minuten_lesen(wert, null_erlaubt):
    if isinstance(wert, bool) or not isinstance(wert, int) or (wert not in MINUTEN and not (null_erlaubt and wert == 0)):
        fehler(400, 'Erinnerung: 10, 15, 30 oder 60 Minuten' + (' (0 = aus).' if null_erlaubt else '.'))
    return wert


def konto_minuten(b):
    e = db.verbindung().execute('SELECT minuten FROM erinnerung_konto WHERE benutzer_id = ?', (b['id'],)).fetchone()
    return e['minuten'] if e else 0


@router.get('/api/konto/erinnerung')
def einstellung_lesen(req: Request):
    return {'minuten': konto_minuten(benutzer_aus_sitzung(req))}


@router.put('/api/konto/erinnerung')
async def einstellung_setzen(req: Request):
    b = benutzer_aus_sitzung(req)
    m = minuten_lesen((await json_body(req)).get('minuten'), True)
    c = db.verbindung()
    c.execute('DELETE FROM erinnerung_konto WHERE benutzer_id = ?', (b['id'],))
    if m:
        c.execute('INSERT INTO erinnerung_konto VALUES (?, ?)', (b['id'], m))
    return {'minuten': m}


konto.EXPORT.append(lambda b: {'erinnerung_minuten': konto_minuten(b)})


# ---------------------------------------------------------------- Eingeladene vormerken (aus raeume.einladen)
def vormerken(r, adressen, minuten):
    """Adressen für eine Erinnerung vor dem nächsten Termin speichern (nur geplante Meetings)."""
    n = raeume.termin(r)
    if r['art'] != 'geplant' or not adressen or n is None:
        return 0
    c = db.verbindung()
    for a in adressen:
        c.execute('INSERT INTO erinnerung_gaeste (raum_id, email, minuten, termin, erstellt) VALUES (?, ?, ?, ?, ?) '
                  'ON CONFLICT(raum_id, email) DO UPDATE SET minuten = excluded.minuten, termin = excluded.termin, erstellt = excluded.erstellt',
                  (r['id'], a, minuten, n, jetzt()))
    return len(adressen)


# ---------------------------------------------------------------- Versand
def wann_text(t, zeitzone, sprache='de'):
    lokal = datetime.fromtimestamp(t, ics.zone(zeitzone))
    tage = texte.tx(sprache, 'termin.tage').split(',')
    return texte.tx(sprache, 'termin.erinnerungWann', tag=tage[lokal.weekday()], datum=texte.datum(lokal, sprache),
                    uhr=lokal.strftime('%H:%M'), zone=zeitzone)


def _erinnern(sprache, *args, **kw):
    """Erinnerung in der Sprache des Empfängers (Konto) bzw. des einladenden Kontos (Gäste) – auch Kalender-Links."""
    with texte.mit_sprache(sprache):
        mail.erinnerung_mail(*args, kalender=raeume.kalender_links(kw.pop('raum')), sprache=texte.gueltig(sprache), **kw)


def _einmal(schluessel, t, senden):
    """Idempotent: Schlüssel zuerst eintragen, dann senden; bei Fehler Schlüssel wieder entfernen.
    → 'gesendet' | 'schon' (früher erledigt) | 'fehler' (nächste Minute neu versuchen)"""
    c = db.verbindung()
    k = h(schluessel)
    if c.execute('INSERT OR IGNORE INTO erinnerungen_gesendet VALUES (?, ?)', (k, t)).rowcount == 0:
        return 'schon'
    try:
        senden()
    except Exception as e:   # noqa: BLE001
        c.execute('DELETE FROM erinnerungen_gesendet WHERE schluessel = ?', (k,))
        print(f'erinnerung-fehler {type(e).__name__}', flush=True)
        return 'fehler'
    return 'gesendet'


def faellig(termin, minuten, seit, t):
    """Im Fenster [Termin − minuten, Termin) und das Fenster begann erst nach `seit` (Anlage/Einladung)."""
    beginn = termin - minuten * 60
    return beginn <= t < termin and seit <= beginn


def pruefen(t=None):
    """Hintergrund (jede Minute): fällige Erinnerungen verschicken, erledigte Gast-Adressen löschen. → Anzahl gesendet."""
    t = t or jetzt()
    c = db.verbindung()
    gesendet = 0
    besitzer = {}
    for e in c.execute('SELECT e.minuten, b.* FROM erinnerung_konto e JOIN benutzer b ON b.id = e.benutzer_id WHERE b.bestaetigt = 1').fetchall():
        for r in c.execute("SELECT * FROM raeume WHERE besitzer_id = ? AND art = 'geplant'", (e['id'],)).fetchall():
            n = raeume.termin(r, t)
            if n is None or not faellig(n, e['minuten'], r['erstellt'], t):
                continue
            if _einmal(f'konto:{r["id"]}:{n}:{e["id"]}', t, lambda e=e, r=r, n=n: _erinnern(
                    e['sprache'], e['email'], e['name'], r['titel'], raeume.link(r['code']), wann_text(n, e['zeitzone'], texte.gueltig(e['sprache'])),
                    e['minuten'], True, raum=r)) == 'gesendet':
                gesendet += 1
    for g in c.execute('SELECT * FROM erinnerung_gaeste').fetchall():
        r = c.execute('SELECT * FROM raeume WHERE id = ?', (g['raum_id'],)).fetchone()
        n = raeume.termin(r, t) if r else None
        # Ziel: einmalig → der (ggf. verschobene) Termin; wiederkehrend → das Vorkommen, zu dem eingeladen wurde
        ziel = n if r is not None and r['wiederholung'] == 'keine' else g['termin']
        if n is None or ziel is None or t >= ziel + (r['dauer_min'] or 60) * 60 or t - g['erstellt'] > GAST_MAX_S:
            c.execute('DELETE FROM erinnerung_gaeste WHERE raum_id = ? AND email = ?', (g['raum_id'], g['email']))
            continue
        if n != ziel or not faellig(ziel, g['minuten'], g['erstellt'], t):
            continue
        if r['besitzer_id'] not in besitzer:
            besitzer[r['besitzer_id']] = c.execute('SELECT name, sprache FROM benutzer WHERE id = ?', (r['besitzer_id'],)).fetchone() \
                or {'name': '', 'sprache': 'de'}
        if _einmal(f'gast:{r["id"]}:{n}:{g["email"].lower()}', t, lambda g=g, r=r, n=n, bs=besitzer[r['besitzer_id']]: _erinnern(
                bs['sprache'], g['email'], None, r['titel'], raeume.link(r['code']), wann_text(n, r['zeitzone'] or 'UTC', texte.gueltig(bs['sprache'])),
                g['minuten'], False, raum=r, von_name=bs['name'])) == 'gesendet':
            gesendet += 1
    c.execute('DELETE FROM erinnerungen_gesendet WHERE zeit < ?', (t - 2 * 86400,))
    return gesendet
