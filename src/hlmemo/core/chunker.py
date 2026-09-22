"""Write-time chunking (PHASE0-SPEC §4 step 13): E5 tokenizer, ``CHUNK_TOK`` tokens per chunk,
``CHUNK_OVERLAP`` overlap, exact char offsets into the *original* text.

The tokenizer is ``onnx/tokenizer.json`` (XLM-R Unigram) loaded with ``tokenizers`` — no
``sentencepiece`` dependency. Token offsets are relative to the original (un-normalised)
input, so ``text[char_start:char_end] == chunk.text`` always holds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tokenizers import Tokenizer

CHUNK_TOK = 400
CHUNK_OVERLAP = 40


@dataclass(frozen=True, slots=True)
class Chunk:
    ordinal: int
    char_start: int
    char_end: int
    text: str
    e5_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_tokenizer(model_dir: str | Path) -> Tokenizer:
    """Tokenizer from ``<model_dir>/onnx/tokenizer.json`` with truncation and padding disabled."""
    path = Path(model_dir)
    if path.is_file():
        tok_path = path
    else:
        tok_path = path / "onnx" / "tokenizer.json"
    if not tok_path.is_file():
        raise FileNotFoundError(f"E5 tokenizer not found: {tok_path}")
    tok = Tokenizer.from_file(str(tok_path))
    tok.no_truncation()
    tok.no_padding()
    return tok


class Chunker:
    def __init__(
        self,
        tokenizer: Tokenizer | str | Path,
        *,
        chunk_tok: int = CHUNK_TOK,
        overlap: int = CHUNK_OVERLAP,
    ) -> None:
        if chunk_tok < 1:
            raise ValueError("chunk_tok must be >= 1")
        if not 0 <= overlap < chunk_tok:
            raise ValueError("overlap must satisfy 0 <= overlap < chunk_tok")
        self._tok = tokenizer if isinstance(tokenizer, Tokenizer) else load_tokenizer(tokenizer)
        self.chunk_tok = chunk_tok
        self.overlap = overlap

    def count_tokens(self, text: str) -> int:
        return len(self._tok.encode(text, add_special_tokens=False).ids)

    def chunk(self, text: str) -> list[Chunk]:
        """Split ``text`` into windows of ``chunk_tok`` E5 tokens stepping ``chunk_tok - overlap``.

        The last window is shortened to the end of the text (never a window that is fully
        contained in the previous one). Whitespace at the window edges is trimmed by moving the
        char offsets inward; a window that is whitespace-only is dropped. Empty text → ``[]``.
        """
        if not isinstance(text, str):
            raise TypeError(f"chunk() expects str, got {type(text).__name__}")
        if not text.strip():
            return []
        enc = self._tok.encode(text, add_special_tokens=False)
        # Drop zero-width tokens (offsets (a, a)) — they carry no text.
        offsets = [(a, b) for (a, b) in enc.offsets if b > a]
        n = len(offsets)
        if n == 0:
            return []

        step = self.chunk_tok - self.overlap
        chunks: list[Chunk] = []
        start = 0
        while True:
            end = min(start + self.chunk_tok, n)
            cs = offsets[start][0]
            ce = max(b for (_, b) in offsets[start:end])
            # trim whitespace at the edges (offsets stay exact)
            while cs < ce and text[cs].isspace():
                cs += 1
            while ce > cs and text[ce - 1].isspace():
                ce -= 1
            if ce > cs:
                piece = text[cs:ce]
                assert piece == text[cs:ce]
                chunks.append(Chunk(len(chunks), cs, ce, piece, end - start))
            if end >= n:
                break
            start += step
        return chunks

    def chunk_dicts(self, text: str) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.chunk(text)]


__all__ = ["CHUNK_TOK", "CHUNK_OVERLAP", "Chunk", "Chunker", "load_tokenizer"]
