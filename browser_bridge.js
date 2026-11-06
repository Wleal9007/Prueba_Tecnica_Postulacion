/**
 * SNIPPET PUENTE PARA LA CONSOLA DEL NAVEGADOR (F12)
 *
 * Si estás en https://stacker.news/signup?callbackUrl=https%3A%2F%2Fstacker.news%2F&type=nostr
 * y no tienes una extensión instalada, pega este código en la Consola (F12) y presiona Enter.
 *
 * Luego haz clic en el botón: "Login with browser extension".
 * Stacker News detectará este window.nostr y delegará la firma a tu servidor local en 127.0.0.1.
 */

(() => {
  const LOCAL_SIGNER_URL = 'http://127.0.0.1:8000';

  window.nostr = {
    async getPublicKey() {
      const res = await fetch(`${LOCAL_SIGNER_URL}/api/bunker`);
      const data = await res.json();
      if (!data.pubkey) {
        alert('Por favor desbloquea o genera una clave en http://127.0.0.1:8000 primero.');
        throw new Error('Firmante bloqueado');
      }
      return data.pubkey;
    },

    async signEvent(event) {
      console.log('[NIP-46 Bridge] Solicitando firma para evento kind', event.kind, event);
      const res = await fetch(`${LOCAL_SIGNER_URL}/api/sign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event })
      });
      if (!res.ok) {
        const err = await res.json();
        alert('Error al firmar: ' + (err.error || 'Desconocido'));
        throw new Error(err.error);
      }
      const signed = await res.json();
      console.log('[NIP-46 Bridge] Evento firmado recibido:', signed);
      return signed;
    }
  };

  console.log('%c [NIP-46 Bridge Activo] Ahora puedes hacer clic en "Login with browser extension" en Stacker News.', 'color: #10b981; font-weight: bold; font-size: 14px;');
})();
