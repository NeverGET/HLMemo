"""E5 embeddings via ONNX Runtime (PHASE0-SPEC §4 step 7/13, §5 models.lock).

``intfloat/multilingual-e5-small`` @ ``MODEL_REVISION``: ``onnx/model.onnx`` (fp32, CPU),
tokenizer ``onnx/tokenizer.json``, mean pooling over the attention mask, L2 normalisation,
384 dims, max 512 tokens, ``"query: "`` / ``"passage: "`` prefixes.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from tokenizers import Tokenizer

from hlmemo.core import EMBEDDING_DIMS, MODEL_ID, MODEL_REVISION

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
MAX_TOKENS = 512
DEFAULT_BATCH = 32
MODEL_FILES = ("onnx/model.onnx", "onnx/tokenizer.json", "tokenizer_config.json", "config.json")
HASHED_FILES = ("onnx/model.onnx", "onnx/tokenizer.json")


class ModelHashMismatch(RuntimeError):
    pass


def repo_root() -> Path | None:
    """``<root>`` when running from a source checkout (``<root>/src/hlmemo/core/embedder.py``)."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").exists() or (root / "src" / "hlmemo").is_dir() else None


class EmbedConfigMismatch(RuntimeError):
    """``HLM_EMBED_MODEL`` / ``HLM_EMBED_REVISION`` name something other than the pinned model."""


def _models_lock_path() -> Path | None:
    root = repo_root()
    for cand in ((root / "models.lock") if root else None, Path.cwd() / "models.lock"):
        if cand is not None and cand.is_file():
            return cand
    return None


def pinned_model() -> tuple[str, str]:
    """``(model_id, revision)`` the Phase-0 contract pins: the ``models.lock`` header when the
    file is found, else the compiled-in constants (which the lock must equal)."""
    lock = _models_lock_path()
    if lock is None:
        return MODEL_ID, MODEL_REVISION
    head: dict[str, str] = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition(":")
        if sep and key in ("model", "revision") and key not in head:
            head[key] = value.strip()
    return head.get("model", MODEL_ID), head.get("revision", MODEL_REVISION)


def embed_config_check(model: str | None, revision: str | None) -> dict[str, object]:
    """F04: the embedder is pinned (models.lock); a configured model/revision that differs is an
    error, never silently ignored. Returns a ``/ready``-style check ``{ok, pinned, configured[,
    error]}``; ``None`` means "not configured" (the pinned value applies)."""
    lock_model, lock_rev = pinned_model()
    pinned = f"{lock_model}@{lock_rev}"
    configured = f"{model or lock_model}@{revision or lock_rev}"
    out: dict[str, object] = {"ok": True, "pinned": pinned, "configured": configured}
    if (lock_model, lock_rev) != (MODEL_ID, MODEL_REVISION):
        out.update(
            ok=False,
            error=f"models.lock pins {pinned} but this build embeds with {MODEL_ID}@{MODEL_REVISION}",
        )
    elif configured != pinned:
        out.update(
            ok=False,
            error=(
                f"HLM_EMBED_MODEL/HLM_EMBED_REVISION = {configured} differ from the pinned embedder "
                f"{pinned} (models.lock); the Phase-0 embedder is not configurable — unset them or "
                "set them to the pinned values"
            ),
        )
    return out


def require_pinned_embed_config(model: str | None, revision: str | None) -> None:
    """Fail fast at start-up (api + worker) on a non-pinned embed configuration (F04)."""
    check = embed_config_check(model, revision)
    if not check["ok"]:
        raise EmbedConfigMismatch(str(check["error"]))


def default_models_dir() -> Path:
    """``$HLM_MODELS_DIR`` if set, else ``<repo root>/models``, else ``./models``."""
    env = os.environ.get("HLM_MODELS_DIR")
    if env:
        return Path(env)
    root = repo_root()
    return (root / "models") if root else (Path.cwd() / "models")


def default_model_dir(model_id: str = MODEL_ID) -> Path:
    return default_models_dir() / model_id.rsplit("/", 1)[-1]


def download_model(
    dest: str | Path | None = None,
    revision: str = MODEL_REVISION,
    *,
    model_id: str = MODEL_ID,
) -> Path:
    """``snapshot_download`` of exactly the files we need into ``dest`` (default
    ``<repo>/models/<model name>``). Idempotent: cached files are not re-downloaded."""
    from huggingface_hub import snapshot_download

    dest_path = Path(dest) if dest is not None else default_model_dir(model_id)
    dest_path.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=model_id,
        revision=revision,
        local_dir=str(dest_path),
        allow_patterns=list(MODEL_FILES),
    )
    missing = [f for f in MODEL_FILES if not (dest_path / f).is_file()]
    if missing:
        raise FileNotFoundError(f"model download incomplete, missing {missing} under {dest_path}")
    return dest_path


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def model_hashes(model_dir: str | Path) -> dict[str, str]:
    """sha256 of ``onnx/model.onnx`` and ``onnx/tokenizer.json`` (the ``models.lock`` content)."""
    d = Path(model_dir)
    return {rel: sha256_file(d / rel) for rel in HASHED_FILES}


class Embedder:
    """CPU fp32 ONNX Runtime session for the pinned E5 model."""

    dims = EMBEDDING_DIMS
    model_id = MODEL_ID
    revision = MODEL_REVISION

    def __init__(
        self,
        model_dir: str | Path,
        *,
        max_tokens: int = MAX_TOKENS,
        batch_size: int = DEFAULT_BATCH,
        threads: int | None = None,
        expected_hashes: dict[str, str] | None = None,
    ) -> None:
        import onnxruntime as ort

        self.model_dir = Path(model_dir)
        model_path = self.model_dir / "onnx" / "model.onnx"
        tok_path = self.model_dir / "onnx" / "tokenizer.json"
        for p in (model_path, tok_path):
            if not p.is_file():
                raise FileNotFoundError(f"missing model file {p}; run download_model()")
        if expected_hashes:
            actual = model_hashes(self.model_dir)
            bad = {k: (actual.get(k), v) for k, v in expected_hashes.items() if actual.get(k) != v}
            if bad:
                raise ModelHashMismatch(f"model files differ from models.lock: {bad}")

        self.max_tokens = max_tokens
        self.batch_size = batch_size
        self._tok = Tokenizer.from_file(str(tok_path))
        self._tok.enable_truncation(max_tokens)
        self._tok.no_padding()  # we pad per batch ourselves
        pad = self._tok.token_to_id("<pad>")
        self._pad_id = pad if pad is not None else 1

        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        self._sess = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._sess.get_inputs()}
        self._output_name = self._sess.get_outputs()[0].name

    # -- internals ----------------------------------------------------------
    def _encode_batch(self, texts: list[str]) -> dict[str, np.ndarray]:
        encs = self._tok.encode_batch(texts, add_special_tokens=True)
        width = max(len(e.ids) for e in encs)
        n = len(encs)
        ids = np.full((n, width), self._pad_id, dtype=np.int64)
        mask = np.zeros((n, width), dtype=np.int64)
        for i, e in enumerate(encs):
            k = len(e.ids)
            ids[i, :k] = e.ids
            mask[i, :k] = 1
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros((n, width), dtype=np.int64)
        return feeds

    def _run(self, texts: list[str]) -> np.ndarray:
        feeds = self._encode_batch(texts)
        (hidden,) = self._sess.run([self._output_name], feeds)  # (n, L, 384) float32
        mask = feeds["attention_mask"][..., None].astype(np.float32)
        summed = (hidden * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), 1e-9, None)
        pooled = summed / counts
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norms, 1e-12, None)).astype(np.float32)

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Embed already-prefixed texts. Returns float32 ``(n, 384)``, rows L2-normalised."""
        if len(texts) == 0:
            return np.zeros((0, self.dims), dtype=np.float32)
        out = [self._run(list(texts[i : i + self.batch_size])) for i in range(0, len(texts), self.batch_size)]
        return np.concatenate(out, axis=0)

    # -- public -------------------------------------------------------------
    def embed_passages(self, passages: Sequence[str]) -> np.ndarray:
        return self.embed_texts([PASSAGE_PREFIX + p for p in passages])

    def embed_query(self, query: str) -> np.ndarray:
        """Single query → float32 ``(384,)``."""
        return self.embed_texts([QUERY_PREFIX + query])[0]

    def embed_queries(self, queries: Sequence[str]) -> np.ndarray:
        return self.embed_texts([QUERY_PREFIX + q for q in queries])


__all__ = [
    "EmbedConfigMismatch",
    "Embedder",
    "ModelHashMismatch",
    "embed_config_check",
    "pinned_model",
    "require_pinned_embed_config",
    "QUERY_PREFIX",
    "PASSAGE_PREFIX",
    "MAX_TOKENS",
    "MODEL_FILES",
    "download_model",
    "default_model_dir",
    "default_models_dir",
    "model_hashes",
    "sha256_file",
]
