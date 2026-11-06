"""
Módulo de almacenamiento seguro de la clave secreta (nsec) cifrada en reposo.
Implementa KDF scrypt para derivación de claves a partir de una passphrase,
cifrado autenticado AES-GCM (256 bits), ubicación por defecto fuera del proyecto
en el directorio personal del usuario ($HOME/.nostr/signer.enc) y permisos de archivo 0600.
Nunca expone la clave secreta en texto plano ni en logs.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from src.keys import import_secret_key

logger = logging.getLogger("storage")

# Parámetros recomendados de Scrypt (N=16384, r=8, p=1, length=32)
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN = 32


def get_default_storage_path() -> Path:
    """Retorna la ruta por defecto fuera del proyecto: $HOME/.nostr/signer.enc."""
    env_path = os.environ.get("NOSTR_KEY_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path.home() / ".nostr" / "signer.enc"


def mask_secret(secret: str) -> str:
    """Ofusca una clave o secreto para que nunca se imprima en texto plano en logs."""
    if not secret:
        return "<vacío>"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}...{secret[-4:]}"


def derive_key_scrypt(passphrase: str, salt: bytes) -> bytes:
    """Deriva una clave simétrica de 256 bits mediante Scrypt a partir de una passphrase."""
    if not passphrase:
        raise ValueError("La passphrase no puede estar vacía")
    kdf = Scrypt(
        salt=salt,
        length=KEY_LEN,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_nsec(sec_key: str, passphrase: str) -> Dict[str, Any]:
    """
    Cifra una clave secreta (nsec o hex) con AES-256-GCM y Scrypt.
    Retorna un diccionario estructurado listo para serializar en disco.
    """
    # Normalizar a hex de 64 caracteres
    clean_hex = import_secret_key(sec_key)
    salt = os.urandom(16)
    nonce = os.urandom(12)  # Nonce estándar para AES-GCM

    key = derive_key_scrypt(passphrase, salt)
    aesgcm = AESGCM(key)

    # AESGCM.encrypt genera ciphertext + tag de autenticación de 16 bytes al final
    ciphertext_with_tag = aesgcm.encrypt(nonce, clean_hex.encode("utf-8"), None)

    return {
        "version": 1,
        "kdf": {
            "name": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": salt.hex(),
        },
        "cipher": {
            "name": "aes-256-gcm",
            "nonce": nonce.hex(),
            "ciphertext": ciphertext_with_tag.hex(),
        },
    }


def decrypt_nsec(payload: Dict[str, Any], passphrase: str) -> str:
    """
    Descifra un payload generado por encrypt_nsec utilizando la passphrase.
    Valida la autenticidad con el tag GCM y retorna la clave hex.
    """
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Estructura de archivo de clave cifrada inválida o versión incompatible")

    kdf_info = payload.get("kdf", {})
    cipher_info = payload.get("cipher", {})

    salt = bytes.fromhex(kdf_info["salt"])
    nonce = bytes.fromhex(cipher_info["nonce"])
    ciphertext_with_tag = bytes.fromhex(cipher_info["ciphertext"])

    kdf = Scrypt(
        salt=salt,
        length=KEY_LEN,
        n=kdf_info.get("n", SCRYPT_N),
        r=kdf_info.get("r", SCRYPT_R),
        p=kdf_info.get("p", SCRYPT_P),
    )
    key = kdf.derive(passphrase.encode("utf-8"))

    aesgcm = AESGCM(key)
    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except Exception as e:
        raise ValueError("Passphrase incorrecta o archivo de clave manipulado (fallo de autenticación)") from e

    return decrypted_bytes.decode("utf-8")


def set_secure_file_permissions(path: Path) -> None:
    """Aplica permisos 0600 (lectura y escritura solo para el dueño)."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception as e:
        logger.warning("No se pudo aplicar chmod 0600 en %s: %e", path, e)


def save_encrypted_key(payload: Dict[str, Any], path: Optional[Union[str, Path]] = None) -> Path:
    """
    Guarda el payload cifrado en disco aplicando permisos restrictivos (0600)
    y asegurando que los directorios padres existan.
    """
    target_path = Path(path).resolve() if path else get_default_storage_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Escribir con permisos restrictivos
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(target_path, flags, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    set_secure_file_permissions(target_path)
    logger.info("Clave cifrada guardada en %s con permisos 0600", target_path)
    return target_path


def load_encrypted_key(passphrase: str, path: Optional[Union[str, Path]] = None) -> str:
    """Lee el archivo cifrado del disco y descifra la clave secreta con la passphrase."""
    target_path = Path(path).resolve() if path else get_default_storage_path()
    if not target_path.exists():
        raise FileNotFoundError(f"No existe el archivo de clave cifrada en {target_path}")

    with open(target_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    return decrypt_nsec(payload, passphrase)
