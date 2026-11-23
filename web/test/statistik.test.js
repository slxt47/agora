// Tests der Statistik-Rechenhilfen (WebRTC getStats → Messwerte): npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bewerten, schnappschuss, auswerten, dauerhaftSchlecht, schnitt } from '../src/raum/statistik-rechnen.js';
import { t } from '../src/i18n.js';

/** RTCStatsReport-Ersatz: Map id → Eintrag. */
const bericht = (...eintraege) => new Map(eintraege.map((e) => [e.id, e]));

const senden = (bytes, { breite = 640, rtt = 0.04, kandidat = 'host', protokoll = 'udp', fernVerlust = 0 } = {}) => bericht(
  { id: 'T1', type: 'transport', selectedCandidatePairId: 'CP1' },
  { id: 'CP1', type: 'candidate-pair', localCandidateId: 'L1', currentRoundTripTime: rtt, nominated: true, state: 'succeeded' },
  { id: 'CP2', type: 'candidate-pair', localCandidateId: 'L2', currentRoundTripTime: 9, state: 'failed' },
  { id: 'L1', type: 'local-candidate', candidateType: kandidat, protocol: protokoll },
  { id: 'L2', type: 'local-candidate', candidateType: 'relay', protocol: 'udp' },
  { id: 'O1', type: 'outbound-rtp', kind: 'video', bytesSent: bytes, frameWidth: breite, frameHeight: breite * 9 / 16, framesPerSecond: 24.4 },
  { id: 'O2', type: 'outbound-rtp', kind: 'video', bytesSent: 0, frameWidth: 320, frameHeight: 180, framesPerSecond: 24 },
  { id: 'R1', type: 'remote-inbound-rtp', kind: 'video', fractionLost: fernVerlust, jitter: 0.09 },
);
const empfangen = (bytes, pakete, verloren, jitter = 0.012) => bericht(
  { id: 'I1', type: 'inbound-rtp', kind: 'audio', bytesReceived: bytes, packetsReceived: pakete, packetsLost: verloren, jitter },
);

test('Bewertung: gleiche Schwellen wie der Server', () => {
  assert.equal(bewerten({ rtt: 40, verlust: 0.5, jitter: 8 }), 'gut');
  assert.equal(bewerten({ rtt: 250, verlust: 0, jitter: 0 }), 'mittel');
  assert.equal(bewerten({ rtt: 50, verlust: 2, jitter: 0 }), 'mittel');
  assert.equal(bewerten({ rtt: 50, verlust: 5, jitter: 0 }), 'schlecht');
  assert.equal(bewerten({ rtt: 400, verlust: 0, jitter: 0 }), 'schlecht');
  assert.equal(bewerten({ rtt: 0, verlust: 0, jitter: 60 }), 'schlecht');
});

test('Schnappschuss liest nur das aktive Kandidatenpaar und die größte Auflösung', () => {
  const s = schnappschuss([senden(1000), empfangen(500, 100, 2)], 1000);
  assert.equal(s.rtt, 40);
  assert.equal(s.kandidat, 'udp');
  assert.equal(s.aufloesung, '640x360');
  assert.equal(s.fps, 24);
  assert.equal(s.gesendet, 1000);
  assert.equal(s.empfangen, 500);
  assert.equal(Math.round(s.jitter), 12);                    // Ton-Jitter zählt, nicht der Video-Jitter (90 ms)
  assert.equal(Math.round(schnappschuss([senden(0)]).jitter), 90);   // ohne Ton: Video
  assert.equal(schnappschuss([senden(0, { kandidat: 'relay' })]).kandidat, 'relay');
  assert.equal(schnappschuss([senden(0, { kandidat: 'host', protokoll: 'tcp' })]).kandidat, 'tcp');
  assert.deepEqual(schnappschuss([]).rtt, null);
});

test('Auswerten: Bitraten aus der Differenz, Verlust eingehend oder laut Gegenstelle', () => {
  const a = schnappschuss([senden(0), empfangen(0, 0, 0)], 0);
  const b = schnappschuss([senden(1_250_000), empfangen(625_000, 950, 50)], 10_000);
  const w = auswerten(a, b);
  assert.equal(w.aus, 1000);                     // 1,25 MB in 10 s = 1000 kbit/s
  assert.equal(w.ein, 500);
  assert.equal(w.verlust, 5);                    // 50 von 1000 Paketen
  assert.equal(w.bewertung, 'schlecht');
  const c = schnappschuss([senden(2_500_000, { fernVerlust: 0.03 }), empfangen(1_250_000, 1950, 50)], 20_000);
  assert.equal(auswerten(b, c).verlust, 3);      // eingehend 0 %, aber Gegenstelle meldet 3 %
  assert.equal(auswerten(null, c).aus, 0);       // ohne Vorgänger keine Bitrate
  const neu = schnappschuss([senden(10), empfangen(10, 1, 0)], 30_000);
  assert.equal(auswerten(c, neu).aus, 0);        // Zähler zurückgesetzt (neue Verbindung) → nie negativ
});

test('Dauerhaft schlecht und Schnitt', () => {
  const g = { bewertung: 'gut', rtt: 20, verlust: 0, jitter: 2, ein: 100, aus: 200 };
  const s = { bewertung: 'schlecht', rtt: 500, verlust: 8, jitter: 60, ein: 50, aus: 60 };
  assert.equal(dauerhaftSchlecht([g, s, s]), false);
  assert.equal(dauerhaftSchlecht([g, s, s, s]), true);
  assert.deepEqual(schnitt([g, s]), { proben: 2, rtt: 260, verlust: 4, jitter: 31, ein: 75, aus: 130, schlecht_anteil: 50 });
  assert.equal(schnitt([]), null);
});

test('Texte für Statistik vorhanden', () => {
  for (const k of ['stat.titel', 'stat.warnung', 'stat.bewertung.gut', 'stat.bewertung.mittel', 'stat.bewertung.schlecht',
    'stat.kandidat.udp', 'stat.kandidat.tcp', 'stat.kandidat.relay', 'stat.kandidat.unbekannt', 'stat.leuteLabel']) assert.notEqual(t(k), k);
});
