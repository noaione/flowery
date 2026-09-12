# flowery

When girls meet each others.

> **Bring your own account.** `flowery` authenticates with credentials you provide and only
> downloads content that account already has access to. It does not purchase, unlock or
> bypass paid chapters; locked items are reported and skipped.

---

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or any Python package manager
- [`ffmpeg`](https://ffmpeg.org/) on `PATH` — optional, used to remux video into MP4.
  Without it, video is kept as a raw `.ts` stream.

## Install

```bash
git clone <this repo> && cd flowery
uv sync
uv run flowery --help
```

Or install it as a tool:

```bash
uv tool install .
flowery --help
```

## Quick start

```bash
# 1. Sign in once. The session is stored outside the repo.
flowery auth login

# 2. See what is available.
flowery works
flowery works --type novel --search autumn

# 3. Inspect a work before downloading.
flowery info spring-tide-reverie
flowery list spring-tide-reverie

# 4. Download.
flowery video spring-tide-reverie
flowery novel letters-in-autumn-en --chapters 1-10 --format md,epub
flowery manhua the-tea-club-en -c 1-5
flowery audio night-ferry
```

`flowery download <slug>` detects the media type for you and forwards to the right command.

## Commands

| Command | Description |
| --- | --- |
| `auth login` | Sign in and persist the session. Supports `--stdin` for `email\npassword`. |
| `auth status` | Show the signed-in account and token expiry. |
| `auth logout` | Forget the stored session. |
| `auth refresh` | Force an exchange of the refresh token. |
| `works` | List every work. Filter with `--type`, `--search`, `--free`. |
| `info` | Show metadata for one work (original title, translator, schedule, ...). |
| `list` | List the chapters or episodes of a work. |
| `calendar` | Show the release schedule for a month. |
| `manhua` | Download manhua chapters as page images, plus CBZ archives. |
| `novel` | Download novel chapters as Markdown / text / HTML / EPUB. |
| `video` | Download videos, decrypting the AES-128 HLS stream. |
| `audio` | Download audio drama / audiobook episodes as MP3. |
| `download` | Detect the media type of a work and download it. |

Every listing command accepts `--json` for scripting, and every download command accepts
`-o/--output` to override the output root, `-j/--concurrency` for parallelism, and `--force`
to re-download existing files.

### Selecting chapters

`--chapters` / `--episodes` accept a comma separated list of numbers and ranges:

```bash
-c 3            # just chapter 3
-c 1-5          # chapters 1 through 5
-c 1-5,8,12-    # chapters 1-5, 8, and everything from 12 onward
-c all          # everything (the default)
```

### Video specifics

```bash
flowery video spring-tide-reverie -q 480p        # best | worst | 720p | 1080p | ...
flowery video spring-tide-reverie --no-remux     # keep the raw .ts stream
flowery video spring-tide-reverie --no-subtitles # skip .vtt sidecars
flowery video spring-tide-reverie --keep-segments
```

## Where files go

```
DOWNLOADS/
└── <work-slug>/
    ├── work.json                     # work level metadata
    ├── index.json                    # chapter/episode index
    └── 001-chapter-1/                # one directory per chapter/episode
        ├── 001.jpg …                 # manhua: pages, plus 001-chapter-1.cbz alongside
        ├── chapter.md / chapter.txt  # novel
        ├── <slug>.mp4                # video
        ├── <slug>.mp3                # audio
        └── <slug>.<lang>.vtt         # subtitles
```

The root defaults to `$CWD/DOWNLOADS`. Override it per run with `-o/--output`, or permanently
by setting `download_dir` in the config file.

## Configuration

Stored at:

- Windows — `%LOCALAPPDATA%\flowery\user.json`
- macOS / Linux — `~/.config/flowery/user.json`

The file holds the access token, refresh token, expiry and account details. Access tokens are
refreshed automatically before they expire. On POSIX systems the file is written with `0600`
permissions.

## Development

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
```

## Legal

For personal archival of content you legitimately have access to. Respect the site's terms of
service and the rights of the authors and translators; do not redistribute what you download.
