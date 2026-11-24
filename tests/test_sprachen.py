"""Deutsch/Englisch im Backend (ADR 0006): Fehlermeldungen je Accept-Language, Konto-Sprache, Mails in der Sprache
des Empfängers bzw. der einladenden Seite. Hilfen aus test_api (gleiche Testumgebung)."""
import ast
import glob
import re

from test_api import konto, link_aus, mails_an, neu_client, raum  # noqa: F401

from app import db, mail, texte

EN = {'Accept-Language': 'en-US,en;q=0.9'}


# ---------------------------------------------------------------- Katalog vollständig
def _meldungen():
    """Alle Texte aus fehler(code, …) und breakout.zahl(…, text) im Code: (feste Texte, f-String-Beispiele)."""
    fest, beispiele = set(), []
    for pfad in glob.glob('app/*.py'):
        for n in ast.walk(ast.parse(open(pfad, encoding='utf-8').read())):
            if not isinstance(n, ast.Call):
                continue
            name = getattr(n.func, 'id', None)
            if name == 'fehler' and len(n.args) >= 2:
                arg = n.args[1]
            elif name == 'zahl' and len(n.args) == 5:
                arg = n.args[4]
            else:
                continue
            if isinstance(arg, ast.JoinedStr):       # Werte durch Beispiele ersetzen
                beispiele.append(''.join(v.value if isinstance(v, ast.Constant) else '7' for v in arg.values))
            elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                fest.add(arg.value)
            elif isinstance(arg, ast.Dict):
                fest.update(v.value for v in arg.values if isinstance(v, ast.Constant))
            elif isinstance(arg, ast.BinOp):         # 'Text' + ('a' if x else 'b')
                pass
    return fest, beispiele


def test_alle_fehlermeldungen_uebersetzt():
    fest, beispiele = _meldungen()
    assert len(fest) > 150
    fehlt = sorted(t for t in fest if texte.fehler_text(t, 'en') == t)
    assert not fehlt, fehlt
    fehlt = [b for b in beispiele if texte.fehler_text(b, 'en') == b]
    assert not fehlt, fehlt
    # zusammengesetzte Meldungen
    assert texte.fehler_text('Erinnerung: 10, 15, 30 oder 60 Minuten (0 = aus).', 'en').startswith('Reminder')
    assert texte.fehler_text('Aktion: hervorheben, verwerfen.', 'en') == 'Action: hervorheben, verwerfen.'
    assert texte.fehler_text('Die Aufnahme auf dem Server ließ sich nicht starten: unbekannt', 'en').endswith('unknown')
    # Englisch nie mit Umlauten, Deutsch unverändert
    assert not [v for v in texte.FEHLER_EN.values() if re.search('[äöüÄÖÜß]', v)]
    assert texte.fehler_text('Bitte anmelden.', 'de') == 'Bitte anmelden.'


def test_mailtexte_vollstaendig():
    assert set(texte.MAIL['de']) == set(texte.MAIL['en'])
    for k, v in texte.MAIL['de'].items():
        assert set(re.findall(r'\{(\w+)\}', v)) == set(re.findall(r'\{(\w+)\}', texte.MAIL['en'][k])), k


def test_accept_language():
    assert texte.aus_header(None) == 'de'
    assert texte.aus_header('') == 'de'
    assert texte.aus_header('en-US,en;q=0.9') == 'en'
    assert texte.aus_header('de-AT,de;q=0.9,en;q=0.8') == 'de'
    assert texte.aus_header('fr-FR,fr;q=0.9,en;q=0.8,de;q=0.7') == 'en'
    assert texte.aus_header('en;q=0.2,de;q=0.8') == 'de'
    assert texte.aus_header('fr') == 'de'


# ---------------------------------------------------------------- API
def test_fehler_je_sprache():
    c = neu_client()
    r = c.post('/api/konto/anmelden', json={'email': 'niemand@example.org', 'passwort': 'falsch-falsch-1'}, headers=EN)
    assert r.status_code in (400, 401, 403) and r.json()['fehler'] == 'Wrong email or password.'
    r = c.post('/api/konto/anmelden', json={'email': 'niemand@example.org', 'passwort': 'falsch-falsch-1'},
               headers={'Accept-Language': 'de-DE'})
    assert r.json()['fehler'] == 'E-Mail oder Passwort falsch.'
    assert c.get('/api/konto/gibtsnicht', headers=EN).json()['fehler'] == 'Not found'
    assert c.get('/api/raeume/abc-defg-hij', headers=EN).json()['fehler'] in ('This meeting doesn’t exist.', 'Not found')
    # CSRF-Schutz (Middleware) und Eingabefehler
    roh = neu_client()
    roh.headers.pop('X-Agora')
    assert roh.post('/api/konto/abmelden', headers=EN).json()['fehler'] == 'Request denied'


def test_registrierung_englisch():
    c = neu_client()
    email = 'en-neu@example.org'
    assert c.post('/api/konto/registrieren', json={'name': 'Eve', 'email': email, 'passwort': 'geheim-passwort-1'}, headers=EN).status_code == 200
    an, betreff, text, html_text, _ = mails_an(email)[-1]
    assert betreff == 'Agora: confirm your email address'
    assert text.startswith('Hi Eve,')
    assert '<html lang="en">' in html_text and 'Legal notice (German)' in html_text
    c.get(link_aus(email), follow_redirects=False)
    ich = c.get('/api/konto/ich').json()['ich']
    assert ich['sprache'] == 'en'
    pers = db.verbindung().execute("SELECT titel FROM raeume WHERE art = 'persoenlich' AND besitzer_id = ?", (ich['id'],)).fetchone()
    assert pers['titel'] == 'Eve’s personal room'
    # Passwort vergessen → Mail in der Konto-Sprache, auch wenn die Anfrage deutsch ist
    neu_client().post('/api/konto/passwort-vergessen', json={'email': email}, headers={'Accept-Language': 'de'})
    assert mails_an(email)[-1][1] == 'Agora: reset your password'


def test_einladung_in_sprache_der_seite():
    c, _, _ = konto()          # deutsches Konto
    r = raum(c, titel='Sync', geplant={'start': '2030-05-01T08:00:00Z', 'dauer_min': 30, 'wiederholung': 'woechentlich'})
    assert c.post(f'/api/raeume/{r["code"]}/einladen', json={'emails': ['gast-en@example.org']}, headers=EN).json()['gesendet'] == 1
    an, betreff, text, html_text, anhaenge = mails_an('gast-en@example.org')[-1]
    assert betreff == 'Invitation: Sync'
    assert 'invites you to a meeting: “Sync”' in text and 'Repeats: weekly' in text and 'Meeting code: ' in text
    assert 'When: 1 May 2030, 10:00 (Europe/' in text or 'When: 1 May 2030' in text
    assert 'Add to Google Calendar' in text
    assert anhaenge[0][0] == 'invitation.ics' and 'Join: https://agora.slxt.dev/r/' in anhaenge[0][3].decode().replace('\r\n ', '')
    # deutsch wie bisher
    c.post(f'/api/raeume/{r["code"]}/einladen', json={'emails': ['gast-de@example.org']})
    assert mails_an('gast-de@example.org')[-1][1] == 'Einladung: Sync'


def test_profil_sprache():
    c, _, _ = konto()
    assert c.patch('/api/konto/ich', json={'sprache': 'en'}).status_code == 200
    assert c.get('/api/konto/ich').json()['ich']['sprache'] == 'en'
    assert c.patch('/api/konto/ich', json={'sprache': 'fr'}, headers=EN).json()['fehler'] == 'Language: de or en.'
