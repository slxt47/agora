// Virtuelle Hintergründe im slxt-Stil (nur Node-Bordmittel, keine Fotos, keine fremden Rechte): Nachthimmel, Polarlicht,
// Violetter Nebel, Dämmerung, Studio, Morgenlicht. Erzeugt SVGs (je wenige kB) nach src/raum/hintergruende/ – Vite hängt
// einen Hash an (/assets/…, immutable). effekte.js rastert sie im Browser einmal auf 1280×720 (Canvas → JPEG-Blob) und gibt
// das Ergebnis an den Hintergrund-Prozessor. Zufall mit festem Startwert → jeder Build erzeugt dieselben Bilder.
import { mkdirSync, writeFileSync } from 'node:fs';

const ZIEL = new URL('../src/raum/hintergruende/', import.meta.url);
mkdirSync(ZIEL, { recursive: true });
const W = 1280, H = 720;

function zufall(start) {
  let a = start >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
const r1 = (x) => Math.round(x * 10) / 10;

function sterne(z, n, maxY, { hell = 1 } = {}) {
  let s = '';
  for (let i = 0; i < n; i++) {
    const gross = z() < 0.07;
    const rad = gross ? 1.3 + z() * 0.7 : 0.35 + z() * 0.75;
    const farbe = z() < 0.18 ? '#cdd8ff' : '#ffffff';
    s += `<circle cx="${r1(z() * W)}" cy="${r1(Math.pow(z(), 1.25) * maxY)}" r="${r1(rad)}" fill="${farbe}" fill-opacity="${r1((0.25 + z() * 0.7) * hell * 100) / 100}"/>`;
  }
  return s;
}
const blur = (id, std) => `<filter id="${id}" filterUnits="userSpaceOnUse" x="-400" y="-400" width="${W + 800}" height="${H + 800}"><feGaussianBlur stdDeviation="${std}"/></filter>`;
const verlauf = (id, stops, { x2 = 0, y2 = 1 } = {}) =>
  `<linearGradient id="${id}" x1="0" y1="0" x2="${x2}" y2="${y2}">${stops.map(([o, c, a = 1]) => `<stop offset="${o}" stop-color="${c}"${a < 1 ? ` stop-opacity="${a}"` : ''}/>`).join('')}</linearGradient>`;
const leuchten = (id, cx, cy, r, farbe, a) =>
  `<radialGradient id="${id}" cx="${cx}" cy="${cy}" r="${r}"><stop offset="0" stop-color="${farbe}" stop-opacity="${a}"/><stop offset="1" stop-color="${farbe}" stop-opacity="0"/></radialGradient>`;
/** Hügelkette als geschlossener Pfad (unten bündig) */
function huegel(z, basis, hoehe, zacken, farbe, a = 1) {
  let d = `M0 ${H}L0 ${r1(basis)}`;
  for (let i = 1; i <= zacken; i++) {
    const x = (W * i) / zacken, y = basis - z() * hoehe;
    const cx = x - W / zacken / 2;
    d += `Q${r1(cx)} ${r1(y - hoehe * 0.35 * z())} ${r1(x)} ${r1(basis - z() * hoehe * 0.6)}`;
  }
  return `<path d="${d}L${W} ${H}Z" fill="${farbe}"${a < 1 ? ` fill-opacity="${a}"` : ''}/>`;
}
/** Polarlicht-Band: Fläche zwischen zwei Wellen, unten hell, nach oben ausblendend */
function band(z, y0, amp, dicke, verlaufId) {
  const pkt = [], phase = z() * Math.PI * 2, f = 1.4 + z() * 1.2;
  for (let x = -80; x <= W + 80; x += 40) {
    const t = x / W;
    pkt.push([x, y0 + Math.sin(t * Math.PI * f + phase) * amp + Math.sin(t * 9 + phase) * amp * 0.18]);
  }
  const oben = pkt.map(([x, y], i) => [x, y - dicke * (0.55 + 0.45 * Math.sin(i * 0.5 + phase))]);
  const d = `M${pkt.map(([x, y]) => `${r1(x)} ${r1(y)}`).join('L')}L${oben.reverse().map(([x, y]) => `${r1(x)} ${r1(y)}`).join('L')}Z`;
  return `<path d="${d}" fill="url(#${verlaufId})"/>`;
}
const svg = (defs, inhalt) => `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><defs>${defs}</defs>${inhalt}</svg>\n`;

const BILDER = {
  nachthimmel() {
    const z = zufall(11);
    return svg(
      verlauf('g', [[0, '#04050d'], [0.55, '#0b0e2a'], [1, '#1a1240']]) + leuchten('a', 0.12, 1, 0.75, '#6d4aff', 0.42) + leuchten('b', 0.92, 0.05, 0.6, '#3b82f6', 0.22)
      + `<linearGradient id="m" gradientUnits="userSpaceOnUse" x1="820" y1="120" x2="1010" y2="205"><stop offset="0" stop-color="#bed7ff" stop-opacity="0"/><stop offset="1" stop-color="#ffffff" stop-opacity=".9"/></linearGradient>`
      + blur('w', 40) + blur('s', 2.2),
      `<rect width="${W}" height="${H}" fill="url(#g)"/><rect width="${W}" height="${H}" fill="url(#a)"/><rect width="${W}" height="${H}" fill="url(#b)"/>`
      + `<ellipse cx="700" cy="300" rx="760" ry="70" transform="rotate(-18 700 300)" fill="#9fb4ff" fill-opacity=".07" filter="url(#w)"/>`
      + sterne(z, 230, H * 0.95)
      + `<g filter="url(#s)">${sterne(zufall(5), 6, H * 0.7, { hell: 1 }).replace(/r="[\d.]+"/g, 'r="2.6"')}</g>`
      + `<line x1="820" y1="120" x2="1010" y2="205" stroke="url(#m)" stroke-width="2.2" stroke-linecap="round"/>`);
  },
  polarlicht() {
    const z = zufall(23);
    return svg(
      verlauf('g', [[0, '#02040c'], [0.6, '#061428'], [1, '#0b0e2a']])
      + verlauf('p1', [[0, '#3ddc9f', 0], [0.75, '#3ddc9f', 0.55], [1, '#a7f3d0', 0.15]])
      + verlauf('p2', [[0, '#8b5cf6', 0], [0.7, '#8b5cf6', 0.5], [1, '#38bdf8', 0.15]])
      + verlauf('p3', [[0, '#38bdf8', 0], [0.8, '#2dd4bf', 0.4], [1, '#2dd4bf', 0.1]])
      + blur('w', 22) + blur('w2', 34) + leuchten('h', 0.5, 1, 0.7, '#2dd4bf', 0.16),
      `<rect width="${W}" height="${H}" fill="url(#g)"/>` + sterne(z, 170, H * 0.75)
      + `<g filter="url(#w2)">${band(z, 330, 70, 220, 'p2')}</g>`
      + `<g filter="url(#w)">${band(z, 400, 60, 200, 'p1')}${band(z, 470, 40, 140, 'p3')}</g>`
      + `<rect width="${W}" height="${H}" fill="url(#h)"/>`
      + huegel(z, 610, 90, 7, '#0a1424') + huegel(z, 660, 70, 9, '#050a14'));
  },
  nebel() {
    const z = zufall(37);
    const kreise = [[300, 240, 280, '#6d4aff', 0.7], [960, 520, 320, '#3b82f6', 0.5], [820, 140, 210, '#b04aff', 0.45], [140, 640, 230, '#4f46e5', 0.45], [600, 420, 160, '#7c5cff', 0.35]];
    return svg(
      blur('w', 95),
      `<rect width="${W}" height="${H}" fill="#0b0a1f"/>`
      + `<g filter="url(#w)">${kreise.map(([x, y, r, c, a]) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${c}" fill-opacity="${a}"/>`).join('')}</g>`
      + sterne(z, 50, H, { hell: 0.45 }));
  },
  daemmerung() {
    const z = zufall(41);
    return svg(
      verlauf('g', [[0, '#0b0e2a'], [0.34, '#2b1a5c'], [0.6, '#8a3f8c'], [0.76, '#e27a7a'], [0.9, '#fdbf8f']])
      + leuchten('s', 0.62, 0.88, 0.42, '#ffd9b0', 0.75),
      `<rect width="${W}" height="${H}" fill="url(#g)"/><rect width="${W}" height="${H}" fill="url(#s)"/>`
      + sterne(z, 70, H * 0.3, { hell: 0.8 })
      + huegel(z, 560, 80, 6, '#5a2c6e', 0.85) + huegel(z, 610, 70, 8, '#2e1847') + huegel(z, 665, 50, 10, '#140b24'));
  },
  studio() {
    const z = zufall(53);
    let bokeh = '';
    for (let i = 0; i < 16; i++) {
      const r = 18 + z() * 60;
      bokeh += `<circle cx="${r1(z() * W)}" cy="${r1(z() * H * 0.8)}" r="${r1(r)}" fill="${z() < 0.5 ? '#ffffff' : '#c9bbff'}" fill-opacity="${r1((0.04 + z() * 0.09) * 100) / 100}"/>`;
    }
    return svg(
      leuchten('g', 0.32, 0.28, 0.95, '#3a3d5c', 1) + blur('w', 7)
      + `<radialGradient id="r" cx=".5" cy=".5" r=".8"><stop offset=".55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".55"/></radialGradient>`,
      `<rect width="${W}" height="${H}" fill="#14162a"/><rect width="${W}" height="${H}" fill="url(#g)"/>`
      + `<g filter="url(#w)">${bokeh}</g><rect width="${W}" height="${H}" fill="url(#r)"/>`);
  },
  morgen() {
    const kreise = [[260, 180, 260, '#ffffff', 0.75], [1000, 160, 240, '#a993ff', 0.35], [880, 600, 300, '#7cb4ff', 0.3], [200, 620, 220, '#f6c8e4', 0.45]];
    return svg(
      verlauf('g', [[0, '#dfe5ff'], [0.55, '#efe3f8'], [1, '#fde7da']]) + blur('w', 90),
      `<rect width="${W}" height="${H}" fill="url(#g)"/>`
      + `<g filter="url(#w)">${kreise.map(([x, y, r, c, a]) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${c}" fill-opacity="${a}"/>`).join('')}</g>`);
  },
};

for (const [name, fn] of Object.entries(BILDER)) writeFileSync(new URL(`${name}.svg`, ZIEL), fn());
console.log(`Hintergründe erzeugt: ${Object.keys(BILDER).join(', ')}`);
