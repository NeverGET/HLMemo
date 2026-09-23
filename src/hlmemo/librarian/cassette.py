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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hlmemo.librarian.errors import CassetteMiss
from hlmemo.librarian.redact import Redactor

SEP = "‖"  # ‖


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def cassette_key(
    model_id: str,
    prompt_version: str,
    schema_version: str,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    attempt: int = 1,
) -> str:
    """``attempt`` is the response attempt of one call (2 = the schema retry). Attempt 1 keeps the
    original key, so cassettes recorded before retries were keyed still replay unchanged."""
    parts = [model_id, prompt_version, schema_version, canonical(messages), canonical(params)]
    if attempt > 1:
        parts.append(f"attempt={attempt}")
    return hashlib.sha256(SEP.join(parts).encode("utf-8")).hexdigest()


UNSUPPORTED = "⟦CONTENT:unsupported⟧"
TOOL_CALLS = "⟦CONTENT:tool_calls⟧"
_TEXT_PART_TYPES = frozenset({"text", "output_text"})


def normalize_content(msg: dict[str, Any]) -> str | None:
    """Any assistant ``message`` shape → plain text (Sol 37 #2): a string stays; an array of
    parts keeps only the text of text parts (any other part becomes a content-free marker);
    tool calls are never kept (marker); anything else becomes the unsupported marker."""
    content = msg.get("content")
    tool = msg.get("tool_calls") or msg.get("function_call")
    if isinstance(content, str):
        text: str | None = content
    elif content is None:
        text = None
    elif isinstance(content, list):
        pieces = []
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") in _TEXT_PART_TYPES
                and isinstance(part.get("text"), str)
            ):
                pieces.append(part["text"])
            elif isinstance(part, str):
                pieces.append(part)
            else:
                pieces.append(UNSUPPORTED)
        text = "".join(pieces)
    else:
        text = UNSUPPORTED
    if tool:
        text = TOOL_CALLS if text is None else f"{text}{TOOL_CALLS}"
    return text


def sanitize_response(body: dict[str, Any], redact: Callable[[str], str] | None = None) -> dict[str, Any]:
    """The only response shape ever persisted: normalized (and, when given, redacted) text."""
    choice = (body.get("choices") or [{}])[0] or {}
    msg = choice.get("message") or {}
    text = normalize_content(msg if isinstance(msg, dict) else {"content": msg})
    if text is not None and redact is not None:
        text = redact(text)
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
                "message": {"role": "assistant", "content": text},
                "finish_reason": choice.get("finish_reason"),
            }
        ],
        "usage": keep_usage,
    }


def sanitize_messages(messages: list[dict[str, Any]], redact: Callable[[str], str]) -> list[dict[str, Any]]:
    """Request messages as persisted: role + normalized, redacted text only (any content shape)."""
    out = []
    for m in messages:
        m = m if isinstance(m, dict) else {"content": m}
        text = normalize_content(m)
        out.append({"role": str(m.get("role") or "user"), "content": None if text is None else redact(text)})
    return out


class CassetteStore:
    """All ``*.jsonl`` files of one directory; ``record`` appends to ``<dir>/<name>.jsonl``.

    ``put`` is the only persistence path and trusts NO caller (Sol 38 #2): it normalizes every
    message and the response to plain text and runs its own redactor over them, the params and the
    task/model labels before anything reaches disk."""

    def __init__(
        self, directory: Path, *, record_name: str = "recorded", redactor: Redactor | None = None
    ) -> None:
        self.directory = Path(directory)
        self.record_name = record_name
        self.redactor = redactor or Redactor()
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
            red = self.redactor
            rec = {
                "match": key,  # not named "key": secret scanners flag key=<hex>
                "task": red.text(str(task)),
                "model_id": red.text(str(model_id)),
                "prompt_version": red.text(str(prompt_version)),
                "schema_version": red.text(str(schema_version)),
                "messages": sanitize_messages(messages, red.text),
                "params": red.value(params),
                "response": sanitize_response(response, redact=red.text),
            }
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / f"{self.record_name}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(canonical(rec) + "\n")
            index[key] = rec


__all__ = [
    "CassetteStore",
    "canonical",
    "cassette_key",
    "normalize_content",
    "sanitize_messages",
    "sanitize_response",
]
