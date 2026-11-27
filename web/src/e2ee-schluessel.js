// Ende-zu-Ende-Verschlüsselung: das Geheimnis eines Raums (nur im Browser, nie beim Server).
// Format: 32 Zufallsbytes als base64url (43 Zeichen), verteilt im Link-Fragment `#k=…` – Fragmente gehen nicht an den
// Server. Daraus leitet HKDF-SHA-256 zwei voneinander unabhängige Werte ab:
//   - den Medienschlüssel (32 Byte) für LiveKits ExternalE2EEKeyProvider (der leitet daraus selbst AES-GCM ab),
//   - einen Prüfwert (16 Byte, hex), den der Server speichert, damit die Lobby einen falschen Schlüssel sofort erkennt.
// Reine Funktionen (WebCrypto) – laufen auch unter Node (npm test).
export const SCHLUESSEL_MUSTER = /^[A-Za-z0-9_-]{43}$/;
const SALZ = 'agora-e2ee-v1';
const SPEICHER = 'agora-e2ee:';

const krypto = () => globalThis.crypto;

function b64url(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function ausB64url(text) {
  const s = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

/** Neues Geheimnis (256 Bit). */
export function schluesselNeu() {
  return b64url(krypto().getRandomValues(new Uint8Array(32)));
}

/** Eingabe säubern (Leerzeichen, ganzer Link mit #k=…) → Geheimnis oder ''. */
export function schluesselAusText(text) {
  const roh = String(text || '').trim();
  const m = roh.match(/(?:^|[#&])k=([A-Za-z0-9_-]+)/);
  const s = (m ? m[1] : roh).replace(/\s+/g, '');
  return SCHLUESSEL_MUSTER.test(s) ? s : '';
}

/** Geheimnis aus einem Fragment wie „#k=…“ (oder „#x=1&k=…“). */
export function schluesselAusFragment(hash) {
  const m = String(hash || '').match(/^#(?:.*&)?k=([A-Za-z0-9_-]+)/);
  return m && SCHLUESSEL_MUSTER.test(m[1]) ? m[1] : '';
}

/** Link mit Schlüssel im Fragment (ohne Schlüssel unverändert). */
export function linkMitSchluessel(link, schluessel) {
  if (!schluessel) return link;
  return `${String(link).split('#')[0]}#k=${schluessel}`;
}

async function ableiten(schluessel, info, bytes) {
  const roh = ausB64url(schluessel);
  const basis = await krypto().subtle.importKey('raw', roh, 'HKDF', false, ['deriveBits']);
  const bits = await krypto().subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt: new TextEncoder().encode(SALZ), info: new TextEncoder().encode(info) }, basis, bytes * 8);
  return new Uint8Array(bits);
}

/** Prüfwert für den Server (32 Hex-Zeichen). */
export async function pruefwert(schluessel) {
  const b = await ableiten(schluessel, 'pruefwert', 16);
  return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
}

/** Medienschlüssel (ArrayBuffer, 32 Byte) für ExternalE2EEKeyProvider.setKey (→ HKDF im LiveKit-Worker). */
export async function medienSchluessel(schluessel) {
  return (await ableiten(schluessel, 'medien', 32)).buffer;
}

/** Schlüssel für die Fernsteuerung (AES-GCM, eigene Ableitung): Eingaben werden in E2EE-Meetings zusätzlich selbst
 *  verschlüsselt, weil livekit-client bei verschlüsselten Datenpaketen dem Empfänger nicht verlässlich meldet, dass sie
 *  verschlüsselt waren (encryptionType kommt als NONE an) – so weiß die Desktop-App sicher, dass ein Paket den Schlüssel
 *  kannte. */
export async function fernsteuerungSchluessel(schluessel) {
  return krypto().subtle.importKey('raw', await ableiten(schluessel, 'fernsteuerung', 32), 'AES-GCM', false, ['encrypt', 'decrypt']);
}

/** Host-Gerät merkt sich das Geheimnis je Raum (damit „Link kopieren“ im Konto den Schlüssel enthält). */
export function schluesselMerken(code, schluessel, speicher = globalThis.localStorage) {
  try { if (schluessel) speicher.setItem(SPEICHER + code, schluessel); else speicher.removeItem(SPEICHER + code); } catch { /* privat */ }
}
export function schluesselGemerkt(code, speicher = globalThis.localStorage) {
  try { const s = speicher.getItem(SPEICHER + code) || ''; return SCHLUESSEL_MUSTER.test(s) ? s : ''; } catch { return ''; }
}
