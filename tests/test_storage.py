"""
Tests unitarios para el módulo de almacenamiento seguro cifrado en reposo (src/storage.py).
"""

import os
import stat
import pytest
from src.keys import KeyPair
from src.storage import (
    decrypt_nsec,
    encrypt_nsec,
    load_encrypted_key,
    mask_secret,
    save_encrypted_key,
)


def test_encrypt_decrypt_roundtrip():
    """Verifica que una clave se cifre con Scrypt+AES-GCM y se recupere intacta con la misma passphrase."""
    pair = KeyPair.generate()
    passphrase = "password_muy_segura_123!"

    payload = encrypt_nsec(pair.secret_key_hex, passphrase)
    assert payload["version"] == 1
    assert payload["kdf"]["name"] == "scrypt"
    assert payload["cipher"]["name"] == "aes-256-gcm"

    # La clave en texto plano NO debe estar en ningún campo del payload
    serialized = str(payload)
    assert pair.secret_key_hex not in serialized
    assert pair.nsec not in serialized

    recovered_hex = decrypt_nsec(payload, passphrase)
    assert recovered_hex == pair.secret_key_hex


def test_encrypt_nsec_format_import():
    """Verifica que acepte tanto formato nsec1... como hex para cifrar."""
    pair = KeyPair.generate()
    passphrase = "clave_de_paso"

    payload = encrypt_nsec(pair.nsec, passphrase)
    recovered = decrypt_nsec(payload, passphrase)
    assert recovered == pair.secret_key_hex


def test_wrong_passphrase_fails():
    """Una passphrase incorrecta debe provocar un error de autenticación criptográfica."""
    pair = KeyPair.generate()
    payload = encrypt_nsec(pair.secret_key_hex, "passphrase_correcta")

    with pytest.raises(ValueError, match="Passphrase incorrecta o archivo de clave manipulado"):
        decrypt_nsec(payload, "passphrase_equivocada")


def test_tampered_ciphertext_fails():
    """Cualquier alteración en el ciphertext debe ser detectada por el tag GCM."""
    pair = KeyPair.generate()
    payload = encrypt_nsec(pair.secret_key_hex, "password123")

    # Modificar el primer caracter hex del ciphertext
    ct = payload["cipher"]["ciphertext"]
    corrupted_ct = ("0" if ct[0] != "0" else "1") + ct[1:]
    payload["cipher"]["ciphertext"] = corrupted_ct

    with pytest.raises(ValueError, match="Passphrase incorrecta o archivo de clave manipulado"):
        decrypt_nsec(payload, "password123")


def test_save_and_load_file_with_permissions(tmp_path):
    """Guarda en un archivo temporal fuera del repo y valida lectura y permisos 0600."""
    pair = KeyPair.generate()
    passphrase = "mi_secreto_super_protegido"
    file_path = tmp_path / "subdir" / "test_signer.enc"

    payload = encrypt_nsec(pair.secret_key_hex, passphrase)
    saved_path = save_encrypted_key(payload, file_path)

    assert saved_path.exists()

    # Comprobar permisos en sistemas tipo POSIX (en Windows st_mode refleja solo lectura/escritura del usuario)
    mode = os.stat(saved_path).st_mode
    assert mode & stat.S_IRUSR
    assert mode & stat.S_IWUSR

    loaded_hex = load_encrypted_key(passphrase, saved_path)
    assert loaded_hex == pair.secret_key_hex


def test_mask_secret():
    """Valida que la función de ofuscación oculte la clave y no imprima secretos completos."""
    sample_nsec = "nsec1vl029mgpspedva04g90vltkh6fvh240eqtv9xxl2xme3qlsvnxqq2rs460"
    masked = mask_secret(sample_nsec)
    assert masked.startswith("nsec")
    assert masked.endswith("460")
    assert "..." in masked
    assert sample_nsec[5:20] not in masked

    assert mask_secret("") == "<vacío>"
    assert mask_secret("12345") == "****"
