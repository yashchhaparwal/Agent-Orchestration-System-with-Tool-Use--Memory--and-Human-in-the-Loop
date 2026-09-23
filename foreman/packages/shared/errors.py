"""Error taxonomy (Rules.md §4). Catch the most specific class first."""

from __future__ import annotations


class ForemanError(Exception):
    """Base class for every error raised by Foreman code."""


class RetryableError(ForemanError):
    """429, 5xx, timeouts, connection failures — safe to retry or move to the next chain entry."""


class NonRetryableError(ForemanError):
    """4xx other than 429, misconfiguration, unknown models — retrying will not help."""


class ToolDeniedError(ForemanError):
    """The permission gate blocked a tool call."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ApprovalRequiredError(ForemanError):
    """The permission gate requires a human decision before this call may run."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BudgetExceededError(ForemanError):
    """Iterations, tokens, cost, or wall-clock budget exhausted."""


class SchemaValidationError(ForemanError):
    """Model output did not match the requested schema."""
