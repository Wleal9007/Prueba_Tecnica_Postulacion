"""
Servidor HTTP local utilizando exclusivamente la biblioteca estándar de Python (http.server).
Restringido estrictamente a 127.0.0.1 para máxima seguridad.
Sirve la interfaz web estática y expone la API JSON para gestión de claves y conexión NIP-46.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import secrets
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.keys import KeyPair, derive_xonly_pubkey, hex_to_npub, import_secret_key
from src.nip46 import Nip46Signer
from src.relay import RelayClient
from src.storage import (
    encrypt_nsec,
    get_default_storage_path,
    load_encrypted_key,
    mask_secret,
    save_encrypted_key,
)

logger = logging.getLogger("web_server")
STATIC_DIR = Path(__file__).parent.parent / "static"
DEFAULT_RELAY = "wss://relay.damus.io"


class SignerManager:
    """Gestiona el estado del firmante, la custodia en memoria y el loop del relay."""

    def __init__(self):
        self.signer: Optional[Nip46Signer] = None
        self.bunker_uri: str = ""
        self.secret_token: str = ""
        self.relay_url: str = DEFAULT_RELAY
        self.logs: List[str] = []
        self._lock = threading.Lock()
        self._relay_client: Optional[RelayClient] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker_thread: Optional[threading.Thread] = None

    def log(self, message: str) -> None:
        with self._lock:
            self.logs.append(message)
            if len(self.logs) > 100:
                self.logs.pop(0)
        logger.info(message)

    def set_key(self, secret_hex: str) -> Dict[str, Any]:
        """Inicializa o actualiza la clave en el firmante y arranca el relay."""
        with self._lock:
            self.secret_token = secrets.token_hex(16)
            self.signer = Nip46Signer(user_privkey=secret_hex, secret=self.secret_token)
            self.bunker_uri = self.signer.create_bunker_uri([self.relay_url])
            self.log(f"Firmante activo para npub: {hex_to_npub(self.signer.user_pubkey_hex)[:16]}...")
            self.log(f"Bunker URI: {self.bunker_uri}")

        self._start_relay_worker()

        return {
            "pubkey": self.signer.user_pubkey_hex,
            "npub": hex_to_npub(self.signer.user_pubkey_hex),
            "bunker_uri": self.bunker_uri,
            "is_active": True,
        }

    def _start_relay_worker(self) -> None:
        """Arranca el hilo de asyncio para escuchar eventos en el relay."""
        if self._worker_thread and self._worker_thread.is_alive():
            return

        def run_loop():
            self._async_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._async_loop)

            async def main_async():
                async def on_event_cb(event_dict):
                    if not self.signer:
                        return
                    self.log(f"Evento NIP-46 entrante de: {event_dict.get('pubkey', '')[:10]}...")
                    resp_event = self.signer.process_incoming_event(event_dict)
                    if resp_event and self._relay_client:
                        await self._relay_client.publish(resp_event)
                        self.log("Respuesta NIP-46 enviada al relay exitosamente.")

                self._relay_client = RelayClient(self.relay_url, on_event=on_event_cb)
                await self._relay_client.start()
                await self._relay_client.subscribe(
                    "sub-bunker",
                    {"kinds": [24133], "#p": [self.signer.bunker_pubkey_hex]},
                )
                self.log(f"Suscrito a eventos kind 24133 en {self.relay_url}")

                # Mantener vivo
                while True:
                    await asyncio.sleep(1)

            try:
                self._async_loop.run_until_complete(main_async())
            except Exception as e:
                self.log(f"Error en relay worker: {e}")

        self._worker_thread = threading.Thread(target=run_loop, daemon=True, name="nip46-relay-thread")
        self._worker_thread.start()


manager = SignerManager()


class LocalHTTPRequestHandler(BaseHTTPRequestHandler):
    """Handler HTTP de la biblioteca estándar restringido a localhost."""

    def log_message(self, format: str, *args: Any) -> None:
        # Silenciar logs ruidosos de peticiones estáticas para mantener limpia la consola
        pass

    def _send_json(self, status: int, data: Any) -> None:
        response_bytes = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8000")
        self.end_headers()
        self.wfile.write(response_bytes)

    def _send_error_json(self, status: int, error_msg: str) -> None:
        self._send_json(status, {"error": error_msg})

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        # API: Obtener estado del bunker
        if path == "/api/bunker":
            if manager.signer:
                data = {
                    "pubkey": manager.signer.user_pubkey_hex,
                    "npub": hex_to_npub(manager.signer.user_pubkey_hex),
                    "bunker_uri": manager.bunker_uri,
                    "is_active": True,
                }
            else:
                data = {
                    "pubkey": "",
                    "npub": "",
                    "bunker_uri": "",
                    "is_active": False,
                }
            self._send_json(200, data)
            return

        # API: Logs de actividad
        if path == "/api/logs":
            self._send_json(200, manager.logs)
            return

        # Servir archivos estáticos
        if path == "/":
            file_path = STATIC_DIR / "index.html"
        else:
            file_path = STATIC_DIR / path.lstrip("/")

        if file_path.exists() and file_path.is_file():
            mime_type, _ = mimetypes.guess_type(str(file_path))
            content = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type or "text/plain")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len)

        try:
            body = json.loads(post_body.decode("utf-8")) if post_body else {}
        except Exception:
            self._send_error_json(400, "Cuerpo JSON inválido")
            return

        path = self.path.split("?")[0]

        # 1. Crear clave nueva
        if path == "/api/key/generate":
            passphrase = body.get("passphrase")
            if not passphrase:
                self._send_error_json(400, "Passphrase requerida")
                return

            pair = KeyPair.generate()
            encrypted_payload = encrypt_nsec(pair.secret_key_hex, passphrase)
            saved_path = save_encrypted_key(encrypted_payload)

            res = manager.set_key(pair.secret_key_hex)
            manager.log(f"Clave generada y guardada cifrada en {saved_path}")
            self._send_json(200, res)
            return

        # 2. Importar clave existente
        if path == "/api/key/import":
            nsec_or_hex = body.get("nsec_or_hex")
            passphrase = body.get("passphrase")
            if not nsec_or_hex or not passphrase:
                self._send_error_json(400, "Clave y passphrase son requeridas")
                return

            try:
                clean_hex = import_secret_key(nsec_or_hex)
                encrypted_payload = encrypt_nsec(clean_hex, passphrase)
                saved_path = save_encrypted_key(encrypted_payload)
                res = manager.set_key(clean_hex)
                manager.log(f"Clave importada y guardada cifrada en {saved_path}")
                self._send_json(200, res)
            except Exception as e:
                self._send_error_json(400, f"Error al importar clave: {e}")
            return

        # 3. Desbloquear clave existente
        if path == "/api/key/unlock":
            passphrase = body.get("passphrase")
            if not passphrase:
                self._send_error_json(400, "Passphrase requerida")
                return

            try:
                secret_hex = load_encrypted_key(passphrase)
                res = manager.set_key(secret_hex)
                manager.log("Firmante desbloqueado con éxito.")
                self._send_json(200, res)
            except Exception as e:
                self._send_error_json(401, f"Error al desbloquear: {e}")
            return

        # 4. Firma local para bridges de navegador (window.nostr)
        if path == "/api/sign":
            if not manager.signer:
                self._send_error_json(403, "Firmante no desbloqueado")
                return
            event_data = body.get("event")
            if not event_data:
                self._send_error_json(400, "Falta evento a firmar")
                return

            try:
                rpc_resp = manager.signer.handle_rpc_request("local-bridge", {
                    "id": "local-req",
                    "method": "sign_event",
                    "params": [event_data]
                })
                if rpc_resp.get("error"):
                    self._send_error_json(400, rpc_resp["error"])
                else:
                    signed_event = json.loads(rpc_resp["result"])
                    self._send_json(200, signed_event)
            except Exception as e:
                self._send_error_json(500, f"Error interno al firmar: {e}")
            return

        self._send_error_json(404, "Ruta no encontrada")


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Ejecuta el servidor HTTP en el host y puerto especificados (estrictamente 127.0.0.1)."""
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, LocalHTTPRequestHandler)
    print(f"\n=======================================================")
    print(f"  Firmante Nostr NIP-46 iniciado en: http://{host}:{port}")
    print(f"  Restringido estrictamente a localhost (127.0.0.1)")
    print(f"=======================================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo servidor...")
        httpd.shutdown()


if __name__ == "__main__":
    run_server()
