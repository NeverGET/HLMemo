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
from pathlib import Path
from typing import Any

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

    # --- librarian LLM: parsed and validated in Phase 0, never called (D-017/D-019) ---
    profile: str = DEFAULT_PROFILE
    fallback_profile: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_reasoning: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    # --- server / auth (§2) ---
    admin_token: SecretStr | None = None
    registration_secret: SecretStr | None = None
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

    @field_validator("llm_reasoning", "extra", mode="before")
    @classmethod
    def _json_string(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            return json.loads(v)
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
        # env > hlm.toml > profile (both inside HlmTomlSource) > defaults
        return (init_settings, env_settings, HlmTomlSource(settings_cls))

    @property
    def admin_enabled(self) -> bool:
        return self.admin_token is not None and bool(self.admin_token.get_secret_value())


def get_settings(**overrides: Any) -> Settings:
    return Settings(**overrides)
