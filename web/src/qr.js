// QR-Code lokal im Browser (qrcode-generator, ohne Abhängigkeiten, nichts vom CDN) – für die Einrichtung der
// Zwei-Faktor-Anmeldung. Ergebnis als SVG-Pfad; gezeichnet wird über DOM (createElementNS), nie per innerHTML,
// keine style-Attribute (strenge CSP).
import qrcode from 'qrcode-generator';

const RAND = 4;      // Ruhezone in Modulen (Norm: 4)

/** Text → {groesse (Module inkl. Rand), d (SVG-Pfad der dunklen Module)}. Fehlerkorrektur M, Version automatisch. */
export function qrPfad(text) {
  const qr = qrcode(0, 'M');
  qr.addData(text, 'Byte');
  qr.make();
  const n = qr.getModuleCount();
  const teile = [];
  for (let y = 0; y < n; y++) {
    let x = 0;
    while (x < n) {
      if (!qr.isDark(y, x)) { x++; continue; }
      const start = x;
      while (x < n && qr.isDark(y, x)) x++;
      teile.push(`M${start + RAND} ${y + RAND}h${x - start}v1h-${x - start}z`);    // Läufe je Zeile → kurzer Pfad
    }
  }
  return { groesse: n + 2 * RAND, module: n, d: teile.join('') };
}

/** SVG-Element mit weißem Grund (QR-Leser brauchen hellen Rand, auch im Dunkelmodus). */
export function qrSvg(text, { label = '' } = {}) {
  const NS = 'http://www.w3.org/2000/svg';
  const { groesse, d } = qrPfad(text);
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${groesse} ${groesse}`);
  svg.setAttribute('shape-rendering', 'crispEdges');
  svg.setAttribute('role', 'img');
  if (label) svg.setAttribute('aria-label', label);
  const grund = document.createElementNS(NS, 'rect');
  for (const [k, v] of [['width', groesse], ['height', groesse], ['fill', '#ffffff']]) grund.setAttribute(k, String(v));
  const pfad = document.createElementNS(NS, 'path');
  pfad.setAttribute('d', d);
  pfad.setAttribute('fill', '#000000');
  svg.append(grund, pfad);
  return svg;
}
