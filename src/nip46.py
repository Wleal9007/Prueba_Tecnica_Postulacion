"""
Módulo de implementación del protocolo NIP-46 (Nostr Remote Signing).
Maneja parsing/generación de URIs bunker://, validación de sesiones JSON-RPC y firma delegada.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

from src.events import NostrEvent, create_and_sign_event
from src.keys import derive_xonly_pubkey, import_secret_key
from src.nip04 import nip04_decrypt, nip04_encrypt

logger = logging.getLogger("nip46")


@dataclass
class BunkerURI:
    """Representa una URI de conexión de firmante remoto bunker://."""
    signer_pubkey: str
    relays: List[str]
    secret: Optional[str] = None

    def to_uri(self) -> str:
        """Serializa a formato canónico bunker://<signer_pubkey>?relay=<url>&secret=<token>."""
        query_params = []
        for r in self.relays:
            query_params.append(("relay", r))
        if self.secret:
            query_params.append(("secret", self.secret))
        qs = urllib.parse.urlencode(query_params)
        return f"bunker://{self.signer_pubkey}?{qs}"

    @classmethod
    def parse(cls, uri_str: str) -> BunkerURI:
        """Parsea una URI bunker:// validando pubkey y parámetros."""
        cleaned = uri_str.strip()
        if not cleaned.startswith("bunker://"):
            raise ValueError("La URI debe iniciar con el esquema 'bunker://'")

        parsed = urllib.parse.urlparse(cleaned)
        signer_pubkey = parsed.netloc.lower()
        if len(signer_pubkey) != 64:
            raise ValueError(f"Pubkey de firmante inválida en bunker URI: '{signer_pubkey}'")

        params = urllib.parse.parse_qs(parsed.query)
        relays = params.get("relay", [])
        if not relays:
            raise ValueError("La URI bunker debe incluir al menos un parámetro 'relay'")

        secret = params.get("secret", [None])[0]
        return cls(signer_pubkey=signer_pubkey, relays=relays, secret=secret)


class Nip46Signer:
    """
    Firmante remoto NIP-46.
    Custodia la clave privada del usuario, procesa peticiones JSON-RPC kind 24133
    y produce respuestas cifradas según la especificación NIP-46.
    """

    def __init__(
        self,
        user_privkey: Union[str, bytes],
        secret: Optional[str] = None,
        bunker_privkey: Optional[Union[str, bytes]] = None,
    ):
        self.user_privkey_hex = import_secret_key(user_privkey) if isinstance(user_privkey, str) else user_privkey.hex()
        self.user_pubkey_hex = derive_xonly_pubkey(self.user_privkey_hex)

        # Si no se especifica una clave independiente para el bunker, se usa la del usuario
        if bunker_privkey:
            self.bunker_privkey_hex = import_secret_key(bunker_privkey) if isinstance(bunker_privkey, str) else bunker_privkey.hex()
        else:
            self.bunker_privkey_hex = self.user_privkey_hex
        self.bunker_pubkey_hex = derive_xonly_pubkey(self.bunker_privkey_hex)

        self.expected_secret = secret
        self.authorized_clients: Set[str] = set()

    def create_bunker_uri(self, relays: List[str]) -> str:
        """Genera la URI bunker:// para compartir con clientes como Stacker News."""
        bunker = BunkerURI(
            signer_pubkey=self.bunker_pubkey_hex,
            relays=relays,
            secret=self.expected_secret,
        )
        return bunker.to_uri()

    def handle_rpc_request(self, client_pubkey: str, rpc_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Procesa una solicitud JSON-RPC NIP-46 y devuelve el diccionario de respuesta:
        {"id": req_id, "result": ..., "error": ...}
        """
        req_id = rpc_dict.get("id", "")
        method = rpc_dict.get("method", "")
        params = rpc_dict.get("params", [])

        logger.info("NIP-46 Request de %s: id=%s method=%s", client_pubkey[:8], req_id, method)

        if not req_id:
            return {"id": "", "result": None, "error": "Missing 'id' in request"}

        # Método: connect
        if method == "connect":
            client_provided_pubkey = params[0] if len(params) > 0 else client_pubkey
            client_secret = params[1] if len(params) > 1 else None

            if self.expected_secret and client_secret != self.expected_secret:
                logger.warning("Conexión rechazada: secret inválido desde %s", client_pubkey[:8])
                return {"id": req_id, "result": None, "error": "Invalid secret"}

            self.authorized_clients.add(client_pubkey.lower())
            logger.info("Cliente autorizado: %s", client_pubkey[:8])
            return {"id": req_id, "result": "ack", "error": None}

        # Método: ping
        if method == "ping":
            return {"id": req_id, "result": "pong", "error": None}

        # Método: get_public_key
        if method == "get_public_key":
            return {"id": req_id, "result": self.user_pubkey_hex, "error": None}

        # Método: sign_event
        if method == "sign_event":
            if not params:
                return {"id": req_id, "result": None, "error": "Missing event parameter for sign_event"}

            raw_event_param = params[0]
            try:
                if isinstance(raw_event_param, str):
                    event_data = json.loads(raw_event_param)
                elif isinstance(raw_event_param, dict):
                    event_data = raw_event_param
                else:
                    return {"id": req_id, "result": None, "error": "Invalid event data format"}

                # Crear evento y asignarle la clave pública del usuario
                event = NostrEvent(
                    pubkey=self.user_pubkey_hex,
                    created_at=int(event_data.get("created_at", 0)),
                    kind=int(event_data.get("kind", 0)),
                    tags=event_data.get("tags", []),
                    content=str(event_data.get("content", "")),
                )
                event.sign(self.user_privkey_hex)
                signed_event_json = json.dumps(event.to_dict(), separators=(",", ":"), ensure_ascii=False)
                logger.info("Evento firmado exitosamente: kind=%d id=%s", event.kind, event.id[:8])
                return {"id": req_id, "result": signed_event_json, "error": None}
            except Exception as e:
                logger.error("Error al firmar evento NIP-46: %s", e)
                return {"id": req_id, "result": None, "error": f"Failed to sign event: {e}"}

        return {"id": req_id, "result": None, "error": f"Unsupported method: '{method}'"}

    def process_incoming_event(self, incoming_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Descifra una petición kind 24133 entrante, procesa el JSON-RPC y construye
        el evento de respuesta firmado y cifrado listo para publicar en el relay.
        """
        kind = incoming_event.get("kind")
        if kind != 24133:
            return None

        client_pubkey = incoming_event.get("pubkey", "").lower()
        encrypted_content = incoming_event.get("content", "")

        try:
            decrypted_plaintext = nip04_decrypt(
                privkey=self.bunker_privkey_hex,
                sender_pubkey=client_pubkey,
                encrypted_content=encrypted_content,
            )
            rpc_request = json.loads(decrypted_plaintext)
        except Exception as e:
            logger.error("No se pudo descifrar o parsear mensaje entrante de %s: %s", client_pubkey[:8], e)
            return None

        # Procesar RPC
        rpc_response = self.handle_rpc_request(client_pubkey, rpc_request)
        response_json = json.dumps(rpc_response, separators=(",", ":"), ensure_ascii=False)

        # Cifrar respuesta para el cliente
        encrypted_response = nip04_encrypt(
            privkey=self.bunker_privkey_hex,
            target_pubkey=client_pubkey,
            plaintext=response_json,
        )

        # Construir evento de respuesta kind 24133
        response_event = create_and_sign_event(
            privkey=self.bunker_privkey_hex,
            kind=24133,
            content=encrypted_response,
            tags=[["p", client_pubkey]],
        )
        return response_event
