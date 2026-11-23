// Melden/Blockieren ohne DOM (auch in Node-Tests nutzbar): Gründe wie im Backend (app/melden.py), Konto aus identity.
export const GRUENDE = ['belaestigung', 'hass', 'unangemessen', 'stoerung', 'spam', 'sonstiges'];

/** Konto-ID aus der LiveKit-identity (`u<id>-…`); Gäste (`g-…`) → null. */
export function kontoVon(identity) {
  const m = /^u(\d+)-/.exec(identity || '');
  return m ? Number(m[1]) : null;
}
