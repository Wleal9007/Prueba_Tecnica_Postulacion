"""
Módulo de manejo de claves criptográficas para Nostr (NIP-01, NIP-19, BIP-340).
Utiliza libsecp256k1 (vía coincurve) para derivación y firmas Schnorr BIP-340.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Tuple, Union

import bech32
import coincurve


HEX_64_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")


def _normalize_bytes(key: Union[str, bytes], length: int = 32) -> bytes:
    """Convierte una clave en hex o bytes a bytes de longitud fija."""
    if isinstance(key, str):
        key = key.strip()
        if not HEX_64_REGEX.match(key):
            raise ValueError(f"La clave debe ser una cadena hexadecimal de {length * 2} caracteres")
        raw = bytes.fromhex(key)
    elif isinstance(key, (bytes, bytearray)):
        raw = bytes(key)
    else:
        raise TypeError("La clave debe ser de tipo str o bytes")

    if len(raw) != length:
        raise ValueError(f"Longitud inválida: esperados {length} bytes, recibidos {len(raw)}")
    return raw


def generate_secret_key() -> str:
    """Genera una nueva clave secreta válida en secp256k1 en formato hex de 64 caracteres."""
    priv = coincurve.PrivateKey()
    return priv.secret.hex().lower()


def derive_xonly_pubkey(privkey: Union[str, bytes]) -> str:
    """
    Deriva la clave pública x-only (32 bytes) según BIP-340 a partir de una clave secreta.
    Retorna la clave pública en formato hex lowercase.
    """
    sk_bytes = _normalize_bytes(privkey, 32)
    # Valida que sea una clave privada válida en secp256k1
    try:
        xonly = coincurve.PublicKeyXOnly.from_secret(sk_bytes)
        return xonly.format().hex().lower()
    except Exception as e:
        raise ValueError(f"Error al derivar clave pública x-only: {e}") from e


def hex_to_bech32(hrp: str, hex_data: str) -> str:
    """Codifica datos hexadecimales a Bech32 (NIP-19)."""
    raw_bytes = _normalize_bytes(hex_data, 32)
    five_bit = bech32.convertbits(raw_bytes, 8, 5, True)
    if five_bit is None:
        raise ValueError("Error convirtiendo bits a base 32")
    encoded = bech32.bech32_encode(hrp, five_bit)
    if encoded is None:
        raise ValueError("Error al codificar Bech32")
    return encoded


def bech32_to_hex(expected_hrp: str, bech_str: str) -> str:
    """Decodifica un string Bech32 (NIP-19) y valida el prefijo HRP."""
    if not isinstance(bech_str, str):
        raise TypeError("El valor bech32 debe ser una cadena de texto")

    bech_str = bech_str.strip()
    hrp, data = bech32.bech32_decode(bech_str)
    if hrp is None or data is None:
        raise ValueError("Cadena Bech32 inválida o checksum incorrecto")
    if hrp != expected_hrp:
        raise ValueError(f"HRP incorrecto: esperado '{expected_hrp}', obtenido '{hrp}'")

    raw_bytes_list = bech32.convertbits(data, 5, 8, False)
    if raw_bytes_list is None:
        raise ValueError("Error decodificando bits de Bech32")

    raw_bytes = bytes(raw_bytes_list)
    if len(raw_bytes) != 32:
        raise ValueError(f"Longitud de datos incorrecta: esperados 32 bytes, obtenidos {len(raw_bytes)}")

    return raw_bytes.hex().lower()


def hex_to_nsec(hex_privkey: str) -> str:
    """Convierte clave privada hex a formato nsec1... (NIP-19)."""
    return hex_to_bech32("nsec", hex_privkey)


def nsec_to_hex(nsec_str: str) -> str:
    """Convierte formato nsec1... a clave privada hex (64 caracteres)."""
    return bech32_to_hex("nsec", nsec_str)


def hex_to_npub(hex_pubkey: str) -> str:
    """Convierte clave pública x-only hex a formato npub1... (NIP-19)."""
    return hex_to_bech32("npub", hex_pubkey)


def npub_to_hex(npub_str: str) -> str:
    """Convierte formato npub1... a clave pública hex (64 caracteres)."""
    return bech32_to_hex("npub", npub_str)


def import_secret_key(key_input: str) -> str:
    """
    Importa y valida una clave secreta ingresada por el usuario.
    Acepta formato nsec (Bech32) o hexadecimal (64 caracteres).
    Retorna la clave en formato hexadecimal lowercase.
    """
    if not isinstance(key_input, str):
        raise TypeError("La clave debe ser una cadena de texto")

    cleaned = key_input.strip()
    if cleaned.startswith("nsec1"):
        hex_key = nsec_to_hex(cleaned)
    elif HEX_64_REGEX.match(cleaned):
        hex_key = cleaned.lower()
    else:
        raise ValueError("Formato de clave inválido. Debe ser 'nsec1...' o un hex de 64 caracteres.")

    # Validar que la clave sea un escalar válido dentro del orden del grupo secp256k1
    sk_bytes = bytes.fromhex(hex_key)
    try:
        coincurve.PrivateKey(sk_bytes)
    except Exception as e:
        raise ValueError(f"Clave secreta fuera de rango secp256k1: {e}") from e

    return hex_key


def sign_schnorr(message: bytes, privkey: Union[str, bytes], aux_randomness: bytes | None = None) -> bytes:
    """
    Firma un mensaje de 32 bytes (o hash SHA-256) según BIP-340 Schnorr.
    Retorna una firma de 64 bytes.
    """
    sk_bytes = _normalize_bytes(privkey, 32)
    if len(message) != 32:
        raise ValueError(f"El mensaje debe tener exactamente 32 bytes (típicamente sha256), recibidos {len(message)}")

    pk = coincurve.PrivateKey(sk_bytes)
    aux = aux_randomness if aux_randomness is not None else os.urandom(32)
    return pk.sign_schnorr(message, aux_randomness=aux)


def verify_schnorr(signature: bytes, message: bytes, pubkey_xonly: Union[str, bytes]) -> bool:
    """
    Verifica una firma Schnorr BIP-340 de 64 bytes para un mensaje de 32 bytes y una clave pública x-only.
    """
    if len(signature) != 64:
        return False
    if len(message) != 32:
        return False

    try:
        pk_bytes = _normalize_bytes(pubkey_xonly, 32)
        xonly = coincurve.PublicKeyXOnly(pk_bytes)
        return bool(xonly.verify(signature, message))
    except Exception:
        return False


@dataclass(frozen=True)
class KeyPair:
    """Representa un par de claves Nostr con todas sus representaciones canónicas."""
    secret_key_hex: str
    public_key_hex: str
    nsec: str
    npub: str

    @classmethod
    def generate(cls) -> "KeyPair":
        """Genera un nuevo par de claves aleatorio."""
        sk_hex = generate_secret_key()
        pk_hex = derive_xonly_pubkey(sk_hex)
        return cls(
            secret_key_hex=sk_hex,
            public_key_hex=pk_hex,
            nsec=hex_to_nsec(sk_hex),
            npub=hex_to_npub(pk_hex),
        )

    @classmethod
    def from_secret(cls, key_input: str) -> "KeyPair":
        """Crea el par de claves importando desde nsec o hex."""
        sk_hex = import_secret_key(key_input)
        pk_hex = derive_xonly_pubkey(sk_hex)
        return cls(
            secret_key_hex=sk_hex,
            public_key_hex=pk_hex,
            nsec=hex_to_nsec(sk_hex),
            npub=hex_to_npub(pk_hex),
        )
