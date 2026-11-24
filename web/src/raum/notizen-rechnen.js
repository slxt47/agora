// Gemeinsame Notizen – reine Hilfen (ohne DOM): zeilenweises Zusammenführen zweier Fassungen (3-Wege, wie diff3 in klein)
// und eine sehr kleine Markdown-Vorschau als Bausteine (Überschrift, Liste, Absatz, **fett**, `code`) – nie als HTML.

const GRENZE = 4e6;          // Zellen der LCS-Tabelle; größer → kein automatisches Zusammenführen

/** Änderungen von a nach b als Abschnitte {von, bis, neu:[Zeilen]} (Zeilen a[von..bis) werden durch neu ersetzt) oder null. */
export function abschnitte(a, b) {
  const n = a.length, m = b.length;
  let vorn = 0;
  while (vorn < n && vorn < m && a[vorn] === b[vorn]) vorn++;
  let hinten = 0;
  while (hinten < n - vorn && hinten < m - vorn && a[n - 1 - hinten] === b[m - 1 - hinten]) hinten++;
  const A = a.slice(vorn, n - hinten), B = b.slice(vorn, m - hinten);
  const N = A.length, M = B.length;
  if (N * M > GRENZE) return null;
  const L = Array.from({ length: N + 1 }, () => new Uint32Array(M + 1));
  for (let i = N - 1; i >= 0; i--) for (let j = M - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const aus = [];
  let i = 0, j = 0, h = null;
  while (i < N || j < M) {
    if (i < N && j < M && A[i] === B[j]) { if (h) { aus.push(h); h = null; } i++; j++; continue; }
    h ||= { von: i + vorn, bis: i + vorn, neu: [] };
    if (j < M && (i >= N || L[i][j + 1] >= L[i + 1][j])) { h.neu.push(B[j]); j++; } else { i++; h.bis = i + vorn; }
  }
  if (h) aus.push(h);
  return aus;
}

const gleich = (x, y) => x.von === y.von && x.bis === y.bis && x.neu.length === y.neu.length && x.neu.every((z, k) => z === y.neu[k]);

function kollidiert(x, y) {
  if (gleich(x, y)) return false;
  const xe = x.von === x.bis, ye = y.von === y.bis;      // reine Einfügung
  if (xe && ye) return x.von === y.von;
  if (xe) return y.von <= x.von && x.von < y.bis;
  if (ye) return x.von <= y.von && y.von < x.bis;
  return x.von < y.bis && y.von < x.bis;
}

/** 3-Wege-Zusammenführung: basis → meins und basis → deren. {ok:true, text} oder {ok:false} bei Überschneidung. */
export function zusammenfuehren(basis, meins, deren) {
  if (meins === deren) return { ok: true, text: meins };
  if (meins === basis) return { ok: true, text: deren };
  if (deren === basis) return { ok: true, text: meins };
  const b = basis.split('\n');
  const hm = abschnitte(b, meins.split('\n')), hd = abschnitte(b, deren.split('\n'));
  if (!hm || !hd) return { ok: false };
  for (const x of hm) for (const y of hd) if (kollidiert(x, y)) return { ok: false };
  const alle = [...hm, ...hd.filter((y) => !hm.some((x) => gleich(x, y)))];
  alle.sort((x, y) => y.von - x.von || y.bis - x.bis);      // von hinten einsetzen, damit Indizes stimmen
  const z = [...b];
  for (const x of alle) z.splice(x.von, x.bis - x.von, ...x.neu);
  return { ok: true, text: z.join('\n') };
}

/** Cursor nach dem Ersetzen von alt durch neu möglichst an derselben Stelle halten. */
export function cursorNachher(alt, neu, pos) {
  let vorn = 0;
  while (vorn < alt.length && vorn < neu.length && alt[vorn] === neu[vorn]) vorn++;
  return pos <= vorn ? pos : Math.max(vorn, Math.min(neu.length, pos + neu.length - alt.length));
}

/** Inline: **fett** und `code` → [{text, fett?, code?}]. */
export function inline(zeile) {
  const aus = [];
  const re = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  let rest = 0, m;
  while ((m = re.exec(zeile))) {
    if (m.index > rest) aus.push({ text: zeile.slice(rest, m.index) });
    aus.push(m[1] !== undefined ? { text: m[1], fett: true } : { text: m[2], code: true });
    rest = re.lastIndex;
  }
  if (rest < zeile.length) aus.push({ text: zeile.slice(rest) });
  return aus;
}

/** Markdown light → Blöcke [{art: h1|h2|h3|liste|aufgabe|absatz, teile|punkte}]. */
export function bloecke(text) {
  const aus = [];
  let liste = null;
  for (const roh of String(text || '').split('\n')) {
    const zeile = roh.trimEnd();
    const ue = /^(#{1,3})\s+(.*)$/.exec(zeile);
    const punkt = /^\s*[-*]\s+(\[( |x|X)\]\s+)?(.*)$/.exec(zeile);
    if (punkt) {
      if (!liste) { liste = { art: 'liste', punkte: [] }; aus.push(liste); }
      liste.punkte.push({ teile: inline(punkt[3]), erledigt: punkt[1] ? punkt[2].toLowerCase() === 'x' : null });
      continue;
    }
    liste = null;
    if (ue) aus.push({ art: `h${ue[1].length}`, teile: inline(ue[2]) });
    else if (zeile) aus.push({ art: 'absatz', teile: inline(zeile) });
  }
  return aus;
}
