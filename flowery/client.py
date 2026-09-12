"""Async client for the backend."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import IO, Any, Self

import httpx

from . import constants as c
from .config import Session, UserConfig, load_config, save_config
from .errors import AccessDeniedError, ApiError, AuthError, DownloadError, NotFoundError
from .models import (
    AudioEpisode,
    AudioEpisodeDetail,
    ManhuaSection,
    ManhuaSectionDetail,
    NovelChapter,
    NovelChapterPayload,
    SeriesDetail,
    SignedUrl,
    UpdateCalendarEntry,
    VideoEpisode,
    VideoEpisodeDetail,
    Work,
    WorkType,
)

__all__ = ("AUTH_PREFIX", "REST_PREFIX", "STORAGE_PREFIX", "FloweryClient")

AUTH_PREFIX = "/auth/v1"
REST_PREFIX = "/rest/v1"
STORAGE_PREFIX = "/storage/v1"

# Refresh the access token when it is this close to expiring (seconds).
_REFRESH_MARGIN = 120.0


class FloweryClient:
    """Thin, typed wrapper around the backend's REST/auth/storage surface.

    The client owns an :class:`httpx.AsyncClient` and a persisted
    :class:`~flowery.config.Session`. Use it as an async context manager::

        async with FloweryClient() as client:
            await client.login("me@example.com", "hunter2")
            works = await client.list_works()
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        config: UserConfig | None = None,
        timeout: float = 30.0,
        http2: bool = True,
    ) -> None:
        self.config = config if config is not None else load_config()
        if session is not None:
            self.config.session = session
        self.session = self.config.session
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=c.remote_origin(),
            timeout=httpx.Timeout(timeout, read=timeout * 4),
            http2=http2,
            follow_redirects=True,
            headers={"User-Agent": c.internal_user_agent()},
        )

    # -- lifecycle -------------------------------------------------------------
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()

    @property
    def http(self) -> httpx.AsyncClient:
        """The underlying HTTP client, for streaming downloads."""
        return self._client

    @property
    def authenticated(self) -> bool:
        return self.session.authenticated

    # -- header plumbing -------------------------------------------------------
    def _headers(self, *, auth: bool, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        token = c.anon_key()
        if auth and self.session.access_token:
            token = self.session.access_token
        headers = {
            "apikey": c.anon_key(),
            "Authorization": f"Bearer {token}",
            "X-Client-Info": c.client_info(),
            "X-Supabase-Client-Platform": c.client_platform(),
            "X-Supabase-Client-Platform-Version": c.client_platform_version(),
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Best-effort extraction of a human readable error from a response."""
        try:
            body = response.text
        except (httpx.ResponseNotRead, RuntimeError):
            return response.reason_phrase or "request failed"
        detail = body.strip()
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in ("error_description", "message", "msg", "error", "hint", "details"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    detail = value
                    break
            upstream = payload.get("statusCode")
            if upstream and str(upstream) not in detail:
                detail = f"[{upstream}] {detail}"
        return detail

    @classmethod
    def _raise_for_status(cls, response: httpx.Response) -> None:
        if response.is_success:
            return
        detail = cls._error_detail(response)
        status = response.status_code
        if status == 404:
            raise NotFoundError(detail or "not found", status=status)
        if status in (401, 403):
            raise AccessDeniedError(detail or "access denied", status=status)
        raise ApiError(
            f"{response.request.method} {response.request.url.path} failed ({status}): {detail}",
            status=status,
        )

    @classmethod
    async def _araise_for_status(cls, response: httpx.Response) -> None:
        """Like :meth:`_raise_for_status`, but safe on streaming responses."""
        if response.is_success:
            return
        try:
            await response.aread()
        except httpx.HTTPError:
            pass
        cls._raise_for_status(response)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        auth: bool = True,
        retry_on_401: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        if auth:
            await self.ensure_fresh_token()
        response = await self._client.request(method, url, headers=self._headers(auth=auth), **kwargs)
        if response.status_code == 401 and auth and retry_on_401 and self.session.refresh_token:
            await self._refresh(force=True)
            response = await self._client.request(method, url, headers=self._headers(auth=auth), **kwargs)
        self._raise_for_status(response)
        return response

    async def _rpc(self, name: str, payload: Mapping[str, Any]) -> Any:
        response = await self._request("POST", f"{REST_PREFIX}/rpc/{name}", json=dict(payload))
        return response.json()

    # -- auth ------------------------------------------------------------------
    async def login(self, email: str, password: str) -> Session:
        """Exchange credentials for a session and persist it."""
        response = await self._client.post(
            f"{AUTH_PREFIX}/token",
            params={"grant_type": "password"},
            json={"email": email, "password": password},
            headers=self._headers(auth=False, extra={"Content-Type": "application/json"}),
        )
        if not response.is_success:
            detail = "invalid email or password"
            try:
                payload = response.json()
                detail = payload.get("error_description") or payload.get("msg") or detail
            except ValueError:
                pass
            raise AuthError(detail)
        self._ingest_token(response.json())
        save_config(self.config)
        return self.session

    async def logout(self) -> None:
        """Forget the persisted session."""
        self.session = Session()
        self.config.session = self.session
        save_config(self.config)

    async def _refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if not force and not self._needs_refresh():
                return
            token = (self.session.refresh_token or "").strip()
            if not token:
                raise AuthError("no refresh token available, please log in again")
            response = await self._client.post(
                f"{AUTH_PREFIX}/token",
                params={"grant_type": "refresh_token"},
                json={"refresh_token": token},
                headers=self._headers(auth=False, extra={"Content-Type": "application/json"}),
            )
            if not response.is_success:
                raise AuthError("session expired, please log in again")
            self._ingest_token(response.json())
            save_config(self.config)

    def _ingest_token(self, payload: Mapping[str, Any]) -> None:
        user = payload.get("user") or {}
        metadata = (user.get("user_metadata") if isinstance(user, dict) else None) or {}
        expires_at = payload.get("expires_at")
        if expires_at is None and payload.get("expires_in"):
            expires_at = int(time.time()) + int(payload["expires_in"])
        self.session = Session(
            email=user.get("email") or self.session.email,
            user_id=user.get("id") or self.session.user_id,
            display_name=metadata.get("display_name") or self.session.display_name,
            access_token=payload.get("access_token"),
            refresh_token=payload.get("refresh_token") or self.session.refresh_token,
            expires_at=int(expires_at) if expires_at else None,
        )
        self.config.session = self.session

    def _needs_refresh(self) -> bool:
        if not self.session.authenticated:
            return False
        if self.session.expires_at is None:
            return False
        return time.time() >= (self.session.expires_at - _REFRESH_MARGIN)

    async def ensure_fresh_token(self) -> None:
        """Refresh the access token if it is about to expire."""
        if self._needs_refresh():
            await self._refresh(force=True)

    async def refresh_session(self) -> Session:
        """Unconditionally exchange the refresh token for a new session."""
        await self._refresh(force=True)
        return self.session

    def require_auth(self) -> None:
        """Raise :class:`~flowery.errors.AuthError` when no session is stored."""
        from .errors import NotAuthenticatedError

        if not self.session.authenticated:
            raise NotAuthenticatedError("not logged in - run `flowery auth login` first")

    # -- catalogue -------------------------------------------------------------
    async def list_works(self) -> list[Work]:
        """Every published work, across all four media types."""
        data = await self._rpc("get_all_work_basic_data", {})
        return [Work.model_validate(item) for item in data or []]

    async def find_work(self, needle: str) -> Work:
        """Resolve a slug / id / (partial) title into a single :class:`Work`."""
        works = await self.list_works()
        lowered = needle.strip().lower()
        if not lowered:
            raise NotFoundError("no work specified")

        exact = [w for w in works if lowered in (w.slug.lower(), w.id.lower())]
        if len(exact) == 1:
            return exact[0]

        partial = [
            w for w in works if lowered in w.slug.lower() or lowered in w.title.lower() or lowered == w.id.lower()
        ]
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise NotFoundError(f"no work matches {needle!r}")
        candidates = ", ".join(sorted(w.slug for w in partial))
        from .errors import FloweryError

        raise FloweryError(f"{needle!r} is ambiguous, did you mean one of: {candidates}")

    # -- metadata --------------------------------------------------------------
    async def manhua_translation(self, work_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"{REST_PREFIX}/manhua_translations",
            params={
                "id": f"eq.{work_id}",
                "select": "*,manhuas(title,author_id:authors(id,display_name,slug)),translators(id,display_name,slug)",
            },
        )
        rows = payload.json()
        return rows[0] if rows else {}

    async def novel_translation(self, work_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"{REST_PREFIX}/novel_translations",
            params={
                "id": f"eq.{work_id}",
                "select": "*,novels(title,author_id:authors(id,display_name,slug)),translators(id,display_name,slug)",
            },
        )
        rows = payload.json()
        return rows[0] if rows else {}

    async def series(self, work: Work) -> SeriesDetail:
        """Fetch the ``video_series`` / ``audio_series`` row for a work."""
        table = "video_series" if work.work_type is WorkType.VIDEO else "audio_series"
        payload = await self._request(
            "GET",
            f"{REST_PREFIX}/{table}",
            params={"id": f"eq.{work.id}", "select": "*,authors:author_id(id,display_name,slug)"},
        )
        rows = payload.json()
        if not rows:
            raise NotFoundError(f"series for {work.slug!r} not found")
        return SeriesDetail.model_validate(rows[0])

    # -- chapter / episode listings -------------------------------------------
    async def manhua_sections(self, translation_id: str, *, detail: bool = True) -> list[ManhuaSection]:
        data = await self._rpc("get_sections_with_access", {"p_translation_id": translation_id})
        model = ManhuaSectionDetail if detail else ManhuaSection
        sections = [model.model_validate(item) for item in data or []]
        return sorted(sections, key=lambda s: s.section_number)

    async def manhua_section(self, translation_id: str, number: int) -> ManhuaSectionDetail:
        data = await self._rpc(
            "get_section_by_index_with_access",
            {"p_translation_id": translation_id, "p_section_number": number},
        )
        if not data:
            raise NotFoundError(f"manhua section {number} not found")
        return ManhuaSectionDetail.model_validate(data)

    async def novel_chapters(self, translation_id: str) -> list[NovelChapter]:
        data = await self._rpc("get_chapters_with_access", {"p_translation_id": translation_id})
        chapters = [NovelChapter.model_validate(item) for item in data or []]
        return sorted(chapters, key=lambda ch: ch.chapter_number)

    async def novel_chapter(self, translation_id: str, number: int) -> NovelChapterPayload:
        data = await self._rpc(
            "get_chapter_by_index_with_access",
            {"p_translation_id": translation_id, "p_chapter_number": number},
        )
        if not data:
            raise NotFoundError(f"novel chapter {number} not found")
        return NovelChapterPayload.model_validate(data)

    async def video_episodes(self, series_id: str) -> list[VideoEpisode]:
        data = await self._rpc("get_video_episodes_with_access", {"p_series_id": series_id})
        episodes = [VideoEpisode.model_validate(item) for item in data or []]
        return sorted(episodes, key=lambda ep: ep.episode_number)

    async def video_episode(self, series_id: str, number: int) -> VideoEpisodeDetail:
        data = await self._rpc(
            "get_video_episode_by_index_with_access",
            {"p_series_id": series_id, "p_episode_number": number},
        )
        if not data:
            raise NotFoundError(f"video episode {number} not found")
        return VideoEpisodeDetail.model_validate(data)

    async def audio_episodes(self, series_id: str) -> list[AudioEpisode]:
        data = await self._rpc("get_audio_episodes_with_access", {"p_series_id": series_id})
        episodes = [AudioEpisode.model_validate(item) for item in data or []]
        return sorted(episodes, key=lambda ep: ep.episode_number)

    async def audio_episode(self, series_id: str, number: int) -> AudioEpisodeDetail:
        data = await self._rpc(
            "get_audio_episode_by_index_with_access",
            {"p_series_id": series_id, "p_episode_number": number},
        )
        if not data:
            raise NotFoundError(f"audio episode {number} not found")
        return AudioEpisodeDetail.model_validate(data)

    async def update_calendar(self, year: int, month: int) -> list[UpdateCalendarEntry]:
        data = await self._rpc("get_update_calendar", {"p_year": year, "p_month": month})
        return [UpdateCalendarEntry.model_validate(item) for item in data or []]

    # -- storage ---------------------------------------------------------------
    def public_url(self, path: str) -> str:
        """URL for an object in a public bucket."""
        return f"{c.remote_origin()}{STORAGE_PREFIX}/object/public/{path.lstrip('/')}"

    def authenticated_url(self, bucket: str, path: str) -> str:
        """URL for an object that requires an ``Authorization`` header."""
        return f"{c.remote_origin()}{STORAGE_PREFIX}/object/authenticated/{bucket}/{path.lstrip('/')}"

    def manhua_image_url(self, path: str) -> str:
        """URL of a single manhua page image."""
        return self.authenticated_url("manhua", path)

    async def sign(self, bucket: str, paths: Sequence[str], *, expires_in: int = 3600) -> dict[str, str]:
        """Create signed URLs for ``paths`` inside ``bucket``."""
        if not paths:
            return {}
        payload = await self._request(
            "POST",
            f"{STORAGE_PREFIX}/object/sign/{bucket}",
            json={"paths": list(paths), "expiresIn": expires_in},
        )
        rows = [SignedUrl.model_validate(item) for item in payload.json() or []]
        base = f"{c.remote_origin()}{STORAGE_PREFIX}"
        out: dict[str, str] = {}
        for row in rows:
            if row.error or not row.signed_url:
                raise DownloadError(f"could not sign {row.path!r}: {row.error or 'unknown error'}")
            url = row.signed_url
            out[row.path] = url if url.startswith("http") else f"{base}{url}"
        return out

    async def fetch_bytes(self, url: str, *, authenticated: bool = False) -> bytes:
        """Fetch a small file (e.g. the HLS key) into memory."""
        headers = self._headers(auth=authenticated)
        headers["User-Agent"] = c.user_agent()
        headers.pop("Accept", None)
        response = await self._client.get(url, headers=headers)
        self._raise_for_status(response)
        return response.content

    async def write_to(
        self,
        url: str,
        sink: IO[bytes],
        *,
        authenticated: bool = False,
        chunk_size: int = 64 * 1024,
    ) -> int:
        """Stream a remote object into ``sink``, returning the byte count."""
        headers = self._headers(auth=authenticated)
        headers["User-Agent"] = c.user_agent()
        size = 0
        async with self._client.stream("GET", url, headers=headers) as response:
            await self._araise_for_status(response)
            async for chunk in response.aiter_bytes(chunk_size):
                sink.write(chunk)
                size += len(chunk)
        return size


def group_by_title(items: Iterable[Any]) -> list[tuple[str, str, int, list[Any]]]:
    """Bucket chapter-like objects by ``(group_title, group_slug, group_sort_order)``."""
    buckets: dict[tuple[str, str, int], list[Any]] = {}
    for item in items:
        key = (
            getattr(item, "group_title", None) or "Main Story",
            getattr(item, "group_slug", None) or "main",
            getattr(item, "group_sort_order", 0) or 0,
        )
        buckets.setdefault(key, []).append(item)
    return [
        (title, slug, order, sorted(chapters, key=lambda ch: getattr(ch, "position_in_group", 0)))
        for (title, slug, order), chapters in sorted(buckets.items(), key=lambda kv: kv[0][2])
    ]
