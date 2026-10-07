"""`hlm migrate run|verify`: import the batches, oldest first, with the guards the first migrations needed.

- Each batch is classified ONCE against the target. The classification may only show `new`, and `missing`
  (the items of other batches are simply not in this run: keep-missing semantics); `verify` expects
  `unchanged`; `--resume` also allows `unchanged` (the part of a batch an interrupted apply already wrote).
- `--apply` writes exactly the plan whose classification was checked, never a re-classification (review 113):
  right before the write it re-reads the open source keys and stops if a planned new key appeared meanwhile,
  and the write itself treats a version conflict as a failure instead of turning a new record into a revision.
- The write stops on any failed, rejected, skipped, changed or closed count, or when it wrote fewer items than
  the plan called new.
- prod needs a valid seal and `HLM_MIGRATE_ALLOW_PROD=1`; local needs a loopback server.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from hlmemo.importers.common import ParseResult
from hlmemo.migrate import seal as sealmod
from hlmemo.migrate.batches import Loaded, batch_keys, subset
from hlmemo.migrate.spec import MigrationSpec, SpecError, Target

ALLOW_PROD_ENV = "HLM_MIGRATE_ALLOW_PROD"

Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
SessionFactory = Callable[[Target], contextlib.AbstractAsyncContextManager[Call]]


class RunRefused(RuntimeError):
    """A precondition failed before anything was sent (target, seal, environment)."""


class HardStop(RuntimeError):
    """A batch classified or wrote outside its allowed counts; nothing further was sent."""

    def __init__(self, message: str, report: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.report = report


@dataclass
class Classified:
    """One batch's classification: the plan that may be written, and its report."""

    plan: Any
    manifest: list[dict[str, Any]]
    report: dict[str, Any]
    new_keys: frozenset[str]


class Engine(Protocol):
    async def classify(self, call: Call, importer: str, parsed: ParseResult, slug: str) -> Classified: ...

    async def open_keys(self, call: Call, slug: str) -> set[str]: ...

    async def write(self, call: Call, c: Classified) -> dict[str, Any]: ...


class ImporterEngine:
    """The real importer, split so that the checked classification is the one that is written."""

    def __init__(self) -> None:
        from hlmemo.core.budget import Meter

        self.meter = Meter()

    async def classify(self, call: Call, importer: str, parsed: ParseResult, slug: str) -> Classified:
        from hlmemo.importers.plan import classify, report
        from hlmemo.importers.runner import fetch_items, resolve_missing

        manifest, _as_of = await fetch_items(call, slug)
        plan = classify(slug, importer, parsed, manifest, self.meter)
        await resolve_missing(call, plan, confirm_close=False)
        # keep-missing (as `hlm import --keep-missing`): vanished sources are kept, not closed
        replaced = {it["logical_id"] for it in plan.replaced_items}
        plan.kept += [
            {"key": f"{it['source']['system']}:{it['source']['path']}", "reason": "keep-missing"}
            for it in plan.closes
            if it["logical_id"] not in replaced
        ]
        plan.closes = [it for it in plan.closes if it["logical_id"] in replaced]
        new = frozenset(e.record.key for e in plan.entries if e.action == "new")
        return Classified(plan, manifest, report(plan, dry_run=True), new)

    async def open_keys(self, call: Call, slug: str) -> set[str]:
        from hlmemo.importers.plan import source_key
        from hlmemo.importers.runner import fetch_items

        items, _as_of = await fetch_items(call, slug)
        return {k for it in items if it.get("valid_to") is None for k in [source_key(it.get("source"))] if k}

    async def write(self, call: Call, c: Classified) -> dict[str, Any]:
        from hlmemo.importers.runner import run_import

        return await run_import(
            call,
            c.plan,
            manifest=c.manifest,
            meter=self.meter,
            progress=False,
            close=False,
            reclassify_on_conflict=False,
        )


def check_counts(counts: dict[str, int], *, verify: bool = False, resume: bool = False) -> dict[str, int]:
    """The classification counts that stop a run; `missing` never stops one."""
    if verify:
        allowed: tuple[str, ...] = ("unchanged", "missing")
    elif resume:
        allowed = ("new", "unchanged", "missing")
    else:
        allowed = ("new", "missing")
    return {k: v for k, v in counts.items() if v and k not in allowed}


def check_writes(planned_new: int, writes: dict[str, Any]) -> dict[str, Any]:
    """The write results that stop a run."""
    bad: dict[str, Any] = {}
    failed = writes.get("failed") or []
    if failed:
        bad["write_failed"] = len(failed)
    for k in ("revisions", "closed", "link_revisions"):
        if writes.get(k):
            bad[k] = writes[k]
    written = (writes.get("written") or 0) + (writes.get("replayed") or 0)
    if written != planned_new:
        bad["written_vs_new"] = f"{written}!={planned_new}"
    return bad


def check_target(spec: MigrationSpec, name: str, loaded: list[Loaded]) -> Target:
    """Refuse before any network call. The target must exist; local must be loopback; prod needs the env
    opt-in and a seal that matches the tree and the parse."""
    try:
        target = spec.target(name)
    except SpecError as exc:
        raise RunRefused(str(exc)) from None
    if name == "local" and not target.is_loopback:
        raise RunRefused("the local target is not a loopback URL")
    if name == "prod":
        if os.environ.get(ALLOW_PROD_ENV) != "1":
            raise RunRefused(f"prod needs {ALLOW_PROD_ENV}=1 (and the owner's OK on the review package)")
        verdict = sealmod.verify(spec, loaded)
        if not verdict.ok:
            raise RunRefused("the tree does not match its seal: " + "; ".join(verdict.problems[:5]))
    return target


@contextlib.asynccontextmanager
async def default_session(target: Target) -> AsyncIterator[Call]:
    """An MCP session to the target. It never reads hlm.toml: the token comes from HLM_DEVICE_TOKEN or the
    credential store for (server, device)."""
    from hlmemo.cli import credentials
    from hlmemo.cli.client_config import mcp_url
    from hlmemo.cli.mcp_client import MemoryClient

    token = credentials.load_token(target.server, target.device)
    if not token:
        raise RunRefused(
            f"no token for device {target.device!r} at the {target.name} target (set HLM_DEVICE_TOKEN)"
        )
    async with MemoryClient(mcp_url(target.server), token, timeout_s=60.0).session() as call:
        yield call


def _line(key: str, ld: Loaded, sub: ParseResult, c: Classified, mode: str) -> dict[str, Any]:
    return {
        "batch": key,
        "source": ld.source.importer,
        "mode": mode,
        "items": len(sub.records),
        "counts": dict(c.report.get("counts") or {}),
        "tokens": (c.report.get("token_estimate") or {}).get("tokens"),
    }


async def run(
    spec: MigrationSpec,
    loaded: list[Loaded],
    target_name: str,
    *,
    batch: str | None = None,
    apply: bool = False,
    verify: bool = False,
    resume: bool = False,
    session: SessionFactory | None = None,
    engine: Engine | None = None,
    log: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    if apply and verify:
        raise RunRefused("verify never writes: drop --apply")
    if resume and not apply:
        raise RunRefused("--resume only applies to --apply")
    target = check_target(spec, target_name, loaded)
    keys = batch_keys(loaded)
    if batch is not None and batch not in keys:
        raise RunRefused(f"unknown batch {batch!r}; batches are {keys}")
    session = session or default_session
    engine = engine or ImporterEngine()
    report: list[dict[str, Any]] = []

    def record(line: dict[str, Any]) -> None:
        report.append(line)
        if log:
            log(line)

    # a stop is raised AFTER the session closed: an exception inside the MCP session would be reported as a
    # transport failure by the client
    stop: str | None = None
    async with session(target) as call:
        for key in keys:
            if stop or (batch is not None and key != batch):
                continue
            for ld in loaded:
                sub = subset(ld, key)
                if not sub.records:
                    continue
                where = f"batch {key} ({ld.source.importer})"
                c = await engine.classify(call, ld.source.importer, sub, spec.slug)
                line = _line(key, ld, sub, c, "verify" if verify else "check")
                record(line)
                bad = check_counts(line["counts"], verify=verify, resume=resume)
                if bad:
                    stop = f"{where} classification: {bad}"
                    break
                if not apply:
                    continue
                appeared = sorted(c.new_keys & await engine.open_keys(call, spec.slug))
                if appeared:
                    stop = (
                        f"{where}: {len(appeared)} planned new key(s) appeared on the server since the check"
                    )
                    record({**line, "mode": "apply", "written": 0, "appeared": len(appeared)})
                    break
                writes = await engine.write(call, c)
                done = {
                    **line,
                    "mode": "apply",
                    "written": writes.get("written"),
                    "failed": len(writes.get("failed") or []),
                }
                record(done)
                bad = check_writes(len(c.new_keys), writes)
                if bad:
                    stop = f"{where} apply: {bad}"
                    break
    if stop:
        raise HardStop(stop, report)
    return report


__all__ = [
    "ALLOW_PROD_ENV",
    "Classified",
    "Engine",
    "HardStop",
    "ImporterEngine",
    "RunRefused",
    "check_counts",
    "check_target",
    "check_writes",
    "default_session",
    "run",
]
