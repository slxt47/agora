// Reine Rechenhilfen für die Verbindungsstatistik (ohne DOM) – laufen im Browser und unter `node --test`
// (test/statistik.test.js). Eingabe sind RTCStatsReports (getStats() der Verbindungen) bzw. Map-artige Objekte.

/** Gleiche Schwellen wie der Server (app/qualitaet.py). */
export function bewerten({ rtt = 0, verlust = 0, jitter = 0 } = {}) {
  if (verlust >= 5 || rtt >= 400 || jitter >= 50) return 'schlecht';
  if (verlust >= 2 || rtt >= 200 || jitter >= 30) return 'mittel';
  return 'gut';
}

const werte = (bericht) => (typeof bericht?.values === 'function' ? [...bericht.values()] : Object.values(bericht || {}));

/**
 * Aus einem oder mehreren getStats()-Berichten einen Schnappschuss mit Zählerständen machen:
 * {zeit, gesendet, empfangen (Bytes), pakete, verloren (eingehend), jitter (ms, max; Ton zählt vor Video, weil Video-
 * Bilder in Paketschüben kommen und den Jitter künstlich hochtreiben), rtt (ms, Schnitt der aktiven
 * Kandidatenpaare), fernVerlust (% laut Gegenstelle), kandidat ('udp'|'tcp'|'relay'|''), aufloesung, fps}.
 */
export function schnappschuss(berichte, zeit = Date.now()) {
  const s = { zeit, gesendet: 0, empfangen: 0, pakete: 0, verloren: 0, jitter: 0, rtt: null, fernVerlust: 0, kandidat: '', aufloesung: '', fps: 0 };
  const rtts = [];
  const jitter = { audio: 0, video: 0 };
  let breite = 0;
  for (const bericht of berichte) {
    const alle = werte(bericht);
    const nachId = new Map(alle.map((x) => [x.id, x]));
    const gewaehlt = new Set(alle.filter((x) => x.type === 'transport' && x.selectedCandidatePairId).map((x) => x.selectedCandidatePairId));
    for (const x of alle) {
      if (x.type === 'candidate-pair') {
        const aktiv = gewaehlt.size ? gewaehlt.has(x.id) : (x.nominated && x.state === 'succeeded');
        if (!aktiv) continue;
        if (typeof x.currentRoundTripTime === 'number') rtts.push(x.currentRoundTripTime * 1000);
        const lokal = nachId.get(x.localCandidateId);
        if (lokal && !s.kandidat) s.kandidat = lokal.candidateType === 'relay' ? 'relay' : (lokal.protocol === 'tcp' ? 'tcp' : 'udp');
      } else if (x.type === 'outbound-rtp') {
        s.gesendet += x.bytesSent || 0;
        if (x.kind === 'video' && (x.frameWidth || 0) > breite) {
          breite = x.frameWidth;
          s.aufloesung = `${x.frameWidth}x${x.frameHeight || 0}`;
          s.fps = Math.round(x.framesPerSecond || 0);
        }
      } else if (x.type === 'inbound-rtp') {
        s.empfangen += x.bytesReceived || 0;
        s.pakete += x.packetsReceived || 0;
        s.verloren += Math.max(0, x.packetsLost || 0);
        if (typeof x.jitter === 'number') jitter[x.kind === 'video' ? 'video' : 'audio'] = Math.max(jitter[x.kind === 'video' ? 'video' : 'audio'], x.jitter * 1000);
      } else if (x.type === 'remote-inbound-rtp') {
        if (typeof x.fractionLost === 'number') s.fernVerlust = Math.max(s.fernVerlust, x.fractionLost * 100);
        if (typeof x.jitter === 'number') jitter[x.kind === 'video' ? 'video' : 'audio'] = Math.max(jitter[x.kind === 'video' ? 'video' : 'audio'], x.jitter * 1000);
        if (!rtts.length && typeof x.roundTripTime === 'number') rtts.push(x.roundTripTime * 1000);
      }
    }
  }
  s.jitter = jitter.audio || jitter.video;
  if (rtts.length) s.rtt = rtts.reduce((a, b) => a + b, 0) / rtts.length;
  return s;
}

const r1 = (x) => Math.round(x * 10) / 10;

/**
 * Zwei Schnappschüsse → Messwerte der letzten Spanne: {rtt, verlust (%), jitter (ms), ein, aus (kbit/s), kandidat,
 * aufloesung, fps, bewertung}. Ohne vorherigen Schnappschuss gibt es keine Bitraten (0).
 */
export function auswerten(vorher, jetzt) {
  const dt = vorher ? (jetzt.zeit - vorher.zeit) / 1000 : 0;
  const delta = (k) => (vorher ? Math.max(0, jetzt[k] - vorher[k]) : 0);
  const kbit = (k) => (dt > 0 ? Math.round((delta(k) * 8) / 1000 / dt) : 0);
  const pakete = delta('pakete'), verloren = delta('verloren');
  const eingehend = pakete + verloren > 0 ? (verloren / (pakete + verloren)) * 100 : 0;
  const w = {
    rtt: Math.round(jetzt.rtt ?? 0),
    verlust: r1(Math.min(100, Math.max(eingehend, jetzt.fernVerlust || 0))),
    jitter: Math.round(jetzt.jitter || 0),
    ein: kbit('empfangen'),
    aus: kbit('gesendet'),
    kandidat: jetzt.kandidat || '',
    aufloesung: jetzt.aufloesung || '',
    fps: jetzt.fps || 0,
  };
  w.bewertung = bewerten(w);
  return w;
}

/** Läuft es seit `n` Messungen schlecht? (für den Warnhinweis) */
export function dauerhaftSchlecht(verlauf, n = 3) {
  return verlauf.length >= n && verlauf.slice(-n).every((w) => w.bewertung === 'schlecht');
}

/** Schnitt über einen Verlauf von Messwerten (für die Rückmeldung am Ende). */
export function schnitt(verlauf) {
  if (!verlauf.length) return null;
  const mittel = (k) => verlauf.reduce((a, w) => a + (w[k] || 0), 0) / verlauf.length;
  const n = verlauf.length;
  return {
    proben: n, rtt: Math.round(mittel('rtt')), verlust: r1(mittel('verlust')), jitter: Math.round(mittel('jitter')),
    ein: Math.round(mittel('ein')), aus: Math.round(mittel('aus')),
    schlecht_anteil: Math.round((verlauf.filter((w) => w.bewertung === 'schlecht').length / n) * 100),
  };
}
