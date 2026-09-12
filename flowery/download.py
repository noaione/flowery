"""File downloading helpers: plain objects plus encrypted HLS streams."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)

from .errors import DownloadError

if TYPE_CHECKING:  # pragma: no cover
    from .client import FloweryClient

__all__ = [
    "DownloadReport",
    "FileJob",
    "download_hls",
    "fetch_many",
    "human_size",
    "parse_playlist",
    "pick_variant",
    "remove_temp",
    "safe_name",
]

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_name(name: str, *, fallback: str = "unnamed", max_length: int = 120) -> str:
    """Make ``name`` safe to use as a single path component on any platform."""
    cleaned = _UNSAFE.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > max_length:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and len(suffix) <= 10:
            cleaned = stem[: max_length - len(suffix) - 1] + "." + suffix
        else:
            cleaned = cleaned[:max_length]
    return cleaned


def human_size(num_bytes: float) -> str:
    """Format a byte count for humans."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024 or unit == "TiB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TiB"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def remove_temp(root: Path) -> None:
    """Delete ``*.part`` leftovers below ``root``."""
    if not root.exists():
        return
    for leftover in root.rglob("*.part"):
        leftover.unlink(missing_ok=True)


@dataclass(slots=True)
class FileJob:
    """A single remote object to write to disk."""

    url: str
    dest: Path
    authenticated: bool = False
    label: str = ""

    def display(self) -> str:
        return self.label or self.dest.name


@dataclass(slots=True)
class DownloadReport:
    """Outcome of a :func:`fetch_many` call."""

    written: int = 0
    skipped: int = 0
    failed: list[tuple[FileJob, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        bits = [f"{self.written} downloaded"]
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        if self.failed:
            bits.append(f"{self.failed.__len__()} failed")
        return ", ".join(bits)


async def _fetch_one(client: FloweryClient, job: FileJob) -> int:
    """Stream a single :class:`FileJob` to disk. Returns bytes written."""
    if job.dest.exists() and job.dest.stat().st_size > 0:
        return 0
    job.dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = job.dest.with_name(job.dest.name + ".part")
    try:
        with tmp.open("wb") as handle:
            size = await client.write_to(job.url, handle, authenticated=job.authenticated)
        if size == 0:
            raise DownloadError("empty response body")
        tmp.replace(job.dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return size


async def fetch_many(
    client: FloweryClient,
    jobs: Sequence[FileJob],
    *,
    concurrency: int = 8,
    description: str = "Downloading",
    console: Console | None = None,
    skip_existing: bool = True,
) -> DownloadReport:
    """Download ``jobs`` concurrently, rendering a Rich progress bar."""
    report = DownloadReport()
    pending: list[FileJob] = []
    for job in jobs:
        if skip_existing and job.dest.exists() and job.dest.stat().st_size > 0:
            report.skipped += 1
        else:
            pending.append(job)
    if not pending:
        return report

    semaphore = asyncio.Semaphore(max(1, concurrency))
    console = console or Console()
    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("{task.completed}/{task.total}"),
        TextColumn("[dim]{task.fields[current]}"),
        console=console,
        transient=True,
    )

    async def worker(job: FileJob, task_id: TaskID) -> None:
        async with semaphore:
            progress.update(task_id, current=job.display()[:48])
            try:
                await _fetch_one(client, job)
            except Exception as exc:
                report.failed.append((job, str(exc)))
            else:
                report.written += 1
            finally:
                progress.advance(task_id)
                progress.refresh()

    with progress:
        task_id = progress.add_task(description, total=len(pending), current="")
        await asyncio.gather(*(worker(job, task_id) for job in pending))
    return report


# --------------------------------------------------------------------------- #
# HLS
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _KeyInfo:
    uri: str | None = None
    iv: bytes | None = None
    method: str = "NONE"


@dataclass(slots=True)
class _Variant:
    uri: str
    bandwidth: int = 0
    resolution: str = ""

    @property
    def height(self) -> int:
        if "x" not in self.resolution:
            return 0
        try:
            return int(self.resolution.split("x", 1)[1])
        except ValueError:
            return 0

    def label(self) -> str:
        return self.resolution or (f"{self.bandwidth // 1000}kbps" if self.bandwidth else self.uri)


@dataclass(slots=True)
class _Playlist:
    segments: list[str] = field(default_factory=list)
    key: _KeyInfo = field(default_factory=_KeyInfo)
    media_sequence: int = 0
    variants: list[_Variant] = field(default_factory=list)
    is_master: bool = False


def parse_playlist(text: str) -> _Playlist:
    """Parse an m3u8 manifest (master or media) into a :class:`_Playlist`."""
    playlist = _Playlist()
    lines = [line.strip() for line in text.splitlines()]
    pending: dict[str, str] | None = None
    for line in lines:
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                playlist.media_sequence = int(line.split(":", 1)[1].strip())
            except ValueError:
                playlist.media_sequence = 0
        elif line.startswith("#EXT-X-KEY:"):
            playlist.key = _parse_key(line)
        elif line.startswith("#EXT-X-STREAM-INF:"):
            pending = dict(_ATTR.findall(line.split(":", 1)[1]))
            playlist.is_master = True
        elif line.startswith("#"):
            continue
        elif pending is not None:
            playlist.variants.append(
                _Variant(
                    uri=line,
                    bandwidth=int(pending.get("BANDWIDTH") or 0),
                    resolution=(pending.get("RESOLUTION") or "").strip('"'),
                )
            )
            pending = None
        else:
            playlist.segments.append(line)
    return playlist


def pick_variant(variants: Sequence[_Variant], quality: str) -> _Variant:
    """Choose a variant from a master playlist.

    ``quality`` accepts ``best``, ``worst`` or a resolution such as ``720p`` /
    ``1280x720``. An exact match falls back to the closest larger variant, then
    the closest smaller one.
    """
    if not variants:
        raise DownloadError("master playlist contains no variants")
    ordered = sorted(variants, key=lambda v: (v.height, v.bandwidth))
    wanted = quality.strip().lower()
    if wanted in ("", "best", "max", "highest"):
        return ordered[-1]
    if wanted in ("worst", "min", "lowest"):
        return ordered[0]

    text = wanted.removesuffix("p")
    if "x" in text:
        text = text.split("x", 1)[1]
    try:
        target = int(text)
    except ValueError as exc:
        raise DownloadError(f"unrecognised quality {quality!r}") from exc

    taller = [v for v in ordered if v.height >= target]
    if taller:
        return taller[0]
    return ordered[-1]


_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _parse_key(line: str) -> _KeyInfo:
    attrs = {key: value.strip('"') for key, value in _ATTR.findall(line.split(":", 1)[1])}
    iv: bytes | None = None
    raw_iv = attrs.get("IV")
    if raw_iv:
        raw_iv = raw_iv[2:] if raw_iv.lower().startswith("0x") else raw_iv
        iv = bytes.fromhex(raw_iv.rjust(32, "0"))
    return _KeyInfo(uri=attrs.get("URI"), iv=iv, method=attrs.get("METHOD", "NONE"))


def _derive_iv(media_sequence: int) -> bytes:
    return media_sequence.to_bytes(16, "big")


def decrypt_segment(payload: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC decrypt a single HLS segment."""
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    return decryptor.update(payload) + decryptor.finalize()


def _resolve(base: str, reference: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, reference)


async def download_hls(
    client: FloweryClient,
    manifest_url: str,
    dest: Path,
    key: bytes | None = None,
    *,
    quality: str = "best",
    concurrency: int = 6,
    console: Console | None = None,
    remux: bool = True,
    keep_segments: bool = False,
) -> Path:
    """Download an AES-128 encrypted HLS stream and write a playable file.

    ``dest`` should be the final media path (``*.mp4``). Segments are fetched
    concurrently into a scratch directory, decrypted, concatenated and — when
    ``ffmpeg`` is available — remuxed into an MP4 container.

    ``key`` is the content key. Pass it explicitly: the ``EXT-X-KEY`` URI in the
    manifest points at an authenticated endpoint that returns 401 to API
    clients, so it is only used as a last resort.
    """
    console = console or Console()
    text = (await client.fetch_bytes(manifest_url)).decode("utf-8", errors="replace")
    playlist = parse_playlist(text)

    if playlist.is_master and playlist.variants and not playlist.segments:
        variant = pick_variant(playlist.variants, quality)
        console.print(f"[dim]quality[/] {variant.label()} [dim]({variant.uri})")
        manifest_url = _resolve(manifest_url, variant.uri)
        text = (await client.fetch_bytes(manifest_url)).decode("utf-8", errors="replace")
        playlist = parse_playlist(text)

    if not playlist.segments:
        raise DownloadError(f"no media segments found in {manifest_url}")

    if playlist.key.method not in ("NONE", "AES-128"):
        raise DownloadError(f"unsupported HLS encryption method {playlist.key.method}")
    key_is_used = playlist.key.method == "AES-128"

    segment_key = key
    if key_is_used and segment_key is None:
        if not playlist.key.uri:
            raise DownloadError("stream is AES-128 encrypted but no key was supplied")
        segment_key = await client.fetch_bytes(_resolve(manifest_url, playlist.key.uri))
    if key_is_used and segment_key is None:
        raise DownloadError("stream is AES-128 encrypted but no key was supplied")
    if key_is_used and len(segment_key or b"") != 16:
        raise DownloadError(f"expected a 16-byte AES key, got {len(segment_key or b'')} bytes")

    scratch = dest.with_name(dest.name + ".segments")
    scratch.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max(1, concurrency))
    failures: list[tuple[str, str]] = []
    decrypted = 0

    progress = Progress(
        SpinnerColumn(style="magenta"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        TextColumn("[dim]{task.fields[size]}"),
        console=console,
        transient=True,
    )

    async def grab(index: int, reference: str, task_id: TaskID) -> None:
        nonlocal decrypted
        segment_path = scratch / f"{index:05d}.ts"
        if segment_path.exists() and segment_path.stat().st_size > 0:
            decrypted += segment_path.stat().st_size
            progress.advance(task_id)
            return
        url = _resolve(manifest_url, reference)
        async with semaphore:
            try:
                payload = await client.fetch_bytes(url)
            except Exception as exc:
                failures.append((reference, str(exc)))
                return
            if key_is_used and segment_key:
                iv = playlist.key.iv or _derive_iv(playlist.media_sequence + index)
                payload = decrypt_segment(payload, segment_key, iv)
            segment_path.write_bytes(payload)
            decrypted += len(payload)
            progress.update(task_id, advance=1, size=human_size(decrypted))
            progress.refresh()

    with progress:
        task_id = progress.add_task(dest.name, total=len(playlist.segments), size="")
        await asyncio.gather(*(grab(i, ref, task_id) for i, ref in enumerate(playlist.segments)))

    if failures:
        raise DownloadError(f"{len(failures)} segment(s) failed, e.g. {failures[0][1]}")
    console.print(f"[dim]decrypted {human_size(decrypted)} across {len(playlist.segments)} segments[/]")

    dest.parent.mkdir(parents=True, exist_ok=True)
    merged = dest.with_suffix(".ts") if remux else dest
    with merged.open("wb") as out:
        for index in range(len(playlist.segments)):
            out.write((scratch / f"{index:05d}.ts").read_bytes())

    if not keep_segments and not remux:
        shutil.rmtree(scratch, ignore_errors=True)

    if remux:
        final = _remux(merged, dest, console)
        if not keep_segments:
            shutil.rmtree(scratch, ignore_errors=True)
        return final
    return merged


def _remux(source: Path, dest: Path, console: Console) -> Path:
    """Remux a concatenated MPEG-TS file into an MP4 using ffmpeg, if present."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        console.print("[yellow]ffmpeg not found - keeping the raw .ts stream[/]")
        return source
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        str(dest),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        console.print(f"[yellow]ffmpeg remux failed ({exc}); keeping the raw .ts stream[/]")
        return source
    source.unlink(missing_ok=True)
    return dest
