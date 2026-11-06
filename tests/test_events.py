"""
Tests unitarios para la serialización canónica NIP-01, cálculo de ID, firma y verificación de eventos Nostr.
"""

import hashlib
import json
import pytest
from src.events import NostrEvent, compute_event_id, create_and_sign_event, serialize_event_payload, verify_event
from src.keys import KeyPair


def test_nip01_canonical_serialization_and_id():
    """Verifica el formato canónico JSON estricto sin espacios según NIP-01."""
    pubkey = "3bf0c63fcb93463407af97b5e5ee64fa883d107ef9e558472c4eb9aaaefa459d"
    created_at = 1672531199
    kind = 1
    tags = [["p", "dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659"]]
    content = "Hola Nostr!"

    serialized = serialize_event_payload(pubkey, created_at, kind, tags, content)

    # El formato debe ser un arreglo JSON con exactamente 6 elementos y sin espacios tras comas o dos puntos
    expected_json = (
        f'[0,"{pubkey}",{created_at},{kind},'
        f'[["p","dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659"]],'
        f'"{content}"]'
    )
    assert serialized == expected_json

    expected_sha256 = hashlib.sha256(expected_json.encode("utf-8")).hexdigest().lower()
    computed_id = compute_event_id(pubkey, created_at, kind, tags, content)
    assert computed_id == expected_sha256


def test_sign_and_verify_valid_event():
    """Flujo exitoso: crear, firmar y verificar un evento Nostr válido."""
    pair = KeyPair.generate()
    event_dict = create_and_sign_event(
        privkey=pair.secret_key_hex,
        kind=1,
        content="Prueba de mensaje legítimo",
        tags=[["t", "nostr"]],
        created_at=1700000000,
    )

    assert event_dict["pubkey"] == pair.public_key_hex
    assert len(event_dict["id"]) == 64
    assert len(event_dict["sig"]) == 128  # 64 bytes hex
    assert verify_event(event_dict) is True


def test_verify_event_nip98_auth_structure():
    """Verifica un evento kind 27235 (autenticación HTTP NIP-98 como el usado por Stacker News)."""
    pair = KeyPair.generate()
    challenge = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    tags = [
        ["challenge", challenge],
        ["u", "https://stacker.news"],
        ["method", "GET"],
    ]

    event_dict = create_and_sign_event(
        privkey=pair.secret_key_hex,
        kind=27235,
        content="Stacker News Authentication",
        tags=tags,
    )

    event = NostrEvent.from_dict(event_dict)
    valid, reason = event.verify_with_reason()
    assert valid is True
    assert reason == "OK"


# ==============================================================================
# Casos que DEBEN fallar
# ==============================================================================

def test_tampered_id_fails_verification():
    """Un evento cuyo ID haya sido alterado debe ser rechazado."""
    pair = KeyPair.generate()
    event_dict = create_and_sign_event(
        privkey=pair.secret_key_hex,
        kind=1,
        content="Mensaje original",
    )

    # Alterar el primer caracter del ID
    original_id = event_dict["id"]
    corrupted_id = ("0" if original_id[0] != "0" else "1") + original_id[1:]
    event_dict["id"] = corrupted_id

    event = NostrEvent.from_dict(event_dict)
    valid, reason = event.verify_with_reason()
    assert valid is False
    assert "ID de evento inválido" in reason


def test_invalid_signature_fails_verification():
    """Un evento con firma corrupta o de otra clave debe ser rechazado."""
    pair = KeyPair.generate()
    event_dict = create_and_sign_event(
        privkey=pair.secret_key_hex,
        kind=1,
        content="Mensaje original",
    )

    # Alterar el último caracter de la firma hex
    original_sig = event_dict["sig"]
    corrupted_sig = original_sig[:-1] + ("0" if original_sig[-1] != "0" else "1")
    event_dict["sig"] = corrupted_sig

    event = NostrEvent.from_dict(event_dict)
    valid, reason = event.verify_with_reason()
    assert valid is False
    assert "Firma Schnorr BIP-340 inválida" in reason


def test_modified_content_fails_verification():
    """Si el contenido se altera después de firmado, el ID ya no coincide con el payload."""
    pair = KeyPair.generate()
    event_dict = create_and_sign_event(
        privkey=pair.secret_key_hex,
        kind=1,
        content="Contenido legítimo",
    )

    # Modificar el contenido
    event_dict["content"] = "Contenido adulterado"

    event = NostrEvent.from_dict(event_dict)
    valid, reason = event.verify_with_reason()
    assert valid is False
    assert "ID de evento inválido" in reason


def test_recalculated_id_with_modified_content_fails_signature():
    """Si un atacante modifica el contenido Y recalcula el ID para coincidir, la firma Schnorr debe fallar."""
    pair = KeyPair.generate()
    event_dict = create_and_sign_event(
        privkey=pair.secret_key_hex,
        kind=1,
        content="Transferir 10 sats",
    )

    # Atacante adultera contenido y recalcula id, pero no tiene la clave privada
    event_dict["content"] = "Transferir 1000000 sats"
    event_dict["id"] = compute_event_id(
        pubkey=event_dict["pubkey"],
        created_at=event_dict["created_at"],
        kind=event_dict["kind"],
        tags=event_dict["tags"],
        content=event_dict["content"],
    )

    event = NostrEvent.from_dict(event_dict)
    valid, reason = event.verify_with_reason()
    assert valid is False
    assert "Firma Schnorr BIP-340 inválida" in reason


def test_modified_tags_fails_verification():
    """Modificar los tags debe invalidar la verificación."""
    pair = KeyPair.generate()
    event_dict = create_and_sign_event(
        privkey=pair.secret_key_hex,
        kind=1,
        content="Hola",
        tags=[["p", "destinatario1"]],
    )

    # Alterar tag
    event_dict["tags"] = [["p", "destinatario_hacker"]]

    assert verify_event(event_dict) is False


def test_modified_pubkey_fails_verification():
    """Atribuir el evento a otra pubkey debe fallar."""
    pair1 = KeyPair.generate()
    pair2 = KeyPair.generate()

    event_dict = create_and_sign_event(
        privkey=pair1.secret_key_hex,
        kind=1,
        content="Hola",
    )

    event_dict["pubkey"] = pair2.public_key_hex

    assert verify_event(event_dict) is False
