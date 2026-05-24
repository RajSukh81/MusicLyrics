"""YouTube search and download helpers using youtube-search-python + yt-dlp."""

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
    """Search YouTube and return the first result dict.

    Keys: title, url, duration (seconds), thumbnail, channel.
    Returns ``None`` when nothing is found.
    """
    try:
        from youtubesearchpython.__future__ import VideosSearch

        search = VideosSearch(query, limit=max_results)
        results = await search.next()
        if not results or not results.get("result"):
            return None
        item = results["result"][0]
        raw_dur = item.get("duration", "0:00")
        return {
            "title": item.get("title", "Unknown"),
            "url": item.get("link", ""),
            "duration": _parse_duration(raw_dur),
            "thumbnail": _best_thumbnail(item),
            "channel": item.get("channel", {}).get("name", "Unknown"),
        }
    except Exception:
        LOG.exception("YouTube search failed for query: %s", query)
        return None


async def search_youtube_many(query: str, limit: int = 5) -> list[dict]:
    """Return up to *limit* results."""
    try:
        from youtubesearchpython.__future__ import VideosSearch

        search = VideosSearch(query, limit=limit)
        results = await search.next()
        if not results or not results.get("result"):
            return []
        out: list[dict] = []
        for item in results["result"]:
            out.append({
                "title": item.get("title", "Unknown"),
                "url": item.get("link", ""),
                "duration": _parse_duration(item.get("duration", "0:00")),
                "thumbnail": _best_thumbnail(item),
                "channel": item.get("channel", {}).get("name", "Unknown"),
            })
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


def _parse_duration(raw: str) -> int:
    """Convert '3:45' or '1:02:30' to total seconds."""
    if not raw:
        return 0
    parts = raw.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


def _best_thumbnail(item: dict) -> str:
    thumbs = item.get("thumbnails")
    if thumbs and isinstance(thumbs, list):
        return thumbs[-1].get("url", "")
    return ""


def is_youtube_url(url: str) -> bool:
    return bool(
        re.match(
            r"https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/",
            url,
        )
    )
