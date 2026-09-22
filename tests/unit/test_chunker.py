import random

import pytest

from hlmemo.core import CHUNKER_VERSION
from hlmemo.core.chunker import CHUNK_OVERLAP, CHUNK_TOK, Chunk, Chunker

TR = "İstanbul'da dün gece yağmur yağdı; ıslak yollarda trafik çok yavaştı. Şoförler dikkatli sürdü. "
DE = "Die Straße über den Fluss war gestern wegen Bauarbeiten gesperrt; Über Nacht wurde sie geräumt. "
EN = "The docker compose stack failed to boot because APP_DB_DSN pointed at path/to/file.py with error E4193. "


def _check_offsets(text: str, chunks: list[Chunk]) -> None:
    for i, c in enumerate(chunks):
        assert c.ordinal == i
        assert text[c.char_start : c.char_end] == c.text
        assert c.text == c.text.strip()
        assert 1 <= c.e5_tokens <= CHUNK_TOK
    for a, b in zip(chunks, chunks[1:]):
        assert a.char_start < b.char_start
        assert a.char_end < b.char_end


def test_version_and_constants():
    assert CHUNKER_VERSION == 1
    assert (CHUNK_TOK, CHUNK_OVERLAP) == (400, 40)


def test_empty_and_whitespace(chunker):
    assert chunker.chunk("") == []
    assert chunker.chunk("   \n\t ") == []


def test_short_text_single_chunk(chunker):
    for text in (TR, DE, EN):
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        _check_offsets(text, chunks)
        assert chunks[0].text == text.strip()
        assert chunks[0].e5_tokens == chunker.count_tokens(text)


@pytest.mark.parametrize("unit, reps", [(TR, 40), (DE, 40), (EN, 40)])
def test_long_text_windows_and_overlap(chunker, unit, reps):
    text = "\n\n".join(f"Paragraf {i}: " + unit for i in range(reps))
    n_tok = chunker.count_tokens(text)
    assert n_tok > 2 * CHUNK_TOK
    chunks = chunker.chunk(text)
    _check_offsets(text, chunks)
    step = CHUNK_TOK - CHUNK_OVERLAP
    expected = 1 + -(-(n_tok - CHUNK_TOK) // step)  # ceil
    assert len(chunks) == expected
    assert all(c.e5_tokens == CHUNK_TOK for c in chunks[:-1])
    # consecutive chunks share text (overlap), and the union covers the doc
    for a, b in zip(chunks, chunks[1:]):
        assert b.char_start < a.char_end
    assert chunks[0].char_start == 0 and chunks[-1].char_end == len(text.rstrip())


def test_mixed_language_and_combining_marks(chunker):
    text = (TR + DE + EN + "ábc ") * 12
    chunks = chunker.chunk(text)
    _check_offsets(text, chunks)
    assert len(chunks) >= 2


def test_dict_view(chunker):
    d = chunker.chunk_dicts(EN)
    assert d[0].keys() == {"ordinal", "char_start", "char_end", "text", "e5_tokens"}


def test_custom_sizes(model_dir):
    c = Chunker(model_dir, chunk_tok=10, overlap=3)
    text = " ".join(f"tok{i}" for i in range(50))
    chunks = c.chunk(text)
    _check_offsets(text, chunks)
    assert all(ch.e5_tokens <= 10 for ch in chunks)
    with pytest.raises(ValueError):
        Chunker(model_dir, chunk_tok=10, overlap=10)


def test_90kb_synthetic_document(chunker):
    rng = random.Random(20260922)
    parts = []
    size = 0
    while size < 90_000:
        p = rng.choice([TR, DE, EN]) + f"svc-qx{rng.randint(1, 99)} E{rng.randint(1000, 9999)} "
        parts.append(p)
        size += len(p.encode("utf-8"))
    text = "".join(parts)
    assert len(text.encode("utf-8")) >= 90_000
    chunks = chunker.chunk(text)
    _check_offsets(text, chunks)
    n_tok = chunker.count_tokens(text)
    step = CHUNK_TOK - CHUNK_OVERLAP
    expected = 1 + -(-(n_tok - CHUNK_TOK) // step)
    assert len(chunks) == expected
    print(f"\n90KB doc: {len(text)} chars, {n_tok} e5 tokens, {len(chunks)} chunks")
