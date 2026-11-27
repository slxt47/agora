# Anmelden über einen echten Identity-Provider (Dex, tests/e2e/sso.sh): Playwright klickt auf der Startseite
# „Mit Google anmelden“ (AGORA_GOOGLE_* zeigt auf Dex) bzw. „Mit Firmenkonto anmelden“ (Dex als OIDC-IdP einer
# Organisation, im Konto vom Owner eingerichtet), meldet sich auf der Login-Seite von Dex an und kommt zurück.
# Geprüft: neues Konto, bestehendes Konto nur nach Agora-Passwort, Wiederkehr ohne Passwort, Profil (verknüpft,
# Konto ohne Passwort), Firmen-Login (Einrichtung, Beitritt, fremde Domain, Erzwingen), Fehlerseite. Exit ≠ 0 bei Fehlern.
import json
import os
import re
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

B = os.environ.get('BASIS', 'http://127.0.0.1:8291')
DEX = os.environ.get('DEX', 'http://127.0.0.1:5556/dex')
AUS = os.environ.get('AUSGABE', '/e2e/bilder/')
PW_DEX = 'dex-passwort-1'
FEHLER = []


def ok(name, bedingung, info=''):
    print(('  ok  ' if bedingung else '  !!  ') + name + ('' if bedingung else f'  → {info}'), flush=True)
    if not bedingung:
        FEHLER.append(name)
    return bedingung


def api(pfad, methode='GET', daten=None):
    req = urllib.request.Request(B + pfad, method=methode, data=json.dumps(daten).encode() if daten is not None else None,
                                 headers={'X-Agora': '1', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b'null')


def konto_mit_passwort(name, email, pw):
    api('/api/konto/registrieren', 'POST', {'name': name, 'email': email, 'passwort': pw})
    text = [m for m in api('/api/test/posteingang')['mails'] if m['an'] == email][-1]['text']
    link = re.search(r'(/api/konto/bestaetigen\?t=[\w\-.~]+)', text).group(1)
    urllib.request.urlopen(urllib.request.Request(B + link))      # bestätigen (Weiterleitung ist egal)


def dex_login(pg, email, bild=None):
    """Login-Seite von Dex ausfüllen (ein Connector → direkt das Formular)."""
    pg.wait_for_url(re.compile(re.escape(DEX) + '.*'), timeout=15000)
    pg.wait_for_selector('input[name=login]', timeout=15000)
    if bild:
        pg.screenshot(path=AUS + bild)
    pg.fill('input[name=login]', email)
    pg.fill('input[name=password]', PW_DEX)
    pg.click('button[type=submit]')


def ich(pg):
    return pg.evaluate("async () => (await (await fetch('/api/konto/ich')).json())")


def anmelden_mit(pg, anbieter='google'):
    pg.goto(B + '/')
    pg.wait_for_selector(f'button[data-anbieter={anbieter}]', timeout=10000)
    pg.click(f'button[data-anbieter={anbieter}]')


with sync_playwright() as p:
    br = p.chromium.launch()

    print('Einstellungen')
    e = api('/api/einstellungen')
    ok('Anbieter „google“ (Dex) angeboten', e.get('anmeldung') == [{'id': 'google', 'name': 'Google'}], e)

    # ---------------------------------------------------------------- 1. neues Konto über „Google“ (Dex)
    print('Neues Konto über Google → Dex')
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    anmelden_mit(pg)
    dex_login(pg, 'gerda@dex.example', bild='sso-01-dex-login.png')
    pg.wait_for_url(B + '/', timeout=15000)
    pg.wait_for_selector('#angemeldet:not([hidden])', timeout=10000)
    j = ich(pg)
    ok('angemeldet als gerda@dex.example', j.get('angemeldet') and j['ich']['email'] == 'gerda@dex.example', j)
    ok('Name vom Anbieter übernommen', j['ich']['name'] == 'Gerda Google', j)
    ok('Konto ohne Passwort', j['ich']['passwort_gesetzt'] is False, j)
    pg.screenshot(path=AUS + 'sso-02-angemeldet.png')
    # Profil: verknüpft, „Passwort festlegen“ statt „ändern“, Trennen gesperrt
    pg.goto(B + '/konto#profil')
    pg.wait_for_selector('#anmeldungen:not([hidden]) li[data-anbieter=google]', timeout=10000)
    ok('Profil zeigt Google verknüpft', 'gerda@dex.example' in pg.inner_text('#liste-anmeldungen'))
    ok('ohne Passwort: kein Trennen-Knopf', pg.locator('#liste-anmeldungen li[data-anbieter=google] button').count() == 0)
    ok('„Passwort festlegen“ statt „Passwort ändern“', pg.is_visible('#pw-festlegen') and not pg.locator('#f-pw').count())
    pg.screenshot(path=AUS + 'sso-03-profil.png', full_page=True)
    ctx.close()

    # ---------------------------------------------------------------- 2. bestehendes Konto: erst Passwort, dann verknüpft
    print('Bestehendes Konto → nur mit Agora-Passwort verknüpfen')
    konto_mit_passwort('Olaf', 'olaf@dex.example', 'olaf-agora-passwort-1')
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    anmelden_mit(pg)
    dex_login(pg, 'olaf@dex.example')
    pg.wait_for_selector('#f-verknuepfen:not([hidden])', timeout=15000)
    ok('Verknüpfen-Formular statt Anmeldung', not ich(pg).get('angemeldet'))
    ok('Fragment aus der Adresse entfernt', '#verknuepfen' not in pg.url, pg.url)
    ok('Text nennt Adresse und Anbieter', 'olaf@dex.example' in pg.inner_text('#verknuepfen-text') and 'Google' in pg.inner_text('#verknuepfen-text'))
    pg.screenshot(path=AUS + 'sso-04-verknuepfen.png')
    pg.fill('#f-verknuepfen [name=passwort]', 'falsches-passwort-1')
    pg.click('#f-verknuepfen button[type=submit]')
    pg.wait_for_selector('#status:not([hidden])', timeout=5000)
    ok('falsches Passwort abgelehnt', 'Passwort falsch' in pg.inner_text('#status') and not ich(pg).get('angemeldet'))
    pg.fill('#f-verknuepfen [name=passwort]', 'olaf-agora-passwort-1')
    pg.click('#f-verknuepfen button[type=submit]')
    pg.wait_for_selector('#angemeldet:not([hidden])', timeout=10000)
    j = ich(pg)
    ok('nach Passwort angemeldet (bestehendes Konto)', j.get('angemeldet') and j['ich']['email'] == 'olaf@dex.example' and j['ich']['passwort_gesetzt'], j)
    ctx.close()
    # Wiederkehr: direkt, ohne Passwort
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    anmelden_mit(pg)
    dex_login(pg, 'olaf@dex.example')
    pg.wait_for_selector('#angemeldet:not([hidden])', timeout=15000)
    ok('Wiederkehr über Google ohne Passwort', ich(pg)['ich']['email'] == 'olaf@dex.example')
    # Profil: Trennen möglich (Passwort gesetzt), danach nicht mehr verknüpft
    pg.goto(B + '/konto#profil')
    pg.wait_for_selector('#liste-anmeldungen li[data-anbieter=google] button', timeout=10000)
    pg.click('#liste-anmeldungen li[data-anbieter=google] button')
    pg.click('.blatt button.gefahr, dialog button.gefahr')
    time.sleep(1)
    r = pg.evaluate("async () => (await (await fetch('/api/konto/anmeldungen')).json())")
    ok('Trennen im Profil', r['verknuepft'] == [], r)
    ereignisse = pg.evaluate("async () => (await (await fetch('/api/konto/sicherheit')).json()).ereignisse.map(e => e.art)")
    ok('Sicherheitsereignisse verknüpft/angemeldet/getrennt', {'extern_verknuepft', 'anmeldung_extern', 'extern_getrennt'} <= set(ereignisse), ereignisse)
    ctx.close()

    # ---------------------------------------------------------------- 3. Firmen-Login (generisches OIDC, Dex als IdP der Firma)
    print('Firmen-Login: Owner richtet Dex ein')
    konto_mit_passwort('Chefin', 'chef@firma.example', 'chef-agora-passwort-1')
    konto_mit_passwort('Anna', 'anna@firma.example', 'anna-agora-passwort-1')
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    pg.goto(B + '/')
    pg.fill('#f-anmelden [name=email]', 'chef@firma.example')
    pg.fill('#f-anmelden [name=passwort]', 'chef-agora-passwort-1')
    pg.click('#f-anmelden button[type=submit]')
    pg.wait_for_selector('#angemeldet:not([hidden])', timeout=10000)
    slug = pg.evaluate("""async () => (await (await fetch('/api/orgs', {method: 'POST', headers: {'X-Agora': '1', 'Content-Type': 'application/json'},
        body: JSON.stringify({name: 'Firma Beispiel'})})).json()).org.slug""")
    pg.goto(B + '/konto#teams')
    pg.click(f'[data-org-oeffnen="{slug}"]')
    pg.wait_for_selector('#f-org-sso', timeout=10000)
    ok('Redirect-URI angezeigt', pg.inner_text('#sso-redirect') == B + '/api/anmeldung/firma/zurueck', pg.inner_text('#sso-redirect'))
    ok('Domain vorgeschlagen (eigene Adresse)', pg.input_value('#f-org-sso [name=domain]') == 'firma.example')
    pg.fill('#f-org-sso [name=issuer]', DEX)
    pg.fill('#f-org-sso [name=client_id]', 'agora-firma')
    pg.fill('#f-org-sso [name=client_secret]', 'firma-geheimnis-nur-test')
    pg.click('#f-org-sso button[type=submit]')
    pg.wait_for_selector('#f-org-sso [name=client_secret][placeholder*="gespeichert"]', timeout=10000)
    sso = pg.evaluate(f"async () => (await (await fetch('/api/orgs/{slug}/sso')).json()).sso")
    ok('Firmen-Login gespeichert (Geheimnis nicht lesbar)', sso and sso['issuer'] == DEX and sso['geheimnis_gesetzt'] and 'nur-test' not in json.dumps(sso), sso)
    pg.screenshot(path=AUS + 'sso-06-org-einrichten.png', full_page=True)
    ctx.close()
    ok('Startseite bietet Firmen-Login an', api('/api/einstellungen').get('firmen_login') is True)

    def firma_login(pg, email):
        pg.goto(B + '/')
        pg.wait_for_selector('button[data-anbieter=firma]', timeout=10000)
        pg.click('button[data-anbieter=firma]')
        pg.fill('#f-firma [name=email]', email)
        pg.click('#f-firma button[type=submit]')
        dex_login(pg, email)

    print('Firmen-Login: neues Konto (Bernd) → Mitglied')
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    firma_login(pg, 'bernd@firma.example')
    pg.wait_for_selector('#angemeldet:not([hidden])', timeout=15000)
    j = ich(pg)
    ok('Bernd angemeldet (neues Konto)', j.get('angemeldet') and j['ich']['email'] == 'bernd@firma.example' and j['ich']['name'] == 'Bernd Neu', j)
    orgs = pg.evaluate("async () => (await (await fetch('/api/orgs')).json()).orgs")
    ok('Bernd automatisch Mitglied', [(o['slug'], o['rolle']) for o in orgs] == [(slug, 'mitglied')], orgs)
    ctx.close()

    print('Firmen-Login: bestehendes Konto (Anna) nur mit Passwort')
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    firma_login(pg, 'anna@firma.example')
    pg.wait_for_selector('#f-verknuepfen:not([hidden])', timeout=15000)
    ok('Anna: erst Passwort, nicht angemeldet', not ich(pg).get('angemeldet') and 'Firma Beispiel' in pg.inner_text('#verknuepfen-text'))
    pg.fill('#f-verknuepfen [name=passwort]', 'anna-agora-passwort-1')
    pg.click('#f-verknuepfen button[type=submit]')
    pg.wait_for_selector('#angemeldet:not([hidden])', timeout=10000)
    ok('Anna angemeldet und Mitglied', ich(pg)['ich']['email'] == 'anna@firma.example'
       and any(o['slug'] == slug for o in pg.evaluate("async () => (await (await fetch('/api/orgs')).json()).orgs")))
    ctx.close()

    print('Firmen-Login: Adresse außerhalb der Domain')
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    pg.goto(B + '/')
    pg.click('button[data-anbieter=firma]')
    pg.fill('#f-firma [name=email]', 'mallory@fremd.example')
    pg.click('#f-firma button[type=submit]')
    pg.wait_for_selector('#status:not([hidden])', timeout=5000)
    ok('fremde Domain: kein Firmen-Login', 'kein Firmen-Login' in pg.inner_text('#status'))
    # über die Domain starten, beim IdP aber als fremde Adresse anmelden → abgelehnt
    pg.fill('#f-firma [name=email]', 'x@firma.example')
    pg.click('#f-firma button[type=submit]')
    dex_login(pg, 'mallory@fremd.example')
    pg.wait_for_selector('#status:not([hidden])', timeout=15000)
    ok('IdP liefert fremde Domain → abgelehnt', 'passt nicht zur Domain' in pg.inner_text('#status') and not ich(pg).get('angemeldet'),
       pg.inner_text('#status'))
    ctx.close()

    print('Firmen-Login erzwingen')
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    pg.goto(B + '/')
    pg.fill('#f-anmelden [name=email]', 'chef@firma.example')
    pg.fill('#f-anmelden [name=passwort]', 'chef-agora-passwort-1')
    pg.click('#f-anmelden button[type=submit]')
    pg.wait_for_selector('#angemeldet:not([hidden])', timeout=10000)
    r = pg.evaluate(f"""async () => (await fetch('/api/orgs/{slug}/sso', {{method: 'PUT', headers: {{'X-Agora': '1', 'Content-Type': 'application/json'}},
        body: JSON.stringify({{issuer: '{DEX}', client_id: 'agora-firma', domain: 'firma.example', erzwingen: true}})}})).status""")
    ok('Owner schaltet „erzwingen“ ein', r == 200, r)
    ctx.close()
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    pg.goto(B + '/')
    pg.fill('#f-anmelden [name=email]', 'anna@firma.example')
    pg.fill('#f-anmelden [name=passwort]', 'anna-agora-passwort-1')
    pg.click('#f-anmelden button[type=submit]')
    pg.wait_for_selector('#status:not([hidden])', timeout=5000)
    ok('Anna: Passwort-Login gesperrt (Firmen-Login Pflicht)', 'Firmen-Login' in pg.inner_text('#status') and not ich(pg).get('angemeldet'))
    firma_login(pg, 'anna@firma.example')
    pg.wait_for_selector('#angemeldet:not([hidden])', timeout=15000)
    ok('Anna: über den Firmen-Login weiter möglich', ich(pg)['ich']['email'] == 'anna@firma.example')
    ctx.close()

    # ---------------------------------------------------------------- 4. Fehler: gefälschte Rückkehr
    print('Gefälschte Rückkehr (state ohne Vorgang)')
    ctx = br.new_context(locale='de-DE')
    pg = ctx.new_page()
    pg.goto(B + '/api/anmeldung/google/zurueck?state=' + 'x' * 43 + '&code=abc')
    pg.wait_for_selector('#status:not([hidden])', timeout=10000)
    ok('Fehlermeldung statt Anmeldung', 'abgelaufen oder ungültig' in pg.inner_text('#status') and not ich(pg).get('angemeldet'))
    ok('Fehler-Parameter aus der Adresse entfernt', 'anmeldung=' not in pg.url, pg.url)
    pg.screenshot(path=AUS + 'sso-05-fehler.png')
    ctx.close()
    br.close()

if FEHLER:
    print(f'\n{len(FEHLER)} FEHLER: {", ".join(FEHLER)}')
    sys.exit(1)
print('\nSSO-E2E: alles ok')
