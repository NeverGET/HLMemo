"""Pre-registration of the ceiling experiment (PLAN §3: the rubric and the bars are hashed before any
arm runs).

``write`` renders ``<private>/PREREG.md``: the rubric (the reader instructions, verbatim), the
go/no-go bars and rules, the arm configuration (profile, model, prices, CLI flags, runs, spend cap),
and the sha256 of every input the arms and the scorer depend on (config, task prompts, schemas,
reader instructions, profile file, every packet, every must-know file), plus the harness commit. A
machine-readable copy sits in the one fenced ``json`` block. ``PREREG.sha256`` holds the file's
sha256. It refuses to overwrite a pre-registration once any arm output exists.

``verify`` is called by every runner and by the scorer: it recomputes the file hash and every
recorded input hash and refuses on any difference (a changed prompt, packet, bar or rubric after the
pre-registration voids the run).
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import al_common as C

PREREG = "PREREG.md"
PREREG_SHA = "PREREG.sha256"
_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.S)


def _git_head() -> dict[str, Any]:
    def git(*args: str) -> str:
        proc = subprocess.run(["git", "-C", str(C.ROOT), *args], capture_output=True, text=True, check=False)  # noqa: S603,S607
        return proc.stdout.strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty_harness": bool(git("status", "--porcelain", "--", "eval/active")),
    }


def _claude_version(cli: str) -> str | None:
    try:
        proc = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None


def profile_file(name: str) -> Path | None:
    for d in (C.ROOT / "profiles",):
        p = d / f"{name}.toml"
        if p.is_file():
            return p
    return None


def mustknow_dir() -> Path:
    return C.pdir("mustknow")


def packet_hashes(exp: str) -> dict[str, str]:
    d = C.packets_dir(exp)
    m = d / "manifest.json"
    if not m.is_file():
        return {}
    manifest = C.read_json(m)
    if exp == "E0":
        return {manifest["file"]: C.sha256_file(d / manifest["file"])}
    return {row["file"]: C.sha256_file(d / row["file"]) for row in manifest["packets"]}


def inputs(cfg_path: Path | None = None) -> dict[str, Any]:
    """Every hashed input, as it is NOW."""
    cfg_path = cfg_path or C.CONFIG_PATH
    cfg = C.load_config(cfg_path)
    out: dict[str, Any] = {
        "config_sha256": C.sha256_file(cfg_path),
        "reader_instructions_sha256": C.sha256_file(C.READER_TEMPLATE),
        "prompts": {},
        "packets": {exp: packet_hashes(exp) for exp in C.EXPERIMENTS},
        "mustknow": {
            f.name: C.sha256_file(f)
            for f in sorted(mustknow_dir().glob("*.json"))
            if f.parent == mustknow_dir()
        },
    }
    for exp in C.ARM_EXPERIMENTS:
        md, schema = C.prompt_files(exp)
        out["prompts"][exp] = {"prompt_sha256": C.sha256_file(md), "schema_sha256": C.sha256_file(schema)}
    pf = profile_file(cfg["arms"]["gemini"]["profile"])
    out["profile_sha256"] = C.sha256_file(pf) if pf else None
    return out


def arm_config(cfg: dict[str, Any]) -> dict[str, Any]:
    from hlmemo.librarian.profiles import named_profile

    g = cfg["arms"]["gemini"]
    prof = named_profile(g["profile"])
    o = cfg["arms"]["opus"]
    return {
        "gemini": {
            **g,
            "model_id": prof.model_id,
            "base_url": prof.base_url,
            "extra": prof.extra,
            "price_in_per_m": str(prof.price_in_per_m),
            "price_out_per_m": str(prof.price_out_per_m),
            "price_valid_until": str(prof.price_valid_until),
            "usage_reasoning": prof.usage_reasoning,
        },
        "opus": {**o, "cli_version": _claude_version(o.get("cli", "claude"))},
        "max_tokens": cfg["max_tokens"],
        "spend_cap_usd": cfg["spend_cap_usd"],
    }


def _outputs_exist() -> bool:
    d = C.private_dir() / "outputs"
    return d.is_dir() and any(p.is_file() for p in d.rglob("*.json"))


def render(record: dict[str, Any], rubric: str) -> str:
    bars = record["bars"]
    lines = [
        "# Active librarian ceiling experiment: pre-registration",
        "",
        f"Written {record['written_at']} before any arm ran. The sha256 of this file is in {PREREG_SHA};",
        "every runner and the scorer verify it and every input hash below, and refuse on a difference.",
        "",
        "## Go/no-go bars (PLAN §3)",
        "",
        f"- harmful-stale on hiding/closing units: at most {bars['harmful_stale_hiding_max']};"
        f" on other units: at most {bars['harmful_stale_other_max_rate']:.0%}",
        f"- grounded >= {bars['grounded_min']}, correct >= {bars['correct_min']}",
        "- useful >= " + ", ".join(f"{k} {v}" for k, v in bars["useful_min"].items()),
        "- coverage >= " + ", ".join(f"{k} {v}" for k, v in bars["coverage_min"].items()),
        f"- Opus at most {bars['opus_margin_max']} better on {', '.join(bars['opus_margin_metrics'])}",
        f"- E0: curated precision >= {bars['e0']['precision_go']} with at most"
        f" {bars['e0']['false_invalidations_max']} false invalidations -> AL2 promotion becomes a"
        f" measurement exercise; below {bars['e0']['precision_floor']} -> AL2 stays observer",
        "",
        "## Rules",
        "",
        *(f"- {k}: {v}" for k, v in record["rules"].items()),
        "",
        "## Arms",
        "",
        f"- Gemini: profile {record['arms']['gemini']['profile']} ({record['arms']['gemini']['model_id']}),"
        f" {record['arms']['gemini']['runs']} runs, via the product Provider",
        f"- Opus: `{record['arms']['opus']['cli']} -p --model {record['arms']['opus']['model']}"
        f" --output-format stream-json` effort {record['arms']['opus'].get('effort')},"
        f" {record['arms']['opus']['runs']} run(s); CLI {record['arms']['opus'].get('cli_version')}",
        f"- max_tokens {record['arms']['max_tokens']}; hard spend cap ${record['arms']['spend_cap_usd']}",
        "",
        "## Rubric (reader instructions, verbatim)",
        "",
        rubric.strip(),
        "",
        "## Machine-readable record",
        "",
        "```json",
        json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False),
        "```",
        "",
    ]
    return "\n".join(lines)


def write(*, force: bool = False) -> tuple[Path, str]:
    if _outputs_exist():
        raise C.HarnessError("arm outputs already exist: a pre-registration must precede every run")
    target = C.ensure_private(C.private_dir() / PREREG)
    if target.is_file() and not force:
        raise C.HarnessError(f"{target} exists (pass --force to rewrite it; no arm has run yet)")
    cfg = C.load_config()
    ins = inputs()
    missing = [exp for exp in C.EXPERIMENTS if not ins["packets"][exp]]
    if missing:
        raise C.HarnessError(f"build the packets first (missing: {', '.join(missing)})")
    check_mustknow(int(cfg["selection"]["E2"]["must_know"]))
    record = {
        "schema": "al-prereg/1",
        "written_at": datetime.now(UTC).isoformat(),
        "harness": _git_head(),
        "bars": cfg["bars"],
        "rules": cfg["rules"],
        "grading": cfg["grading"],
        "selection": cfg["selection"],
        "arms": arm_config(cfg),
        "inputs": ins,
        "mustknow_required": {"E2": sorted(f"{p}.json" for p in _e2_packet_ids())},
    }
    text = render(record, C.READER_TEMPLATE.read_text(encoding="utf-8"))
    C.write_text(target, text)
    digest = C.sha256_file(target)
    C.write_text(C.private_dir() / PREREG_SHA, f"{digest}  {PREREG}\n")
    return target, digest


def check_mustknow(n: int) -> None:
    """Every E2 packet has its must-know file (written beforehand by an independent agent, PLAN §3)
    with exactly ``n`` facts, each with an id, a fact and a quote that occurs verbatim in the cited
    source of the packet."""
    from al_grounding import quote_status

    problems = []
    for pid in _e2_packet_ids():
        f = mustknow_dir() / f"{pid}.json"
        if not f.is_file():
            problems.append(f"{pid}: missing")
            continue
        facts = C.read_json(f).get("facts") or []
        if len(facts) != n or len({x.get("id") for x in facts}) != n:
            problems.append(f"{pid}: {len(facts)} facts (need {n} with distinct ids)")
            continue
        sources = {
            s["handle"]: s["text"] for s in C.read_json(C.packets_dir("E2") / f"{pid}.json")["sources"]
        }
        bad = [
            x.get("id")
            for x in facts
            if quote_status(str(x.get("quote", "")), sources.get(str(x.get("source")), "")) != "verbatim"
        ]
        if bad:
            problems.append(f"{pid}: quotes not verbatim in the cited source for {bad}")
    if problems:
        raise C.HarnessError(
            "must-know facts not ready (mustknow-kit -> independent agent): " + "; ".join(problems)
        )


def _e2_packet_ids() -> list[str]:
    d = C.packets_dir("E2")
    m = d / "manifest.json"
    return [row["packet_id"] for row in C.read_json(m)["packets"]] if m.is_file() else []


def load() -> tuple[dict[str, Any], str]:
    f = C.private_dir() / PREREG
    s = C.private_dir() / PREREG_SHA
    if not f.is_file() or not s.is_file():
        raise C.HarnessError("no pre-registration: run `al.py prereg` before any arm")
    digest = C.sha256_file(f)
    recorded = s.read_text(encoding="utf-8").split()[0]
    if digest != recorded:
        raise C.HarnessError("PREREG.md does not match PREREG.sha256 (edited after registration)")
    m = _JSON_BLOCK.search(f.read_text(encoding="utf-8"))
    if m is None:
        raise C.HarnessError("PREREG.md has no machine-readable record")
    return json.loads(m.group(1)), digest


def verify(exp: str | None = None) -> tuple[dict[str, Any], str]:
    """The pre-registration, after checking that nothing it hashed has changed since. With ``exp``:
    that experiment's prompt, schema and packets; always: config, rubric and profile."""
    record, digest = load()
    then, now = record["inputs"], inputs()
    diffs = [
        k
        for k in ("config_sha256", "reader_instructions_sha256", "profile_sha256")
        if then.get(k) != now.get(k)
    ]
    if exp is not None:
        if exp in C.ARM_EXPERIMENTS and then["prompts"].get(exp) != now["prompts"].get(exp):
            diffs.append(f"prompts.{exp}")
        if then["packets"].get(exp) != now["packets"].get(exp):
            diffs.append(f"packets.{exp}")
        if exp == "E2":
            need = record.get("mustknow_required", {}).get("E2", [])
            lost = [
                n
                for n in need
                if then["mustknow"].get(n) is None or then["mustknow"].get(n) != now["mustknow"].get(n)
            ]
            if lost:
                diffs.append("mustknow (missing at registration or changed): " + ", ".join(lost))
    if diffs:
        raise C.HarnessError("inputs changed since the pre-registration: " + "; ".join(diffs))
    return record, digest


__all__ = ["PREREG", "PREREG_SHA", "inputs", "load", "render", "verify", "write"]
