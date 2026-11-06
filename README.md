# Firmante Remoto Nostr (NIP-46) & Login en Stacker News

Implementación de un firmante remoto Nostr según la especificación **NIP-46** con custodia local de claves cifrada en reposo (**Scrypt + AES-256-GCM**), interfaz web minimalista en **Vanilla HTML/CSS/JS** servida exclusivamente en `127.0.0.1` mediante la biblioteca estándar de Python, y demostración reproducible de autenticación contra **Stacker News** (NIP-98).

---

## 1. Qué Hace el Proyecto

> **Nota para la Evaluación:** El registro completo y cronológico de prompting con el agente de IA requerido por la sección 4.3 del PDF se encuentra disponible en [`REGISTRO_PROMPTING_IA.md`](REGISTRO_PROMPTING_IA.md).

1. **Gestión Segura de Claves:**
   * Genera o importa claves Nostr en formato `nsec1...` (Bech32 NIP-19) o hexadecimal.
   * Deriva la clave pública x-only (32 bytes) y genera firmas Schnorr compatibles con **BIP-340** usando bindings directos en C a `libsecp256k1` (`coincurve`).
   * Cifra la clave privada en reposo con **AES-256-GCM** y clave derivada mediante **KDF Scrypt** ($N=16384, r=8, p=1$), guardando el archivo fuera del proyecto (`~/.nostr/signer.enc`) con permisos restringidos `0600`.
   * Ofusca las claves en logs de consola (`nsec1***...`).
2. **Firmante Remoto NIP-46 (Bunker):**
   * Genera y parsea URIs canónicas `bunker://<pubkey>?relay=<url>&secret=<token>`.
   * Se suscribe mediante WebSockets asíncronos (`asyncio`) a eventos `kind: 24133` en relays Nostr con filtro `{"#p": [<pubkey>]}`.
   * Cifra y descifra mensajes RPC con **NIP-04** (Diffie-Hellman secp256k1 + AES-256-CBC).
   * Implementa los métodos RPC: `connect` (validando el `secret`), `get_public_key`, `sign_event` y `ping`.
3. **Autenticación en Stacker News (NIP-98):**
   * Firma el desafío `k1` generado por la API GraphQL de Stacker News en un evento canónico `kind: 27235`.
   * Entrega scripts reproducibles de inicio de sesión real contra Stacker News.

---

## 2. Instalación y Ejecución

### Requisitos Previos
* **Python 3.11+**
* Recomendado: [`uv`](https://github.com/astral-sh/uv) o `pip` estándar.

### Paso 1: Instalar Dependencias
```bash
# Con uv (rápido):
uv venv --python 3.11
uv pip install -r requirements.txt

# O con venv estándar de Python:
python -m venv .venv
# En Windows: .venv\Scripts\activate
# En Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

### Paso 2: Iniciar la Interfaz Web y el Demonio NIP-46
```bash
python -m src.web_server
```
Abre en tu navegador: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 3. Ejecución de Tests Automatizados

La suite cuenta con **31 pruebas unitarias e integración** sin dependencias externas ni conexión obligatoria a Internet:
* Vectores oficiales de prueba de **BIP-340** (Bitcoin Core).
* Round-trips de codificación Bech32 **NIP-19** (`nsec` y `npub`).
* Serialización canónica e integridad de eventos **NIP-01**.
* Cifrado, descifrado, KDF Scrypt y permisos de archivo `src/storage.py`.

Para ejecutar todos los tests:
```bash
pytest -v
```

---

## 4. Demostración Paso a Paso de Inicio de Sesión

### Opción A: Demostración Rápida por Terminal (100% Real contra Stacker News)
Ejecuta el script reproducible de verificación:
```bash
python demo_login.py
```
**Lo que hace el script en vivo:**
1. Consulta la API GraphQL de Stacker News (`https://stacker.news/api/graphql`) y obtiene un desafío criptográfico `k1` real.
2. Construye el evento HTTP Auth `kind: 27235` con tags `[['challenge', k1], ['u', 'https://stacker.news'], ['method', 'GET']]`.
3. Firma el evento mediante Schnorr BIP-340 con la clave del usuario.
4. Envía el evento firmado a `https://stacker.news/api/auth/callback/nostr`.
5. Recibe respuesta **HTTP 200** y las cookies de sesión oficiales de Stacker News (`__Host-next-auth.csrf-token`).

---

### Opción B: Demostración Gráfica en Stacker News

1. Inicia el servidor local: `python -m src.web_server`.
2. En [http://127.0.0.1:8000](http://127.0.0.1:8000), ingresa una contraseña y haz clic en **"Generar Clave y Guardar Cifrada"**.
3. Abre [https://stacker.news/signup?callbackUrl=https%3A%2F%2Fstacker.news%2F&type=nostr](https://stacker.news/signup?callbackUrl=https%3A%2F%2Fstacker.news%2F&type=nostr).
4. Abre la consola del navegador (**F12** &rarr; pestaña *Console*), pega el contenido de [`browser_bridge.js`](browser_bridge.js) y presiona Enter.
5. Haz clic en el botón de Stacker News: **"Login with browser extension"**.
6. Stacker News solicitará la firma al firmante local en `127.0.0.1:8000` y la página recargará logueada con la identidad generada.

---

## 5. Supuestos de Diseño

* **Interfaz de Usuario:** Se optó por una interfaz minimalista en HTML/CSS/JS plano sin frameworks (React/Vue) servida con `http.server` de la biblioteca estándar de Python y restringida a `127.0.0.1`, evitando vectores de ataque en red local.
* **Cifrado NIP-04 vs NIP-44:** NIP-46 soporta conceptualmente NIP-44 (más moderno con ChaCha20Poly1305), pero Stacker News y su cliente NDK v2 implementan **NIP-04** para el canal remoto. Para asegurar compatibilidad inmediata en la evaluación, se implementó NIP-04.
* **Relay Nostr:** Se utiliza un relay público estándar (`wss://relay.damus.io`), además de proveer una clase `FakeRelay` en memoria para ejecutar tests sin Internet.
* **Política de Autorización:** Para una demo fluida dentro del límite de 3 horas, el firmante valida el `secret` en `connect` y auto-aprueba las firmas solicitadas reportándolas en consola.

---

## 6. Fuera de Alcance (*Non-Goals*)

* **Sin extensión de navegador:** No se empaquetó como extensión de Chrome/Firefox (requerimiento explícito de la prueba).
* **Sin firmante universal:** No se implementan métodos auxiliares de NIP-46 innecesarios para login (`nip04_encrypt`, delegaciones complejas o sub-cuentas).
* **Sin billetera / Lightning Network (NIP-47):** Aunque Stacker News soporta zaps y pagos, la prueba técnica se enfoca exclusivamente en **autenticación remota**.
* **Sin recuperación de semilla BIP-39:** El sistema trabaja directamente con claves privadas en formato `nsec` y hexadecimal.

---

## 7. Limitaciones Conocidas y Riesgos Abiertos

1. **Clave en memoria durante ejecución:**
   * La clave privada se mantiene en texto plano en la memoria del proceso mientras el firmante está activo. Un volcado de memoria (*core dump*) o acceso `root` al sistema operativo podría extraerla. *En producción requeriría un módulo de hardware seguro (HSM / Secure Enclave).*
2. **Entropía de la Passphrase:**
   * Si el usuario elige una contraseña débil, el archivo cifrado con Scrypt sigue siendo susceptible a ataques de fuerza bruta offline.
3. **Semántica de permisos en Windows:**
   * `os.chmod(..., 0o600)` en Windows NTFS solo gestiona el atributo de solo lectura. Para una restricción estricta de ACL en Windows se requeriría configurar descriptores de seguridad Win32 vía API (`icacls`).
4. **Metadatos en el Relay:**
   * Aunque el payload JSON-RPC viaja cifrado con NIP-04, las claves públicas emisora y receptora en los eventos `kind: 24133` son visibles para los operadores de los relays.
