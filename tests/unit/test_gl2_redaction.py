"""G-L2 — redaction corpus (D-016, CC-5; PHASE2-4-ROADMAP W2a).

60 seeded secrets (15 kinds × TR/DE/EN contexts) appear in 0 outgoing provider requests and 0
audit payloads; the false-positive rate on 200 clean, identifier-rich chunks is ≤ 1%.

Every secret is assembled at run time from fragments and a seeded RNG, so no secret-shaped literal
exists in this file (the pre-commit gate and gitleaks scan the source).
"""

from __future__ import annotations

import json
import random
import string
from decimal import Decimal

import httpx
import pytest

from hlmemo.librarian.budget import NoBudget
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor

SEED = 20260923
ALNUM = string.ascii_letters + string.digits
B64U = ALNUM + "-_"


def _r(rng: random.Random, n: int, alphabet: str = ALNUM) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


def _iban(rng: random.Random, country: str, length: int) -> str:
    bban = "".join(rng.choice(string.digits) for _ in range(length - 4))
    digits = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    check = 98 - int(digits) % 97
    raw = f"{country}{check:02d}{bban}"
    return " ".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def secrets(rng: random.Random) -> list[tuple[str, str]]:
    """(kind, secret) — one per kind; called four times for 60 distinct secrets."""
    pem_head = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
    pem_tail = "-----END " + "RSA PRIVATE" + " KEY-----"
    jwt = lambda: "eyJ" + _r(rng, 20, B64U) + ".eyJ" + _r(rng, 30, B64U) + "." + _r(rng, 43, B64U)  # noqa: E731
    return [
        ("openrouter", "sk-" + "or-v1-" + _r(rng, 64, "0123456789abcdef")),
        ("openai", "sk-" + "proj-" + _r(rng, 48)),
        ("anthropic", "sk-" + "ant-api03-" + _r(rng, 40, B64U)),
        ("github", "gh" + "p_" + _r(rng, 36)),
        ("github_pat", "github" + "_pat_" + _r(rng, 22) + "_" + _r(rng, 59)),
        ("aws", "AK" + "IA" + _r(rng, 16, string.ascii_uppercase + string.digits)),
        ("google", "AI" + "za" + _r(rng, 35, B64U)),
        ("slack", "xo" + "xb-" + _r(rng, 12, string.digits) + "-" + _r(rng, 24)),
        ("hlm", "hlm_" + _r(rng, 43, B64U)),
        ("jwt", jwt()),
        ("pem", pem_head + "\n" + _r(rng, 64) + "\n" + _r(rng, 64) + "\n" + pem_tail),
        ("dsn", "postgresql://app:" + _r(rng, 20) + "@db.internal:5432/app"),
        ("assignment", "DB_PASSWORD=" + _r(rng, 12, string.ascii_lowercase) + _r(rng, 6, string.digits)),
        ("iban", _iban(rng, rng.choice(["DE", "TR", "NL"]), rng.choice([22, 26, 18]))),
        ("bearer", "Authorization: Bearer " + _r(rng, 40, B64U)),
    ]


CONTEXTS = [
    "EN: use {s} for the staging deploy, it expires next week.",
    "DE: Der Zugang lautet {s} und darf nie ins Repo.",
    "TR: Üretim erişimi {s} olarak .env içinde duruyor, commit etme.",
    "EN mixed: rotate {s} — deploy log line 42 (svc-qx7, E4193).",
]


def seeded_corpus() -> list[tuple[str, str, str]]:
    rng = random.Random(SEED)
    out = []
    for ctx in CONTEXTS:
        for kind, s in secrets(rng):
            out.append((kind, s, ctx.format(s=s)))
    return out


def secret_core(kind: str, s: str) -> list[str]:
    """The sensitive substrings that must never survive (not the harmless prefix words)."""
    if kind == "dsn":
        return [s.split("://app:")[1].split("@")[0]]
    if kind == "assignment":
        return [s.split("=", 1)[1]]
    if kind == "bearer":
        return [s.rsplit(" ", 1)[1]]
    if kind == "pem":
        return s.split("\n")[1:3]
    if kind == "iban":
        return [s, s.replace(" ", "")]
    return [s]


CLEAN_TEMPLATES = [
    "Service svc-{a} reads APP_DB_DSN from the environment and reports E{n} on timeout.",
    "Commit {sha} moved the retry policy to worker/lease.py (attempts ≤ 5, backoff 1/2/4/8 s).",
    "token_budget: {n} and max_tokens={m} are validated before any provider call.",
    'HLM_LLM_API_KEY = "env:OPENROUTER_API_KEY" lives in the profile; the api_key is configured in llm.env.',
    "Das Deployment nutzt Image sha256:{sha64} auf dem Host 10.0.{m}.{n2}, Port 8765.",
    "Karte v{n} güncellendi: proje hlmemo-{a}, kimlik {uuid}, sürüm 0.0.{m}.",
    "The password field is required; the login form shows 'password: required' on submit.",
    "psql postgresql://127.0.0.1:5432/hlm_test_{a} -c 'SELECT count(*) FROM jobs' returned {n}.",
    "Siehe https://github.com/example/{a}/blob/{sha}/README.md und docs/decisions/D-0{m}.md.",
    "Run `alembic upgrade phase0@head` then `pytest -q tests/integration` (job {uuid}).",
    "Bearer tokens are checked by the middleware before the body is read (E_AUTH on failure).",
    "Invoice ref INV-{n}-{a} paid; order id {uuid}; build #{m} green in {n2} s.",
]


def clean_corpus() -> list[str]:
    rng = random.Random(SEED + 1)
    out = []
    for i in range(200):
        t = CLEAN_TEMPLATES[i % len(CLEAN_TEMPLATES)]
        out.append(
            t.format(
                a=_r(rng, 3, string.ascii_lowercase + string.digits),
                n=rng.randint(100, 99999),
                n2=rng.randint(1, 254),
                m=rng.randint(10, 999),
                sha="".join(rng.choice("0123456789abcdef") for _ in range(40)),
                sha64="".join(rng.choice("0123456789abcdef") for _ in range(64)),
                uuid="-".join(_r(rng, k, "0123456789abcdef") for k in (8, 4, 4, 4, 12)),
            )
        )
    return out


def test_gl2_corpus_shape() -> None:
    corpus = seeded_corpus()
    assert len(corpus) == 60 and len({s for _, s, _ in corpus}) == 60
    assert {k for k, _, _ in corpus} >= {"openrouter", "hlm", "jwt", "pem", "dsn", "iban", "bearer"}
    assert len(clean_corpus()) == 200


def test_gl2_redactor_removes_every_seeded_secret() -> None:
    r = Redactor()
    for kind, s, text in seeded_corpus():
        red = r.redact(text)
        for core in secret_core(kind, s):
            assert core not in red.text, (kind, red.text)
        assert "⟦REDACTED:" in red.text
        assert all(v in text for v in red.mapping.values())  # the mapping is in-memory only


def test_gl2_redaction_is_deterministic() -> None:
    r = Redactor()
    _, _, text = seeded_corpus()[0]
    assert r.text(text) == r.text(text)


def test_gl2_false_positive_rate_at_most_one_percent() -> None:
    r = Redactor()
    flagged = [c for c in clean_corpus() if r.redact(c).count]
    assert len(flagged) / 200 <= 0.01, flagged


def _profile() -> LlmProfile:
    return LlmProfile(
        name="stub",
        base_url="http://stub.invalid/v1",
        model_id="stub/model",
        api_key="k",
        reasoning=None,
        extra={"temperature": 0},
        price_in_per_m=Decimal("1"),
        price_out_per_m=Decimal("1"),
        supports_json_schema=False,
        prompt_overrides={},
    )


@pytest.mark.asyncio
async def test_gl2_zero_secrets_in_requests_and_audit_payloads() -> None:
    corpus = seeded_corpus()
    sent: list[str] = []
    echo = iter(corpus)

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode("utf-8"))
        _, _, fresh = next(echo)  # the model "leaks" a secret into free text: the audit redacts it
        out = {"contradicts": True, "supersedes": "B", "reason": fresh}
        body = {"choices": [{"message": {"content": json.dumps(out)}, "finish_reason": "stop"}], "usage": {}}
        return httpx.Response(200, json=body)

    redactor = Redactor()
    provider = Provider(
        [_profile()],
        budget=NoBudget(),
        ledger=MemoryLedger(),
        transport=httpx.MockTransport(handler),
        redactor=redactor,
    )
    task = load_task("contradiction")
    audits = []
    for _, _, text in corpus:
        user = f"JOB: contradiction\nINPUT: {json.dumps({'A': {'text': text}, 'B': {'text': 'ok'}})}"
        res = await provider.complete(task, user)
        audits.append(json.dumps(res.audit(redactor), ensure_ascii=False))
    await provider.aclose()
    assert len(sent) == 60
    for kind, s, _ in corpus:
        for core in secret_core(kind, s):
            core_json = json.dumps(core)[1:-1]  # as it appears inside a JSON string
            assert not any(core in b or core_json in b for b in sent), (kind, "request")
            assert not any(core in a or core_json in a for a in audits), (kind, "audit")
