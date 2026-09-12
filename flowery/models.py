"""Pydantic models mirroring the backend payloads."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AudioEpisode",
    "AudioEpisodeDetail",
    "ChapterGroup",
    "Localized",
    "ManhuaSection",
    "ManhuaSectionDetail",
    "NovelChapter",
    "NovelChapterDetail",
    "NovelChapterPayload",
    "SeriesDetail",
    "SignedUrl",
    "UpdateCalendarEntry",
    "VideoEpisode",
    "VideoEpisodeDetail",
    "Work",
    "WorkType",
]


class WorkType(StrEnum):
    """The kind of content a work represents."""

    NOVEL = "novel"
    MANHUA = "manhua"
    VIDEO = "video"
    AUDIO = "audio"


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


Localized = dict[str, str]


class Work(_Model):
    """An entry of ``get_all_work_basic_data``."""

    id: str
    work_type: WorkType
    title: str
    slug: str
    cover: str | None = None
    created_at: str | None = None
    is_free: bool = False

    def display_title(self) -> str:
        return f"{self.title} [{self.work_type.value}]"


class _Chapterish(_Model):
    """Fields shared by every downloadable unit."""

    slug: str
    title: str | Localized | None = None
    status: str | None = None
    is_free: bool = False
    price: int = 0
    has_access: bool = True
    view_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None

    def _title_text(self, language: str = "en") -> str:
        """Flatten a localized title into a single string."""
        if isinstance(self.title, dict):
            return self.title.get(language) or next(iter(self.title.values()), "")
        return self.title or ""

    def label(self, language: str = "en") -> str:
        """Human readable title, preferring the requested locale."""
        return self._title_text(language) or self.slug


# --------------------------------------------------------------------------- #
# manhua
# --------------------------------------------------------------------------- #
class ManhuaSection(_Chapterish):
    """A manhua chapter as returned by ``get_sections_with_access``."""

    id: str
    section_number: int
    chapter_kind: str | None = None
    group_id: str | None = None
    group_title: str | None = None
    group_slug: str | None = None
    group_sort_order: int = 0
    position_in_group: int = 0
    manhua_id: str | None = None
    translation_id: str | None = None
    translator_id: str | None = None
    is_mature: bool = False
    purchase_count: int = 0
    image_urls: list[str] = Field(default_factory=list)
    header_image_url: str | None = None
    header_image_public_path: str | None = None


class ManhuaSectionDetail(ManhuaSection):
    """``get_section_by_index_with_access`` adds a couple of flags."""

    language: str | None = None
    support_page_mode: bool = False


# --------------------------------------------------------------------------- #
# novel
# --------------------------------------------------------------------------- #
class NovelChapter(_Chapterish):
    """A novel chapter as returned by ``get_chapters_with_access``."""

    chapter_id: str
    chapter_number: int
    chapter_kind: str | None = None
    group_id: str | None = None
    group_title: str | None = None
    group_slug: str | None = None
    group_sort_order: int = 0
    position_in_group: int = 0
    translation_id: str | None = None
    word_count: int = 0
    is_mature: bool = False
    purchase_count: int = 0

    @property
    def id(self) -> str:
        """Alias matching the other chapter models."""
        return self.chapter_id


class NovelChapterDetail(_Chapterish):
    """The inner ``chapter`` object of ``get_chapter_by_index_with_access``."""

    id: str
    chapter_number: int
    content: str = ""
    language: str | None = None
    chapter_kind: str | None = None
    group_id: str | None = None
    position_in_group: int = 0
    novel_id: str | None = None
    translation_id: str | None = None
    translator_id: str | None = None
    contributor_id: str | None = None
    word_count: int = 0
    is_mature: bool = False
    purchase_count: int = 0
    scheduled_at: str | None = None


class NovelChapterPayload(_Model):
    """Envelope returned by ``get_chapter_by_index_with_access``."""

    chapter: NovelChapterDetail
    has_access: bool = False


# --------------------------------------------------------------------------- #
# video
# --------------------------------------------------------------------------- #
class VideoEpisode(_Chapterish):
    """A video episode as returned by ``get_video_episodes_with_access``."""

    id: str
    episode_number: int
    description: Localized | None = None
    duration: int | None = None
    thumbnail_url: str | None = None
    video_url: str | None = None
    subtitle_url: Localized | None = None


class VideoEpisodeDetail(VideoEpisode):
    """``get_video_episode_by_index_with_access`` adds series level fields."""

    series_id: str | None = None
    total_episodes: int | None = None


# --------------------------------------------------------------------------- #
# audio
# --------------------------------------------------------------------------- #
class AudioEpisode(_Chapterish):
    """An audiobook/drama episode."""

    id: str
    episode_number: int
    description: Localized | None = None
    duration: int | None = None
    thumbnail_url: str | None = None
    audio_url: str | None = None
    subtitle_url: Localized | None = None


class AudioEpisodeDetail(AudioEpisode):
    """``get_audio_episode_by_index_with_access`` adds series level fields."""

    series_id: str | None = None
    total_episodes: int | None = None


# --------------------------------------------------------------------------- #
# series / misc
# --------------------------------------------------------------------------- #
class SeriesDetail(_Model):
    """Row from ``video_series`` / ``audio_series``."""

    id: str
    slug: str
    title: Localized | None = None
    description: Localized | None = None
    cover_url: str | None = None
    key_file_url: str | None = None
    authors: dict[str, Any] | None = None
    languages: list[str] = Field(default_factory=list)
    status: str | None = None
    update_status: str | None = None
    view_count: int = 0
    is_free: bool = False
    total_episode_count: int | None = None
    video_type: str | None = None


class SignedUrl(_Model):
    """One element of the ``storage/v1/object/sign/*`` response."""

    error: str | None = None
    path: str
    signed_url: str | None = Field(default=None, alias="signedURL")


class UpdateCalendarEntry(_Model):
    """Row of ``get_update_calendar``."""

    v_id: str
    v_content_type: str
    v_title: str
    v_episode_number: int | None = None
    v_scheduled_at: str | None = None
    v_work_slug: str | None = None
    v_work_title: str | None = None
    v_status: str | None = None


class ChapterGroup(BaseModel):
    """Chapters bucketed by their ``group_title``."""

    title: str
    slug: str = "main"
    sort_order: int = 0
    chapters: list[ManhuaSection | NovelChapter] = Field(default_factory=list)
