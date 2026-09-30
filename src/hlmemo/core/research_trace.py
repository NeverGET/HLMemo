"""D-189 ``memory.ask`` trace recorder ("brain surgery"): with ``HLM_RESEARCH_TRACE_DIR`` set, each
request writes ONE JSON file ``<dir>/<UTC timestamp>-<sha8 of the question>.json`` that follows every
step of the question: the request, the Memory Map the planner saw, every LLM call (all attempts,
retries and fallbacks, with the exact messages sent and the raw outputs), each query's hit list with
its component ranks, the fusion, the drill and every dropped handle with its reason, the excerpts
exactly as the writer saw them, the prose validation per sentence and literal, the attribution and
the final response.

It never changes behaviour: the run holds a recorder (or None) and records COPIES only (JSON-safe
snapshots, never live objects); a recording or write failure is logged and never reaches the
request. The prompts it records are the redacted ones the provider sends; no API key is ever in
them (profiles are recorded by name and model only).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

log = logging.getLogger("hlmemo.core.research_trace")

#: the sections of a trace file, in order (``rerank``/``refine``/``expand`` stay empty lists when not
#: run; D-193 (5b) ``rerank``: the rerank call, its candidates, the handles kept or why it fell back)
SECTIONS = (
    "request",
    "map",
    "plan",
    "rerank",
    "refine",
    "retrieval",
    "excerpts",
    "write",
    "expand",
    "validation",
    "attribution",
    "response",
    "calls",
)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot(value: Any) -> Any:
    """A JSON-safe deep copy (dataclasses as dicts, sets as sorted lists, Decimal/datetime as
    strings): the recorder never keeps a reference to live state."""
    if is_dataclass(value) and not isinstance(value, type):
        return snapshot(asdict(value))
    if isinstance(value, dict):
        return {str(k): snapshot(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [snapshot(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted(snapshot(v) for v in value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return copy.copy(value)
    return str(value)


class TraceRecorder:
    """One request's trace. Every method is best effort: an exception inside it is logged and
    swallowed (``_safe``), so a recorder bug can never break or alter an answer."""

    def __init__(self, directory: Path, question: str) -> None:
        self.directory = directory
        self.question = question
        self.started = datetime.now(UTC)
        self.t0 = time.perf_counter()
        self.data: dict[str, Any] = {
            k: ([] if k in ("plan", "rerank", "refine", "write", "expand", "calls") else {}) for k in SECTIONS
        }
        self.data["retrieval"] = {"queries": [], "phases": []}
        self.data["attribution"] = {"calls": []}
        #: the provider's per-attempt events (``observe``), in order
        self.events: list[dict[str, Any]] = []
        self.last_call: dict[str, Any] | None = None

    @classmethod
    def maybe(cls, settings: Any, question: str) -> TraceRecorder | None:
        directory = str(getattr(settings, "research_trace_dir", None) or "").strip()
        return cls(Path(directory), question) if directory else None

    # ---- recording (copies only)
    def set(self, section: str, value: Any) -> None:
        self._safe(lambda: self.data.__setitem__(section, snapshot(value)))

    def update(self, section: str, **values: Any) -> None:
        def do() -> None:
            target = self.data.setdefault(section, {})
            target.update({k: snapshot(v) for k, v in values.items()})

        self._safe(do)

    def append(self, section: str, value: Any, key: str | None = None) -> None:
        def do() -> None:
            target = self.data[section] if key is None else self.data[section].setdefault(key, [])
            target.append(snapshot(value))

        self._safe(do)

    def observe(self, event: dict[str, Any]) -> None:
        """The provider's per-attempt callback: the exact messages sent and the raw output."""
        self._safe(lambda: self.events.append(snapshot(event)))

    def call(self, record: dict[str, Any]) -> None:
        """One logical LLM call (its attempts and outputs are already in the record)."""

        def do() -> None:
            rec = snapshot(record)
            self.data["calls"].append(rec)
            job = rec.get("job")
            section = {"plan": "plan", "rerank": "rerank", "refine": "refine", "expand": "expand"}.get(job)
            if job == "attribute":
                self.data["attribution"]["calls"].append(rec)
            elif section is not None:
                self.data[section].append(rec)
            else:  # answer, check, select, write, prose: the writing step
                self.data["write"].append(rec)
            self.last_call = rec

        self._safe(do)

    def enrich_last(self, **values: Any) -> None:
        """Parsed results added to the last call record (queries, answer fields, ...)."""

        def do() -> None:
            if self.last_call is not None:
                self.last_call.update({k: snapshot(v) for k, v in values.items()})

        self._safe(do)

    # ---- output
    def path(self) -> Path:
        stamp = self.started.strftime("%Y%m%dT%H%M%S%fZ")
        return self.directory / f"{stamp}-{sha256(self.question)[:8]}.json"

    def write(self) -> Path | None:
        """Write the trace file; None (logged) when it cannot be written. Never raises."""
        try:
            self.data["request"]["finished_at"] = datetime.now(UTC).isoformat()
            self.data["request"]["elapsed_ms"] = int((time.perf_counter() - self.t0) * 1000)
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.path()
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            tmp.replace(target)
            return target
        except Exception:  # noqa: BLE001 - a trace is diagnostics: it never fails a request
            log.warning("memory.ask trace not written to %s", self.directory, exc_info=True)
            return None

    @staticmethod
    def _safe(fn: Any) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - see the class doc
            log.warning("memory.ask trace: a record failed", exc_info=True)


__all__ = ["SECTIONS", "TraceRecorder", "sha256", "snapshot"]
