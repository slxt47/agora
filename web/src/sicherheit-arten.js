// Sicherheitsereignisse (GET /api/konto/sicherheit): Art → Symbol und Ton. Ohne DOM, auch in Node-Tests nutzbar.
const ARTEN = {
  anmeldung: ['abmelden', 'ok'], anmeldung_2fa: ['schloss', 'ok'], anmeldung_code: ['schloss', 'warn'],
  fehlversuch_passwort: ['schliessen', 'warn'], fehlversuch_code: ['schliessen', 'warn'], fehlversuch_gesperrt: ['schliessen', 'warn'],
  passwort_geaendert: ['stift', 'ok'], passwort_zurueckgesetzt: ['mail', 'ok'], email_bestaetigt: ['haken', 'ok'],
  zwei_faktor_an: ['schloss', 'ok'], zwei_faktor_aus: ['schlossOffen', 'warn'], zwei_faktor_codes: ['kopie', 'ok'],
  zwei_faktor_code_benutzt: ['schloss', 'warn'], zwei_faktor_admin: ['schild', 'warn'], gesperrt: ['schild', 'warn'], entsperrt: ['schild', 'ok'],
  api_schluessel_neu: ['schluessel', 'ok'], api_schluessel_weg: ['schluessel', 'ok'],
  anmeldung_extern: ['abmelden', 'ok'], extern_verknuepft: ['link', 'ok'], extern_getrennt: ['link', 'warn'],
};
export const SICHERHEIT_ARTEN = Object.keys(ARTEN);

export function sicherheitArt(art) {
  const [symbol, ton] = ARTEN[art] || ['info', 'ok'];
  return { symbol, ton };
}
