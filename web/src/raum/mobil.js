// Meeting auf Handy/Tablet: Drehen (Menüs zu, eigenes Bild zurück in die Ecke, Bühne neu verteilen) und Rückkehr aus
// dem Hintergrund – vor allem iOS-Safari: dort kann die Kamera-/Mikrofonspur beim Wechsel in eine andere App enden
// (Anruf, Kamera-App), Video-/Audio-Elemente pausieren, und die Audioausgabe hängt nach einem Wechsel der Audio-Route
// (Kopfhörer, Bluetooth). Beim Zurückkommen: beendete Spuren neu holen, Wiedergabe fortsetzen, Ton wieder freigeben.
// Den Bildschirm hält meeting.js wach (Screen Wake Lock, nach dem Zurückkommen neu angefordert).
import { Track } from 'livekit-client';
import { menueSchliessen } from './menue.js';
import './mobil.css';

export function mobilEinrichten(m) {
  const drehen = () => {
    menueSchliessen();
    const s = document.getElementById('selbst');
    if (s) for (const k of ['left', 'top', 'right', 'bottom']) s.style[k] = '';
    m.buehne?.planen();
  };
  if (screen.orientation?.addEventListener) screen.orientation.addEventListener('change', drehen);
  else addEventListener('orientationchange', drehen);

  const zurueck = async () => {
    if (document.hidden || m.beendet || !m.room) return;
    const lp = m.room.localParticipant;
    for (const pub of lp.trackPublications.values()) {
      const spur = pub.track;
      if (!spur || pub.isMuted || (pub.source !== Track.Source.Camera && pub.source !== Track.Source.Microphone)) continue;
      if (spur.mediaStreamTrack?.readyState === 'ended') await spur.restartTrack().catch(() => {});
    }
    m.room.startAudio?.().catch(() => {});
    for (const el of document.querySelectorAll('#v-meeting video, #audio-ablage audio')) if (el.paused && el.srcObject) el.play().catch(() => {});
  };
  document.addEventListener('visibilitychange', zurueck);
  addEventListener('pageshow', zurueck);
  // Neue Audio-Route (Kopfhörer rein/raus): Wiedergabe neu anstoßen, sonst bleibt es auf iOS manchmal still
  navigator.mediaDevices?.addEventListener?.('devicechange', () => setTimeout(zurueck, 300));
}
