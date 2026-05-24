"""YouTube search and download helpers using yt-dlp Python API."""

from __future__ import annotations

import asyncio
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
    """Search YouTube using yt-dlp Python API and return the first result.

    Keys: title, url, duration (seconds), thumbnail, channel.
    Returns ``None`` when nothing is found.
    """
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _search_sync, query, max_results)
        return result
    except Exception:
        LOG.exception("YouTube search failed for query: %s", query)
        return None


def _search_sync(query: str, max_results: int = 1) -> Optional[dict]:
    """Synchronous YouTube search using yt-dlp."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "extract_flat": False,
        "default_search": f"ytsearch{max_results}",
        "socket_timeout": 15,
        "retries": 2,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if not info:
                return None

            # If it's a search result, entries will be in 'entries'
            entries = info.get("entries")
            if entries:
                item = entries[0] if entries else None
            else:
                item = info  # Direct URL

            if not item:
                return None

            return {
                "title": item.get("title", "Unknown"),
                "url": item.get("webpage_url", item.get("url", "")),
                "duration": int(item.get("duration") or 0),
                "thumbnail": item.get("thumbnail", ""),
                "channel": item.get("uploader", item.get("channel", "Unknown")),
            }
    except Exception:
        LOG.exception("yt-dlp search_sync failed for: %s", query)
        return None


async def search_youtube_many(query: str, limit: int = 5) -> list[dict]:
    """Return up to *limit* results."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _search_many_sync, query, limit)
    except Exception:
        LOG.exception("YouTube multi-search failed: %s", query)
        return []


def _search_many_sync(query: str, limit: int = 5) -> list[dict]:
    """Synchronous multi-result search."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "extract_flat": True,
        "default_search": f"ytsearch{limit}",
        "socket_timeout": 15,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if not info:
                return []

            entries = info.get("entries", [])
            out: list[dict] = []
            for item in entries:
                if not item:
                    continue
                out.append({
                    "title": item.get("title", "Unknown"),
                    "url": item.get("webpage_url", item.get("url", "")),
                    "duration": int(item.get("duration") or 0),
                    "thumbnail": item.get("thumbnail", ""),
                    "channel": item.get("uploader", item.get("channel", "Unknown")),
                })
            return out
    except Exception:
        LOG.exception("yt-dlp search_many_sync failed: %s", query)
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

        opts = {
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "socket_timeout": 15,
        }
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
            "duration": int(info.get("duration") or 0),
            "thumbnail": info.get("thumbnail", ""),
            "channel": info.get("uploader", "Unknown"),
        }
    except Exception:
        LOG.exception("yt-dlp info extraction failed: %s", url)
        return None


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _run_ytdlp(url: str, opts: dict) -> Optional[str]:
    import yt_dlp
    import glob

    loop = asyncio.get_running_loop()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            if not info:
                return None
            path = ydl.prepare_filename(info)
            # yt-dlp may change extension after post-processing
            if os.path.exists(path):
                return path
            # Try without extension
            base = os.path.splitext(path)[0]
            matches = glob.glob(f"{base}.*")
            if matches:
                return matches[0]
            return path
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
