// Dolmetscher-Kanäle: reine Logik ohne DOM/LiveKit (auch in den Node-Tests).
export const SPUR = 'dolmetsch:';
export const LEISE = 0.2;              // Saal-Lautstärke, solange ein Dolmetsch-Kanal läuft
export const SPRACHEN_VORSCHLAG = ['de', 'en', 'fr', 'es', 'it', 'pl', 'tr', 'uk', 'ar'];

/** 'dolmetsch:en' → 'en', sonst null. */
export function kanalSpur(name) {
  const m = /^dolmetsch:([a-z]{2,3})$/.exec(name || '');
  return m ? m[1] : null;
}

/** Welche Spur soll ein Zuhörer für den Kanal abonnieren? → {identity, spur} oder null (Originalton). Nur die vom Server
 *  in den Metadaten genannte Person zählt – eine gleichnamige Spur von jemand anderem wird nie abonniert. */
export function kanalZiel(zustand, kanal) {
  const id = kanal && zustand?.kanaele?.[kanal];
  return id ? { identity: id, spur: SPUR + kanal } : null;
}

/** Soll diese Spur (identity, Spurname) beim gewählten Kanal abonniert sein? */
export function abonnieren(zustand, kanal, identity, spurName) {
  const z = kanalZiel(zustand, kanal);
  return !!z && z.identity === identity && z.spur === spurName;
}

/** Andere Dolmetscher mit demselben Sprachpaar (für Übergabe). */
export function partner(zustand, ich) {
  const d = zustand?.dolmetscher || {};
  const meins = d[ich];
  if (!meins) return [];
  return Object.entries(d).filter(([id, v]) => id !== ich && new Set([v.a, v.b, meins.a, meins.b]).size === 2).map(([id, v]) => ({ identity: id, ...v }));
}

/** Sprachname in der Sprache der Oberfläche (Intl), sonst der Code. */
export function sprachName(code, gebiet = 'de') {
  try { return new Intl.DisplayNames([gebiet], { type: 'language' }).of(code) || code; } catch { return code; }
}
