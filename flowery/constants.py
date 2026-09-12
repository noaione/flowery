"""Obfuscated constants.

Host names, endpoints and user-agent strings tied to the upstream service are
stored base64-encoded and only decoded at runtime, so that the source tree does
not contain plain-text references to them.
"""

from __future__ import annotations

import base64
from functools import lru_cache

__all__ = [
    "anon_key",
    "client_info",
    "client_platform",
    "client_platform_version",
    "d",
    "internal_user_agent",
    "remote_host",
    "remote_origin",
    "site_host",
    "user_agent",
    "video_cdn",
]


@lru_cache(maxsize=None)
def d(value: str) -> str:
    """Decode a base64 payload into its UTF-8 string form."""
    return base64.b64decode(value.encode("ascii")).decode("utf-8")


_ORIGIN = "aHR0cHM6Ly9sZ3VjcHZsd2p3aXJobHVrYmtpYi5zdXBhYmFzZS5jbw=="
_HOST = "bGd1Y3B2bHdqd2lyaGx1a2JraWIuc3VwYWJhc2UuY28="
_ANON = (
    "ZXlKaGJHY2lPaUpJVXpJMU5pSXNJblI1Y0NJNklrcFhWQ0o5LmV5SnBjM01pT2lKemRYQmhZbUZ6"
    "WlNJc0luSmxaaUk2SW14bmRXTndkbXgzYW5kcGNtaHNkV3RpYTJsaUlpd2ljbTlzWlNJNkltRnVi"
    "MjRpTENKcFlYUWlPakUzTXpNek56Y3lOamNzSW1WNGNDSTZNakEwT0RrMU16STJOMzAuU1BRM01v"
    "T0xxZGZSaDQybzNpX29Ua202TkM2b0JVQURoR3B4cXprRVBlOA=="
)
_CDN = "Y2RuLXZpZGVvLmJhaWhldmVyc2UuY29t"
_SITE = "YmFpaGV2ZXJzZS5jb20="
_UA_APP = "QmFpaGV2ZXJzZS8xLjAgKEFuZHJvaWQp"
_UA_INTERNAL = "a3Rvci1jbGllbnQ="
_CLIENT_INFO = "c3VwYWJhc2Uta3QvMy4yLjY="
_PLATFORM = "QW5kcm9pZA=="
_PLATFORM_VERSION = "MTE="


def remote_origin() -> str:
    """Base URL of the backend REST/auth/storage service."""
    return d(_ORIGIN)


def remote_host() -> str:
    """Bare host name of the backend service."""
    return d(_HOST)


def anon_key() -> str:
    """Public anonymous API key used as the ``apikey`` header."""
    return d(_ANON)


def video_cdn() -> str:
    """Host serving the encrypted HLS manifests and segments."""
    return d(_CDN)


def site_host() -> str:
    """Public website host."""
    return d(_SITE)


def user_agent() -> str:
    """User agent used for the public site and the video CDN."""
    return d(_UA_APP)


def internal_user_agent() -> str:
    """User agent used for backend API calls."""
    return d(_UA_INTERNAL)


def client_info() -> str:
    """Value of the ``X-Client-Info`` header."""
    return d(_CLIENT_INFO)


def client_platform() -> str:
    """Value of the ``X-Supabase-Client-Platform`` header."""
    return d(_PLATFORM)


def client_platform_version() -> str:
    """Value of the ``X-Supabase-Client-Platform-Version`` header."""
    return d(_PLATFORM_VERSION)
