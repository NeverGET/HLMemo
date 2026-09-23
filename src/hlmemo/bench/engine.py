"""``hlm bench`` orchestration: resolve the profile, build the items, run them, summarize (W2f).

Budget choice (documented in bench/README): the run's ``--max-usd`` cap is the production
reservation contract in process (``hlmemo.librarian.budget.MemoryBudget``: the provider reserves the
worst case ``ceil(input × 1.10) × price_in + max_tokens × price_out`` before every network attempt and
settles the actual cost after it), so a user needs no database to bench a model. ``--budget-dsn``
additionally reserves every attempt against a Postgres ``llm_budget`` (the deployment's hour / day /
month windows, ``DbBudget``), chained after the in-process cap.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from hlmemo.bench import report, v1, v2
from hlmemo.bench.runner import ChainBudget, Item, Outcome, run_items
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.errors import LlmConfigError
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile, named_profile, primary_profile
from hlmemo.librarian.prompts import TaskSpec, load_task
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import REDACTION_VERSION, Redactor
from hlmemo.librarian.tasks import JOB_NAMES, user_message

REPO_GUESS = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class BenchConfig:
    suite: str = "v2"
    profile: str | None = None  # profile file name; None = the configured primary profile
    model: str | None = None  # override the profile's model id
    reasoning: str | None = None  # None = keep the profile's; "none" = drop; else a JSON object
    price_in: float | None = None  # USD per 1M tokens (needed when --model has no known price)
    price_out: float | None = None
    tasks: list[str] | None = None
    runs: int = 1
    limit: int = 0
    max_usd: float = 1.0
    packs: list[str] = field(default_factory=list)  # v2: extra pack files/dirs (e.g. the private pack)
    builtin: bool = True  # v2: include the bundled public packs
    gold: str = "adjusted"  # v2: raw | adjusted (the adjudication overlay)
    mode: str = "live"  # live | record | replay
    cassette_dir: Path | None = None
    record_name: str = "bench"
    concurrency: int = 4
    calls_per_day: int = 60
    budget_dsn: str | None = None
    timeout_s: float = 120.0


@dataclass(slots=True)
class BenchResult:
    rows: list[dict[str, Any]]
    summary: dict[str, Any]
    meta: dict[str, Any]
    markdown: str
    aborted: bool
    spent_usd: Decimal
    ledger: list[dict[str, Any]]


# --------------------------------------------------------------------------- profile
def catalogue_price(model: str) -> tuple[float, float] | None:
    """USD/1M prices for ``model`` from the bench catalogue (``bench/models.json``), if present."""
    for base in (Path.cwd(), REPO_GUESS):
        p = base / "bench" / "models.json"
        if p.is_file():
            for alias, v in json.loads(p.read_text(encoding="utf-8")).items():
                if model in (alias, v.get("id")) and v.get("prompt_usd_per_m") is not None:
                    return float(v["prompt_usd_per_m"]), float(v.get("completion_usd_per_m") or 0)
    return None


def resolve_profile(cfg: BenchConfig) -> LlmProfile:
    if cfg.profile:
        prof = named_profile(cfg.profile)
    else:
        from hlmemo.config import get_settings

        prof = primary_profile(get_settings())
    changes: dict[str, Any] = {}
    if cfg.model and cfg.model != prof.model_id:
        changes["model_id"] = cfg.model
        changes["name"] = f"{prof.name}:{cfg.model}"
        found = catalogue_price(cfg.model)
        changes["price_in_per_m"] = Decimal(str(found[0])) if found else None
        changes["price_out_per_m"] = Decimal(str(found[1])) if found else None
    if cfg.reasoning is not None:
        changes["reasoning"] = None if cfg.reasoning.strip().lower() == "none" else json.loads(cfg.reasoning)
    if cfg.price_in is not None:
        changes["price_in_per_m"] = Decimal(str(cfg.price_in))
    if cfg.price_out is not None:
        changes["price_out_per_m"] = Decimal(str(cfg.price_out))
    prof = dataclasses.replace(prof, **changes) if changes else prof
    if cfg.mode != "replay" and not prof.priced:
        raise LlmConfigError(
            f"profile {prof.name!r} has no prices: pass --price-in/--price-out (USD per 1M tokens) so the"
            " --max-usd reservation can compute the worst case"
        )
    return prof


# --------------------------------------------------------------------------- items
def _safe(fn):  # noqa: ANN001, ANN202 - a validator must never crash the provider loop
    def check(obj: dict[str, Any]) -> str | None:
        try:
            return fn(obj)
        except Exception as exc:  # noqa: BLE001 - a malformed answer is a schema failure
            return f"validator error: {type(exc).__name__}: {exc}"[:300]

    return check


def v1_items(cfg: BenchConfig) -> tuple[list[Item], dict[str, Any]]:
    tasks = [t for t in v1.TASKS if not cfg.tasks or t in cfg.tasks]
    unknown = set(cfg.tasks or []) - set(v1.TASKS)
    if unknown:
        raise ValueError(f"unknown v1 tasks {sorted(unknown)}; use {v1.TASKS}")
    fixtures = {t: v1.load_fixture(t) for t in tasks}
    specs = {t: load_task(t) for t in tasks}
    items: list[Item] = []
    for rep in range(cfg.runs):
        for t in tasks:
            fx = fixtures[t]
            cases = fx["cases"][: cfg.limit] if cfg.limit else fx["cases"]
            for case in cases:
                items.append(
                    Item(
                        suite="v1",
                        task=t,
                        job=JOB_NAMES[t],
                        case_id=case["id"],
                        tier=None,
                        pack="v1",
                        rep=rep,
                        spec=specs[t],
                        user=user_message(t, v1.case_payload(t, case)),
                        validate=_safe(v1.semantic_check(t, case)),
                        scorer=lambda obj, t=t, case=case, fx=fx: v1.score(t, obj, case, fx),
                    )
                )
    meta = {
        "packs": "v1 fixtures",
        "fixtures_sha256": {t: v1.fixture_sha256(t) for t in tasks},
        "prompt_versions": {t: specs[t].prompt_version for t in tasks},
        "schema_versions": {t: specs[t].schema_version for t in tasks},
        "gold": "v1",
    }
    return items, meta


def v2_spec(job: str, max_tokens: int, cache: dict[tuple[str, int], TaskSpec]) -> TaskSpec:
    key = (job, max_tokens)
    if key not in cache:
        cache[key] = TaskSpec(
            name=f"bench_v2_{job}",
            prompt_version=v2.PROMPT_VERSION,
            schema_version=v2.SCHEMA_VERSION,
            system=v2.SYSTEM_PROMPT_V2,
            schema=v2.SCHEMAS[job],
            max_tokens=max_tokens,
        )
    return cache[key]


def v2_items(cfg: BenchConfig) -> tuple[list[Item], dict[str, Any]]:
    paths = (v2.default_packs() if cfg.builtin else []) + v2.expand_pack_paths(cfg.packs)
    if not paths:
        raise ValueError("no v2 packs selected")
    only = set(cfg.tasks) if cfg.tasks else None
    packs = v2.load_packs(paths, only, gold=cfg.gold)
    cache: dict[tuple[str, int], TaskSpec] = {}
    items: list[Item] = []
    for rep in range(cfg.runs):
        for fam, pack in packs:
            cases = pack["cases"][: cfg.limit] if cfg.limit else pack["cases"]
            for case in cases:
                job = v2.job_of(fam, case, pack)
                items.append(
                    Item(
                        suite="v2",
                        task=fam,
                        job=job,
                        case_id=case["id"],
                        tier=case.get("tier"),
                        pack=str(pack.get("pack", "public")),
                        rep=rep,
                        spec=v2_spec(job, v2.max_tokens_for(fam, case), cache),
                        user=v2.build_user_message(fam, case, pack),
                        validate=_safe(lambda obj, f=fam, c=case, p=pack: v2.validate(f, obj, c, p)),
                        scorer=lambda obj, f=fam, c=case, p=pack: v2.score(f, obj, c, p, raw=v2.raw_of(obj)),
                    )
                )
    pack_names = sorted({f"{Path(p['_path']).name}@v{p.get('version', 1)}" for _, p in packs})
    meta = {
        "packs": ", ".join(sorted({str(p.get("pack")) for _, p in packs})),
        "pack_files": pack_names,
        "prompt_versions": f"{v2.PROMPT_VERSION} (sha256 {v2.PROMPT_SHA256[:12]})",
        "schema_versions": v2.SCHEMA_VERSION,
        "gold": "raw" if cfg.gold == "raw" else v2.overlay_version(),
    }
    return items, meta


# --------------------------------------------------------------------------- run
def to_row(o: Outcome) -> dict[str, Any]:
    it = o.item
    usage = o.usage or {}
    details = usage.get("completion_tokens_details") or {}
    pdetails = usage.get("prompt_tokens_details") or {}
    return {
        "suite": it.suite,
        "task": it.task,
        "job": it.job,
        "case_id": it.case_id,
        "tier": it.tier,
        "pack": it.pack,
        "rep": it.rep,
        "status": o.status,
        "score": o.score,
        "detail": o.detail,
        "latency_ms": o.latency_ms,
        "cost_usd": float(o.cost_usd),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cached_tokens": pdetails.get("cached_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "attempts": o.attempts,
        "outcomes": o.outcomes,
        "first_json_fail": o.first_json_fail,
        "error": o.error,
        "output": o.output,
    }


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _budget(cfg: BenchConfig) -> tuple[MemoryBudget, Any]:
    cap = MemoryBudget(Decimal(str(cfg.max_usd)))
    if not cfg.budget_dsn:
        return cap, cap
    import contextlib

    import psycopg

    from hlmemo.config import get_settings
    from hlmemo.librarian.budget import Caps, DbBudget

    dsn = cfg.budget_dsn

    @contextlib.asynccontextmanager
    async def conn():  # noqa: ANN202
        c = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        try:
            yield c
        finally:
            await c.close()

    db = DbBudget(conn, Caps.from_settings(get_settings()))
    return cap, ChainBudget([cap, db])


async def run_bench(
    cfg: BenchConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    progress: Any = None,
) -> BenchResult:
    if cfg.suite not in ("v1", "v2"):
        raise ValueError(f"suite must be v1|v2, not {cfg.suite!r}")
    if cfg.mode not in ("live", "record", "replay"):
        raise ValueError(f"mode must be live|record|replay, not {cfg.mode!r}")
    profile = resolve_profile(cfg)
    items, meta = v1_items(cfg) if cfg.suite == "v1" else v2_items(cfg)
    cassettes = None
    if cfg.mode != "live":
        if cfg.cassette_dir is None:
            raise LlmConfigError(f"--mode {cfg.mode} needs --cassette-dir")
        cassettes = CassetteStore(Path(cfg.cassette_dir), record_name=cfg.record_name, redactor=Redactor())
    cap, guard = _budget(cfg)
    ledger = MemoryLedger()
    provider = Provider(
        [profile],
        mode=cfg.mode,
        budget=guard,
        ledger=ledger,
        cassettes=cassettes,
        transport=transport,
        redactor=Redactor(),
        timeout_s=cfg.timeout_s,
    )
    try:
        outcomes = await run_items(items, provider, ledger, concurrency=cfg.concurrency, progress=progress)
    finally:
        await provider.aclose()
    rows = [to_row(o) for o in outcomes]
    aborted = any(r["status"] == "budget_deferred" for r in rows)
    summary = report.summarize(rows, suite=cfg.suite, calls_per_day=cfg.calls_per_day)
    meta = {
        "suite": cfg.suite,
        "profile": profile.name,
        "model_id": profile.model_id,
        "reasoning": profile.reasoning,
        "mode": cfg.mode,
        "reps": cfg.runs,
        "limit": cfg.limit,
        "max_usd": cfg.max_usd,
        "redaction_version": REDACTION_VERSION,
        "aborted": aborted,
        **meta,
    }
    md = report.render_md(summary, meta)
    ledger_rows = ledger.as_dicts()
    for r in ledger_rows:  # call ids are random per run; keep the report reproducible
        r.pop("call_id", None)
    return BenchResult(rows, summary, meta, md, aborted, cap.spent + cap.reserved, ledger_rows)


def write_result(res: BenchResult, out_dir: Path, stamp: str) -> tuple[Path, Path]:
    """``<stamp>-hlm-<suite>-<model>.{json,md}``. The JSON keeps outputs (it may hold private-case
    answers: bench/results/*.json is gitignored); the markdown holds aggregates only."""
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = str(res.meta["model_id"]).replace("/", "_").replace(":", "_")
    base = f"{stamp}-hlm-{res.meta['suite']}-{slug}"
    doc = {
        "meta": {**res.meta, "commit": git_commit(), "stamp": stamp, "spent_usd": str(res.spent_usd)},
        "summary": res.summary,
        "rows": res.rows,
        "ledger": res.ledger,
    }
    jp, mp = out_dir / f"{base}.json", out_dir / f"{base}.md"
    jp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    mp.write_text(res.markdown, encoding="utf-8")
    return jp, mp


__all__ = ["BenchConfig", "BenchResult", "catalogue_price", "resolve_profile", "run_bench", "write_result"]
