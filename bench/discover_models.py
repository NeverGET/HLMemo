#!/usr/bin/env python3
"""Resolve candidate librarian models to exact OpenRouter ids + pricing.

Writes bench/models.json. Re-run whenever OpenRouter's catalogue changes.
Usage:  python discover_models.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")

API = "https://openrouter.ai/api/v1"

# alias -> (exact OpenRouter id, note). Aliases are what run.py --models accepts.
CANDIDATES: dict[str, tuple[str, str]] = {
    "deepseek-v4-flash":      ("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash 0423 (base V4 Flash id)."),
    "deepseek-v4-flash-0731": ("deepseek/deepseek-v4-flash-0731", "V4 Flash 0731 re-post-train; still listed on OpenRouter under its own id (retired on DeepSeek first-party API, where canonical `deepseek-flash` now = V4.1 Flash). Cheaper input, 3.6x pricier output than 0423."),
    "deepseek-v4.1-flash":    ("deepseek/deepseek-v4.1-flash", "Newest DeepSeek Flash (V4.1, CED arch) = first-party canonical `deepseek-flash`. OpenRouter alias ~deepseek/deepseek-flash-latest points here."),
    "gemini-flash":           ("google/gemini-3.8-flash", "Latest 3.x Gemini Flash (3.8). ~google/gemini-flash-latest aliases here."),
    "gemini-flash-lite":      ("google/gemini-3.5-flash-lite", "Latest Gemini Flash-Lite (3.5)."),
    "gemini-3.1-flash-lite":  ("google/gemini-3.1-flash-lite", "Older, cheaper Flash-Lite."),
    "gpt-5-mini":             ("openai/gpt-5-mini", "GPT-5 mini (reasoning model; expect reasoning tokens)."),
    "gpt-5-nano":             ("openai/gpt-5-nano", "GPT-5 nano."),
    "gpt-5.4-nano":           ("openai/gpt-5.4-nano", "Newer cheap tier."),
    "gpt-5.6-luna":           ("openai/gpt-5.6-luna", "Newest cheap OpenAI tier (GPT-5.6 Luna, $0.20/$1.20)."),
    "qwen3.5-flash":          ("qwen/qwen3.5-flash-02-23", "Qwen3.5 Flash."),
    "qwen3-235b":             ("qwen/qwen3-235b-a22b-2507", "Qwen3 235B A22B instruct (2507)."),
    "qwen3.8-flash":          ("qwen/qwen3.8-flash", "Newest Qwen Flash."),
    "mistral-small-4":        ("mistralai/mistral-small-2603", "Mistral Small 4 (id is mistral-small-2603)."),
    "claude-haiku-4.5":       ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5."),
    "kimi-k2":                ("moonshotai/kimi-k2-0905", "Kimi K2 0905 (the K2 variant that supports response_format)."),
    "kimi-k2.6":              ("moonshotai/kimi-k2.6", "Newer Kimi K2.6."),
    "glm-4.6":                ("z-ai/glm-4.6", "GLM 4.6."),
    "glm-5":                  ("z-ai/glm-5", "GLM 5."),
    "glm-5.3-flash":          ("z-ai/glm-5.3-flash", "Newest cheap GLM Flash. ~z-ai/glm-flash-latest aliases here."),
    "minimax-m2":             ("minimax/minimax-m2", "MiniMax M2."),
    "minimax-m3":             ("minimax/minimax-m3", "Newest MiniMax."),
}


def per_million(s: str | None) -> float | None:
    return None if s in (None, "") else round(float(s) * 1_000_000, 4)


def main() -> int:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY missing in .env", file=sys.stderr)
        return 1
    r = requests.get(f"{API}/models", headers={"Authorization": f"Bearer {key}"}, timeout=60)
    r.raise_for_status()
    catalogue = {m["id"]: m for m in r.json()["data"]}

    out: dict[str, dict] = {}
    for alias, (mid, note) in CANDIDATES.items():
        m = catalogue.get(mid)
        if not m:
            print(f"WARN: {alias} -> {mid} not found in catalogue", file=sys.stderr)
            out[alias] = {"id": mid, "found": False, "note": note}
            continue
        p = m.get("pricing", {})
        sp = m.get("supported_parameters", []) or []
        out[alias] = {
            "id": mid,
            "found": True,
            "name": m.get("name"),
            "prompt_usd_per_m": per_million(p.get("prompt")),
            "completion_usd_per_m": per_million(p.get("completion")),
            "cache_read_usd_per_m": per_million(p.get("input_cache_read")),
            "context_length": m.get("context_length"),
            "supports_response_format": "response_format" in sp,
            "supports_structured_outputs": "structured_outputs" in sp,
            "note": note,
        }
    (HERE / "models.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {HERE / 'models.json'} ({len(out)} models)")
    for a, v in out.items():
        print(f"  {a:24s} {v['id']:42s} in={v.get('prompt_usd_per_m')} out={v.get('completion_usd_per_m')} rf={v.get('supports_response_format')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
