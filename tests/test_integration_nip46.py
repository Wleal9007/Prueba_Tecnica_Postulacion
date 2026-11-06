"""
Test de integración NIP-46 de punta a punta utilizando FakeRelay en memoria.
Simula el cliente de Stacker News realizando el flujo completo de autenticación:
connect -> get_public_key -> sign_event (kind 27235 NIP-98).
"""

import asyncio
import json
import pytest
from src.events import NostrEvent, create_and_sign_event, verify_event
from src.keys import KeyPair
from src.nip04 import nip04_decrypt, nip04_encrypt
from src.nip46 import Nip46Signer
from src.relay import FakeRelay, RelayClient


def test_full_nip46_flow_with_fake_relay():
    """Flujo de integración completo sin conexión a Internet."""
    asyncio.run(_run_test_flow())


async def _run_test_flow():
    # 1. Iniciar FakeRelay en memoria
    relay = FakeRelay()
    relay_url = await relay.start()

    # 2. Configurar firmante remoto NIP-46
    user_pair = KeyPair.generate()
    bunker_secret = "test_token_secreto_123"
    signer = Nip46Signer(user_privkey=user_pair.secret_key_hex, secret=bunker_secret)

    # Cliente de relay para el firmante
    async def signer_on_event(event_dict):
        resp_event = signer.process_incoming_event(event_dict)
        if resp_event:
            await signer_relay_client.publish(resp_event)

    signer_relay_client = RelayClient(relay_url, on_event=signer_on_event)
    await signer_relay_client.start()
    assert await signer_relay_client.wait_connected(timeout=2.0)

    # Suscribir el firmante a peticiones NIP-46 dirigidas a su pubkey
    await signer_relay_client.subscribe(
        "sub-signer",
        {"kinds": [24133], "#p": [signer.bunker_pubkey_hex]},
    )

    # 3. Configurar cliente simulado (Stacker News)
    client_pair = KeyPair.generate()
    client_received_responses = asyncio.Queue()

    async def client_on_event(event_dict):
        # Descifrar respuesta NIP-04 dirigida al cliente
        if event_dict.get("kind") == 24133:
            decrypted = nip04_decrypt(
                privkey=client_pair.secret_key_hex,
                sender_pubkey=signer.bunker_pubkey_hex,
                encrypted_content=event_dict["content"],
            )
            rpc = json.loads(decrypted)
            await client_received_responses.put(rpc)

    client_relay = RelayClient(relay_url, on_event=client_on_event)
    await client_relay.start()
    assert await client_relay.wait_connected(timeout=2.0)

    # Suscribir el cliente a respuestas del firmante
    await client_relay.subscribe(
        "sub-client",
        {"kinds": [24133], "#p": [client_pair.public_key_hex]},
    )

    # Helper para enviar peticiones RPC desde el cliente hacia el firmante
    async def send_rpc(method: str, params: list, req_id: str):
        payload = {"id": req_id, "method": method, "params": params}
        payload_str = json.dumps(payload)
        encrypted = nip04_encrypt(
            privkey=client_pair.secret_key_hex,
            target_pubkey=signer.bunker_pubkey_hex,
            plaintext=payload_str,
        )
        req_event = create_and_sign_event(
            privkey=client_pair.secret_key_hex,
            kind=24133,
            content=encrypted,
            tags=[["p", signer.bunker_pubkey_hex]],
        )
        await client_relay.publish(req_event)

    try:
        # -------------------------------------------------------------
        # Paso A: Solicitud 'connect' con secret correcto
        # -------------------------------------------------------------
        await send_rpc("connect", [client_pair.public_key_hex, bunker_secret], "req-conn-1")
        resp_conn = await asyncio.wait_for(client_received_responses.get(), timeout=3.0)
        assert resp_conn["id"] == "req-conn-1"
        assert resp_conn["result"] == "ack"
        assert resp_conn["error"] is None

        # -------------------------------------------------------------
        # Paso B: Solicitud 'get_public_key'
        # -------------------------------------------------------------
        await send_rpc("get_public_key", [], "req-pubkey-2")
        resp_pub = await asyncio.wait_for(client_received_responses.get(), timeout=3.0)
        assert resp_pub["id"] == "req-pubkey-2"
        assert resp_pub["result"] == user_pair.public_key_hex
        assert resp_pub["error"] is None

        # -------------------------------------------------------------
        # Paso C: Solicitud 'sign_event' (Desafío de auth Stacker News kind 27235)
        # -------------------------------------------------------------
        auth_challenge_event = {
            "kind": 27235,
            "created_at": 1700000000,
            "tags": [
                ["challenge", "abc123k1challenge456"],
                ["u", "https://stacker.news"],
                ["method", "GET"],
            ],
            "content": "Stacker News Authentication",
        }

        await send_rpc("sign_event", [json.dumps(auth_challenge_event)], "req-sign-3")
        resp_sign = await asyncio.wait_for(client_received_responses.get(), timeout=3.0)
        assert resp_sign["id"] == "req-sign-3"
        assert resp_sign["error"] is None

        # Validar el evento firmado retornado
        signed_event_dict = json.loads(resp_sign["result"])
        assert signed_event_dict["pubkey"] == user_pair.public_key_hex
        assert signed_event_dict["kind"] == 27235
        assert signed_event_dict["tags"][0] == ["challenge", "abc123k1challenge456"]

        # Verificar canónicamente el ID y la firma Schnorr BIP-340
        assert verify_event(signed_event_dict) is True

        # -------------------------------------------------------------
        # Paso D: Solicitud 'connect' con secret inválido debe devolver error RPC
        # -------------------------------------------------------------
        await send_rpc("connect", [client_pair.public_key_hex, "secret_falso"], "req-conn-fail")
        resp_fail = await asyncio.wait_for(client_received_responses.get(), timeout=3.0)
        assert resp_fail["id"] == "req-conn-fail"
        assert resp_fail["result"] is None
        assert resp_fail["error"] == "Invalid secret"

    finally:
        # Cierre limpio de todas las conexiones
        await client_relay.close()
        await signer_relay_client.close()
        await relay.stop()
