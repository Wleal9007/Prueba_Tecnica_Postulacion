"""
Módulo de serialización canónica, cálculo de ID, firma y verificación de eventos Nostr (NIP-01).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from src.keys import derive_xonly_pubkey, import_secret_key, sign_schnorr, verify_schnorr


def serialize_event_payload(
    pubkey: str,
    created_at: int,
    kind: int,
    tags: List[List[str]],
    content: str,
) -> str:
    """
    Serializa los campos del evento en el formato canónico JSON UTF-8 según NIP-01:
    [0, <pubkey-hex>, <created_at-int>, <kind-int>, <tags-array>, <content-str>]
    utilizando separadores compactos sin espacios (separators=(',', ':')) y ensure_ascii=False.
    """
    payload = [
        0,
        pubkey.lower().strip(),
        int(created_at),
        int(kind),
        tags,
        str(content),
    ]
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def compute_event_id(
    pubkey: str,
    created_at: int,
    kind: int,
    tags: List[List[str]],
    content: str,
) -> str:
    """Calcula el hash SHA-256 en formato hex de 64 caracteres de la serialización canónica NIP-01."""
    serialized = serialize_event_payload(pubkey, created_at, kind, tags, content)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


@dataclass
class NostrEvent:
    """Representación de un evento Nostr según NIP-01."""
    pubkey: str
    created_at: int
    kind: int
    tags: List[List[str]] = field(default_factory=list)
    content: str = ""
    id: str = ""
    sig: str = ""

    def serialize(self) -> str:
        """Retorna la cadena JSON canónica NIP-01 para el cálculo del ID."""
        return serialize_event_payload(
            pubkey=self.pubkey,
            created_at=self.created_at,
            kind=self.kind,
            tags=self.tags,
            content=self.content,
        )

    def calculate_id(self) -> str:
        """Calcula el ID SHA-256 canónico del evento."""
        return compute_event_id(
            pubkey=self.pubkey,
            created_at=self.created_at,
            kind=self.kind,
            tags=self.tags,
            content=self.content,
        )

    def sign(self, privkey: Union[str, bytes]) -> NostrEvent:
        """Calcula el ID canónico y genera la firma Schnorr BIP-340 con la clave privada proporcionada."""
        sk_hex = import_secret_key(privkey) if isinstance(privkey, str) else privkey.hex()
        derived_pk = derive_xonly_pubkey(sk_hex)

        if self.pubkey and self.pubkey.lower() != derived_pk.lower():
            raise ValueError(
                f"La clave pública del evento ({self.pubkey}) no coincide con la derivada de la privada ({derived_pk})"
            )
        self.pubkey = derived_pk.lower()

        # Calcula ID
        self.id = self.calculate_id()

        # Firma Schnorr sobre los 32 bytes del ID (digest sha256)
        id_bytes = bytes.fromhex(self.id)
        sig_bytes = sign_schnorr(id_bytes, sk_hex)
        self.sig = sig_bytes.hex().lower()
        return self

    def verify(self) -> bool:
        """Verifica la integridad del ID y la validez de la firma Schnorr BIP-340."""
        valid, _ = self.verify_with_reason()
        return valid

    def verify_with_reason(self) -> Tuple[bool, str]:
        """Verifica el evento y retorna una tupla (valido, motivo_del_fallo)."""
        if not self.id:
            return False, "Evento sin campo 'id'"
        if not self.sig:
            return False, "Evento sin campo 'sig'"
        if not self.pubkey:
            return False, "Evento sin campo 'pubkey'"

        # 1. Verificar coincidencia del ID canónico
        expected_id = self.calculate_id()
        if self.id.lower() != expected_id.lower():
            return False, f"ID de evento inválido: esperado {expected_id}, recibido {self.id}"

        # 2. Verificar firma Schnorr de 64 bytes
        try:
            id_bytes = bytes.fromhex(self.id)
            sig_bytes = bytes.fromhex(self.sig)
            if len(sig_bytes) != 64:
                return False, f"Firma inválida: se esperaban 64 bytes, obtenidos {len(sig_bytes)}"

            is_valid_sig = verify_schnorr(sig_bytes, id_bytes, self.pubkey)
            if not is_valid_sig:
                return False, "Firma Schnorr BIP-340 inválida para la pubkey e id proporcionados"
        except Exception as e:
            return False, f"Error durante la verificación criptográfica: {e}"

        return True, "OK"

    def to_dict(self) -> Dict[str, Any]:
        """Serializa el evento completo como diccionario para transmisión por relay."""
        return {
            "id": self.id,
            "pubkey": self.pubkey,
            "created_at": self.created_at,
            "kind": self.kind,
            "tags": self.tags,
            "content": self.content,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NostrEvent:
        """Instancia un NostrEvent a partir de un diccionario."""
        return cls(
            id=data.get("id", ""),
            pubkey=data.get("pubkey", ""),
            created_at=int(data.get("created_at", 0)),
            kind=int(data.get("kind", 0)),
            tags=data.get("tags", []),
            content=data.get("content", ""),
            sig=data.get("sig", ""),
        )


def create_and_sign_event(
    privkey: Union[str, bytes],
    kind: int,
    content: str = "",
    tags: Optional[List[List[str]]] = None,
    created_at: Optional[int] = None,
) -> Dict[str, Any]:
    """Crea, sella con ID canónico y firma un evento Nostr retornándolo como diccionario."""
    if tags is None:
        tags = []
    if created_at is None:
        created_at = int(time.time())

    event = NostrEvent(
        pubkey="",
        created_at=created_at,
        kind=kind,
        tags=tags,
        content=content,
    )
    event.sign(privkey)
    return event.to_dict()


def verify_event(event_data: Union[Dict[str, Any], NostrEvent]) -> bool:
    """Verifica si un evento en forma de diccionario u objeto NostrEvent es legítimo."""
    if isinstance(event_data, dict):
        event = NostrEvent.from_dict(event_data)
    else:
        event = event_data
    return event.verify()
