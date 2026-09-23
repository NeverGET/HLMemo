"""Librarian exception hierarchy.

``ProviderUnavailable`` (transient exhaustion, breaker open) is systemic: the worker re-queues the
job without consuming an attempt, so an LLM outage never turns into lost jobs (G-L3).
``BudgetDeferred`` pauses the librarian (heartbeat ``breaker_state=budget``). ``SchemaFail`` and
handler errors are job-specific and count toward the job's attempt cap.
"""

from __future__ import annotations


class LibrarianError(RuntimeError):
    """Base class."""


class LlmConfigError(LibrarianError):
    """A profile cannot be used as configured (no model, no key, no prices for a live call)."""


class LlmDisabled(LibrarianError):
    """``HLM_LLM_MODE=off``: no provider call is ever made (replay, maintenance)."""


class CassetteMiss(LibrarianError):
    """Strict replay found no recorded response for the request key (CC-5)."""


class ProviderUnavailable(LibrarianError):
    """Every profile exhausted its transient retries, or every breaker is open."""

    def __init__(self, message: str, *, retry_after_s: float = 5.0) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class BreakerOpen(ProviderUnavailable):
    pass


class BudgetDeferred(LibrarianError):
    """A reservation window (hour/day/month) has no room for the worst case of the next call."""


class JobCallCapExceeded(LibrarianError):
    """A job asked for more provider calls than ``HLM_LLM_JOB_CALL_CAP`` (loop guard)."""


class SchemaFail(LibrarianError):
    """The model answered twice with output that is not schema-valid JSON."""


class AuthorityLost(LibrarianError):
    """The apply-time capability recheck failed (CC-3)."""


class PrivacyDenied(LibrarianError):
    """The privacy gate denied an item of the prompt immediately before a provider attempt."""


class RoleNotAuthorized(LibrarianError):
    """The configured role has no matching owner decision event (§4b role ladder)."""


__all__ = [
    "AuthorityLost",
    "BreakerOpen",
    "BudgetDeferred",
    "CassetteMiss",
    "JobCallCapExceeded",
    "LibrarianError",
    "LlmConfigError",
    "LlmDisabled",
    "PrivacyDenied",
    "ProviderUnavailable",
    "RoleNotAuthorized",
    "SchemaFail",
]
