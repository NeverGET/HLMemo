"""Provider profiles (D-017, D-019): the primary profile from ``Settings`` plus the fallback profile.

The primary profile is what ``Settings`` resolved (profile file < hlm.toml < ``HLM_*`` env), so a
deployment can override the model or key by env. The fallback profile is loaded from its own file
only (``profiles/<name>.toml`` or an inline ``[profiles.<name>]``): env overrides of ``HLM_LLM_*``
never leak into it. Nothing outside a profile names a vendor, model id or price.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from hlmemo.config import Settings, _strip_prefix, expand_env, load_profile
from hlmemo.librarian.errors import LlmConfigError


@dataclass(frozen=True, slots=True)
class LlmProfile:
    name: str
    base_url: str
    model_id: str
    api_key: str | None = field(repr=False)
    reasoning: dict[str, Any] | None
    extra: dict[str, Any]
    price_in_per_m: Decimal | None
    price_out_per_m: Decimal | None
    supports_json_schema: bool
    prompt_overrides: dict[str, Any]

    @property
    def priced(self) -> bool:
        return self.price_in_per_m is not None and self.price_out_per_m is not None

    def worst_usd(self, input_tokens: int, max_tokens: int) -> Decimal:
        """``ceil(input × 1.10) × price_in + max_tokens × price_out`` (W2a reservation)."""
        if not self.priced:
            raise LlmConfigError(f"profile {self.name!r} has no prices")
        padded = -(-input_tokens * 11 // 10)  # ceil(input_tokens * 1.10) in integers
        per = Decimal(1_000_000)
        return (Decimal(padded) * self.price_in_per_m + Decimal(max_tokens) * self.price_out_per_m) / per

    def cost_usd(self, input_tokens: int, output_tokens: int) -> Decimal:
        if not self.priced:
            return Decimal(0)
        per = Decimal(1_000_000)
        return (
            Decimal(input_tokens) * self.price_in_per_m + Decimal(output_tokens) * self.price_out_per_m
        ) / per


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _json(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return json.loads(v) if v else None
    return v


def _build(name: str, raw: dict[str, Any]) -> LlmProfile:
    base_url = raw.get("llm_base_url")
    model = raw.get("llm_model")
    if not base_url or not model:
        raise LlmConfigError(f"profile {name!r} needs HLM_LLM_BASE_URL and HLM_LLM_MODEL")
    key = raw.get("llm_api_key")
    if hasattr(key, "get_secret_value"):
        key = key.get_secret_value()
    return LlmProfile(
        name=name,
        base_url=str(base_url).rstrip("/"),
        model_id=str(model),
        api_key=str(key) if key else None,
        reasoning=_json(raw.get("llm_reasoning")),
        extra=dict(_json(raw.get("extra")) or {}),
        price_in_per_m=_dec(raw.get("price_in_per_m")),
        price_out_per_m=_dec(raw.get("price_out_per_m")),
        supports_json_schema=bool(raw.get("supports_json_schema", False)),
        prompt_overrides=dict(_json(raw.get("prompt_overrides")) or {}),
    )


def primary_profile(settings: Settings) -> LlmProfile:
    return _build(
        settings.profile,
        {
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "llm_api_key": settings.llm_api_key,
            "llm_reasoning": settings.llm_reasoning,
            "extra": settings.extra,
            "price_in_per_m": settings.price_in_per_m,
            "price_out_per_m": settings.price_out_per_m,
            "supports_json_schema": settings.supports_json_schema,
            "prompt_overrides": settings.prompt_overrides,
        },
    )


def named_profile(name: str) -> LlmProfile:
    """A profile from its file alone (fallback profiles, the live gate's per-profile runs)."""
    raw = load_profile(name)
    if not raw:
        raise LlmConfigError(f"profile {name!r} not found")
    return _build(name, {k: expand_env(v) for k, v in _strip_prefix(raw).items()})


def profile_chain(settings: Settings) -> list[LlmProfile]:
    """``[primary]`` or ``[primary, fallback]`` (the fallback is tried once, D-019)."""
    chain = [primary_profile(settings)]
    if settings.fallback_profile and settings.fallback_profile != settings.profile:
        chain.append(named_profile(settings.fallback_profile))
    return chain


__all__ = ["LlmProfile", "named_profile", "primary_profile", "profile_chain"]
