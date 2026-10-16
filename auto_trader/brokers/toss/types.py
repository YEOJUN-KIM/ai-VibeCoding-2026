"""Shared broker errors and connection result."""

from dataclasses import dataclass


class TossApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


@dataclass(frozen=True)
class TossConnectionResult:
    connected: bool
    account_count: int
    message: str
