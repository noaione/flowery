"""Exceptions raised by :mod:`flowery`."""

from __future__ import annotations

__all__ = [
    "AccessDeniedError",
    "ApiError",
    "AuthError",
    "DownloadError",
    "FloweryError",
    "NotAuthenticatedError",
    "NotFoundError",
]


class FloweryError(Exception):
    """Base class for every error raised by this package."""


class AuthError(FloweryError):
    """Login or token refresh failed."""


class NotAuthenticatedError(AuthError):
    """The command needs a signed-in session but none is available."""


class ApiError(FloweryError):
    """The backend returned a non-successful response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AccessDeniedError(ApiError):
    """The account is not entitled to the requested content."""


class NotFoundError(ApiError):
    """The requested work or chapter does not exist."""


class DownloadError(FloweryError):
    """A file could not be fetched or written."""
