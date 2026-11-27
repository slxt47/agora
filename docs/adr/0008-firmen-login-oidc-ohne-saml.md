# ADR 0008: Firmen-Login über OpenID Connect, kein SAML

**Status:** angenommen (27.11.2026) · Code: `app/firmen_login.py`, `app/anmeldung.py`, `app/oidc.py` · Beweis:
`sh tests/e2e/sso.sh` (Dex) · Anleitung: [`docs/ANMELDUNG.md`](../ANMELDUNG.md#firmen-login-oidc-je-organisation)

## Kontext
Offen war „SAML / OIDC für Unternehmen“: Organisationen (Teams) sollen ihre Mitglieder über den eigenen
Identity-Provider (IdP) anmelden. Agora hat dafür schon OAuth/OIDC für Google und Microsoft (PKCE, Discovery, JWKS,
id_token-Prüfung mit PyJWT + cryptography). Regeln des Projekts: alles selbst gehostet, keine schweren Abhängigkeiten,
standardmäßig aus.

## Entscheidung
1. **Firmen-Login nur über OpenID Connect** (Authorization Code + PKCE, `state` an ein Browser-Cookie gebunden,
   `nonce`), mit demselben Code wie Google/Microsoft. Je Organisation trägt ein Owner Aussteller-URL, Client-ID,
   Client-Geheimnis (AES-256-GCM, Schlüssel per HKDF aus `AGORA_GEHEIM`, an die Organisation gebunden) und die
   E-Mail-Domain ein; optional „automatisch beitreten“ und „erzwingen“. Global erst mit `AGORA_FIRMEN_LOGIN=1`.
2. **Kein SAML 2.0.** Wir liefern keinen eigenen XML-Signatur-Code aus.

## Warum kein SAML
- SAML-Antworten sind mit **XML-DSig** signiert. Sicher prüfen heißt: XML kanonisieren (C14N/exc-C14N), Transforms,
  Referenzen auflösen und – entscheidend – sicherstellen, dass die **signierte** Assertion genau die ist, die man danach
  ausliest. Genau daran scheitern Implementierungen seit Jahren: XML Signature Wrapping (Somorovsky u. a. 2012),
  Kommentar-Trick bei der NameID (CVE-2017-11427 ff., Duo 2018, betraf python-saml, ruby-saml, OneLogin …),
  ruby-saml CVE-2024-45409 und CVE-2025-25291/25292 (Parser-Differenzen, Authentifizierung umgehbar), xml-crypto
  CVE-2025-29774/29775, Samlify CVE-2025-47949. Das sind ausgereifte, viel geprüfte Bibliotheken.
- Die solide Python-Lösung ist `python3-saml`/`pysaml2` auf **xmlsec1 + libxml2** (native Bibliotheken, eigenes
  Build, eigene Sicherheitsupdates) – das widerspricht „keine schweren Abhängigkeiten“ und vergrößert die
  Angriffsfläche des App-Containers. `signxml` (reines Python + lxml) bringt mit lxml ebenfalls native Teile und hat
  selbst Wrapping-Fixes hinter sich.
- Eine eigene, „kleine“ Prüfung in reinem Python wäre genau die Art Code, die die Liste oben füllt. Lieber kein SAML
  als ein unsicheres.
- Der Nutzen ist klein: **Alle verbreiteten IdPs sprechen OIDC** – Microsoft Entra ID (App-Registrierung, Aussteller
  `https://login.microsoftonline.com/<tenant>/v2.0`), Okta (`https://<org>.okta.com` bzw. Custom Authorization
  Server), Google Workspace (`https://accounts.google.com`, Domain über die Agora-Einstellung), Keycloak
  (`https://<host>/realms/<realm>`), Authentik, Auth0, Ping, JumpCloud, OneLogin, ADFS ab 2016
  (`https://<host>/adfs`). Wer nur SAML anbietet (altes ADFS, Shibboleth), kann einen OIDC-Brücken-IdP (Keycloak, Dex)
  davor setzen.

## Sicherheitsregeln für den Firmen-Login
- **SSRF:** Discovery, JWKS und Token-Endpunkt trägt ein Owner ein (indirekt über die Aussteller-URL). Nur `https`,
  nur öffentliche Adressen (wie bei Webhooks: auch NAT64/6to4/Teredo, gemappte IPv4), Verbindung zur geprüften IP
  (kein DNS-Rebinding), keine Weiterleitungen, höchstens 1 MB, 10 s. Beim Speichern wird die Discovery abgerufen und der
  Aussteller verglichen. Ausnahme nur im Testmodus (`AGORA_TEST=1` + `AGORA_SSO_LOKAL=1`).
- **Kein Übernehmen fremder Konten:** Ein Owner kontrolliert seinen IdP und kann dort jede Adresse behaupten. Deshalb
  verknüpft Agora ein **bestehendes** Konto – wie bei Google – nur nach einmaliger Eingabe des Agora-Passworts.
  Neue Konten legt der IdP nur für Adressen **seiner** Domain an (eine Domain gehört höchstens einer Organisation und
  muss zur Adresse des Owners passen; Freemail-Domains sind verboten). Setzt die echte Person später per Mail-Link ein
  Passwort (Postfach bewiesen), werden die Firmen-Verknüpfungen des Kontos gelöscht.
- **Erzwingen** gilt nur für Mitglieder der Organisation mit Adresse in der Domain, nie für Owner (Notzugang, falls der
  IdP ausfällt oder falsch eingerichtet ist). Für sie sind Passwort-Login und Google/Microsoft/GitHub gesperrt; das
  einmalige Verknüpfen mit Passwort im Firmen-Login geht weiter.
- `email_verified`: Fehlt es (Entra ID schickt es nicht), gilt die Adresse in der eigenen Domain als vom IdP der
  Organisation verantwortet; ist es `false`, wird abgelehnt.

## Folgen
- Kein SAML-Knopf; Unternehmen mit reinem SAML brauchen eine OIDC-Brücke.
- Eine Redirect-URI für alle Organisationen: `<AGORA_SEITE>/api/anmeldung/firma/zurueck` (der Vorgang kennt die
  Organisation).
- **Restrisiko:** Die Domain ist nur über die bestätigte Adresse des Owners „bewiesen“, nicht per DNS-TXT. Ein
  Mitarbeiter kann für seine Firmen-Domain eine Organisation anlegen, bevor die IT es tut, und so die Domain belegen
  (nicht übernehmen: bestehende Konten brauchen weiter ihr Passwort). Abhilfe bei Bedarf: DNS-TXT-Nachweis
  (`_agora-verify.<domain>`), Admin kann die Domain einer Organisation über die Datenbank lösen.
- Würde SAML später zwingend, dann nur mit `python3-saml` (xmlsec1) in einem eigenen, kleinen Dienst, nicht im
  App-Prozess.
