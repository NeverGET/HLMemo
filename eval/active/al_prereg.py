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

E4 (lessons v2) is a separate suite (``al_common.E4_SUITE``): ``PREREG-E4.md`` in the E4 private
directory, ``config-e4.json``, ``READER-INSTRUCTIONS-E4.md``, and as extra inputs the episode file
and the frozen clustering record. Every function takes the suite (default: E0-E3).

``amend`` is the one documented way to change a hashed input after the pre-registration: ONLY
``spend_cap_usd`` (allow-list), only upwards. It writes ``AMENDMENT-<n>.md`` + ``.sha256`` next to the
PREREG (n, UTC time, field, old -> new, reason, the PREREG sha it amends, the new config sha) and edits
that one line of config.json. ``verify`` accepts a config hash that differs from the PREREG's only
when the amendment chain explains it exactly (see ``_amendment_chain``); every other input must
still match the pre-registration byte for byte.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import al_common as C

PREREG = "PREREG.md"
PREREG_SHA = "PREREG.sha256"
AMENDABLE = ("spend_cap_usd",)
_AMEND_FILE = re.compile(r"^AMENDMENT-(\d+)\.md$")
_CAP_LINE = re.compile(r'("spend_cap_usd"\s*:\s*")([^"]*)(")')
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


def e4_inputs(suite_name: str = C.E4) -> dict[str, Any]:
    """A lesson suite's extra inputs: the episode file the clusters were built from and the clustering
    record; E4B also the covered-lessons file (E4's shape stays as registered)."""
    import al_e4

    files = [("episodes", al_e4.episodes_path()), ("clusters", al_e4.clusters_path())]
    if suite_name == C.E4B:
        files.append(("covered", al_e4.covered_path()))
    out: dict[str, Any] = {}
    for name, f in files:
        out[f"{name}_sha256"] = C.sha256_file(f) if f.is_file() else None
    return out


def inputs(cfg_path: Path | None = None, suite: C.Suite = C.MAIN_SUITE) -> dict[str, Any]:
    """Every hashed input, as it is NOW."""
    cfg_path = cfg_path or suite.config_path
    cfg = C.load_config(cfg_path)
    if suite.name in C.LESSON_EXPS:
        md, schema = C.prompt_files(suite.name)
        pf = profile_file(cfg["arms"]["gemini"]["profile"])
        return {
            "config_sha256": C.sha256_file(cfg_path),
            "reader_instructions_sha256": C.sha256_file(suite.reader_template),
            "prompts": {
                suite.name: {"prompt_sha256": C.sha256_file(md), "schema_sha256": C.sha256_file(schema)}
            },
            "packets": {suite.name: packet_hashes(suite.name)},
            "e4": e4_inputs(suite.name),
            "profile_sha256": C.sha256_file(pf) if pf else None,
        }
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


def render_e4(record: dict[str, Any], rubric: str, suite: C.Suite = C.E4_SUITE) -> str:
    bars, clu = record["bars"], record.get("clustering") or {}
    title = "Lessons v2 (E4)" if suite.name == C.E4 else "Lessons v2b (E4B: status + model era)"
    lines = [
        f"# {title} ceiling experiment: pre-registration",
        "",
        f"Written {record['written_at']} before any arm ran."
        f" The sha256 of this file is in {suite.prereg_sha};",
        "every runner and the scorer verify it and every input hash below, and refuse on a difference.",
        "",
        "## Go/no-go bars (D-222)",
        "",
        f"- grounded >= {bars['grounded_min']}, correct >= {bars['correct_min']},"
        f" useful >= {bars['useful_min'][suite.name]}",
        f"- harmful: at most {bars['harmful_max']};"
        f" overgeneralized rate <= {bars['overgeneralized_max_rate']}",
        f"- Opus at most {bars['opus_margin_max']} better on {', '.join(bars['opus_margin_metrics'])}",
        "",
        "## Clustering (frozen before this registration)",
        "",
        f"- E5 embeddings of lesson + symptom; agglomerative, {clu.get('linkage')} linkage, cosine distance",
        f"- threshold {clu.get('threshold')} ({clu.get('threshold_source', 'tuned on the held-out split')};"
        f" held-out {clu.get('holdout_frac')}, seed {clu.get('seed')}, criterion {clu.get('criterion')})",
        f"- episodes {clu.get('episodes')}, clusters {clu.get('clusters')}, cross-project packets"
        f" {clu.get('cross_project')}, project-local packets {clu.get('project_local')},"
        f" already covered by imported lessons {clu.get('already_covered', 0)}",
        f"- eligibility: {clu.get('eligibility')}",
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


def _write_e4(target: Path, suite: C.Suite) -> tuple[Path, str]:
    import al_e4

    cfg = C.load_config(suite.config_path)
    ins = inputs(suite=suite)
    if not ins["packets"][suite.name] or not ins["e4"]["clusters_sha256"]:
        raise C.HarnessError(f"build the {suite.name} packets first (al.py build --exp {suite.name})")
    clusters = C.read_json(al_e4.clusters_path())
    record = {
        "schema": f"al-prereg-{suite.name.lower()}/1",
        "written_at": datetime.now(UTC).isoformat(),
        "harness": _git_head(),
        "bars": cfg["bars"],
        "rules": cfg["rules"],
        "grading": cfg["grading"],
        "selection": cfg["selection"],
        "clustering": clusters["summary"],
        "arms": arm_config(cfg),
        "inputs": ins,
    }
    text = render_e4(record, suite.reader_template.read_text(encoding="utf-8"), suite)
    C.write_text(target, text)
    digest = C.sha256_file(target)
    C.write_text(C.private_dir() / suite.prereg_sha, f"{digest}  {suite.prereg}\n")
    return target, digest


def write(*, force: bool = False, suite: C.Suite = C.MAIN_SUITE) -> tuple[Path, str]:
    if _outputs_exist():
        raise C.HarnessError("arm outputs already exist: a pre-registration must precede every run")
    target = C.ensure_private(C.private_dir() / suite.prereg)
    if target.is_file() and not force:
        raise C.HarnessError(f"{target} exists (pass --force to rewrite it; no arm has run yet)")
    if suite.name in C.LESSON_EXPS:
        return _write_e4(target, suite)
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


def load(suite: C.Suite = C.MAIN_SUITE) -> tuple[dict[str, Any], str]:
    f = C.private_dir() / suite.prereg
    s = C.private_dir() / suite.prereg_sha
    if not f.is_file() or not s.is_file():
        raise C.HarnessError(f"no pre-registration ({suite.prereg}): run `al.py prereg` before any arm")
    digest = C.sha256_file(f)
    recorded = s.read_text(encoding="utf-8").split()[0]
    if digest != recorded:
        raise C.HarnessError(f"{suite.prereg} does not match {suite.prereg_sha} (edited after registration)")
    m = _JSON_BLOCK.search(f.read_text(encoding="utf-8"))
    if m is None:
        raise C.HarnessError("PREREG.md has no machine-readable record")
    return json.loads(m.group(1)), digest


def _set_cap(config_text: str, value: str) -> str:
    """config.json with its spend_cap_usd replaced (a textual edit: every other byte is kept)."""
    if len(_CAP_LINE.findall(config_text)) != 1:
        raise C.HarnessError("config.json must contain exactly one spend_cap_usd string")
    return _CAP_LINE.sub(lambda m: f"{m.group(1)}{value}{m.group(3)}", config_text, count=1)


def _amendment_files() -> list[tuple[int, Path]]:
    d = C.private_dir()
    found = [(int(m.group(1)), p) for p in d.glob("AMENDMENT-*.md") if (m := _AMEND_FILE.match(p.name))]
    return sorted(found)


def _load_amendment(n: int, path: Path) -> dict[str, Any]:
    sha_file = path.with_suffix(".sha256")
    if not sha_file.is_file() or C.sha256_file(path) != sha_file.read_text(encoding="utf-8").split()[0]:
        raise C.HarnessError(f"{path.name} does not match its .sha256 (edited after it was written)")
    m = _JSON_BLOCK.search(path.read_text(encoding="utf-8"))
    if m is None:
        raise C.HarnessError(f"{path.name} has no machine-readable record")
    rec = json.loads(m.group(1))
    if rec.get("n") != n:
        raise C.HarnessError(f"{path.name}: records n={rec.get('n')}")
    return rec


def _amendment_chain(prereg_digest: str) -> list[dict[str, Any]]:
    """The valid amendments in order, or HarnessError. Each file hash matches, numbers run 1..k,
    each names this PREREG sha and the value the previous one set, and only allow-listed fields."""
    chain: list[dict[str, Any]] = []
    for i, (n, path) in enumerate(_amendment_files(), start=1):
        if n != i:
            raise C.HarnessError(f"amendment numbering has a gap: expected AMENDMENT-{i}.md, found {n}")
        rec = _load_amendment(n, path)
        if rec.get("prereg_sha256") != prereg_digest:
            raise C.HarnessError(f"{path.name} amends a different pre-registration")
        if rec.get("field") not in AMENDABLE:
            raise C.HarnessError(f"{path.name}: field {rec.get('field')!r} is not amendable")
        if chain and rec.get("old") != chain[-1]["new"]:
            raise C.HarnessError(f"{path.name}: old value does not continue the previous amendment")
        if Decimal(str(rec["new"])) < Decimal(str(rec["old"])):
            raise C.HarnessError(f"{path.name}: lowers the cap")
        chain.append(rec)
    return chain


def _config_explained(
    then_sha: str, chain: list[dict[str, Any]], prereg_cap: str, config_path: Path | None = None
) -> bool:
    """Undo the chain on the CURRENT config text: every intermediate hash and the pre-registered
    config hash must be reproduced exactly."""
    if not chain or chain[0]["old"] != prereg_cap:
        return False
    text = (config_path or C.CONFIG_PATH).read_text(encoding="utf-8")
    for rec in reversed(chain):
        m = _CAP_LINE.search(text)
        if C.sha256_text(text) != rec["config_sha256_after"] or m is None or m.group(2) != str(rec["new"]):
            return False
        text = _set_cap(text, str(rec["old"]))
    return C.sha256_text(text) == then_sha


def amend(field: str, value: str, reason: str, suite: C.Suite = C.MAIN_SUITE) -> tuple[Path, str]:
    if field not in AMENDABLE:
        raise C.HarnessError(f"only {', '.join(AMENDABLE)} may be amended (got {field!r})")
    if not reason.strip():
        raise C.HarnessError("--reason is required")
    try:
        new = Decimal(value)
    except InvalidOperation as exc:
        raise C.HarnessError(f"--value {value!r} is not a decimal") from exc
    if not new.is_finite() or new <= 0:
        raise C.HarnessError("--value must be a positive decimal")
    _record, digest = verify(suite.exps[0] if suite.name in C.LESSON_EXPS else None)  # intact registration
    chain = _amendment_chain(digest)
    text = suite.config_path.read_text(encoding="utf-8")
    old = str(C.load_config(suite.config_path)[field])
    if new < Decimal(old):
        raise C.HarnessError(f"the new cap {new} is below the current {old}: a cap can only be raised")
    new_text = _set_cap(text, value)
    n = len(chain) + 1
    rec = {
        "schema": "al-amendment/1",
        "n": n,
        "amended_at": datetime.now(UTC).isoformat(),
        "field": field,
        "old": old,
        "new": value,
        "reason": reason.strip(),
        "prereg_sha256": digest,
        "config_sha256_before": C.sha256_text(text),
        "config_sha256_after": C.sha256_text(new_text),
    }
    body = "\n".join(
        [
            f"# Pre-registration amendment {n}",
            "",
            f"- n: {n}",
            f"- timestamp (UTC): {rec['amended_at']}",
            f"- field: {field}",
            f"- change: {old} -> {value}",
            f"- reason: {rec['reason']}",
            f"- amends PREREG sha256: {digest}",
            f"- new config.json sha256: {rec['config_sha256_after']}",
            f"- (this file's own sha256 is in AMENDMENT-{n}.sha256)",
            "",
            "```json",
            json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False),
            "```",
            "",
        ]
    )
    target = C.private_dir() / f"AMENDMENT-{n}.md"
    C.write_text(target, body)
    own = C.sha256_file(target)
    C.write_text(C.private_dir() / f"AMENDMENT-{n}.sha256", f"{own}  AMENDMENT-{n}.md\n")
    suite.config_path.write_text(new_text, encoding="utf-8")  # the one in-repo file an amendment edits
    return target, own


def verify(exp: str | None = None) -> tuple[dict[str, Any], str]:
    """The pre-registration, after checking that nothing it hashed has changed since. With ``exp``:
    that experiment's prompt, schema and packets; always: config, rubric and profile."""
    suite = C.suite_of(exp)
    record, digest = load(suite)
    then, now = record["inputs"], inputs(suite=suite)
    diffs = [
        k
        for k in ("config_sha256", "reader_instructions_sha256", "profile_sha256")
        if then.get(k) != now.get(k)
    ]
    if suite.name in C.LESSON_EXPS and then.get("e4") != now.get("e4"):
        diffs.append("e4 episodes/clusters")
    chain = _amendment_chain(digest)
    record["_amendments"] = [
        {k: a[k] for k in ("n", "amended_at", "field", "old", "new", "reason")} for a in chain
    ]
    if "config_sha256" in diffs and _config_explained(
        then["config_sha256"], chain, str(record["arms"]["spend_cap_usd"]), suite.config_path
    ):
        diffs.remove("config_sha256")
    if exp is not None:
        if exp in suite.arm_exps and then["prompts"].get(exp) != now["prompts"].get(exp):
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


__all__ = [
    "AMENDABLE",
    "PREREG",
    "PREREG_SHA",
    "amend",
    "e4_inputs",
    "inputs",
    "load",
    "render",
    "render_e4",
    "verify",
    "write",
]
