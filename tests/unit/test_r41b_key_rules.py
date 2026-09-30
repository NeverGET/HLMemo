"""R4.1 review Sol F-1: ``env-secret-assignment`` catches generic ``*_KEY`` names (whole component),
without coming back to substring matching (Astra F-3 / Sol F-5)."""

from __future__ import annotations

import pytest

from hlmemo.importers import common

V = "abc123" + "def456"  # built at runtime: the literal would trip the repo secret scan


def test_generic_key_assignment_is_blocked() -> None:  # Sol's exact reproducer
    assert common.secret_hit("export HMAC_KEY=Abcd1234!") == "env-secret-assignment"


@pytest.mark.parametrize(
    "text",
    [
        "SIGNING_" + "KEY=" + V,
        "KEY=Abcd1234!Xyz",
        "jwt.key=Abcd1234!Xyz",
        "encryption-key = 'Abcd1234!Xyz'",
        "hmacKey=Abcd1234!Xyz",
        "AUTH_TOKEN_SECRET=Abcd1234!Xyz",
        "GITHUB_" + "TOKEN=" + V,
    ],
)
def test_key_and_token_names_hit(text: str) -> None:
    assert common.secret_hit(text) == "env-secret-assignment"


@pytest.mark.parametrize(
    "text",
    [
        "TOKENIZER=cl100k_base",
        "TOKENIZER_MODEL=bert-base-uncased",
        "TOKEN_BUDGET=1234567890",
        "MAX_TOKENS=1234567890",
        "KEYBOARD_LAYOUT=turkish-q-layout",
        "MONKEY_BUSINESS=abcdefgh1234",
        "HMAC_KEY_FILE=/run/secrets/hmac-key-file",
        "HMAC_KEY_ID=abcdefgh1234",
        "PUBLIC_KEY=ssh-ed25519-AAAAC3Nza",
        "SORT_KEY=created_at_descending",
        "HMAC_KEY=<your hmac key>",
        "The HMAC_KEY variable holds the signing key.",
    ],
)
def test_non_secret_names_and_placeholders_do_not_hit(text: str) -> None:
    assert common.secret_hit(text) is None
