"""YouTube search and download helpers.

Search uses YouTube's innertube API directly via aiohttp (no external
library needed — avoids youtube-search-python httpx compatibility issues).
Download/stream uses yt-dlp with anti-bot mitigations.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import random
import re
from typing import Optional

import aiohttp

from config import Config

LOG = logging.getLogger(__name__)

_DOWNLOADS = Config.DOWNLOADS_DIR
os.makedirs(_DOWNLOADS, exist_ok=True)

# ── Cookie support ────────────────────────────────────────────────────────────
_COOKIES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "..", "..", "cookies")
os.makedirs(_COOKIES_DIR, exist_ok=True)

_cookie_files: list[str] = []
_cookies_loaded = False


def _load_cookie_files():
    global _cookies_loaded
    if _cookies_loaded:
        return
    _cookies_loaded = True
    for f in os.listdir(_COOKIES_DIR):
        if f.endswith(".txt"):
            _cookie_files.append(os.path.join(_COOKIES_DIR, f))
    if _cookie_files:
        LOG.info("Loaded %d cookie file(s)", len(_cookie_files))
    else:
        LOG.info("No cookie files in %s", _COOKIES_DIR)


def _get_cookie() -> Optional[str]:
    _load_cookie_files()
    return random.choice(_cookie_files) if _cookie_files else None


def _base_ytdlp_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "socket_timeout": 20,
        "retries": 3,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["mweb", "web"],
            },
        },
    }
    cookie = _get_cookie()
    if cookie:
        opts["cookiefile"] = cookie
    return opts


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH — YouTube Innertube API (direct, no external library)
# ══════════════════════════════════════════════════════════════════════════════

_INNERTUBE_SEARCH_URL = "https://www.youtube.com/youtubei/v1/search"

_INNERTUBE_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20241120.01.00",
        "hl": "en",
        "gl": "US",
    }
}

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.youtube.com",
    "Referer": "https://www.youtube.com/",
}


async def search_youtube(query: str, max_results: int = 1) -> Optional[dict]:
    """Search YouTube via innertube API.  Returns the first result.

    This calls YouTube's own search endpoint directly using aiohttp.
    It does NOT trigger bot detection (search != player).

    Keys: title, url, duration (seconds), thumbnail, channel, video_id.
    """
    try:
        results = await _innertube_search(query, max_results)
        if results:
            return results[0]
    except Exception:
        LOG.exception("Innertube search failed for: %s", query)

    # Fallback: yt-dlp flat search
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, _ytdlp_search_sync, query, max_results
        )
        return result
    except Exception:
        LOG.exception("yt-dlp fallback search also failed: %s", query)
        return None


async def search_youtube_many(query: str, limit: int = 5) -> list[dict]:
    """Return up to *limit* search results."""
    try:
        return await _innertube_search(query, limit)
    except Exception:
        LOG.exception("Multi-search failed: %s", query)
        return []


async def _innertube_search(query: str, limit: int = 5) -> list[dict]:
    """Call YouTube innertube search API and parse results."""
    payload = {
        "context": _INNERTUBE_CONTEXT,
        "query": query,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            _INNERTUBE_SEARCH_URL,
            json=payload,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                LOG.warning("Innertube search HTTP %d for: %s", resp.status, query)
                return []
            data = await resp.json()

    return _parse_innertube_results(data, limit)


def _parse_innertube_results(data: dict, limit: int) -> list[dict]:
    """Extract video results from innertube search response."""
    results = []

    try:
        contents = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
    except (AttributeError, TypeError):
        LOG.warning("Unexpected innertube response structure")
        return []

    for section in contents:
        items = (
            section.get("itemSectionRenderer", {})
            .get("contents", [])
        )
        for item in items:
            vr = item.get("videoRenderer")
            if not vr:
                continue

            video_id = vr.get("videoId", "")
            if not video_id:
                continue

            # Title
            title_runs = vr.get("title", {}).get("runs", [])
            title = title_runs[0]["text"] if title_runs else "Unknown"

            # Duration
            length_text = (
                vr.get("lengthText", {}).get("simpleText", "")
                or vr.get("lengthText", {}).get("accessibility", {})
                .get("accessibilityData", {}).get("label", "")
            )
            duration = _parse_duration(length_text)

            # Thumbnail
            thumbs = vr.get("thumbnail", {}).get("thumbnails", [])
            thumbnail = thumbs[-1]["url"] if thumbs else ""
            # Clean thumbnail URL
            if thumbnail and "?" in thumbnail:
                thumbnail = thumbnail.split("?")[0]

            # Channel
            owner_runs = vr.get("ownerText", {}).get("runs", [])
            channel = owner_runs[0]["text"] if owner_runs else "Unknown"

            results.append({
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "duration": duration,
                "thumbnail": thumbnail,
                "channel": channel,
                "video_id": video_id,
            })

            if len(results) >= limit:
                return results

    return results


def _ytdlp_search_sync(query: str, max_results: int = 1) -> Optional[dict]:
    """Fallback: search using yt-dlp with extract_flat (lightweight)."""
    import yt_dlp

    opts = {
        **_base_ytdlp_opts(),
        "extract_flat": True,
        "default_search": f"ytsearch{max_results}",
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if not info:
                return None
            entries = info.get("entries")
            item = entries[0] if entries else info
            if not item:
                return None
            vid = item.get("id", "")
            return {
                "title": item.get("title", "Unknown"),
                "url": item.get("webpage_url") or item.get("url", ""),
                "duration": int(item.get("duration") or 0),
                "thumbnail": item.get("thumbnail", ""),
                "channel": item.get("uploader") or item.get("channel", "Unknown"),
                "video_id": vid,
            }
    except Exception:
        LOG.exception("yt-dlp search failed: %s", query)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# STREAM URL EXTRACTION (no download needed)
# ══════════════════════════════════════════════════════════════════════════════

async def get_audio_stream_url(url: str) -> Optional[str]:
    """Extract direct audio stream URL (no download)."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, _get_stream_url_sync, url, True
        )
    except Exception:
        LOG.exception("Audio stream URL extraction failed: %s", url)
        return None


async def get_video_stream_url(url: str) -> Optional[str]:
    """Extract direct video stream URL (no download)."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, _get_stream_url_sync, url, False
        )
    except Exception:
        LOG.exception("Video stream URL extraction failed: %s", url)
        return None


def _get_stream_url_sync(url: str, audio_only: bool) -> Optional[str]:
    import yt_dlp

    if audio_only:
        fmt = "bestaudio[ext=webm][acodec=opus]/bestaudio/best"
    else:
        fmt = "best[height<=?720][width<=?1280][ext=mp4]/best[height<=?720]/best"

    opts = {**_base_ytdlp_opts(), "format": fmt}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None

            # Direct URL
            stream_url = info.get("url")
            if stream_url:
                LOG.info("Stream URL obtained for: %s", url)
                return stream_url

            # From requested_formats
            for fmt_info in info.get("requested_formats", []):
                if audio_only and fmt_info.get("acodec", "none") != "none":
                    return fmt_info.get("url")
                if not audio_only and fmt_info.get("vcodec", "none") != "none":
                    return fmt_info.get("url")

            # From all formats
            formats = info.get("formats", [])
            if audio_only:
                audio_fmts = [f for f in formats
                              if f.get("acodec", "none") != "none"
                              and f.get("vcodec") in ("none", None)]
                if audio_fmts:
                    best = max(audio_fmts, key=lambda f: f.get("abr", 0) or 0)
                    return best.get("url")
            else:
                video_fmts = [f for f in formats
                              if f.get("vcodec", "none") != "none"]
                if video_fmts:
                    best = max(video_fmts, key=lambda f: f.get("height", 0) or 0)
                    return best.get("url")

            return None
    except Exception:
        LOG.exception("yt-dlp stream URL failed: %s", url)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD (fallback)
# ══════════════════════════════════════════════════════════════════════════════

async def download_audio(url: str) -> Optional[str]:
    opts = {
        **_base_ytdlp_opts(),
        "format": "bestaudio[ext=webm][acodec=opus]/bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s.%(ext)s"),
        "overwrites": False,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "opus",
            "preferredquality": "128",
        }],
    }
    return await _run_ytdlp(url, opts)


async def download_video(url: str) -> Optional[str]:
    opts = {
        **_base_ytdlp_opts(),
        "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])/best[height<=?720]/best",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s_video.%(ext)s"),
        "merge_output_format": "mp4",
        "overwrites": False,
    }
    return await _run_ytdlp(url, opts)


async def get_video_info(url: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _get_info_sync, url)
    except Exception:
        LOG.exception("Video info extraction failed: %s", url)
        return None


def _get_info_sync(url: str) -> Optional[dict]:
    import yt_dlp
    opts = {**_base_ytdlp_opts(), "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            return {
                "title": info.get("title", "Unknown"),
                "url": info.get("webpage_url", url),
                "duration": int(info.get("duration") or 0),
                "thumbnail": info.get("thumbnail", ""),
                "channel": info.get("uploader") or info.get("channel", "Unknown"),
                "video_id": info.get("id", ""),
            }
    except Exception:
        LOG.exception("yt-dlp info failed: %s", url)
        return None


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
            path = ydl.prepare_filename(info)
            if os.path.exists(path):
                return path
            base = os.path.splitext(path)[0]
            for ext in (".opus", ".m4a", ".webm", ".mp3", ".ogg",
                        ".mp4", ".mkv", ".flv"):
                candidate = base + ext
                if os.path.exists(candidate):
                    return candidate
            matches = sorted(glob.glob(f"{base}.*"),
                             key=os.path.getmtime, reverse=True)
            if matches:
                return matches[0]
            return None
    except Exception:
        LOG.exception("yt-dlp download failed: %s", url)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_duration(duration_str: str) -> int:
    """Parse 'M:SS', 'H:MM:SS', or accessibility label to seconds."""
    if not duration_str:
        return 0
    # Handle accessibility label like "3 minutes, 45 seconds"
    if "minute" in duration_str or "hour" in duration_str:
        total = 0
        import re as _re
        for match in _re.finditer(r"(\d+)\s*(hour|minute|second)", duration_str):
            val, unit = int(match.group(1)), match.group(2)
            if unit == "hour":
                total += val * 3600
            elif unit == "minute":
                total += val * 60
            else:
                total += val
        return total
    # Handle "M:SS" or "H:MM:SS"
    parts = duration_str.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(parts[0])
    except (ValueError, IndexError):
        return 0


def is_youtube_url(url: str) -> bool:
    return bool(
        re.match(
            r"https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/",
            url,
        )
    )
