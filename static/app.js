// Lógica de cliente en Vanilla JavaScript (sin frameworks)

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.getElementById('form-new').classList.add('hidden');
  document.getElementById('form-import').classList.add('hidden');
  document.getElementById('form-unlock').classList.add('hidden');

  if (tab === 'new') {
    document.querySelectorAll('.tab-btn')[0].classList.add('active');
    document.getElementById('form-new').classList.remove('hidden');
  } else if (tab === 'import') {
    document.querySelectorAll('.tab-btn')[1].classList.add('active');
    document.getElementById('form-import').classList.remove('hidden');
  } else if (tab === 'unlock') {
    document.querySelectorAll('.tab-btn')[2].classList.add('active');
    document.getElementById('form-unlock').classList.remove('hidden');
  }
}

function showStatus(message, isError = false) {
  const box = document.getElementById('status-msg');
  box.textContent = message;
  box.className = 'status-box ' + (isError ? 'error' : 'success');
  box.classList.remove('hidden');
}

function appendLog(line) {
  const consoleEl = document.getElementById('log-console');
  consoleEl.textContent += '\n' + line;
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

async function copyToClipboard(elementId) {
  const input = document.getElementById(elementId);
  if (!input.value) return;

  try {
    await navigator.clipboard.writeText(input.value);
    showStatus('Copiado al portapapeles!');
  } catch (err) {
    input.select();
    document.execCommand('copy');
    showStatus('Copiado al portapapeles!');
  }
}

function updateBunkerUI(data) {
  if (data.npub) {
    document.getElementById('display-npub').value = data.npub;
  }
  if (data.bunker_uri) {
    document.getElementById('display-bunker').value = data.bunker_uri;
  }
  if (data.is_active) {
    document.getElementById('relay-dot').classList.add('active');
    document.getElementById('relay-status').textContent = 'Relay: Escuchando peticiones NIP-46';
  }
}

async function handleCreateKey(e) {
  e.preventDefault();
  const passphrase = document.getElementById('passphrase-new').value;

  try {
    const res = await fetch('/api/key/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passphrase })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Error al generar clave');

    showStatus('Clave generada y cifrada en disco con Scrypt+AES-GCM');
    updateBunkerUI(data);
    appendLog(`[${new Date().toLocaleTimeString()}] Nueva clave creada: npub=${data.npub.slice(0, 12)}...`);
    appendLog(`[${new Date().toLocaleTimeString()}] Bunker URI lista para Stacker News.`);
    document.getElementById('passphrase-new').value = '';
  } catch (err) {
    showStatus(err.message, true);
  }
}

async function handleImportKey(e) {
  e.preventDefault();
  const nsec = document.getElementById('input-nsec').value;
  const passphrase = document.getElementById('passphrase-import').value;

  try {
    const res = await fetch('/api/key/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nsec_or_hex: nsec, passphrase })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Error al importar clave');

    showStatus('Clave importada y protegida con éxito');
    updateBunkerUI(data);
    appendLog(`[${new Date().toLocaleTimeString()}] Clave importada: npub=${data.npub.slice(0, 12)}...`);
    document.getElementById('input-nsec').value = '';
    document.getElementById('passphrase-import').value = '';
  } catch (err) {
    showStatus(err.message, true);
  }
}

async function handleUnlockKey(e) {
  e.preventDefault();
  const passphrase = document.getElementById('passphrase-unlock').value;

  try {
    const res = await fetch('/api/key/unlock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passphrase })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Passphrase incorrecta');

    showStatus('Firmante desbloqueado correctamente');
    updateBunkerUI(data);
    appendLog(`[${new Date().toLocaleTimeString()}] Clave desbloqueada: npub=${data.npub.slice(0, 12)}...`);
    document.getElementById('passphrase-unlock').value = '';
  } catch (err) {
    showStatus(err.message, true);
  }
}

async function pollState() {
  try {
    const res = await fetch('/api/bunker');
    if (res.ok) {
      const data = await res.json();
      updateBunkerUI(data);
    }

    const logRes = await fetch('/api/logs');
    if (logRes.ok) {
      const logs = await logRes.json();
      if (logs.length > 0) {
        document.getElementById('log-console').textContent = logs.join('\n');
      }
    }
  } catch (e) {
    // ignorar errores de polling local transitorios
  }
}

// Iniciar sondeo periódico cada 2 segundos
setInterval(pollState, 2000);
pollState();
