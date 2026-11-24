// Reine Hilfsfunktionen ohne DOM – laufen im Browser und unter `node --test` (siehe test/helfer.test.js).
// Texte und Gebietsschema (Datum, Uhrzeit) kommen aus i18n.js (aktive Sprache).
import { t, gebiet, aktuelleSprache } from './i18n.js';

/** Raum-Code: abc-defg-hij (Kleinbuchstaben). Der Server erzeugt nur Buchstaben ohne l/o, wir sind beim Lesen großzügig. */
export const CODE_MUSTER = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/;

/**
 * Holt den Raum-Code aus einer Eingabe: nackter Code („abc-defg-hij“, auch groß, mit Leerzeichen oder ohne Striche)
 * oder ein eingefügter Link („https://agora.slxt.dev/r/abc-defg-hij?x=1“, auch ohne https://).
 * Gibt den normalisierten Code zurück oder null.
 */
export function codeAusText(eingabe) {
  if (typeof eingabe !== 'string') return null;
  let s = eingabe.trim();
  if (!s) return null;
  // Link? Dann den Teil hinter /r/ nehmen
  const link = s.match(/\/r\/([A-Za-z-]{10,12})(?=[/?#\s]|$)/);
  if (link) s = link[1];
  else if (/[/:.?#]/.test(s)) {
    // Irgendein anderer Text mit Link-Zeichen: letzten codeähnlichen Block suchen
    const treffer = s.match(/[A-Za-z]{3}-[A-Za-z]{4}-[A-Za-z]{3}(?![A-Za-z])/g);
    if (!treffer) return null;
    s = treffer[treffer.length - 1];
  }
  const nur = s.toLowerCase().replace(/[\s\-–—_]/g, '');
  if (!/^[a-z]{10}$/.test(nur)) return null;
  const code = `${nur.slice(0, 3)}-${nur.slice(3, 7)}-${nur.slice(7)}`;
  return CODE_MUSTER.test(code) ? code : null;
}

// ---------------------------------------------------------------- Zeit

const sicherZone = (zone) => {
  try { new Intl.DateTimeFormat('de', { timeZone: zone }); return zone; } catch { return 'UTC'; }
};

/** Datum + Uhrzeit lesbar: „Mo., 24. Nov. 2026, 14:00“ in der gewünschten Zeitzone. */
export function zeitText(iso, zone = 'Europe/Vienna', sprache = aktuelleSprache()) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat(sprache === 'en' ? 'en-GB' : 'de-AT', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    timeZone: sicherZone(zone),
  }).format(d);
}

/** Nur Uhrzeit (Chat): „14:03“. */
export function uhrzeitText(iso, zone) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat(gebiet(), { hour: '2-digit', minute: '2-digit', ...(zone ? { timeZone: sicherZone(zone) } : {}) }).format(d);
}

/** Nur Datum: „30.11.2026“. */
export function datumText(iso, zone) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat(gebiet(), { day: '2-digit', month: '2-digit', year: 'numeric', ...(zone ? { timeZone: sicherZone(zone) } : {}) }).format(d);
}

/** Minuten als „45 Min.“, „1 Std.“, „1 Std. 30 Min.“ */
export function minutenText(min) {
  min = Math.max(0, Math.round(Number(min) || 0));
  if (min < 60) return t('zeit.min', { n: min });
  const h = Math.floor(min / 60), m = min % 60;
  return m ? t('zeit.stdMin', { h, m }) : t('zeit.std', { h });
}

/** Sekunden als Uhr: „4:07“ bzw. „1:04:07“ (Meeting-Dauer, Aufnahme). */
export function laufzeitText(sek) {
  sek = Math.max(0, Math.floor(Number(sek) || 0));
  const h = Math.floor(sek / 3600), m = Math.floor((sek % 3600) / 60), s = sek % 60;
  const zz = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${zz(m)}:${zz(s)}` : `${m}:${zz(s)}`;
}

/** Abstand zu jetzt grob: „gerade eben“, „vor 3 Min.“, „vor 2 Std.“ */
export function seitText(iso, jetzt = Date.now()) {
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return '';
  const s = Math.max(0, Math.round((jetzt - d) / 1000));
  if (s < 45) return t('zeit.geradeEben');
  if (s < 3600) return t('zeit.vorMin', { n: Math.max(1, Math.round(s / 60)) });
  if (s < 86400) return t('zeit.vorStd', { n: Math.round(s / 3600) });
  return t('zeit.vorTagen', { n: Math.round(s / 86400) });
}


/** Wiederholung lesbar: „Einmalig“, „Täglich“, „Wöchentlich (montags) bis 30.11.2026“. */
export function wiederholungText(geplant) {
  if (!geplant || !geplant.wiederholung || geplant.wiederholung === 'keine') return t('wdh.einmalig');
  const zone = geplant.zeitzone || 'UTC';
  let text = t('wdh.taeglich');
  if (geplant.wiederholung === 'woechentlich') {
    let tag = '';
    if (geplant.start) {
      const kurz = new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: sicherZone(zone) }).format(new Date(geplant.start));
      const nr = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(kurz);
      tag = nr >= 0 ? t(`wdh.tag.${nr}`) : '';
    }
    text = tag ? t('wdh.woechentlichAm', { tag }) : t('wdh.woechentlich');
  } else if (geplant.wiederholung !== 'taeglich') return t('wdh.einmalig');
  if (geplant.bis) text = t('wdh.bis', { text, datum: datumText(geplant.bis, zone) });
  return text;
}

/** Versatz einer Zeitzone zu UTC in Minuten zu einem Zeitpunkt (Europe/Vienna im Winter: +60). */
export function zonenVersatz(ms, zone) {
  const teile = new Intl.DateTimeFormat('en-US', {
    timeZone: sicherZone(zone), hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date(ms));
  const w = Object.fromEntries(teile.map((p) => [p.type, p.value]));
  const alsUtc = Date.UTC(+w.year, +w.month - 1, +w.day, +w.hour % 24, +w.minute, +w.second);
  return Math.round((alsUtc - Math.floor(ms / 1000) * 1000) / 60000);
}

/** Wanduhr-Zeit („2026-11-24“, „14:00“) in einer Zeitzone → ISO-UTC („2026-11-24T13:00:00.000Z“). */
export function lokalZuUtc(datum, zeit, zone) {
  const [y, mo, d] = String(datum).split('-').map(Number);
  const [h, mi] = String(zeit).split(':').map(Number);
  if (![y, mo, d, h, mi].every(Number.isFinite)) return null;
  const naiv = Date.UTC(y, mo - 1, d, h, mi);
  let ms = naiv - zonenVersatz(naiv, zone) * 60000;
  ms = naiv - zonenVersatz(ms, zone) * 60000;   // zweiter Schritt für Sommerzeit-Grenzen
  return new Date(ms).toISOString();
}

/** ISO-UTC → Wanduhr in Zone: {datum: "2026-11-24", zeit: "14:00"} (für Formulare). */
export function utcZuLokal(iso, zone) {
  const ms = new Date(iso).getTime();
  if (Number.isNaN(ms)) return { datum: '', zeit: '' };
  const l = new Date(ms + zonenVersatz(ms, zone) * 60000);
  const zz = (n) => String(n).padStart(2, '0');
  return { datum: `${l.getUTCFullYear()}-${zz(l.getUTCMonth() + 1)}-${zz(l.getUTCDate())}`, zeit: `${zz(l.getUTCHours())}:${zz(l.getUTCMinutes())}` };
}

// ---------------------------------------------------------------- Text + Links

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

/**
 * Zerlegt Text in Stücke {text} und {link, text} – nur http(s)-Links und www.-Adressen, Satzzeichen am Ende
 * gehören nicht zum Link. Der Aufrufer baut daraus DOM-Knoten (kein innerHTML nötig).
 */
export function linkTeile(text) {
  const s = String(text ?? '');
  const teile = [];
  const re = /\b(?:https?:\/\/|www\.)[^\s<>"']+/gi;
  let rest = 0, m;
  while ((m = re.exec(s))) {
    let url = m[0];
    // Satzzeichen am Ende abschneiden; schließende Klammer nur, wenn keine öffnende im Link steht
    while (/[.,;:!?)\]}'"»”]$/.test(url)) {
      if (url.endsWith(')') && (url.match(/\(/g) || []).length >= (url.match(/\)/g) || []).length) break;
      url = url.slice(0, -1);
    }
    if (m.index > rest) teile.push({ text: s.slice(rest, m.index) });
    const href = /^www\./i.test(url) ? `https://${url}` : url;
    teile.push({ link: sichererLink(href), text: url });
    rest = m.index + url.length;
    re.lastIndex = rest;
  }
  if (rest < s.length) teile.push({ text: s.slice(rest) });
  return teile.map((t) => (t.link === null ? { text: t.text } : t));
}

/** Nur http(s)-Adressen zulassen (kein javascript:, data: …); sonst null. */
export function sichererLink(url) {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null;
  } catch { return null; }
}

/** Text → sicheres HTML mit klickbaren Links (target=_blank, rel=noopener noreferrer). */
export function linkify(text) {
  return linkTeile(text).map((t) => (t.link
    ? `<a href="${escapeHtml(t.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t.text)}</a>`
    : escapeHtml(t.text))).join('');
}

/** „Anna Maria Berger“ → „AB“, „florian“ → „F“. */
export function initialen(name) {
  const w = String(name ?? '').trim().split(/\s+/).filter(Boolean);
  if (!w.length) return '?';
  const erst = (x) => Array.from(x)[0].toUpperCase();
  return w.length === 1 ? erst(w[0]) : erst(w[0]) + erst(w[w.length - 1]);
}

/** Stabile Avatar-Farbe aus einem Text (falls der Server keine liefert). Kräftige Töne, auf Weiß-Schrift lesbar. */
export function farbeAusText(text) {
  const farben = ['#6d4aff', '#3b6ff6', '#0e8a7a', '#b4237a', '#c2410c', '#7c3aed', '#0f766e', '#be123c', '#1d4ed8', '#9333ea'];
  let h = 0;
  for (const c of String(text ?? '')) h = (h * 31 + c.codePointAt(0)) >>> 0;
  return farben[h % farben.length];
}

const dezimal = () => (gebiet().startsWith('de') ? ',' : '.');
export function groesseText(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1).replace('.', dezimal())} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1).replace('.', dezimal())} MB`;
  return `${(n / 1024 ** 3).toFixed(2).replace('.', dezimal())} GB`;
}

// ---------------------------------------------------------------- Layout

/**
 * Bestes Raster für n Kacheln im 16:9-Format in einer Fläche b×h: maximiert die Kachelgröße.
 * Gibt {spalten, zeilen, breite, hoehe} zurück.
 */
export function galerieRaster(n, b, h, verhaeltnis = 16 / 9, abstand = 8) {
  if (n <= 0 || b <= 0 || h <= 0) return { spalten: 1, zeilen: 1, breite: 0, hoehe: 0 };
  let best = { spalten: 1, zeilen: n, breite: 0, hoehe: 0 };
  for (let spalten = 1; spalten <= n; spalten++) {
    const zeilen = Math.ceil(n / spalten);
    const maxB = (b - abstand * (spalten - 1)) / spalten, maxH = (h - abstand * (zeilen - 1)) / zeilen;
    const breite = Math.min(maxB, maxH * verhaeltnis);
    if (breite > best.breite) best = { spalten, zeilen, breite: Math.floor(breite), hoehe: Math.floor(breite / verhaeltnis) };
  }
  return best;
}

/** Seite einer Liste (0-basiert); die Seitenzahl wird in den gültigen Bereich gezogen. */
export function seite(liste, proSeite, nr) {
  const seiten = Math.max(1, Math.ceil(liste.length / proSeite));
  const s = Math.min(Math.max(0, nr), seiten - 1);
  return { eintraege: liste.slice(s * proSeite, (s + 1) * proSeite), seite: s, seiten };
}

/** Kacheln pro Seite nach Fensterbreite (Telefon 6, Tablet 9, Desktop 16, groß 25). */
export function kachelnProSeite(breite) {
  if (breite < 600) return 6;
  if (breite < 1024) return 9;
  if (breite < 1600) return 16;
  return 25;
}

/** Robustes JSON-Lesen (Raum-Metadaten, Datenkanal). */
export function jsonLesen(text, ersatz = null) {
  if (text == null || text === '') return ersatz;
  try { return JSON.parse(typeof text === 'string' ? text : new TextDecoder().decode(text)); } catch { return ersatz; }
}

/** Zufälliger Passcode (6 Ziffern) für das Planen-Formular. */
export function passcodeVorschlag(zufall = Math.random) {
  let s = '';
  for (let i = 0; i < 6; i++) s += Math.floor(zufall() * 10);
  return s;
}

/** E-Mail-Liste aus freiem Text (Komma, Semikolon, Leerzeichen, Zeilen), ohne Duplikate, max. 20. */
export function emailsAusText(text) {
  const alle = String(text ?? '').split(/[\s,;]+/).map((x) => x.trim().toLowerCase()).filter(Boolean);
  const gueltig = [...new Set(alle.filter((x) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(x)))];
  return { gueltig: gueltig.slice(0, 20), ungueltig: alle.filter((x) => !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(x)), zuViele: gueltig.length > 20 };
}
