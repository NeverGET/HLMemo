"""Settings for HLMemo (PHASE0-SPEC §5 `hlm.toml`, D-017).

Precedence (highest first): init kwargs > `HLM_*` environment > nearest `./hlm.toml`
> `~/.config/hlm/hlm.toml` > provider profile (`[profiles.<name>]` in hlm.toml, else
`profiles/<name>.toml`) > field defaults.

Values of the form `env:NAME` or `${NAME}` are expanded from the environment. Secrets
never live in the TOML files; they are referenced through such indirections.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

ENV_PREFIX = "HLM_"
DEFAULT_PROFILE = "openrouter"
_ENV_REF = re.compile(r"^env:(?P<name>[A-Za-z_][A-Za-z0-9_]*)$")
_ENV_BRACES = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")
#: D-094: a per-task fallback override, ``HLM_FALLBACK_PROFILE__<TASK>`` (env, task upper-cased) or
#: ``fallback_profile__<task>`` in the ``[hlm]`` table; generic by task name (no task list here)
TASK_FALLBACK_ENV = f"{ENV_PREFIX}FALLBACK_PROFILE__"
_TASK_FALLBACK_KEY = re.compile(r"^(?:hlm_)?fallback_profile__(?P<task>[a-z][a-z0-9_]*)$")


def task_fallback_overrides(raw: Mapping[str, Any]) -> dict[str, str]:
    """``{task: profile}`` from the keys ``HLM_FALLBACK_PROFILE__<TASK>`` / ``fallback_profile__<task>``
    of ``raw`` (case-insensitive; task names lower-cased). An empty value is no override."""
    out: dict[str, str] = {}
    for key, value in raw.items():
        m = _TASK_FALLBACK_KEY.match(str(key).strip().lower())
        if m and isinstance(value, str) and value.strip():
            out[m.group("task")] = value.strip()
    return out


def expand_env(value: Any) -> Any:
    """Expand `env:NAME` and `${NAME}` references; unset variables become None / ''."""
    if not isinstance(value, str):
        return value
    m = _ENV_REF.match(value)
    if m:
        return os.environ.get(m.group("name"))
    return _ENV_BRACES.sub(lambda mm: os.environ.get(mm.group("name"), ""), value)


def _toml_candidates() -> list[Path]:
    """Nearest `./hlm.toml` (walking up from cwd) first, then the user config file."""
    found: list[Path] = []
    explicit = os.environ.get("HLM_CONFIG")
    if explicit:
        found.append(Path(explicit).expanduser())
    cwd = Path.cwd()
    for d in (cwd, *cwd.parents):
        p = d / "hlm.toml"
        if p.is_file():
            found.append(p)
            break
    found.append(Path.home() / ".config" / "hlm" / "hlm.toml")
    return [p for p in found if p.is_file()]


def _profile_dirs() -> list[Path]:
    dirs: list[Path] = []
    if os.environ.get("HLM_PROFILES_DIR"):
        dirs.append(Path(os.environ["HLM_PROFILES_DIR"]).expanduser())
    dirs.append(Path.cwd() / "profiles")
    # src layout: <repo>/src/hlmemo/config.py -> <repo>/profiles ; container: /app/profiles
    dirs.append(Path(__file__).resolve().parents[2] / "profiles")
    dirs.append(Path("/app/profiles"))
    return [d for d in dirs if d.is_dir()]


def load_profile(name: str, inline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the raw `HLM_*` keys of a provider profile (inline hlm.toml section wins over file)."""
    data: dict[str, Any] = {}
    for d in _profile_dirs():
        p = d / f"{name}.toml"
        if p.is_file():
            with p.open("rb") as fh:
                data.update(tomllib.load(fh))
            break
    if inline:
        data.update(inline)
    return data


def _strip_prefix(raw: dict[str, Any]) -> dict[str, Any]:
    """`HLM_DB_DSN` -> `db_dsn`; non-prefixed keys (profile, fallback_profile, extra) pass through."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = k[len(ENV_PREFIX) :] if k.upper().startswith(ENV_PREFIX) else k
        out[key.lower()] = v
    return out


class HlmTomlSource(PydanticBaseSettingsSource):
    """Settings source: `[hlm]` table of hlm.toml layered over the selected provider profile."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        hlm: dict[str, Any] = {}
        inline_profiles: dict[str, Any] = {}
        client: dict[str, Any] = {}
        preflight: dict[str, Any] = {}
        for path in reversed(_toml_candidates()):  # lowest precedence first, nearest last
            with path.open("rb") as fh:
                doc = tomllib.load(fh)
            hlm.update(doc.get("hlm", {}))
            inline_profiles.update(doc.get("profiles", {}))
            client.update(doc.get("client", {}))
            preflight.update(doc.get("preflight", {}))
        merged = _strip_prefix(hlm)
        profile = os.environ.get(f"{ENV_PREFIX}PROFILE") or merged.get("profile") or DEFAULT_PROFILE
        prof = _strip_prefix(load_profile(profile, inline_profiles.get(profile)))
        layered = {**prof, **merged, "profile": profile}
        # D-094: per-task fallbacks come from the [hlm] table only (a profile never names another)
        tasks = task_fallback_overrides({k: expand_env(v) for k, v in merged.items()})
        if tasks:
            layered["task_fallback_profiles"] = tasks
        if client.get("server_url") and "server_url" not in layered:
            layered["server_url"] = client["server_url"]
        if client:
            layered["client"] = client
        if preflight:
            layered["preflight"] = preflight
        return {k: expand_env(v) for k, v in layered.items()}

    def get_field_value(self, field, field_name: str) -> tuple[Any, str, bool]:  # noqa: ANN001
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if k in self.settings_cls.model_fields and v is not None}


class TaskFallbackEnvSource(PydanticBaseSettingsSource):
    """Settings source (D-094): every ``HLM_FALLBACK_PROFILE__<TASK>`` environment variable becomes
    ``task_fallback_profiles[<task>]``, whatever the task (a task added later needs no code here)."""

    def get_field_value(self, field, field_name: str) -> tuple[Any, str, bool]:  # noqa: ANN001
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        env = {k: v for k, v in os.environ.items() if k.upper().startswith(TASK_FALLBACK_ENV)}
        found = task_fallback_overrides(env)
        return {"task_fallback_profiles": found} if found else {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    # --- storage / embedding (D-008, D-017) ---
    db_dsn: str = "postgresql://hlm:hlm@127.0.0.1:5432/hlm"
    embed_model: str = "intfloat/multilingual-e5-small"
    embed_revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    embed_intra_op_num_threads: int = Field(default=2, gt=0)
    embed_max_batch_tokens: int = Field(default=1024, ge=512)
    worker_batch_chunks: int = Field(default=32, gt=0)
    worker_max_jobs_per_batch: int = Field(default=1, gt=0)
    worker_memory_profile: bool = False
    hosting_target: str = "compose"

    # --- librarian LLM primary profile (D-017/D-019); the fallback is loaded by librarian.profiles ---
    profile: str = DEFAULT_PROFILE
    fallback_profile: str | None = None
    # D-094: per-task fallback overrides, task -> profile name, from HLM_FALLBACK_PROFILE__<TASK>
    # (TaskFallbackEnvSource) or [hlm] fallback_profile__<task>; a task without one falls back to
    # fallback_profile. Resolved and validated by librarian.profiles (names only here, D-017).
    task_fallback_profiles: dict[str, str] = Field(default_factory=dict)
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_reasoning: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    # Profile economics + capabilities (W2a). USD per million tokens; a profile without prices
    # refuses live calls unless llm_budget_disabled (atomic reservation needs a worst case).
    price_in_per_m: float | None = Field(default=None, ge=0)
    price_out_per_m: float | None = Field(default=None, ge=0)
    supports_json_schema: bool = False
    # Model quirks live only here (D-017): {task: {"system_append": str}}.
    prompt_overrides: dict[str, Any] = Field(default_factory=dict)

    # --- librarian runtime (PHASE2-4-ROADMAP W2a) ---
    librarian_enabled: bool = False  # off until R2
    librarian_role: str = Field(default="observer", pattern="^(observer|assistant|autonomous)$")
    librarian_lease_s: int = Field(default=120, gt=0)
    librarian_lease_renew_s: float = Field(default=30.0, gt=0)
    librarian_poll_s: float = Field(default=1.0, gt=0)
    librarian_heartbeat_s: float = Field(default=10.0, gt=0)
    librarian_heartbeat_file: Path | None = Path("/tmp/hlm-librarian-heartbeat.json")
    librarian_memory_rules: int = Field(default=8, gt=0)
    librarian_memory_tokens: int = Field(default=1500, gt=0)
    # W2b: a write_review job waits (handed back, no attempt consumed) up to this long for the
    # subject's embeddings before it runs with the lexical list only.
    librarian_embed_wait_s: float = Field(default=300.0, ge=0)
    # W2b: a relation review job starts this long after its write (the embed worker runs first).
    librarian_review_delay_s: float = Field(default=3.0, ge=0)
    # W2b (D-067): who gives the second opinion on high-impact proposals: "cross" = the other
    # profile of the chain (primary <-> fallback), "self" = the answering profile again.
    librarian_verifier: str = Field(default="cross", pattern="^(cross|self)$")
    # e2e #7: jobs processed concurrently by ONE librarian process (each with its own connection,
    # lease keeper and fenced done; spend guard + lineage ceiling are atomic in the database).
    librarian_concurrency: int = Field(default=3, ge=1, le=16)
    # Sol 56 #5: every database connection of the librarian process (job connections, the shared
    # lease renewer, the loop, the ledger/reservation pool); needs 2 x concurrency + 2.
    librarian_db_connections: int = Field(default=8, ge=4, le=64)
    llm_mode: str = Field(default="live", pattern="^(live|record|replay|off)$")
    llm_cassette_dir: Path | None = None
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_breaker_threshold: int = Field(default=5, gt=0)
    llm_breaker_open_s: float = Field(default=60.0, gt=0)
    llm_breaker_max_open_s: float = Field(default=900.0, gt=0)
    llm_budget_disabled: bool = False
    llm_budget_hour_usd: float = Field(default=3.0, ge=0)
    llm_budget_day_usd: float = Field(default=10.0, ge=0)
    llm_budget_month_usd: float = Field(default=60.0, ge=0)
    llm_reservation_ttl_s: int = Field(default=600, gt=0)
    llm_job_call_cap: int = Field(default=20, gt=0)
    llm_redact_email: bool = False
    llm_redact_phone: bool = False

    # --- server / auth (§2) ---
    admin_token: SecretStr | None = None
    registration_secret: SecretStr | None = None
    # W0a (D-052, D-061), fail-closed: only local compose.yaml and the test fixtures opt in.
    #   closed -> POST /devices/register answers 404 before any body byte is read;
    #   secret -> registration requires X-HLM-Registration-Secret (none configured: always refused);
    #   open   -> Phase-0 behaviour (the secret is still required when one is configured).
    registration_mode: Literal["open", "secret", "closed"] = "closed"
    # disabled -> every admin HTTP route (/admin/*, /devices/{approve,grant,list}) answers 404,
    # POST /devices/revoke is self-only, device 1 is never bound; admin work goes via hlmemo.ops.
    admin_http: Literal["enabled", "disabled"] = "disabled"
    # production -> startup refuses unless registration_mode=closed and admin_http=disabled.
    deployment: Literal["development", "production"] = "development"
    server_url: str = "http://127.0.0.1:8765/mcp"
    api_host: str = "0.0.0.0"
    api_port: int = 8765
    api_limit_concurrency: int = Field(default=512, gt=0)
    api_timeout_keep_alive: int = Field(default=5, gt=0)
    # 50 × 64,000 Unicode characters, including JSON surrogate-pair escaping.
    request_max_body_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    # Inactivity is independent of average throughput, including before the first byte.
    request_body_timeout_s: float = Field(default=30.0, gt=0)
    # Gate-trusted bodies get base + received/rate seconds; total cap uses declared/max size.
    # At 8 KiB/s: 38,400,000 bytes get 4717.5 s; the full 64 MiB gets 8222 s.
    request_body_base_s: float = Field(default=30.0, gt=0)
    request_body_global_budget_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    request_body_client_budget_bytes: int = Field(default=128 * 1024 * 1024, gt=0)
    request_body_min_rate_bytes_s: int = Field(default=8 * 1024, gt=0)
    request_body_client_concurrency: int = Field(default=16, gt=0)
    request_body_spool_threshold_bytes: int = Field(default=1024 * 1024, gt=0)
    request_spool_dir: Path | None = None  # None uses the system temporary directory.
    request_db_timeout_s: float = Field(default=15.0, gt=0)
    readiness_timeout_s: float = Field(default=2.0, gt=0)
    readiness_cache_ttl_s: float = Field(default=1.0, gt=0)
    # No implicit trust, including loopback; configure the actual Caddy subnet explicitly.
    trusted_proxy_ips: str = ""

    # --- pool ---
    pool_min_size: int = 1
    pool_max_size: int = 8
    pool_timeout_s: float = Field(default=5.0, gt=0)
    db_lock_timeout_ms: int = Field(default=2000, gt=0)
    db_statement_timeout_ms: int = Field(default=10000, gt=0)
    db_idle_in_transaction_timeout_ms: int = Field(default=5000, gt=0)
    db_transaction_timeout_ms: int = Field(default=20000, gt=0)

    # --- wrapper sections passed through from hlm.toml ---
    client: dict[str, Any] = Field(default_factory=dict)
    preflight: dict[str, Any] = Field(default_factory=dict)

    @field_validator("llm_reasoning", "extra", "prompt_overrides", mode="before")
    @classmethod
    def _json_string(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            return json.loads(v)
        return v

    @field_validator("task_fallback_profiles", mode="before")
    @classmethod
    def _task_fallbacks(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = json.loads(v) if v.strip() else {}
        if isinstance(v, dict):  # task names are lower-case; an empty profile name is no override
            return {str(k).strip().lower(): str(p).strip() for k, p in v.items() if p and str(p).strip()}
        return v

    @field_validator("llm_api_key", "admin_token", "registration_secret", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # env > hlm.toml > profile (both inside HlmTomlSource) > defaults; the per-task fallback
        # maps of the sources are merged key by key (pydantic-settings deep-merges dicts)
        return (init_settings, env_settings, TaskFallbackEnvSource(settings_cls), HlmTomlSource(settings_cls))

    @field_validator("registration_mode", "admin_http", "deployment", mode="before")
    @classmethod
    def _normalise_mode(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v

    @property
    def admin_http_enabled(self) -> bool:
        return self.admin_http == "enabled"

    @property
    def admin_enabled(self) -> bool:
        """Device 1 is bound only with a token AND admin HTTP enabled (D-061: disabled in production)."""
        return (
            self.admin_http_enabled
            and self.admin_token is not None
            and bool(self.admin_token.get_secret_value())
        )

    def unsafe_config(self) -> list[str]:
        """Reasons a production deployment must refuse to start (empty list: safe)."""
        if self.deployment != "production":
            return []
        reasons = []
        if self.registration_mode != "closed":
            reasons.append(f"HLM_REGISTRATION_MODE={self.registration_mode} (production requires closed)")
        if self.admin_http != "disabled":
            reasons.append(f"HLM_ADMIN_HTTP={self.admin_http} (production requires disabled)")
        return reasons


def get_settings(**overrides: Any) -> Settings:
    return Settings(**overrides)
