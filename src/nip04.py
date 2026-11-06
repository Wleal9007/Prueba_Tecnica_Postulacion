"""
Módulo de cifrado y descifrado NIP-04 (Diffie-Hellman secp256k1 + AES-256-CBC con IV).
Utilizado para el transporte cifrado de mensajes entre clientes y el firmante remoto NIP-46.
"""

from __future__ import annotations

import base64
import os
from typing import Union

import coincurve
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from src.keys import _normalize_bytes


def compute_shared_x(privkey: Union[str, bytes], pubkey_xonly: Union[str, bytes]) -> bytes:
    """
    Calcula el secreto compartido de 32 bytes para NIP-04 (coordenada X sin hashear del punto compartido).
    Según la especificación NIP-04:
    S = privkey * (02 || pubkey_xonly)
    shared_x = S.X (32 bytes)
    """
    sk_bytes = _normalize_bytes(privkey, 32)
    pk_bytes = _normalize_bytes(pubkey_xonly, 32)

    # Convertir pubkey x-only a clave pública comprimida secp256k1 (prefijo 0x02)
    compressed_pub = b"\x02" + pk_bytes
    pub_point = coincurve.PublicKey(compressed_pub)

    # Multiplicación escalar del punto por el escalar privado
    shared_point = pub_point.multiply(sk_bytes)

    # Formato descomprimido es 0x04 || X (32 bytes) || Y (32 bytes)
    raw_uncompressed = shared_point.format(compressed=False)
    return raw_uncompressed[1:33]


def nip04_encrypt(privkey: Union[str, bytes], target_pubkey: Union[str, bytes], plaintext: str) -> str:
    """
    Cifra un texto plano en UTF-8 utilizando AES-256-CBC con padding PKCS7 y el secreto compartido NIP-04.
    Retorna la cadena en formato canónico: "<base64_ciphertext>?iv=<base64_iv>".
    """
    shared_x = compute_shared_x(privkey, target_pubkey)
    iv = os.urandom(16)

    # Padding PKCS7 a bloques de 16 bytes (128 bits)
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(plaintext.encode("utf-8")) + padder.finalize()

    # Cifrado AES-256-CBC
    cipher = Cipher(algorithms.AES(shared_x), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()

    ct_b64 = base64.b64encode(ciphertext).decode("ascii")
    iv_b64 = base64.b64encode(iv).decode("ascii")
    return f"{ct_b64}?iv={iv_b64}"


def nip04_decrypt(privkey: Union[str, bytes], sender_pubkey: Union[str, bytes], encrypted_content: str) -> str:
    """
    Descifra un contenido NIP-04 en formato "<base64_ciphertext>?iv=<base64_iv>".
    Retorna el texto plano en UTF-8.
    """
    if "?iv=" not in encrypted_content:
        raise ValueError("Formato NIP-04 inválido: falta separador '?iv='")

    ct_b64, iv_b64 = encrypted_content.split("?iv=", 1)
    try:
        ciphertext = base64.b64decode(ct_b64)
        iv = base64.b64decode(iv_b64)
    except Exception as e:
        raise ValueError(f"Error decodificando Base64 en NIP-04: {e}") from e

    if len(iv) != 16:
        raise ValueError(f"Longitud de IV inválida: se esperaban 16 bytes, recibidos {len(iv)}")

    shared_x = compute_shared_x(privkey, sender_pubkey)

    cipher = Cipher(algorithms.AES(shared_x), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    try:
        plaintext_bytes = unpadder.update(padded_plaintext) + unpadder.finalize()
    except Exception as e:
        raise ValueError(f"Fallo en PKCS7 unpadding (posible clave incorrecta): {e}") from e

    return plaintext_bytes.decode("utf-8")
