// Reine Hilfen der Zuschauerseite /h/<code> (ohne DOM, getestet in test/hls.test.js).

const TOKEN_RE = /^[\w-]{10,100}$/;

/** Persönlicher Anmelde-Token aus einem eingefügten Link (…?anmeldung=<token>) oder dem Token selbst → Token oder ''. */
export function tokenAusLink(text) {
  const s = String(text ?? '').trim();
  if (!s || s.length > 400) return '';
  const m = /[?&]anmeldung=([\w-]+)/.exec(s);
  const tok = m ? m[1] : s;
  return TOKEN_RE.test(tok) ? tok : '';
}

/** Nur Quellen unserer eigenen Auslieferung abspielen (/hls/<code>/<kennung>/index.m3u8) – nie eine fremde URL. */
export function quelleGueltig(quelle, code) {
  return typeof quelle === 'string' && /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/.test(code || '')
    && quelle === `/hls/${code}/${quelle.split('/')[3]}/index.m3u8` && /^[A-Za-z0-9_-]{22}$/.test(quelle.split('/')[3] || '');
}

/** Abspielweg: Safari (und andere mit eingebautem HLS) nativ, sonst hls.js über Media Source Extensions. */
export function abspielweg(video, mse) {
  if (video?.canPlayType?.('application/vnd.apple.mpegurl')) return 'nativ';
  return mse ? 'hlsjs' : 'keiner';
}

const ZEICHEN = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_';

/** Zufällige Kennung dieser Seite fürs Zählen (bleibt beim Neuladen im selben Tab). */
export function zuschauerId(speicher, zufall = () => crypto.getRandomValues(new Uint8Array(20))) {
  try {
    const alt = speicher?.getItem('agora-hls-id');
    if (alt && /^[A-Za-z0-9_-]{16,40}$/.test(alt)) return alt;
  } catch { /* privat */ }
  const id = [...zufall()].map((b) => ZEICHEN[b & 63]).join('');
  try { speicher?.setItem('agora-hls-id', id); } catch { /* privat */ }
  return id;
}
