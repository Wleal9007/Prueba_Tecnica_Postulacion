"""
Tests unitarios para el módulo de claves (src/keys.py).
Incluye vectores de prueba oficiales de BIP-340 y pruebas de round-trip NIP-19 (nsec/npub).
"""

import pytest
from src.keys import (
    KeyPair,
    bech32_to_hex,
    derive_xonly_pubkey,
    generate_secret_key,
    hex_to_bech32,
    hex_to_npub,
    hex_to_nsec,
    import_secret_key,
    npub_to_hex,
    nsec_to_hex,
    sign_schnorr,
    verify_schnorr,
)


# ==============================================================================
# Vectores Oficiales BIP-340
# Fuente: https://github.com/bitcoin/bips/blob/master/bip-0340/test-vectors.csv
# ==============================================================================

BIP340_VECTORS = [
    # Vector 0 (simple, secret key = 3)
    {
        "index": 0,
        "sk": "0000000000000000000000000000000000000000000000000000000000000003",
        "pk": "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9",
        "aux_rand": "0000000000000000000000000000000000000000000000000000000000000000",
        "msg": "0000000000000000000000000000000000000000000000000000000000000000",
        "sig": "E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA821525F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0",
        "result": True,
    },
    # Vector 1
    {
        "index": 1,
        "sk": "B7E151628AED2A6ABF7158809CF4F3C762E7160F38B4DA56A784D9045190CFEF",
        "pk": "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
        "aux_rand": "0000000000000000000000000000000000000000000000000000000000000001",
        "msg": "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
        "sig": "6896BD60EEAE296DB48A229FF71DFE071BDE413E6D43F917DC8DCF8C78DE33418906D11AC976ABCCB20B091292BFF4EA897EFCB639EA871CFA95F6DE339E4B0A",
        "result": True,
    },
    # Vector 2
    {
        "index": 2,
        "sk": "C90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B14E5C9",
        "pk": "DD308AFEC5777E13121FA72B9CC1B7CC0139715309B086C960E18FD969774EB8",
        "aux_rand": "C87AA53824B4D7AE2EB035A2B5BBBCCC080E76CDC6D1692C4B0B62D798E6D906",
        "msg": "7E2D58D8B3BCDF1ABADEC7829054F90DDA9805AAB56C77333024B9D0A508B75C",
        "sig": "5831AAEED7B44BB74E5EAB94BA9D4294C49BCF2A60728D8B4C200F50DD313C1BAB745879A5AD954A72C45A91C3A51D3C7ADEA98D82F8481E0E1E03674A6F3FB7",
        "result": True,
    },
    # Vector 3 (test fails if msg is reduced modulo p or n)
    {
        "index": 3,
        "sk": "0B432B2677937381AEF05BB02A66ECD012773062CF3FA2549E44F58ED2401710",
        "pk": "25D1DFF95105F5253C4022F628A996AD3A0D95FBF21D468A1B33F8C160D8F517",
        "aux_rand": "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
        "msg": "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
        "sig": "7EB0509757E246F19449885651611CB965ECC1A187DD51B64FDA1EDC9637D5EC97582B9CB13DB3933705B32BA982AF5AF25FD78881EBB32771FC5922EFC66EA3",
        "result": True,
    },
    # Vector 6 (has_even_y(R) is false -> verificación debe ser False)
    {
        "index": 6,
        "sk": None,
        "pk": "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
        "aux_rand": None,
        "msg": "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
        "sig": "FFF97BD5755EEEA420453A14355235D382F6472F8568A18B2F057A14602975563CC27944640AC607CD107AE10923D9EF7A73C643E166BE5EBEAFA34B1AC553E2",
        "result": False,
    },
    # Vector 7 (negated message -> verificación debe ser False)
    {
        "index": 7,
        "sk": None,
        "pk": "DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659",
        "aux_rand": None,
        "msg": "243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89",
        "sig": "1FA62E331EDBC21C394792D2AB1100A7B432B013DF3F6FF4F99FCB33E0E1515F28890B3EDB6E7189B630448B515CE4F8622A954CFE545735AAEA5134FCCDB2BD",
        "result": False,
    },
]


@pytest.mark.parametrize("vec", BIP340_VECTORS)
def test_bip340_vectors(vec):
    """Verifica los vectores oficiales de prueba para derivación y firma BIP-340."""
    pk_bytes = bytes.fromhex(vec["pk"])
    msg_bytes = bytes.fromhex(vec["msg"])
    sig_bytes = bytes.fromhex(vec["sig"])

    # 1. Si incluye clave secreta, verificar la derivación x-only pubkey y la firma
    if vec["sk"]:
        sk_bytes = bytes.fromhex(vec["sk"])
        derived_pk = derive_xonly_pubkey(sk_bytes)
        assert derived_pk.lower() == vec["pk"].lower(), f"Fallo derivación pubkey en vector {vec['index']}"

        aux_rand = bytes.fromhex(vec["aux_rand"]) if vec["aux_rand"] else None
        generated_sig = sign_schnorr(msg_bytes, sk_bytes, aux_randomness=aux_rand)
        assert generated_sig.hex().lower() == vec["sig"].lower(), f"Fallo firma generada en vector {vec['index']}"

    # 2. Verificar la firma contra la clave pública
    valid = verify_schnorr(sig_bytes, msg_bytes, pk_bytes)
    assert valid is vec["result"], f"Resultado de verificación inesperado en vector {vec['index']}: esperado {vec['result']}, obtenido {valid}"


def test_bip340_vector_invalid_pubkey():
    """Vector 5: clave pública que no pertenece a la curva secp256k1 debe ser rechazada."""
    invalid_pk = bytes.fromhex("EEFDEA4CDB677750A420FEE807EACF21EB9898AE79B9768766E4FAA04A2D4A34")
    msg = bytes.fromhex("243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89")
    sig = bytes.fromhex("6CFF5C3BA86C69EA4B7376F31A9BCB4F74C1976089B2D9963DA2E5543E17776969E89B4C5564D00349106B8497785DD7D1D713A8AE82B32FA79D5F7FC407D39B")

    assert verify_schnorr(sig, msg, invalid_pk) is False


# ==============================================================================
# Pruebas de Round-Trip NIP-19 (nsec / npub Bech32)
# ==============================================================================

def test_nip19_roundtrip_nsec():
    """Prueba de ida y vuelta para codificación nsec."""
    sample_privkey = "7f7ff033f214226fb01593eeab77a70307e8d79ec4493a00c4301580a11813b7"
    nsec = hex_to_nsec(sample_privkey)

    assert nsec.startswith("nsec1")
    decoded_hex = nsec_to_hex(nsec)
    assert decoded_hex == sample_privkey


def test_nip19_roundtrip_npub():
    """Prueba de ida y vuelta para codificación npub."""
    sample_pubkey = "3bf0c63fcb93463407af97b5e5ee64fa883d107ef9e558472c4eb9aaaefa459d"
    npub = hex_to_npub(sample_pubkey)

    assert npub.startswith("npub1")
    decoded_hex = npub_to_hex(npub)
    assert decoded_hex == sample_pubkey


def test_bech32_wrong_hrp_rejection():
    """Validar que decodificar nsec con función de npub o viceversa lance error."""
    sample_privkey = "7f7ff033f214226fb01593eeab77a70307e8d79ec4493a00c4301580a11813b7"
    nsec = hex_to_nsec(sample_privkey)

    with pytest.raises(ValueError, match="HRP incorrecto"):
        npub_to_hex(nsec)


def test_bech32_corrupted_checksum():
    """Verificar que un carácter alterado en el checksum de nsec sea rechazado."""
    sample_privkey = "7f7ff033f214226fb01593eeab77a70307e8d79ec4493a00c4301580a11813b7"
    nsec = hex_to_nsec(sample_privkey)

    # Alterar el último carácter
    corrupted = nsec[:-1] + ("q" if nsec[-1] != "q" else "p")
    with pytest.raises(ValueError, match="checksum incorrecto"):
        nsec_to_hex(corrupted)


# ==============================================================================
# Pruebas de Generación e Importación de Claves (KeyPair)
# ==============================================================================

def test_keypair_generation():
    """Prueba la generación de un par de claves nuevo y coherente."""
    pair = KeyPair.generate()

    assert len(pair.secret_key_hex) == 64
    assert len(pair.public_key_hex) == 64
    assert pair.nsec.startswith("nsec1")
    assert pair.npub.startswith("npub1")

    # Derivación manual independiente
    expected_pk = derive_xonly_pubkey(pair.secret_key_hex)
    assert pair.public_key_hex == expected_pk

    # Roundtrip del nsec generado
    assert nsec_to_hex(pair.nsec) == pair.secret_key_hex
    assert npub_to_hex(pair.npub) == pair.public_key_hex


def test_keypair_import_from_nsec():
    """Importar clave existente a través de nsec."""
    sample_sk = "b7e151628aed2a6abf7158809cf4f3c762e7160f38b4da56a784d9045190cfef"
    nsec = hex_to_nsec(sample_sk)

    pair = KeyPair.from_secret(nsec)
    assert pair.secret_key_hex == sample_sk
    assert pair.public_key_hex == "dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659"


def test_keypair_import_from_hex():
    """Importar clave existente a través de hex plano."""
    sample_sk = "b7e151628aed2a6abf7158809cf4f3c762e7160f38b4da56a784d9045190cfef"

    pair = KeyPair.from_secret(sample_sk)
    assert pair.secret_key_hex == sample_sk
    assert pair.public_key_hex == "dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659"


def test_import_invalid_key_format():
    """Rechazar cadenas que no sean nsec ni hex de 64 caracteres."""
    with pytest.raises(ValueError):
        import_secret_key("clave_invalida_muy_corta")

    with pytest.raises(ValueError):
        import_secret_key("zzzz" * 16)  # hex no válido


def test_sign_and_verify_arbitrary_message():
    """Generar clave aleatoria, firmar mensaje SHA-256 arbitrario y verificar con éxito."""
    pair = KeyPair.generate()
    msg = b"\x42" * 32

    sig = sign_schnorr(msg, pair.secret_key_hex)
    assert len(sig) == 64
    assert verify_schnorr(sig, msg, pair.public_key_hex) is True

    # Mensaje manipulado debe fallar
    tampered_msg = b"\x43" * 32
    assert verify_schnorr(sig, tampered_msg, pair.public_key_hex) is False
