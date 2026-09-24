"""Provider profiles (D-017, D-019, D-094): the primary profile from ``Settings``, the fallback
profile, and the per-task fallback overrides.

The primary profile is what ``Settings`` resolved (profile file < hlm.toml < ``HLM_*`` env), so a
deployment can override the model or key by env. A fallback profile is loaded from its own file
only (``profiles/<name>.toml`` or an inline ``[profiles.<name>]``): env overrides of ``HLM_LLM_*``
never leak into it. Nothing outside a profile names a vendor, model id or price.

Per-task fallbacks (D-094): ``HLM_FALLBACK_PROFILE`` is every task's fallback unless the task has
its own ``HLM_FALLBACK_PROFILE__<TASK>`` (task name upper-cased; ``Settings.task_fallback_profiles``).
``profile_chain`` resolves the overrides once and hangs them on the chain's PRIMARY
(``LlmProfile.task_fallbacks``); ``for_task(chain, task)`` — used by ``Provider.complete`` for every
call — puts the task's own fallback in place of the default one. The mechanism is generic by task
name: any caller that builds its provider from ``profile_chain(settings)`` honours the override of
its task without task-specific code. A fallback whose file lists the task in ``disabled_tasks`` is
never used for that task (D-071: that task then runs without a fallback). Breakers, spend guard and
ledger stay per profile, so a task-specific fallback has its own breaker key (its profile name) and
its own prices.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from hlmemo.config import TASK_FALLBACK_ENV, Settings, _strip_prefix, expand_env, load_profile
from hlmemo.librarian.errors import LlmConfigError

FALLBACK_ENV = "HLM_FALLBACK_PROFILE"


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
    #: tasks this profile is not qualified for (its file's ``disabled_tasks``, D-071)
    disabled_tasks: frozenset[str] = frozenset()
    #: D-094, set on a chain's PRIMARY by ``profile_chain``: task -> that task's own fallback profile
    #: (``None``: the override names the primary itself, i.e. no fallback for the task)
    task_fallbacks: Mapping[str, LlmProfile | None] = field(default_factory=dict, repr=False, compare=False)

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


def _tasks(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    return frozenset(str(v) for v in value or [])


def profile_disabled_tasks(name: str) -> frozenset[str]:
    """``disabled_tasks`` of a profile FILE (qualification is configuration, D-017)."""
    raw = load_profile(name)
    return _tasks(raw.get("disabled_tasks") or raw.get("DISABLED_TASKS"))


def _build(name: str, raw: dict[str, Any], disabled: frozenset[str] = frozenset()) -> LlmProfile:
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
        disabled_tasks=disabled or _tasks(raw.get("disabled_tasks")),
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
        profile_disabled_tasks(settings.profile),
    )


def named_profile(name: str) -> LlmProfile:
    """A profile from its file alone (fallback profiles, the live gate's per-profile runs)."""
    raw = load_profile(name)
    if not raw:
        raise LlmConfigError(f"profile {name!r} not found")
    return _build(name, {k: expand_env(v) for k, v in _strip_prefix(raw).items()})


def task_fallback_var(task: str) -> str:
    """The environment variable of ``task``'s fallback override (``HLM_FALLBACK_PROFILE__<TASK>``)."""
    return f"{TASK_FALLBACK_ENV}{task.upper()}"


def task_fallback_names(settings: Any) -> dict[str, str]:
    """The configured per-task overrides, ``{task: profile name}`` (D-094)."""
    raw = getattr(settings, "task_fallback_profiles", None) or {}
    return {str(k).lower(): str(v) for k, v in raw.items()}


def _fallback(var: str, name: str) -> LlmProfile:
    """``named_profile`` with an error that names the setting (startup fails fast on a typo)."""
    try:
        return named_profile(name)
    except LlmConfigError as exc:
        raise LlmConfigError(f"{var}={name!r}: {exc} (profiles/<name>.toml)") from None


def for_task(chain: Sequence[LlmProfile], task: str) -> list[LlmProfile]:
    """The chain a call of ``task`` uses (D-094): when the chain's primary carries an override for
    the task, the task's own fallback replaces the default one; then every fallback whose file lists
    the task in ``disabled_tasks`` is dropped (D-071). The head is kept as it is (an API caller
    drops an unqualified primary itself; a gate measuring one profile alone keeps it). Idempotent,
    so a caller may apply it to a chain it already resolved."""
    if not chain:
        return []
    head, rest = chain[0], list(chain[1:])
    if task in head.task_fallbacks:
        own = head.task_fallbacks[task]
        rest = [] if own is None else [own]
    return [head, *(p for p in rest if task not in p.disabled_tasks)]


def profile_chain(settings: Settings, task: str | None = None) -> list[LlmProfile]:
    """``[primary]`` or ``[primary, fallback]`` (the fallback is tried once, D-019). The primary
    carries the resolved per-task overrides (D-094); with ``task``, the chain of that task
    (``for_task``). An unknown or broken profile raises ``LlmConfigError`` naming the variable."""
    overrides: dict[str, LlmProfile | None] = {}
    for name_task, name in sorted(task_fallback_names(settings).items()):
        overrides[name_task] = (
            None if name == settings.profile else _fallback(task_fallback_var(name_task), name)
        )
    chain = [replace(primary_profile(settings), task_fallbacks=overrides)]
    if settings.fallback_profile and settings.fallback_profile != settings.profile:
        chain.append(_fallback(FALLBACK_ENV, settings.fallback_profile))
    return chain if task is None else for_task(chain, task)


def known_tasks() -> set[str]:
    """The librarian tasks this build defines (the prompt registry)."""
    from hlmemo.librarian.prompts import MAX_TOKENS

    return set(MAX_TOKENS)


def check_chains(settings: Settings) -> list[str]:
    """Startup check (D-094). Resolves the primary, the default fallback and every per-task
    override: an unknown or broken profile raises ``LlmConfigError`` (fail fast, the message names
    the variable). Returns warnings: an override for a task this build does not define (kept, used
    by nothing until such a task exists), and an override whose profile lists its task in
    ``disabled_tasks`` (the task then runs without a fallback, D-071)."""
    chain = profile_chain(settings)
    known = known_tasks()
    warnings: list[str] = []
    for task, own in sorted(chain[0].task_fallbacks.items()):
        var = task_fallback_var(task)
        if task not in known:
            warnings.append(
                f"{var}: unknown librarian task {task!r} (known: {', '.join(sorted(known))});"
                " the override is ignored until a task of that name exists"
            )
        elif own is not None and task in own.disabled_tasks:
            warnings.append(
                f"{var}={own.name}: that profile lists {task!r} in disabled_tasks (not qualified);"
                f" {task} runs without a fallback"
            )
    return warnings


def describe_chains(settings: Settings) -> dict[str, Any]:
    """The effective primary and fallback per task, for ops status (D-094). Never raises: a
    configuration error (or a settings object without an LLM configuration) is reported as
    ``{"error": ...}`` (profile and variable names only)."""
    try:
        chain = profile_chain(settings)
    except LlmConfigError as exc:
        return {"error": str(exc)}
    except (AttributeError, TypeError, ValueError) as exc:  # no/partial LLM settings: say so, never fail
        return {"error": f"no LLM configuration ({type(exc).__name__})"}
    head = chain[0]
    out: dict[str, Any] = {"default_fallback": settings.fallback_profile, "tasks": {}}
    for task in sorted(known_tasks() | set(head.task_fallbacks)):
        eff = for_task(chain, task)
        entry: dict[str, Any] = {
            "primary": head.name,
            "primary_model": head.model_id,
            "fallback": eff[1].name if len(eff) > 1 else None,
            "fallback_model": eff[1].model_id if len(eff) > 1 else None,
            "source": "task" if task in head.task_fallbacks else ("default" if len(chain) > 1 else "none"),
        }
        if task in head.disabled_tasks:
            entry["primary_qualified"] = False
        configured = head.task_fallbacks[task] if task in head.task_fallbacks else next(iter(chain[1:]), None)
        if configured is not None and task in configured.disabled_tasks:
            entry["unqualified_fallback"] = configured.name
        if task not in known_tasks():
            entry["unknown_task"] = True
        out["tasks"][task] = entry
    return out


__all__ = [
    "FALLBACK_ENV",
    "LlmProfile",
    "check_chains",
    "describe_chains",
    "for_task",
    "known_tasks",
    "named_profile",
    "primary_profile",
    "profile_chain",
    "profile_disabled_tasks",
    "task_fallback_names",
    "task_fallback_var",
]
