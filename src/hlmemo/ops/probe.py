"""R4 (R-14): ``python -m hlmemo.ops probe-writer``: ONE minimal, authenticated call of the prose
writer with THIS process's effective settings and profile credentials. Run it inside the api container
(``hlm_ops.sh … probe-writer``), so the check uses exactly the key the api writes with.

The writer is ``HLM_RESEARCH_WRITER_PROFILE`` (its own file and the key its ``HLM_LLM_API_KEY``
names), or, when that is unset, the research primary (the profile that writes the prose answer then).
One POST ``/chat/completions`` with ``max_tokens`` 16 and the profile's own request options (JSON mode,
reasoning): a synthetic prompt, no retry, no fallback, a short timeout. It prints ONE JSON line and
nothing else: ``{"ok": bool, "profile": name, "status": <HTTP status or a reason>, "latency_ms": n}``.
``ok`` means HTTP 200 with a parseable chat-completions body (a ``choices`` list). The model's own
text is not judged: 16 tokens may end in a truncation, and that is no credential error. ``status`` is
the HTTP status (401 for a wrong key), else a reason: ``missing_key``, ``price_expired``, ``timeout``,
``transport``, ``unparseable`` or ``config``. Keys, headers, request and response bodies are never
printed or logged. The probe is not ledgered (a 16-token call; the spend-guard windows are unchanged).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

PROBE_MAX_TOKENS = 16
PROBE_TIMEOUT_S = 30.0
PROBE_SYSTEM = 'Reply with the JSON object {"ok": true} and nothing else.'
PROBE_USER = "ping"


#: a test seam: the HTTP transport of the probe's client (None: the network)
TRANSPORT: httpx.AsyncBaseTransport | None = None


def writer_profile(settings: Any) -> Any:
    """The profile that writes the prose answer under ``settings``: the writer profile when set
    (its own file, as the api resolves it: ``research.writer_chain``), else the research primary
    (``research.research_chain``, the addendum's luna-revert state)."""
    from hlmemo.librarian.errors import LlmConfigError
    from hlmemo.librarian.profiles import named_profile
    from hlmemo.librarian.tasks import research as rs

    name = str(getattr(settings, "research_writer_profile", None) or "").strip()
    if name:
        return named_profile(name)
    chain = rs.research_chain(settings)
    if not chain:
        raise LlmConfigError("no research profile (the primary is missing or not qualified for research)")
    return chain[0]


async def probe(settings: Any, *, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The probe (module doc). Never raises for a provider or configuration failure: ``ok`` is
    False and ``status`` names it."""
    from hlmemo.librarian.errors import LlmConfigError

    out: dict[str, Any] = {"ok": False, "profile": None, "status": None, "latency_ms": 0}
    try:
        profile = writer_profile(settings)
    except (LlmConfigError, OSError, ValueError):
        out["status"] = "config"
        return out
    out["profile"] = profile.name
    if not profile.api_key:
        out["status"] = "missing_key"
        return out
    if profile.price_expired():  # R4 (R-5): the api would never call it
        out["status"] = "price_expired"
        return out
    body: dict[str, Any] = {
        "model": profile.model_id,
        "messages": [{"role": "system", "content": PROBE_SYSTEM}, {"role": "user", "content": PROBE_USER}],
    }
    body.update(profile.extra)
    body["max_tokens"] = PROBE_MAX_TOKENS
    if profile.reasoning is not None:
        body["reasoning"] = profile.reasoning
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            base_url=profile.base_url,
            transport=transport or TRANSPORT,
            timeout=PROBE_TIMEOUT_S,
            follow_redirects=False,
        ) as client:
            resp = await client.post(
                "/chat/completions", json=body, headers={"Authorization": f"Bearer {profile.api_key}"}
            )
    except httpx.TimeoutException:
        out.update(status="timeout", latency_ms=_ms(t0))
        return out
    except httpx.HTTPError:
        out.update(status="transport", latency_ms=_ms(t0))
        return out
    out["latency_ms"] = _ms(t0)
    out["status"] = resp.status_code
    if resp.status_code != 200:
        return out
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
        out["status"] = "unparseable"
        return out
    out["ok"] = True
    return out


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


__all__ = ["PROBE_MAX_TOKENS", "probe", "writer_profile"]
