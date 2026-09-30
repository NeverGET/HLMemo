"""D-211: the importer's secret filter also catches env-style assignments of secret-named variables with a
literal value (``env-secret-assignment``) and email + password credential pairs (``credential-pair``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hlmemo.importers import common

V1 = "abc123" + "def456"  # built at runtime: the literal would trip the repo's secret scan
V2 = "abcdefgh" + "1234"


@pytest.mark.parametrize(
    "text",
    [
        "MINIO_SECRET" + "_KEY=" + V1,
        "export DB_PASSWORD=Sup3rS3cret!",
        "minio_secret" + "_key = '" + V2 + "'",
        "Set `STRIPE_API_KEY=sk_live_abcdefgh` in the env file.",
        'GITHUB_TOKEN: ok\nAPP_TOKEN="abcd-efgh-ijkl"',
        "  - API_KEY=abcdefghij",
        "MY_SECRET=correcthorsebattery",
    ],
)
def test_env_secret_assignment_is_a_hit(text: str) -> None:
    assert common.secret_hit(text) == "env-secret-assignment"


@pytest.mark.parametrize(
    "text",
    [
        "MINIO_SECRET_KEY=<your secret key>",
        "DB_PASSWORD=${DB_PASSWORD}",
        "DB_PASSWORD=$DB_PASSWORD_FROM_VAULT",
        "API_KEY=changeme",
        "API_KEY=xxxxxxxxxxxx",
        "API_KEY=********",
        "MINIO_SECRET_KEY=",
        "MINIO_SECRET_KEY=short",  # under 8 characters
        "HLM_LLM_API_KEY = env:TEST_WRITER_KEY",
        "HLM_TOKEN_FILE=/run/secrets/hlm_token",
        "password = os.environ['DB_PASSWORD']",
        "Set MINIO_SECRET_KEY to the key from the console; DB_PASSWORD is the database password.",
        "if token == expected_token_value_here:",
        "The API_KEY variable holds the provider key.",
    ],
)
def test_env_secret_assignment_negatives(text: str) -> None:
    assert common.secret_hit(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "email: alice@example.com / password: Passw0rd!",
        "user@x.com / Passw0rd!",
        "login alice@example.com\npassword: hunter2hunter",
        "Email: bob@corp.io\nPasswort ist password: Xy7-Qz9!",
        "admin@x.org | S3cure#Pass",
    ],
)
def test_credential_pair_is_a_hit(text: str) -> None:
    assert common.secret_hit(text) == "credential-pair"


@pytest.mark.parametrize(
    "text",
    [
        "Contact alice@example.com for access.",
        "email: a@b.com / password: <your password>",
        "email: a@b.com / password: required",
        "user@x.com / Bob Smith",
        "user@x.com / yes",
        "The password must be rotated every 90 days.\nMail ops@example.com.",
        "password: ${PASSWORD}\nemail: a@b.com",
    ],
)
def test_credential_pair_negatives(text: str) -> None:
    assert common.secret_hit(text) is None


def test_the_importer_skips_the_whole_file(tmp_path: Path) -> None:
    f = tmp_path / "notes.md"
    f.write_text("# Setup\nSome text.\nMINIO_SECRET" + "_KEY=" + V1 + "\n")
    assert common.read_text(f) == (None, "secret-pattern:env-secret-assignment")
    g = tmp_path / "creds.md"
    g.write_text("# Logins\nemail: a@b.com / password: Passw0rd!\n")
    assert common.read_text(g) == (None, "secret-pattern:credential-pair")
