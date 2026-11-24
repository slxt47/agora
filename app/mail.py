"""E-Mail-Versand (Bestätigung, Passwort, Einladungen) über SMTP mit STARTTLS – im slxt-Design.

Jede Mail hat eine Textfassung und eine HTML-Fassung: tabellenbasiert, Styles inline (Gmail/Outlook), Logo als
eingebettetes Bild (cid:, nichts wird nachgeladen – keine Tracking-Pixel), dunkler Nachthimmel wie slxt.org,
helle Variante über prefers-color-scheme (Apple Mail, iOS). Im Testmodus landen Mails nur im Speicher.
Texte in der Sprache des Empfängers (texte.MAIL, Deutsch/Englisch, ADR 0006) – nur Meldungen an Admins bleiben deutsch.
"""
import html
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .texte import gueltig, tx

TEST = os.environ.get('AGORA_TEST') == '1'
POSTEINGANG_TEST = []          # nur im Testmodus: (an, betreff, text, html, anhaenge)
_lock = threading.Lock()
LOGO = os.path.join(os.environ.get('AGORA_WEB', '/srv/web/dist'), 'icons', 'icon-192.png')
SEITE = os.environ.get('AGORA_SEITE', 'https://agora.slxt.dev').rstrip('/')
MARKE = 'Agora'


def html_mail(titel, absaetze, knopf=None, hinweis=None, vorschau='', details=None, links=None, sprache='de'):
    """slxt-Design: Nachthimmel, Glaskarte, violetter Akzent, Systemschrift. Alles inline."""
    e = html.escape
    absatz_html = ''.join(f'<p style="margin:0 0 14px;font-size:16px;line-height:1.55;color:#c9cbe0" class="sec">{e(a)}</p>' for a in absaetze)
    details_html = ''
    if details:
        zeilen = ''.join(f'<tr><td style="padding:4px 12px 4px 0;font-size:14px;color:#8e90ad;vertical-align:top" class="leise">{e(k)}</td>'
                         f'<td style="padding:4px 0;font-size:14px;color:#f2f2f7" class="titel">{e(v)}</td></tr>' for k, v in details)
        details_html = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="code" style="margin:6px 0 18px;'
                        f'background:#211d4a;border:1px solid #4b3fa8;border-radius:16px"><tr><td style="padding:14px 16px"><table role="presentation" cellpadding="0" cellspacing="0">{zeilen}</table></td></tr></table>')
    knopf_html = ''
    if knopf:
        text, url = knopf
        knopf_html = (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 18px"><tr><td style="border-radius:999px;background:#a993ff">'
                      f'<a href="{e(url)}" style="display:inline-block;padding:14px 26px;font-weight:700;font-size:16px;color:#0b0b10;text-decoration:none;border-radius:999px">{e(text)}</a></td></tr></table>')
    hinweis_html = f'<p style="margin:4px 0 0;font-size:13px;line-height:1.5;color:#8e90ad" class="leise">{e(hinweis)}</p>' if hinweis else ''
    if links:      # kleine Textlinks unter dem Knopf, z. B. „Zu Google Kalender“ (nur https-Ziele)
        teile = ' · '.join(f'<a href="{e(u)}" class="akzent" style="color:#a993ff;text-decoration:none">{e(t)}</a>' for t, u in links)
        hinweis_html = f'<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#8e90ad" class="leise">{teile}</p>' + hinweis_html
    sp = gueltig(sprache)
    return f'''<!doctype html>
<html lang="{sp}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><meta name="supported-color-schemes" content="dark light"><title>{e(titel)}</title>
<style>
  @media (prefers-color-scheme: light) {{
    body, .koerper {{ background:#fde7da !important; }}
    .himmel {{ background:#efe3f8 !important; background-image:linear-gradient(180deg,#dfe5ff,#efe3f8 55%,#fde7da) !important; }}
    .karte {{ background:#ffffff !important; border-color:#e4def5 !important; }}
    .titel, .marke {{ color:#1d1d1f !important; }}
    .sec {{ color:#54546a !important; }}
    .leise, .fuss, .fuss a {{ color:#6e6e85 !important; }}
    .fuss a.akzent {{ color:#5b3df5 !important; }}
    .code {{ background:#f1edff !important; border-color:#cfc4ff !important; }}
    .akzent {{ color:#5b3df5 !important; }}
  }}
  @media (max-width: 480px) {{ .karte {{ padding:24px 20px !important; }} }}
</style></head>
<body class="koerper" style="margin:0;padding:0;background:#0b0e2a">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{e(vorschau)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="himmel"
  style="background:#0b0e2a;background-image:linear-gradient(180deg,#04050d,#0b0e2a 55%,#1a1240)"><tr><td align="center" style="padding:36px 16px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px">
    <tr><td style="padding:0 4px 18px">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="vertical-align:middle"><img src="cid:logo" width="36" height="36" alt="" style="display:block;border-radius:9px"></td>
        <td style="vertical-align:middle;padding-left:10px;font:700 19px/1 -apple-system,BlinkMacSystemFont,'SF Pro Rounded','Segoe UI',Roboto,sans-serif;color:#f2f2f7" class="marke">{MARKE}<span class="akzent" style="color:#a993ff">.</span></td>
      </tr></table>
    </td></tr>
    <tr><td class="karte" style="background:#17172e;border:1px solid #2c2b4d;border-radius:24px;padding:30px 28px;font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Roboto,Helvetica,Arial,sans-serif">
      <h1 class="titel" style="margin:0 0 14px;font:800 26px/1.15 -apple-system,BlinkMacSystemFont,'SF Pro Rounded','Segoe UI',Roboto,sans-serif;letter-spacing:-.3px;color:#f2f2f7">{e(titel)}<span class="akzent" style="color:#a993ff">.</span></h1>
      {absatz_html}{details_html}{knopf_html}{hinweis_html}
    </td></tr>
    <tr><td class="fuss" style="padding:20px 8px 0;text-align:center;font:13px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#8e90ad">
      {e(tx(sp, 'fuss'))}<br>
      <a href="{SEITE}" class="akzent" style="color:#a993ff;text-decoration:none">{e(SEITE.split('://')[-1])}</a> ·
      <a href="https://slxt.org/impressum/" style="color:#8e90ad;text-decoration:none">{e(tx(sp, 'impressum'))}</a> ·
      <a href="https://slxt.org/datenschutz/#agora" style="color:#8e90ad;text-decoration:none">{e(tx(sp, 'datenschutz'))}</a>
    </td></tr>
  </table>
</td></tr></table>
</body></html>'''


def senden(an, betreff, text, html_text=None, anhaenge=(), sprache='de'):
    """anhaenge: [(dateiname, maintype, subtype, bytes, params)]"""
    if TEST:
        with _lock:
            POSTEINGANG_TEST.append((an, betreff, text, html_text, list(anhaenge)))
        return
    m = EmailMessage()
    m['From'] = formataddr((MARKE, os.environ['SMTP_FROM']))
    m['To'] = an
    m['Subject'] = betreff
    m['Message-ID'] = make_msgid(domain=os.environ['SMTP_FROM'].split('@')[-1])
    m['Content-Language'] = gueltig(sprache)
    m.set_content(text + f'\n\n– {MARKE} · {SEITE}\n{tx(sprache, "automatisch")}\n')
    if html_text:
        m.add_alternative(html_text, subtype='html')
        if os.path.exists(LOGO):
            with open(LOGO, 'rb') as f:
                m.get_payload()[1].add_related(f.read(), 'image', 'png', cid='<logo>', filename='logo.png')
    for name, haupt, unter, daten, params in anhaenge:
        m.add_attachment(daten, maintype=haupt, subtype=unter, filename=name, params=params or {})
    with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.environ.get('SMTP_PORT', '587')), timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
        s.send_message(m)


def im_hintergrund(funktion, *args):
    """Mail in eigenem Thread schicken; Fehler nur protokollieren (nie Links/Token ins Log)."""
    from .metriken import zaehlen      # spät importiert: metriken braucht db/livekit, mail soll schlank bleiben

    def lauf():
        try:
            funktion(*args)
            zaehlen('agora_mails_total', ergebnis='ok')
        except Exception as e:   # noqa: BLE001
            zaehlen('agora_mails_total', ergebnis='fehler')
            print(f'mail-fehler {type(e).__name__}', flush=True)
    if TEST:
        lauf()                   # im Test sofort, damit die Mail gleich im Posteingang liegt
    else:
        threading.Thread(target=lauf, daemon=True).start()


def bestaetigen_mail(an, name, link, sprache='de'):
    s = sprache
    satz, gilt, ign = tx(s, 'bestaetigen.satz'), tx(s, 'bestaetigen.gilt'), tx(s, 'ignorieren')
    text = f'{tx(s, "hallo", name=name)}\n\n{satz}\n\n{link}\n\n{gilt}\n{ign}'
    senden(an, tx(s, 'bestaetigen.betreff'), text,
           html_mail(tx(s, 'bestaetigen.titel'), [tx(s, 'hallo', name=name), satz], knopf=(tx(s, 'bestaetigen.knopf'), link),
                     hinweis=f'{gilt} {ign}', vorschau=tx(s, 'bestaetigen.vorschau'), sprache=s), sprache=s)


def schon_registriert_mail(an, name, link, sprache='de'):
    s = sprache
    satz, ign = tx(s, 'schon.satz'), tx(s, 'ignorieren')
    text = f'{tx(s, "hallo", name=name)}\n\n{satz}\n\n{link}\n\n{ign}'
    senden(an, tx(s, 'schon.betreff'), text,
           html_mail(tx(s, 'schon.titel'), [tx(s, 'hallo', name=name), satz], knopf=(tx(s, 'schon.knopf'), link),
                     hinweis=ign, vorschau=tx(s, 'schon.vorschau'), sprache=s), sprache=s)


def passwort_mail(an, name, link, sprache='de'):
    s = sprache
    satz, gilt, ign = tx(s, 'passwort.satz'), tx(s, 'passwort.gilt'), tx(s, 'ignorieren')
    text = f'{tx(s, "hallo", name=name)}\n\n{satz}\n\n{link}\n\n{gilt}\n{ign}'
    senden(an, tx(s, 'passwort.betreff'), text,
           html_mail(tx(s, 'passwort.titel'), [tx(s, 'hallo', name=name), satz], knopf=(tx(s, 'passwort.knopf'), link),
                     hinweis=f'{gilt} {ign}', vorschau=tx(s, 'passwort.vorschau'), sprache=s), sprache=s)


KALENDER = ('google', 'outlook', 'office365')


def kalender_namen(sprache):
    """[(schluessel, „Zu Google Kalender“ …)] in der Sprache der Mail."""
    return [(k, tx(sprache, f'kal.{k}')) for k in KALENDER]


def einladung_mail(an, von_name, titel, link, details, ics_daten=None, passcode=None, kalender=None, sprache='de'):
    s = sprache
    saetze = [tx(s, 'einladung.satz', von=von_name, titel=titel)]
    zeilen = list(details)
    if passcode:
        zeilen.append((tx(s, 'einladung.passcode'), passcode))
    text = '\n'.join(saetze) + '\n\n' + '\n'.join(f'{k}: {v}' for k, v in zeilen) + f'\n\n{tx(s, "einladung.beitreten")}: {link}\n'
    links = [(name, kalender[k]) for k, name in kalender_namen(s) if kalender and kalender.get(k)]
    if links:
        text += '\n' + '\n'.join(f'{name}: {u}' for name, u in links) + '\n'
    anhaenge = ([(tx(s, 'einladung.datei'), 'text', 'calendar', ics_daten.encode(), {'method': 'PUBLISH', 'charset': 'utf-8'})]
                if ics_daten else [])
    hinweis = tx(s, 'einladung.ics') if ics_daten else None
    senden(an, tx(s, 'einladung.betreff', titel=titel), text,
           html_mail(tx(s, 'einladung.titel'), saetze, knopf=(tx(s, 'einladung.knopf'), link), details=zeilen,
                     hinweis=hinweis, vorschau=tx(s, 'einladung.vorschau', von=von_name), links=links, sprache=s), anhaenge, sprache=s)


# ---------------------------------------------------------------- Erinnerungen (erinnerungen.py)
def erinnerung_mail(an, name, titel, link, wann, minuten, ist_host, kalender=None, von_name='', sprache='de'):
    """Erinnerung vor einem Termin – an den Besitzer (ist_host) oder an Eingeladene (ohne Namen, ohne Passcode)."""
    s = sprache
    if ist_host:
        saetze = [tx(s, 'hallo', name=name), tx(s, 'erinnerung.hostSatz', titel=titel, n=minuten)]
        knopf = (tx(s, 'erinnerung.hostKnopf'), link)
        hinweis = tx(s, 'erinnerung.hostHinweis')
    else:
        saetze = [tx(s, 'erinnerung.gastSatzVon', titel=titel, von=von_name, n=minuten) if von_name
                  else tx(s, 'erinnerung.gastSatz', titel=titel, n=minuten)]
        knopf = (tx(s, 'erinnerung.gastKnopf'), link)
        hinweis = tx(s, 'erinnerung.gastHinweis')
    zeilen = [(tx(s, 'erinnerung.wann'), wann), (tx(s, 'erinnerung.link'), link)]
    links = [(n, kalender[k]) for k, n in kalender_namen(s) if kalender and kalender.get(k)] if ist_host else []
    text = '\n'.join(saetze) + '\n\n' + '\n'.join(f'{k}: {v}' for k, v in zeilen) + f'\n\n{hinweis}\n'
    senden(an, tx(s, 'erinnerung.betreff', titel=titel, n=minuten), text,
           html_mail(tx(s, 'erinnerung.titel'), saetze, knopf=knopf, details=zeilen, hinweis=hinweis,
                     vorschau=tx(s, 'erinnerung.vorschau', titel=titel, n=minuten), links=links, sprache=s), sprache=s)
