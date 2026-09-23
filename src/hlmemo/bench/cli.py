"""``hlm bench``: benchmark a librarian model on the production path (W2f, D-017).

    hlm bench --suite v2 --profile openrouter-gpt6-luna --runs 1 --max-usd 0.5 --env-file .env
    hlm bench --suite v2 --model openai/gpt-6-luna --price-in 0.2 --price-out 0.75 --pack docs/private/bench-v2
    hlm bench --suite v1 --profile openrouter --mode replay --cassette-dir tests/cassettes/w2a
    hlm bench --compare bench/results/A.json bench/results/B.json#openai/gpt-6-luna
    hlm bench rescore bench/results/20260923-194459-v2.json --gold adjusted --pack docs/private/bench-v2
    hlm bench leaderboard            # re-render eval/results/LEADERBOARD.md from leaderboard.json

Heavy imports happen inside the commands, so ``hlm --help`` stays fast.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

bench_app = typer.Typer(
    help="Benchmark a librarian model (production prompts, provider, redaction, --max-usd reservation).",
    invoke_without_command=True,
    no_args_is_help=False,
    rich_markup_mode=None,
)

EXIT_CAP = 2  # the --max-usd reservation stopped the run (a FAIL, never a skip)


def _load_env_file(path: str) -> None:
    """KEY=VALUE lines; existing environment wins; nothing is printed."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _default_out() -> Path:
    return Path("bench/results") if Path("bench").is_dir() else Path("hlm-bench-results")


def _stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")


def _progress(out: Any) -> None:
    it = out.item
    score = "-" if out.score is None else f"{out.score:.2f}"
    typer.echo(
        f"  {out.status:<15} {it.task}/{it.case_id} rep{it.rep} score={score} "
        f"{out.latency_ms if out.latency_ms is not None else '-'}ms ${float(out.cost_usd):.5f}",
        err=True,
    )


@bench_app.callback()
def bench(
    ctx: typer.Context,
    profile: Annotated[str | None, typer.Option(help="Profile file name (profiles/<name>.toml).")] = None,
    model: Annotated[str | None, typer.Option(help="Override the profile's model id.")] = None,
    reasoning: Annotated[
        str | None, typer.Option(help="Override the profile's reasoning object (JSON) or 'none'.")
    ] = None,
    price_in: Annotated[float | None, typer.Option(help="USD per 1M input tokens (for --model).")] = None,
    price_out: Annotated[float | None, typer.Option(help="USD per 1M output tokens (for --model).")] = None,
    suite: Annotated[str, typer.Option(help="v1 (T1-T4, production prompts) or v2 (T5-T12).")] = "v2",
    tasks: Annotated[str, typer.Option(help="Comma-separated subset (v1: placement,...; v2: T5,T9).")] = "",
    runs: Annotated[int, typer.Option(min=1, help="Repetitions per case.")] = 1,
    limit: Annotated[int, typer.Option(min=0, help="First K cases per task (smoke).")] = 0,
    max_usd: Annotated[float, typer.Option(help="Run cap, enforced by worst-case reservation.")] = 1.0,
    pack: Annotated[
        list[str] | None, typer.Option(help="v2: extra pack file or directory (repeatable).")
    ] = None,
    no_builtin: Annotated[
        bool, typer.Option("--no-builtin", help="v2: skip the bundled public packs.")
    ] = False,
    gold: Annotated[str, typer.Option(help="v2 gold: adjusted (default) or raw (D-066).")] = "adjusted",
    mode: Annotated[str, typer.Option(help="live | record | replay (strict cassettes).")] = "live",
    cassette_dir: Annotated[Path | None, typer.Option(help="Cassette directory (record/replay).")] = None,
    record_name: Annotated[str, typer.Option(help="Cassette file name when recording.")] = "bench",
    concurrency: Annotated[int, typer.Option(min=1, help="Calls in flight.")] = 4,
    calls_per_day: Annotated[int, typer.Option(min=1, help="$/month projection basis.")] = 60,
    budget_dsn: Annotated[
        str | None, typer.Option(help="Also reserve against this Postgres llm_budget (hour/day/month).")
    ] = None,
    env_file: Annotated[str | None, typer.Option(help="Load KEY=VALUE secrets without echoing.")] = None,
    out: Annotated[str | None, typer.Option(help="Result directory ('' = do not write).")] = None,
    compare: Annotated[
        tuple[str, str] | None,
        typer.Option(help="A B: result files (path.json[#model]) or profiles/models to run; McNemar."),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", help="No per-call progress on stderr.")] = False,
) -> None:
    """Run the bench (no subcommand), or --compare two runs."""
    if ctx.invoked_subcommand is not None:
        return
    from hlmemo.bench.engine import BenchConfig

    if env_file:
        _load_env_file(env_file)
    cfg = BenchConfig(
        suite=suite,
        profile=profile,
        model=model,
        reasoning=reasoning,
        price_in=price_in,
        price_out=price_out,
        tasks=[t.strip() for t in tasks.split(",") if t.strip()] or None,
        runs=runs,
        limit=limit,
        max_usd=max_usd,
        packs=list(pack or []),
        builtin=not no_builtin,
        gold=gold,
        mode=mode,
        cassette_dir=cassette_dir,
        record_name=record_name,
        concurrency=concurrency,
        calls_per_day=calls_per_day,
        budget_dsn=budget_dsn,
    )
    out_dir = _default_out() if out is None else (Path(out) if out.strip() else None)
    if compare and compare[0] is not None:
        raise typer.Exit(_compare(cfg, compare, out_dir, quiet))
    raise typer.Exit(_run_one(cfg, out_dir, quiet))


def _run_one(cfg: Any, out_dir: Path | None, quiet: bool) -> int:
    from hlmemo.bench.engine import run_bench, write_result
    from hlmemo.librarian.errors import LibrarianError

    try:
        res = asyncio.run(run_bench(cfg, progress=None if quiet else _progress))
    except (LibrarianError, ValueError) as exc:
        typer.echo(f"hlm bench: {exc}", err=True)
        return 1
    typer.echo(res.markdown, nl=False)  # stdout == the written .md, byte for byte
    if out_dir is not None:
        jp, mp = write_result(res, out_dir, _stamp())
        typer.echo(f"results -> {jp} , {mp}", err=True)
    typer.echo(f"spend ${res.spent_usd} (cap ${cfg.max_usd})", err=True)
    if res.aborted:
        typer.echo("FAIL: the --max-usd reservation stopped the run", err=True)
        return EXIT_CAP
    return 0


def _rows_for(spec: str, cfg: Any, quiet: bool) -> tuple[list[dict], dict, str]:
    from dataclasses import replace

    from hlmemo.bench import report
    from hlmemo.bench.engine import run_bench
    from hlmemo.config import load_profile

    if Path(spec.partition("#")[0]).is_file():
        rows, meta = report.load_rows(spec)
        return rows, meta, spec
    run_cfg = replace(cfg, profile=spec) if load_profile(spec) else replace(cfg, model=spec)
    res = asyncio.run(run_bench(run_cfg, progress=None if quiet else _progress))
    return res.rows, res.meta, spec


def _compare(cfg: Any, pair: tuple[str, str], out_dir: Path | None, quiet: bool) -> int:
    from hlmemo.bench import report

    rows_a, meta_a, name_a = _rows_for(pair[0], cfg, quiet)
    rows_b, meta_b, name_b = _rows_for(pair[1], cfg, quiet)
    suite = meta_a.get("suite") or cfg.suite
    cmp = report.compare(rows_a, rows_b, suite=suite)
    md = report.render_compare_md(cmp, name_a, name_b)
    typer.echo(md)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{_stamp()}-hlm-compare.md").write_text(md, encoding="utf-8")
    return 0


@bench_app.command("rescore")
def rescore(
    result: Annotated[str, typer.Argument(help="Saved result: path.json or path.json#model.")],
    gold: Annotated[str, typer.Option(help="raw | adjusted")] = "adjusted",
    pack: Annotated[
        list[str] | None, typer.Option(help="Extra v2 pack file/dir (e.g. the private pack).")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the summary JSON.")] = False,
) -> None:
    """Re-score a saved v2 result offline (no LLM) under raw or adjusted gold."""
    from hlmemo.bench import report, v2

    rows, meta = report.load_rows(result)
    packs = v2.load_packs(v2.default_packs() + v2.expand_pack_paths(pack or []), gold=gold)
    rescored, skipped = report.rescore_rows(rows, packs)
    summary = report.summarize(rescored, suite="v2")
    meta = {**meta, "gold": gold if gold == "raw" else v2.overlay_version(), "mode": "rescore"}
    if as_json:
        typer.echo(json.dumps({"meta": meta, "skipped": skipped, "summary": summary}, indent=1))
    else:
        typer.echo(report.render_md(summary, meta))
        typer.echo(f"skipped rows (case not in the loaded packs): {skipped['missing_case']}", err=True)


@bench_app.command("leaderboard")
def leaderboard_cmd(
    path: Annotated[Path, typer.Option(help="leaderboard.json")] = Path("eval/results/leaderboard.json"),
    add: Annotated[
        list[str] | None,
        typer.Option(help="Add a result (hlm bench path.json, or bench/run.py path.json#model); repeatable."),
    ] = None,
    gold: Annotated[
        str, typer.Option(help="v2 entries: raw | adjusted | both (re-scored offline).")
    ] = "both",
    pack: Annotated[list[str] | None, typer.Option(help="Extra v2 pack file/dir (the private pack).")] = None,
    run: Annotated[str | None, typer.Option(help="Run label (default: the result file's stem).")] = None,
    commit: Annotated[str | None, typer.Option(help="Commit of the code that produced the run.")] = None,
    board: Annotated[str | None, typer.Option(help="Librarian board (default bench_<suite>).")] = None,
    baseline: Annotated[
        Path | None, typer.Option(help="(Re)seed the retrieval boards from a W-E baseline JSON.")
    ] = None,
) -> None:
    """Add results / seed baselines, then re-render LEADERBOARD.md next to leaderboard.json."""
    from hlmemo.bench import leaderboard, report, v2

    doc = leaderboard.load(path)
    if baseline is not None:
        doc["retrieval"] = leaderboard.retrieval_from_baseline(
            json.loads(baseline.read_text(encoding="utf-8"))
        )
    for spec in add or []:
        rows, meta = report.load_rows(spec)
        suite = meta.get("suite", "v2")
        label = run or Path(spec.partition("#")[0]).stem
        golds = ["raw", "adjusted"] if gold == "both" else [gold]
        for g in golds if suite == "v2" else ["v1"]:
            if suite == "v2":
                packs = v2.load_packs(v2.default_packs() + v2.expand_pack_paths(pack or []), gold=g)
                scored, skipped = report.rescore_rows(rows, packs)
                if g == "raw" and skipped["missing_case"]:
                    raise typer.BadParameter(
                        f"{spec}: {skipped['missing_case']} rows without a case (pass --pack)"
                    )
            else:
                scored = rows
            summary = report.summarize(scored, suite=suite)
            entry = leaderboard.librarian_entry(
                summary,
                meta,
                gold="raw" if g == "raw" else (v2.overlay_version() if g == "adjusted" else g),
                run=label,
                source=Path(spec.partition("#")[0]).name,
                commit=commit or meta.get("commit"),
            )
            leaderboard.add_librarian(doc, board or f"bench_{suite}", entry)
    leaderboard.save(path, doc)
    md = path.with_name("LEADERBOARD.md")
    md.write_text(leaderboard.render_md(doc), encoding="utf-8")
    typer.echo(f"rendered {md}", err=True)


def main() -> None:  # python -m hlmemo.bench.cli
    bench_app(prog_name="hlm bench")


if __name__ == "__main__":
    sys.exit(main())
