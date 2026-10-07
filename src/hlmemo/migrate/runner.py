"""`hlm migrate run|verify`: import the batches, oldest first, with the guards the first migrations needed.

- A dry run classifies against the target and writes nothing. It may only show `new`, and `missing`: the items
  of other batches are simply not in this run (keep-missing semantics).
- `--apply` runs the same batch's dry run immediately before the write and stops unless that dry run passes.
  The write itself stops on any failed, rejected, skipped, changed or closed count, or when it wrote fewer
  items than the dry run called new.
- `--resume` lets an apply continue a batch that an interrupted apply partly wrote (`unchanged` allowed).
- `verify` expects every batch to classify as `unchanged`.
- prod needs a valid seal and `HLM_MIGRATE_ALLOW_PROD=1`; local needs a loopback server.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from hlmemo.importers.common import ParseResult
from hlmemo.migrate import seal as sealmod
from hlmemo.migrate.batches import Loaded, batch_keys, subset
from hlmemo.migrate.spec import MigrationSpec, SpecError, Target

ALLOW_PROD_ENV = "HLM_MIGRATE_ALLOW_PROD"

Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
#: (call, importer, parsed batch, slug, dry_run) -> the importer's report
ImportFn = Callable[[Call, str, ParseResult, str, bool], Awaitable[dict[str, Any]]]
SessionFactory = Callable[[Target], contextlib.AbstractAsyncContextManager[Call]]


class RunRefused(RuntimeError):
    """A precondition failed before anything was sent (target, seal, environment)."""


class HardStop(RuntimeError):
    """A batch classified or wrote outside its allowed counts; nothing further was sent."""

    def __init__(self, message: str, report: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.report = report


def hard_stop(
    line: dict[str, Any], mode: str, *, verify: bool = False, resume: bool = False
) -> dict[str, Any]:
    """The counts that stop a run (`mode` is `dry` or `apply`); `missing` never stops one. `resume` also
    allows `unchanged` in the dry run before an apply: the part an interrupted apply already wrote."""
    c = line.get("counts") or {}
    if mode == "dry":
        if verify:
            allowed: tuple[str, ...] = ("unchanged", "missing")
        elif resume:
            allowed = ("new", "unchanged", "missing")
        else:
            allowed = ("new", "missing")
        bad = {k: v for k, v in c.items() if v and k not in allowed}
    else:
        bad = {
            k: v for k, v in c.items() if v and k in ("failed", "rejected", "skipped", "closed", "changed")
        }
        if line.get("failed"):
            bad["write_failed"] = line["failed"]
        expect = c.get("new", 0)
        if line.get("written") is not None and line["written"] != expect:
            bad["written_vs_new"] = f"{line['written']}!={expect}"
    return bad


def check_target(spec: MigrationSpec, name: str, loaded: list[Loaded]) -> Target:
    """Refuse before any network call. The target must exist; local must be loopback; prod needs the env
    opt-in and a seal that matches the tree and the parse."""
    try:
        target = spec.target(name)
    except SpecError as exc:
        raise RunRefused(str(exc)) from None
    if name == "local" and not target.is_loopback:
        raise RunRefused(f"local target {target.server} is not a loopback URL")
    if name == "prod":
        if os.environ.get(ALLOW_PROD_ENV) != "1":
            raise RunRefused(f"prod needs {ALLOW_PROD_ENV}=1 (and the owner's OK on the review package)")
        verdict = sealmod.verify(spec, loaded)
        if not verdict.ok:
            raise RunRefused("the tree does not match its seal: " + "; ".join(verdict.problems[:5]))
    return target


async def default_import(
    call: Call, importer: str, parsed: ParseResult, slug: str, dry_run: bool
) -> dict[str, Any]:
    from hlmemo.importers.cli import import_async

    return await import_async(
        call, source=importer, parsed=parsed, project=slug, dry_run=dry_run, close=False, progress=False
    )


@contextlib.asynccontextmanager
async def default_session(target: Target) -> AsyncIterator[Call]:
    """An MCP session to the target. It never reads hlm.toml: the token comes from HLM_DEVICE_TOKEN or the
    credential store for (server, device)."""
    from hlmemo.cli import credentials
    from hlmemo.cli.client_config import mcp_url
    from hlmemo.cli.mcp_client import MemoryClient

    token = credentials.load_token(target.server, target.device)
    if not token:
        raise RunRefused(f"no token for device {target.device!r} at {target.server} (set HLM_DEVICE_TOKEN)")
    async with MemoryClient(mcp_url(target.server), token, timeout_s=60.0).session() as call:
        yield call


def _line(key: str, ld: Loaded, sub: ParseResult, rep: dict[str, Any], mode: str) -> dict[str, Any]:
    w = rep.get("writes") or {}
    return {
        "batch": key,
        "source": ld.source.importer,
        "mode": mode,
        "items": len(sub.records),
        "counts": rep.get("counts") or {},
        "written": w.get("written"),
        "failed": w.get("failed"),
        "tokens": (rep.get("token_estimate") or {}).get("tokens"),
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
    importer: ImportFn | None = None,
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
    importer = importer or default_import
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
                dry = _line(
                    key, ld, sub, await importer(call, ld.source.importer, sub, spec.slug, True), "dry"
                )
                record(dry)
                bad = hard_stop(dry, "dry", verify=verify, resume=resume)
                if bad:
                    stop = f"batch {key} ({ld.source.importer}) dry run: {bad}"
                    break
                if not apply:
                    continue
                done = _line(
                    key, ld, sub, await importer(call, ld.source.importer, sub, spec.slug, False), "apply"
                )
                record(done)
                bad = hard_stop(done, "apply")
                if bad:
                    stop = f"batch {key} ({ld.source.importer}) apply: {bad}"
                    break
    if stop:
        raise HardStop(stop, report)
    return report


__all__ = [
    "ALLOW_PROD_ENV",
    "HardStop",
    "RunRefused",
    "check_target",
    "default_import",
    "default_session",
    "hard_stop",
    "run",
]
