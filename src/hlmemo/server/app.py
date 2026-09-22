"""HLMemo API (PHASE0-SPEC §2, §6): Starlette app factory.

  create_app(settings) -> Starlette
    lifespan  : open the pool, bind device 1 from HLM_ADMIN_TOKEN in ONE transaction (§2) — before
                uvicorn opens the listener — then serve; close the pool on shutdown.
    middleware: AuthMiddleware (bearer -> AuthContext on request.state, status gate before routing).
    routes    : GET /health (liveness, no auth) · GET /ready (readiness, no auth) · /devices/* ·
              /admin/* · /mcp (MCP streamable HTTP, gated).

Liveness vs readiness (codex review O1): `/health` only says the process answers. `/ready` verifies
every dependency a write or query needs — DB reachable, migration at `phase0@head`, the pinned
model files present under `HLM_MODELS_DIR` with sha256 matching `models.lock`, and the tokenizer
loading — and answers 503 `not_ready` with the failing checks otherwise. The compose healthcheck
probes `/ready`, so `api` is never "healthy" while `memory.write` / `memory.query` would fail.
The lifespan loads one model/tokenizer session shared by readiness and all queries; missing
files defer that load to readiness after the assets become available. Expensive
file checks (hashing 470 MB) are cached until a model file's size/mtime changes.

`python -m hlmemo.server.app` runs uvicorn with settings from HLM_* / hlm.toml.
The MCP endpoint (`server/mcp_server.py`, five tools of §3) is a plain `Route("/mcp", <ASGI>)` so
that `AuthMiddleware` runs before the MCP session manager sees a byte (§2 status gate); its
session manager is started inside the app lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hlmemo.auth.cursors import load_cursor_secret
from hlmemo.auth.tokens import hash_token
from hlmemo.config import Settings, get_settings
from hlmemo.core.budget import Meter
from hlmemo.core.embedder import (
    HASHED_FILES,
    MODEL_FILES,
    Embedder,
    default_model_dir,
    embed_config_check,
    model_hashes,
    repo_root,
    require_pinned_embed_config,
)
from hlmemo.core.read_service import ReadDeps
from hlmemo.db import auth_queries as q
from hlmemo.db.pool import create_pool
from hlmemo.server import admin, devices
from hlmemo.server.common import device_view
from hlmemo.server.mcp_server import McpEndpoint, create_mcp_endpoint
from hlmemo.server.middleware import AuthMiddleware, RateLimiter, _proxy_networks

log = logging.getLogger("hlmemo.server")

ADMIN_DISABLED_WARNING = "admin device disabled: HLM_ADMIN_TOKEN not set"
PHASE0_BRANCH = "phase0"
PHASE0_HEAD_FALLBACK = "0001_phase0"  # used only when alembic/ is not on disk (never in the image)
MODELS_LOCK = "models.lock"


# --------------------------------------------------------------------------- routes


async def health(request: Request) -> JSONResponse:
    """No auth required. With a bearer (any status) the device is echoed so pending devices can poll."""
    body: dict = {"status": "ok"}
    device = request.state.device
    if device is not None:
        v = device_view(device)
        body["device"] = {"id": v["id"], "name": v["name"], "status": v["status"], "class": v["class"]}
    return JSONResponse(body)


# --------------------------------------------------------------------------- readiness


def _project_file(name: str) -> Path | None:
    """`<repo root>/<name>` in a checkout, `./<name>` or `/app/<name>` in the image."""
    root = repo_root()
    candidates = [root / name if root else None, Path.cwd() / name, Path("/app") / name]
    return next((c for c in candidates if c is not None and c.is_file()), None)


def parse_models_lock(path: Path) -> dict[str, str]:
    """`<file>: sha256:<hex> size:<n>` lines of models.lock -> {file: hex}."""
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if ": sha256:" in line:
            name, rest = line.split(":", 1)
            out[name.strip()] = rest.strip().split()[0].removeprefix("sha256:")
    return out


@lru_cache(maxsize=1)
def phase0_head() -> str:
    """The `phase0@head` revision id from the alembic script directory (never bare `head`: the
    deferred `hnsw` branch is a second head by design)."""
    ini = _project_file("alembic.ini")
    if ini is None:
        return PHASE0_HEAD_FALLBACK
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(ini.parent / "alembic"))
        (rev,) = ScriptDirectory.from_config(cfg).get_revisions(f"{PHASE0_BRANCH}@head")
        return rev.revision
    except Exception:  # noqa: BLE001 - readiness must answer, not crash
        log.warning("could not resolve %s@head from %s; using %s", PHASE0_BRANCH, ini, PHASE0_HEAD_FALLBACK)
        return PHASE0_HEAD_FALLBACK


def _files_signature(model_dir: Path) -> tuple[tuple[str, int, int], ...]:
    out = []
    for rel in HASHED_FILES:
        st = (model_dir / rel).stat()
        out.append((rel, st.st_size, st.st_mtime_ns))
    return tuple(out)


def _verify_models_blocking(
    model_dir: Path,
    lock: Path | None,
    cache: dict[str, Any],
    embedder: Embedder | None,
    meter: Meter | None,
) -> dict[str, Any]:
    """Model files present + sha256 == models.lock + tokenizer parses. Hashes are cached per
    (size, mtime) signature so a healthcheck every few seconds does not re-read 470 MB."""
    result: dict[str, Any] = {"dir": str(model_dir)}
    missing = [rel for rel in MODEL_FILES if not (model_dir / rel).is_file()]
    if missing:
        result.update(ok=False, error="model files missing; run `make models`", missing=missing)
        return result
    if embedder is None or meter is None:
        result.update(ok=False, error="embedding dependencies not initialized")
        return result
    if lock is None:
        result.update(ok=False, error=f"{MODELS_LOCK} not found")
        return result
    expected = parse_models_lock(lock)
    sig = _files_signature(model_dir)
    if cache.get("sig") != sig:
        cache.clear()
        cache["hashes"] = model_hashes(model_dir)
    hashes: dict[str, str] = cache["hashes"]
    bad = [rel for rel in HASHED_FILES if expected.get(rel) != hashes[rel]]
    if bad:
        result.update(ok=False, error=f"model files differ from {MODELS_LOCK}", mismatch=bad)
        return result
    if cache.get("sig") != sig:
        # Exercise inference and budget accounting, including the separate o200k cache.
        embedder.embed_query("readiness")
        meter.count_text("readiness")
        cache["sig"] = sig
    result.update(ok=True, lock=str(lock), tokenizer=True, inference=True, meter=True)
    return result


@dataclass
class _ReadinessProbe:
    """One shielded probe per process; both successes and failures have a short TTL."""

    db_slot: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    task: asyncio.Task | None = None
    result: tuple[bool, dict[str, Any]] | None = None
    expires_at: float = 0.0

    async def get(self, app: Starlette, settings: Settings) -> tuple[bool, dict[str, Any]]:
        if self.result is not None and asyncio.get_running_loop().time() < self.expires_at:
            return self.result
        # There is no suspension between inspecting and publishing the shared task.
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(app, settings))
        # A disconnected/cancelled caller cannot release the DB slot or start another probe.
        return await asyncio.shield(self.task)

    async def _run(self, app: Starlette, settings: Settings) -> tuple[bool, dict[str, Any]]:
        self.result = await _check_readiness(app, settings, self.db_slot)
        self.expires_at = asyncio.get_running_loop().time() + settings.readiness_cache_ttl_s
        return self.result


async def readiness(app: Starlette) -> tuple[bool, dict[str, Any]]:
    settings = getattr(app.state, "settings", None) or get_settings()
    probe = getattr(app.state, "readiness_probe", None)
    if probe is None:
        probe = app.state.readiness_probe = _ReadinessProbe()
    return await probe.get(app, settings)


async def _check_readiness(
    app: Starlette, settings: Settings, db_slot: asyncio.Semaphore
) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {
        "embed_config": embed_config_check(settings.embed_model, settings.embed_revision)
    }
    try:
        _proxy_networks(settings.trusted_proxy_ips)
        checks["trusted_proxy_ips"] = {"ok": True}
    except ValueError as exc:
        checks["trusted_proxy_ips"] = {
            "ok": False,
            "error": f"HLM_TRUSTED_PROXY_IPS must be an empty string or a valid CIDR list: {exc}",
        }
    head = phase0_head()
    try:
        # The dedicated permit covers connect, query AND connection close, independently of
        # either traffic pool. Timeout/cancellation cleanup finishes before it can be reused.
        async with db_slot:
            async with asyncio.timeout(settings.readiness_timeout_s):
                async with await AsyncConnection.connect(
                    settings.db_dsn,
                    autocommit=True,
                    connect_timeout=2,
                    options=f"-c statement_timeout={int(settings.readiness_timeout_s * 1000)}",
                ) as conn:
                    cur = await conn.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
                    applied = [r[0] for r in await cur.fetchall()]
        checks["db"] = {"ok": True}
        checks["migration"] = {"ok": head in applied, "expected": head, "applied": applied}
    except Exception as exc:  # noqa: BLE001 - report, never raise from a probe
        checks["db"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc).strip()[:200]}"}
        checks["migration"] = {"ok": False, "expected": head, "error": "database unavailable"}
    cache: dict[str, Any] | None = getattr(app.state, "model_check_cache", None)
    if cache is None:
        cache = app.state.model_check_cache = {}
    model_check_lock = getattr(app.state, "model_check_lock", None)
    if model_check_lock is None:
        model_check_lock = app.state.model_check_lock = asyncio.Lock()
    model_dir = default_model_dir()  # honours HLM_MODELS_DIR

    async def verify_models() -> dict[str, Any]:
        async with model_check_lock:
            if getattr(app.state, "embedding_deferred", False) and all(
                (model_dir / rel).is_file() for rel in MODEL_FILES
            ):
                await _load_shared_embedder(app, settings, model_dir)
            return await asyncio.to_thread(
                _verify_models_blocking,
                model_dir,
                _project_file(MODELS_LOCK),
                cache,
                getattr(app.state, "embedder", None),
                getattr(getattr(app.state, "read_deps", None), "meter", None),
            )

    try:
        # The shielded task owns the lock: cancelling a probe must not release it while
        # to_thread is still hashing/loading and mutating the shared cache.
        checks["models"] = await asyncio.shield(verify_models())
    except Exception as exc:  # noqa: BLE001
        checks["models"] = {"ok": False, "dir": str(model_dir), "error": f"{type(exc).__name__}: {exc}"}
    return all(c.get("ok") for c in checks.values()), checks


async def ready(request: Request) -> JSONResponse:
    """Readiness: 200 `{"status":"ready"}` only when writes and queries can succeed; 503 otherwise."""
    ok, checks = await readiness(request.app)
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503
    )


def build_routes(mcp: McpEndpoint | None = None) -> list[Route]:
    """All routes; `/mcp` is the MCP ASGI handler (gated by the middleware like every other route)."""
    mcp = mcp or create_mcp_endpoint()
    return [
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
        Route("/devices/register", devices.register, methods=["POST"]),
        Route("/devices/approve", devices.approve, methods=["POST"]),
        Route("/devices/revoke", devices.revoke, methods=["POST"]),
        Route("/devices/grant", devices.grant_add, methods=["POST"]),
        Route("/devices/grant", devices.grant_remove, methods=["DELETE"]),
        Route("/devices/list", devices.list_devices, methods=["GET"]),
        Route("/devices/whoami", devices.whoami, methods=["GET"]),
        Route("/admin/devices/{id}/approve", devices.approve, methods=["POST"]),
        Route("/admin/devices/{id}/revoke", devices.revoke, methods=["POST"]),
        Route("/admin/projects", admin.projects_create, methods=["POST"]),
        Route("/admin/projects", admin.projects_list, methods=["GET"]),
        Route("/admin/projects/{slug}/grants", devices.grant_add, methods=["POST"]),
        Route("/admin/projects/{slug}/grants", devices.grant_remove, methods=["DELETE"]),
        Route("/mcp", mcp.asgi, methods=["GET", "POST", "DELETE"]),
    ]


_SAMPLE_PARAMS = {"id": "2", "slug": "proj"}


def route_table(app: Starlette) -> list[tuple[str, str]]:
    """Every (METHOD, concrete path) the app serves — G5 parametrises the pending-device test over it."""
    out: list[tuple[str, str]] = []
    for r in app.routes:
        if not isinstance(r, Route):
            continue
        path = r.path
        for name, sample in _SAMPLE_PARAMS.items():
            path = path.replace("{" + name + "}", sample)
        for m in sorted(r.methods or ()):
            if m == "HEAD":
                continue
            out.append((m, path))
    return out


# --------------------------------------------------------------------------- lifespan


async def _load_shared_embedder(app: Starlette, settings: Settings, model_dir: Path) -> None:
    """Publish one complete dependency bundle; readiness's shielded lock serializes recovery."""
    embedder = await asyncio.to_thread(Embedder, model_dir, threads=settings.embed_intra_op_num_threads)
    deps = ReadDeps(
        meter=Meter(),
        model_dir=model_dir,
        cursor_secret=app.state.cursor_secret,
        _embedder=embedder,
    )
    app.state.embedder = embedder
    app.state.read_deps = deps
    app.state.embedding_deferred = False


async def bind_admin_device(settings: Settings) -> int:
    """§2 start-up binding, one transaction; returns device 1's new token_generation."""
    pool = create_pool(settings)
    await pool.open()
    try:
        async with pool.connection() as conn:
            token_hash = (
                hash_token(settings.admin_token.get_secret_value()) if settings.admin_enabled else None
            )
            gen = await q.bind_admin_token(conn, token_hash)
            await conn.commit()
    finally:
        await pool.close()
    if settings.admin_enabled:
        log.info("admin device bound from HLM_ADMIN_TOKEN (token_generation=%d)", gen)
    else:
        log.warning(ADMIN_DISABLED_WARNING)
    return gen


def create_app(
    settings: Settings | None = None,
    *,
    register_rate_limit: int | None = 5,
) -> Starlette:
    settings = settings or get_settings()
    require_pinned_embed_config(settings.embed_model, settings.embed_revision)
    mcp = create_mcp_endpoint(max_request_body_size=settings.request_max_body_bytes)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        require_pinned_embed_config(settings.embed_model, settings.embed_revision)
        model_dir = default_model_dir()
        app.state.embedder = None
        app.state.read_deps = None
        app.state.embedding_deferred = True
        try:
            if all((model_dir / rel).is_file() for rel in MODEL_FILES):
                await _load_shared_embedder(app, settings, model_dir)
        except FileNotFoundError:
            # Files can disappear between the presence check and loading. Keep liveness
            # available; readiness reports the missing paths and retries after its TTL.
            log.warning("model files disappeared during loading; initialization deferred to readiness")
        # Binding runs (and commits) before the pool used for traffic is opened and before uvicorn
        # starts accepting connections — no request can observe a half-bound device 1.
        app.state.admin_generation = await bind_admin_device(settings)
        pool = create_pool(settings)
        await pool.open()
        app.state.pool = pool
        admin_pool = create_pool(settings.model_copy(update={"pool_min_size": 1, "pool_max_size": 2}))
        app.state.admin_pool = admin_pool
        try:
            await admin_pool.open()
            async with mcp.run():  # MCP session manager lives exactly as long as the app
                yield
        finally:
            probe = getattr(app.state, "readiness_probe", None)
            if probe is not None and probe.task is not None:
                await asyncio.shield(probe.task)
            await admin_pool.close()
            await pool.close()
            app.state.read_deps = None
            app.state.embedder = None

    app = Starlette(
        routes=build_routes(mcp),
        middleware=[Middleware(AuthMiddleware)],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.mcp = mcp
    app.state.model_check_cache = {}
    app.state.model_check_lock = asyncio.Lock()
    app.state.cursor_secret = load_cursor_secret()
    app.state.register_limiter = RateLimiter(register_rate_limit) if register_rate_limit else None
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    log.info("hlmemo api: MCP streamable HTTP at /mcp (memory.query/drilldown/raw/write/call_the_day)")
    uvicorn.run(
        create_app(s),
        host=s.api_host,
        port=s.api_port,
        log_level="info",
        limit_concurrency=s.api_limit_concurrency,
        timeout_keep_alive=s.api_timeout_keep_alive,
        proxy_headers=False,  # Registration validates forwarded addresses against trusted_proxy_ips.
    )


if __name__ == "__main__":
    main()
