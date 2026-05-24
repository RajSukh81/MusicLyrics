"""YouTube search and download helpers using yt-dlp only."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional

from config import Config

LOG = logging.getLogger(__name__)

_DOWNLOADS = Config.DOWNLOADS_DIR
os.makedirs(_DOWNLOADS, exist_ok=True)


# ── Search ───────────────────────────────────────────────────────────────────

async def search_youtube(query: str, max_results: int = 1) -> Optional[dict]:
    """Search YouTube using yt-dlp and return the first result dict.

    Keys: title, url, duration (seconds), thumbnail, channel.
    Returns ``None`` when nothing is found.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--default-search", f"ytsearch{max_results}",
            "--no-playlist", "--no-download",
            "--geo-bypass", "--no-check-certificates",
            "--socket-timeout", "15",
            query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0 or not stdout:
            LOG.warning("yt-dlp search failed: %s", stderr.decode()[:200] if stderr else "no output")
            return None

        info = json.loads(stdout.decode().split("\n")[0])
        return {
            "title": info.get("title", "Unknown"),
            "url": info.get("webpage_url", ""),
            "duration": int(info.get("duration", 0)),
            "thumbnail": info.get("thumbnail", ""),
            "channel": info.get("uploader", info.get("channel", "Unknown")),
        }
    except asyncio.TimeoutError:
        LOG.error("YouTube search timed out for: %s", query)
        return None
    except Exception:
        LOG.exception("YouTube search failed for query: %s", query)
        return None


async def search_youtube_many(query: str, limit: int = 5) -> list[dict]:
    """Return up to *limit* results."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--default-search", f"ytsearch{limit}",
            "--no-playlist", "--no-download",
            "--geo-bypass", "--no-check-certificates",
            "--socket-timeout", "15",
            query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        if proc.returncode != 0 or not stdout:
            return []

        out: list[dict] = []
        for line in stdout.decode().strip().split("\n"):
            if not line.strip():
                continue
            try:
                info = json.loads(line)
                out.append({
                    "title": info.get("title", "Unknown"),
                    "url": info.get("webpage_url", ""),
                    "duration": int(info.get("duration", 0)),
                    "thumbnail": info.get("thumbnail", ""),
                    "channel": info.get("uploader", info.get("channel", "Unknown")),
                })
            except json.JSONDecodeError:
                continue
        return out
    except Exception:
        LOG.exception("YouTube multi-search failed: %s", query)
        return []


# ── Download ─────────────────────────────────────────────────────────────────

async def download_audio(url: str) -> Optional[str]:
    """Download audio from *url* with yt-dlp; return the local file path."""
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 3,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "opus",
                "preferredquality": "128",
            }
        ],
    }
    return await _run_ytdlp(url, opts)


async def download_video(url: str) -> Optional[str]:
    """Download video+audio from *url* with yt-dlp; return file path."""
    opts = {
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s_video.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 3,
        "merge_output_format": "mp4",
    }
    return await _run_ytdlp(url, opts)


async def get_video_info(url: str) -> Optional[dict]:
    """Extract metadata without downloading."""
    try:
        import yt_dlp

        opts = {"quiet": True, "no_warnings": True, "geo_bypass": True}
        loop = asyncio.get_running_loop()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=False)
            )
        if not info:
            return None
        return {
            "title": info.get("title", "Unknown"),
            "url": info.get("webpage_url", url),
            "duration": int(info.get("duration", 0)),
            "thumbnail": info.get("thumbnail", ""),
            "channel": info.get("uploader", "Unknown"),
        }
    except Exception:
        LOG.exception("yt-dlp info extraction failed: %s", url)
        return None


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _run_ytdlp(url: str, opts: dict) -> Optional[str]:
    import yt_dlp

    loop = asyncio.get_running_loop()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            if not info:
                return None
            return ydl.prepare_filename(info)
    except Exception:
        LOG.exception("yt-dlp download failed: %s", url)
        return None


def is_youtube_url(url: str) -> bool:
    return bool(
        re.match(
            r"https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/",
            url,
        )
    )
