"""E-Mail-Versand (Bestätigung, Passwort, Einladungen) über SMTP mit STARTTLS – im slxt-Design.

Jede Mail hat eine Textfassung und eine HTML-Fassung: tabellenbasiert, Styles inline (Gmail/Outlook), Logo als
eingebettetes Bild (cid:, nichts wird nachgeladen – keine Tracking-Pixel), dunkler Nachthimmel wie slxt.org,
helle Variante über prefers-color-scheme (Apple Mail, iOS). Im Testmodus landen Mails nur im Speicher.
"""
import html
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

TEST = os.environ.get('AGORA_TEST') == '1'
POSTEINGANG_TEST = []          # nur im Testmodus: (an, betreff, text, html, anhaenge)
_lock = threading.Lock()
LOGO = os.path.join(os.environ.get('AGORA_WEB', '/srv/web/dist'), 'icons', 'icon-192.png')
SEITE = os.environ.get('AGORA_SEITE', 'https://agora.slxt.dev').rstrip('/')
MARKE = 'Agora'


def html_mail(titel, absaetze, knopf=None, hinweis=None, vorschau='', details=None):
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
    return f'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
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
      Videokonferenzen · ohne Werbung, ohne Tracking<br>
      <a href="{SEITE}" class="akzent" style="color:#a993ff;text-decoration:none">{e(SEITE.split('://')[-1])}</a> ·
      <a href="https://slxt.org/impressum/" style="color:#8e90ad;text-decoration:none">Impressum</a> ·
      <a href="https://slxt.org/datenschutz/#agora" style="color:#8e90ad;text-decoration:none">Datenschutz</a>
    </td></tr>
  </table>
</td></tr></table>
</body></html>'''


def senden(an, betreff, text, html_text=None, anhaenge=()):
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
    m.set_content(text + f'\n\n– {MARKE} · {SEITE}\nDiese Nachricht wurde automatisch verschickt.\n')
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
    def lauf():
        try:
            funktion(*args)
        except Exception as e:   # noqa: BLE001
            print(f'mail-fehler {type(e).__name__}', flush=True)
    if TEST:
        lauf()                   # im Test sofort, damit die Mail gleich im Posteingang liegt
    else:
        threading.Thread(target=lauf, daemon=True).start()


IGNORIEREN = 'Wenn du das nicht warst, kannst du diese Mail ignorieren. Wir fragen dich nie nach deinem Passwort.'


def bestaetigen_mail(an, name, link):
    satz = 'Bitte bestätige deine E-Mail-Adresse. Danach kannst du Meetings starten, planen und deinen persönlichen Raum nutzen.'
    text = f'Hallo {name},\n\n{satz}\n\n{link}\n\nDer Link gilt 48 Stunden.\n{IGNORIEREN}'
    senden(an, f'{MARKE}: E-Mail-Adresse bestätigen', text,
           html_mail('Willkommen bei Agora', [f'Hallo {name},', satz], knopf=('E-Mail-Adresse bestätigen', link),
                     hinweis='Der Link gilt 48 Stunden. ' + IGNORIEREN, vorschau='Bitte bestätige deine E-Mail-Adresse'))


def schon_registriert_mail(an, name, link):
    satz = 'Jemand wollte mit dieser Adresse ein neues Konto anlegen – du hast aber schon eins. Falls du dein Passwort vergessen hast:'
    text = f'Hallo {name},\n\n{satz}\n\n{link}\n\n{IGNORIEREN}'
    senden(an, f'{MARKE}: Du hast schon ein Konto', text,
           html_mail('Du hast schon ein Konto', [f'Hallo {name},', satz], knopf=('Passwort zurücksetzen', link),
                     hinweis=IGNORIEREN, vorschau='Du hast schon ein Konto bei Agora'))


def passwort_mail(an, name, link):
    satz = 'Mit diesem Link setzt du ein neues Passwort. Danach wirst du überall abgemeldet.'
    text = f'Hallo {name},\n\n{satz}\n\n{link}\n\nDer Link gilt 1 Stunde.\n{IGNORIEREN}'
    senden(an, f'{MARKE}: Passwort zurücksetzen', text,
           html_mail('Passwort zurücksetzen', [f'Hallo {name},', satz], knopf=('Neues Passwort setzen', link),
                     hinweis='Der Link gilt 1 Stunde. ' + IGNORIEREN, vorschau='Neues Passwort setzen'))


def einladung_mail(an, von_name, titel, link, details, ics_daten=None, passcode=None):
    saetze = [f'{von_name} lädt dich zu einem Meeting ein: „{titel}“.']
    zeilen = list(details)
    if passcode:
        zeilen.append(('Passcode', passcode))
    text = '\n'.join(saetze) + '\n\n' + '\n'.join(f'{k}: {v}' for k, v in zeilen) + f'\n\nBeitreten: {link}\n'
    anhaenge = [('einladung.ics', 'text', 'calendar', ics_daten.encode(), {'method': 'PUBLISH', 'charset': 'utf-8'})] if ics_daten else []
    hinweis = 'Den Termin kannst du mit dem Anhang in deinen Kalender übernehmen.' if ics_daten else None
    senden(an, f'Einladung: {titel}', text,
           html_mail('Einladung zum Meeting', saetze, knopf=('Meeting beitreten', link), details=zeilen,
                     hinweis=hinweis, vorschau=f'{von_name} lädt dich ein'), anhaenge)
