"""Clue ids (PHASE0-SPEC §3): ``v<version_id>`` (whole item) or ``v<version_id>.<ordinal>`` (chunk).

Wire pattern is ``^v[0-9]+(\\.[0-9]+)?$``; on top of that we only accept the
*canonical* spelling (no leading zeros, ``version_id >= 1``, ``ordinal >= 0``)
so that ``encode(decode(x)) == x`` for every accepted clue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CLUE_PATTERN = r"^v[0-9]+(\.[0-9]+)?$"
_CLUE_RE = re.compile(r"\Av(?P<vid>0|[1-9][0-9]*)(?:\.(?P<ord>0|[1-9][0-9]*))?\Z")


class InvalidClue(ValueError):
    code = "E_INVALID_ARG"

    def __init__(self, raw: object) -> None:
        super().__init__(f"malformed clue: {raw!r}")
        self.raw = raw


@dataclass(frozen=True, slots=True)
class Clue:
    version_id: int
    ordinal: int | None = None

    @property
    def is_chunk(self) -> bool:
        return self.ordinal is not None

    def encode(self) -> str:
        return encode_clue(self.version_id, self.ordinal)

    def __str__(self) -> str:
        return self.encode()


def encode_clue(version_id: int, ordinal: int | None = None) -> str:
    if isinstance(version_id, bool) or not isinstance(version_id, int) or version_id < 1:
        raise InvalidClue((version_id, ordinal))
    if ordinal is None:
        return f"v{version_id}"
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise InvalidClue((version_id, ordinal))
    return f"v{version_id}.{ordinal}"


def decode_clue(raw: object) -> Clue:
    if not isinstance(raw, str):
        raise InvalidClue(raw)
    m = _CLUE_RE.match(raw)
    if m is None:
        raise InvalidClue(raw)
    vid = int(m.group("vid"))
    if vid < 1:
        raise InvalidClue(raw)
    ordinal = m.group("ord")
    return Clue(vid, int(ordinal) if ordinal is not None else None)


def is_valid_clue(raw: object) -> bool:
    try:
        decode_clue(raw)
    except InvalidClue:
        return False
    return True


__all__ = ["Clue", "InvalidClue", "encode_clue", "decode_clue", "is_valid_clue", "CLUE_PATTERN"]
