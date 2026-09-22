"""Bi-temporal interval math and validation (PHASE0-SPEC §1.1, §3).

Conventions: intervals are half-open ``[from, to)``; an open end is ``None`` in Python and
``'infinity'`` in SQL (``null`` on the wire). All datetimes are timezone-aware UTC with
microsecond precision (Postgres ``timestamptz`` resolution) so JSON round-trips exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hlmemo.core.errors import ToolError, invalid_arg

ONE_US = timedelta(microseconds=1)
FUTURE_SLACK = timedelta(minutes=5)

Ts = datetime | None  # None == open end ('infinity')


# --------------------------------------------------------------------------- parsing/formatting
def parse_ts(raw: object, *, field: str = "timestamp") -> datetime:
    """RFC 3339 / ISO 8601 → aware UTC datetime (naive input is taken as UTC), µs precision."""
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip())
        except ValueError as exc:
            raise invalid_arg(f"{field}: not an RFC 3339 timestamp: {raw!r}", field=field) from exc
    else:
        raise invalid_arg(f"{field}: expected an RFC 3339 timestamp, got {type(raw).__name__}", field=field)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return to_utc(dt)


def parse_opt_ts(raw: object, *, field: str) -> datetime | None:
    return None if raw is None else parse_ts(raw, field=field)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def fmt_ts(dt: datetime | None) -> str | None:
    """Canonical JSON form ``YYYY-MM-DDTHH:MM:SS.ffffffZ``; ``None`` stays ``None``."""
    if dt is None:
        return None
    return to_utc(dt).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --------------------------------------------------------------------------- interval math
@dataclass(frozen=True, slots=True)
class Interval:
    start: datetime
    end: datetime | None = None  # None == infinity

    def __post_init__(self) -> None:
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"empty interval [{self.start}, {self.end})")

    def overlaps(self, other: Interval) -> bool:
        return overlaps(self.start, self.end, other.start, other.end)

    def contains(self, at: datetime) -> bool:
        return self.start <= at and (self.end is None or at < self.end)

    def as_json(self) -> dict[str, str | None]:
        return {"valid_from": fmt_ts(self.start), "valid_to": fmt_ts(self.end)}


def _lt(a: datetime | None, b: datetime | None) -> bool:
    """``a < b`` with ``None`` as +infinity."""
    if a is None:
        return False
    if b is None:
        return True
    return a < b


def overlaps(a_from: datetime, a_to: Ts, b_from: datetime, b_to: Ts) -> bool:
    """Half-open overlap ``[a_from, a_to) ∩ [b_from, b_to) ≠ ∅`` (``None`` = infinity)."""
    return _lt(a_from, b_to) and _lt(b_from, a_to)


def surviving_segments(v_from: datetime, v_to: Ts, c_from: datetime, c_to: Ts) -> list[Interval]:
    """Parts of the current row ``[v_from, v_to)`` outside the correction ``[c_from, c_to)``.

    §1.1 (3): ``[vf, cf)`` if ``vf < cf`` and ``[ct, vt)`` if ``ct < vt``. Only meaningful when
    the two intervals overlap (the caller checks); returns ``[]`` when the correction covers
    the row entirely.
    """
    out: list[Interval] = []
    if v_from < c_from:
        out.append(Interval(v_from, c_from))
    if c_to is not None and _lt(c_to, v_to):
        out.append(Interval(c_to, v_to))
    return out


def select_T(now: datetime, *superseded_recorded_at: datetime) -> datetime:
    """``T = greatest(clock_timestamp(), 1 µs + max(recorded_at of every superseded row))``
    truncated to microseconds (§1.1)."""
    t = to_utc(now)
    for rec in superseded_recorded_at:
        candidate = to_utc(rec) + ONE_US
        if candidate > t:
            t = candidate
    return t


# --------------------------------------------------------------------------- validation
def validate_interval(
    valid_from: datetime, valid_to: datetime | None, *, now: datetime, index: int
) -> Interval:
    """§3 content rules: ``valid_to ≤ valid_from`` or ``valid_from`` more than 5 min in the future
    (relative to ``now`` = T) → ``E_TEMPORAL``."""
    if valid_to is not None and valid_to <= valid_from:
        raise ToolError(
            "E_TEMPORAL",
            f"items[{index}]: valid_to must be later than valid_from",
            index=index,
            valid_from=fmt_ts(valid_from),
            valid_to=fmt_ts(valid_to),
        )
    if valid_from > to_utc(now) + FUTURE_SLACK:
        raise ToolError(
            "E_TEMPORAL",
            f"items[{index}]: valid_from is more than 5 minutes in the future",
            index=index,
            valid_from=fmt_ts(valid_from),
            now=fmt_ts(now),
        )
    return Interval(valid_from, valid_to)


__all__ = [
    "FUTURE_SLACK",
    "Interval",
    "ONE_US",
    "fmt_ts",
    "overlaps",
    "parse_opt_ts",
    "parse_ts",
    "select_T",
    "surviving_segments",
    "to_utc",
    "validate_interval",
]
