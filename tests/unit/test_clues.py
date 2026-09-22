import re

import pytest

from hlmemo.core.clues import CLUE_PATTERN, Clue, InvalidClue, decode_clue, encode_clue, is_valid_clue


def test_encode_item_and_chunk():
    assert encode_clue(7) == "v7"
    assert encode_clue(7, 0) == "v7.0"
    assert encode_clue(123456789, 42) == "v123456789.42"
    assert str(Clue(3, 1)) == "v3.1"


@pytest.mark.parametrize("raw, vid, ordinal", [("v1", 1, None), ("v1.0", 1, 0), ("v99.12", 99, 12), ("v10", 10, None)])
def test_decode_roundtrip(raw, vid, ordinal):
    c = decode_clue(raw)
    assert (c.version_id, c.ordinal) == (vid, ordinal)
    assert c.is_chunk is (ordinal is not None)
    assert c.encode() == raw
    assert re.fullmatch(CLUE_PATTERN, raw)


@pytest.mark.parametrize(
    "raw",
    ["", "v", "1", "V1", "v1.", "v.1", "v1.2.3", "v01", "v1.01", "v0", "v-1", "v1.-1", " v1", "v1 ", "v1\n", "vx", "v1.x", "v1,2", None, 1, 1.5, b"v1"],
)
def test_decode_rejects_malformed(raw):
    with pytest.raises(InvalidClue) as ei:
        decode_clue(raw)
    assert ei.value.code == "E_INVALID_ARG"
    assert is_valid_clue(raw) is False


@pytest.mark.parametrize("args", [(0,), (-1,), (1, -1), (True,), (1, True), ("1",), (1.0,), (1, 2.0)])
def test_encode_rejects_bad_ids(args):
    with pytest.raises(InvalidClue):
        encode_clue(*args)
