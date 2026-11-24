# ADR 0006: Deutsch und Englisch

**Status:** angenommen (24.11.2026)

## Kontext
Agora war nur deutsch. Die Oberfläche hatte schon `t()` (web/src/i18n.js) und ein deutsches Wörterbuch, die HTML-Seiten
aber feste deutsche Texte, die API deutsche Fehlermeldungen und alle Mails waren deutsch. Gewünscht: eine vollständige
englische Fassung, ohne Server-Rendering und ohne die Bundles spürbar zu vergrößern.

## Entscheidung
**Oberfläche**
- Zwei Wörterbücher mit denselben Schlüsseln: `texte-de.js` (Quelle) und `texte-en.js`. `test/i18n.test.js` bricht ab,
  wenn ein Schlüssel fehlt oder zu viel ist, Platzhalter `{x}` oder HTML-Tags abweichen, ein im Code benutzter Schlüssel
  fehlt, ein englischer Text nach Deutsch aussieht oder eine HTML-Seite sichtbaren deutschen Text ohne `data-t` hat.
- Sprache: `?sprache=de|en` → gespeicherte Wahl (`localStorage.sprache`) → `navigator.languages` (erste de/en-Angabe;
  andere Sprachen → Englisch). `<html lang>` wird sofort gesetzt.
- Nur das aktive Wörterbuch wird geladen: `i18n.js` holt es per dynamischem Import mit **Top-Level-await**. Dadurch ist es
  ein eigener Chunk (je ~20 kB gzip), und jedes Modul, das `t()` beim Laden aufruft, sieht schon die richtige Sprache. Beide
  Chunks stehen in der Vorab-Liste des Service Workers (Offline-Seite).
- HTML-Seiten bleiben deutsch im Quelltext; `data-t` (Text), `data-t-html` (Wörterbuch-HTML mit Links/Hervorhebung, nie
  Nutzereingaben), `data-t-aria|title|placeholder` werden beim Laden übersetzt, wenn Englisch aktiv ist.
- Sprachwechsel lädt die Seite neu (Schalter DE/EN in der Kopfzeile neben Hell/Dunkel, Profil). Ein Umschalten „live“
  hätte alle schon gezeichneten Teile neu rendern müssen. Im Meeting gibt es den Schalter deshalb nicht.
- Angemeldete: Die Konto-Sprache (`ich.sprache`) gilt. Weicht sie von der Seite ab, wird sie gemerkt und die Seite neu
  geladen (nur wenn der Browser speichern kann – sonst bliebe es eine Schleife). Der Schalter speichert für Angemeldete
  auch im Konto. Bei der Registrierung bekommt das Konto die Sprache der Seite.
- Datum, Uhrzeit, Zahlen über `Intl` mit `de-AT` bzw. `en-GB` (`gebiet()`); Dauer-/„vor …“-Texte aus dem Wörterbuch.

**Backend: Fehlermeldungen übersetzt der Server** (statt Zuordnung im Client)
- Der Client schickt bei jedem API-Aufruf `Accept-Language: <Sprache der Oberfläche>`. Eine Middleware legt die Sprache
  in einer ContextVar ab (`app/texte.py`).
- Der Code schreibt Fehler weiter deutsch (`fehler(400, '…')`); der Fehler-Handler übersetzt beim Antworten über den
  Katalog `FEHLER_EN` (genaue Texte) und `MUSTER_EN` (Texte mit Zahlen/Namen). `tests/test_sprachen.py` sucht per AST
  alle Meldungen im Code und prüft, dass jede übersetzt ist – eine neue deutsche Meldung ohne Übersetzung lässt den Test
  scheitern.
- Warum nicht im Client: Der Client müsste ~190 Texte und alle Varianten mit Werten kennen und bei jeder Backend-Änderung
  nachziehen; Fremd-Clients (curl, Einbettung) bekämen nie Englisch. Der Katalog im Backend liegt neben dem Code, der die
  Texte erzeugt, und wird dort getestet. Die 190 Aufrufstellen bleiben unverändert (kleiner Diff, keine Schlüssel-IDs).

**Mails**
- `texte.MAIL` (de/en), `tx(sprache, schlüssel, …)`; `html_mail` setzt `lang`, Fuß und Rechtslinks in der Sprache.
- Empfänger mit Konto (Bestätigung, Passwort, „schon registriert“, eigene Erinnerungen): Konto-Sprache.
- Einladungen (+ .ics, Kalender-Links): Sprache der einladenden Seite. Erinnerungen an Eingeladene: Sprache des
  einladenden Kontos (die Gast-Adresse hat keine eigene Sprache). Webinar-Bestätigung: Sprache der Anmeldeseite.
- Meldungen an Admins bleiben deutsch; das Kalender-Abo nutzt die Konto-Sprache.

**Rechtstexte** bleiben auf slxt.org (deutsch); in der englischen Oberfläche und in englischen Mails steht „(German)“.

## Folgen
- Neue Texte: Schlüssel in beiden Wörterbüchern anlegen (der Test erzwingt es); neue Fehlermeldungen in `FEHLER_EN`.
- Admin-Protokoll-Einträge und vom Nutzer vergebene Titel bleiben, wie sie gespeichert sind; Standard-Titel neuer Räume
  („Meeting von …“) entstehen in der Sprache der Anfrage.
- Werte in API-Fehlern, die Feldnamen nennen (z. B. „Rolle: cohost oder teilnehmer“), bleiben auch englisch bei den
  deutschen Feldwerten der API.
