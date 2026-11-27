// Text des Banners aus der Adresse (?text=…, vom Hauptprozess gesetzt) – nur als Text, nie als HTML.
'use strict';
const text = new URLSearchParams(location.search).get('text');
if (text) document.getElementById('text').textContent = text.slice(0, 200);
