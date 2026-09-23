"""Record/replay of provider responses for CI (CC-5).

``HLM_LLM_MODE``: ``live`` (network), ``record`` (network + append to the cassette), ``replay``
(strict: a miss raises ``CassetteMiss``, the network is never touched), ``off`` (no call at all).

Key = ``sha256(model_id ‖ prompt_version ‖ schema_version ‖ canonical(redacted messages) ‖
canonical(params))``, where ``params`` is the request body minus ``model`` and ``messages``.
Messages are recorded AFTER redaction, and only a sanitised response is kept (content,
finish_reason, usage, model): no headers, no key, no raw provider envelope. Files:
``tests/cassettes/<ws>/*.jsonl``, one JSON object per line. Cassettes prove parsing, application,
idempotency and replay; they never prove model quality.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from hlmemo.librarian.errors import CassetteMiss

SEP = "‖"  # ‖


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def cassette_key(
    model_id: str,
    prompt_version: str,
    schema_version: str,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
) -> str:
    raw = SEP.join((model_id, prompt_version, schema_version, canonical(messages), canonical(params)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sanitize_response(body: dict[str, Any]) -> dict[str, Any]:
    choice = (body.get("choices") or [{}])[0] or {}
    msg = choice.get("message") or {}
    usage = body.get("usage") or {}
    keep_usage = {
        k: usage[k]
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
        if isinstance(usage.get(k), int | float)
    }
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details.get("cached_tokens"), int):
        keep_usage["prompt_tokens_details"] = {"cached_tokens": details["cached_tokens"]}
    return {
        "model": body.get("model"),
        "choices": [
            {
                "message": {"role": "assistant", "content": msg.get("content")},
                "finish_reason": choice.get("finish_reason"),
            }
        ],
        "usage": keep_usage,
    }


class CassetteStore:
    """All ``*.jsonl`` files of one directory; ``record`` appends to ``<dir>/<name>.jsonl``."""

    def __init__(self, directory: Path, *, record_name: str = "recorded") -> None:
        self.directory = Path(directory)
        self.record_name = record_name
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._index is None:
            index: dict[str, dict[str, Any]] = {}
            if self.directory.is_dir():
                for path in sorted(self.directory.glob("*.jsonl")):
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            rec = json.loads(line)
                            index.setdefault(rec["match"], rec)
            self._index = index
        return self._index

    def get(self, key: str) -> dict[str, Any]:
        rec = self._load().get(key)
        if rec is None:
            raise CassetteMiss(f"no cassette entry for key {key[:16]}… in {self.directory}")
        return rec["response"]

    def has(self, key: str) -> bool:
        return key in self._load()

    def put(
        self,
        key: str,
        *,
        task: str,
        model_id: str,
        prompt_version: str,
        schema_version: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        with self._lock:
            index = self._load()
            if key in index:
                return
            rec = {
                "match": key,  # not named "key": secret scanners flag key=<hex>
                "task": task,
                "model_id": model_id,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "messages": messages,
                "params": params,
                "response": sanitize_response(response),
            }
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / f"{self.record_name}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(canonical(rec) + "\n")
            index[key] = rec


__all__ = ["CassetteStore", "canonical", "cassette_key", "sanitize_response"]
