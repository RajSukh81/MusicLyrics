"""YouTube search and download helpers.

Uses youtube-search-python for search (avoids bot detection) and
yt-dlp for stream URL extraction / download with anti-bot mitigations.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import random
import re
from typing import Optional

from config import Config

LOG = logging.getLogger(__name__)

_DOWNLOADS = Config.DOWNLOADS_DIR
os.makedirs(_DOWNLOADS, exist_ok=True)

# ── Cookie support (like AnonXMusic) ──────────────────────────────────────────
_COOKIES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "cookies")
os.makedirs(_COOKIES_DIR, exist_ok=True)

_cookie_files: list[str] = []
_cookies_loaded = False


def _load_cookie_files():
    """Scan cookies/ directory for .txt files."""
    global _cookies_loaded
    if _cookies_loaded:
        return
    _cookies_loaded = True
    for f in os.listdir(_COOKIES_DIR):
        if f.endswith(".txt"):
            path = os.path.join(_COOKIES_DIR, f)
            _cookie_files.append(path)
    if _cookie_files:
        LOG.info("Loaded %d cookie file(s) from %s", len(_cookie_files), _COOKIES_DIR)
    else:
        LOG.info("No cookie files found in %s (YouTube may block downloads)", _COOKIES_DIR)


def _get_cookie() -> Optional[str]:
    """Return a random cookie file path, or None."""
    _load_cookie_files()
    if not _cookie_files:
        return None
    return random.choice(_cookie_files)


def _base_ytdlp_opts() -> dict:
    """Base yt-dlp options with anti-bot mitigations."""
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


# ── Search (youtube-search-python — no bot detection) ────────────────────────

async def search_youtube(query: str, max_results: int = 1) -> Optional[dict]:
    """Search YouTube and return the first result.

    Uses youtube-search-python which scrapes YouTube's web search
    and does NOT trigger the 'Sign in to confirm you're not a bot' error.

    Keys: title, url, duration (seconds), thumbnail, channel, video_id.
    Returns None when nothing is found.
    """
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _yts_search_sync, query, max_results)
        return result
    except Exception:
        LOG.exception("youtube-search-python failed, falling back to yt-dlp for: %s", query)
        # Fallback to yt-dlp search
        try:
            result = await loop.run_in_executor(None, _ytdlp_search_sync, query, max_results)
            return result
        except Exception:
            LOG.exception("yt-dlp search also failed for: %s", query)
            return None


def _yts_search_sync(query: str, max_results: int = 1) -> Optional[dict]:
    """Search YouTube using youtube-search-python (no bot detection)."""
    try:
        from youtubesearchpython import VideosSearch
        search = VideosSearch(query, limit=max_results)
        result = search.result()
        if not result or not result.get("result"):
            return None

        item = result["result"][0]
        # Parse duration string "M:SS" or "H:MM:SS" to seconds
        duration = _parse_duration(item.get("duration", "0:00"))
        thumbnails = item.get("thumbnails", [])
        thumb = thumbnails[0]["url"].split("?")[0] if thumbnails else ""

        return {
            "title": item.get("title", "Unknown"),
            "url": item.get("link", ""),
            "duration": duration,
            "thumbnail": thumb,
            "channel": item.get("channel", {}).get("name", "Unknown"),
            "video_id": item.get("id", ""),
        }
    except ImportError:
        LOG.warning("youtube-search-python not installed, using yt-dlp for search")
        return _ytdlp_search_sync(query, max_results)
    except Exception:
        LOG.exception("youtube-search-python search failed: %s", query)
        return None


def _ytdlp_search_sync(query: str, max_results: int = 1) -> Optional[dict]:
    """Fallback: search using yt-dlp (may trigger bot detection)."""
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
                "url": item.get("webpage_url", item.get("url", "")),
                "duration": int(item.get("duration") or 0),
                "thumbnail": item.get("thumbnail", ""),
                "channel": item.get("uploader", item.get("channel", "Unknown")),
                "video_id": vid,
            }
    except Exception:
        LOG.exception("yt-dlp search failed: %s", query)
        return None


async def search_youtube_many(query: str, limit: int = 5) -> list[dict]:
    """Return up to *limit* results."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _yts_search_many_sync, query, limit)
    except Exception:
        LOG.exception("YouTube multi-search failed: %s", query)
        return []


def _yts_search_many_sync(query: str, limit: int = 5) -> list[dict]:
    """Multi-result search using youtube-search-python."""
    try:
        from youtubesearchpython import VideosSearch
        search = VideosSearch(query, limit=limit)
        result = search.result()
        if not result or not result.get("result"):
            return []
        out = []
        for item in result["result"]:
            duration = _parse_duration(item.get("duration", "0:00"))
            thumbnails = item.get("thumbnails", [])
            thumb = thumbnails[0]["url"].split("?")[0] if thumbnails else ""
            out.append({
                "title": item.get("title", "Unknown"),
                "url": item.get("link", ""),
                "duration": duration,
                "thumbnail": thumb,
                "channel": item.get("channel", {}).get("name", "Unknown"),
                "video_id": item.get("id", ""),
            })
        return out
    except ImportError:
        LOG.warning("youtube-search-python not installed")
        return []
    except Exception:
        LOG.exception("youtube-search-python multi-search failed: %s", query)
        return []


# ── Stream URL Extraction (no download needed) ───────────────────────────────

async def get_audio_stream_url(url: str) -> Optional[str]:
    """Extract direct audio stream URL using yt-dlp (no download).

    The returned URL can be passed directly to py-tgcalls MediaStream.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _get_stream_url_sync, url, True)
    except Exception:
        LOG.exception("Audio stream URL extraction failed: %s", url)
        return None


async def get_video_stream_url(url: str) -> Optional[str]:
    """Extract direct video+audio stream URL using yt-dlp (no download).

    Returns a single URL with both video and audio (best merged).
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _get_stream_url_sync, url, False)
    except Exception:
        LOG.exception("Video stream URL extraction failed: %s", url)
        return None


def _get_stream_url_sync(url: str, audio_only: bool) -> Optional[str]:
    """Synchronous stream URL extraction."""
    import yt_dlp

    if audio_only:
        fmt = "bestaudio[ext=webm][acodec=opus]/bestaudio/best"
    else:
        fmt = "best[height<=?720][width<=?1280][ext=mp4]/best[height<=?720]/best"

    opts = {
        **_base_ytdlp_opts(),
        "format": fmt,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None

            # Direct URL from the selected format
            stream_url = info.get("url")
            if stream_url:
                LOG.info("Got stream URL for: %s (audio_only=%s)", url, audio_only)
                return stream_url

            # If merged formats, get from requested_formats
            requested = info.get("requested_formats", [])
            if audio_only:
                for fmt_info in requested:
                    if fmt_info.get("acodec", "none") != "none":
                        return fmt_info.get("url")
            else:
                # For video, return the video+audio merged URL or video URL
                for fmt_info in requested:
                    if fmt_info.get("vcodec", "none") != "none":
                        return fmt_info.get("url")

            # Last resort: try formats list
            formats = info.get("formats", [])
            if audio_only:
                # Pick best audio format
                audio_fmts = [f for f in formats if f.get("acodec", "none") != "none"
                              and f.get("vcodec") in ("none", None)]
                if audio_fmts:
                    best = max(audio_fmts, key=lambda f: f.get("abr", 0) or 0)
                    return best.get("url")
            else:
                video_fmts = [f for f in formats if f.get("vcodec", "none") != "none"]
                if video_fmts:
                    best = max(video_fmts, key=lambda f: (f.get("height", 0) or 0))
                    return best.get("url")

            return None
    except Exception:
        LOG.exception("yt-dlp stream URL extraction failed: %s", url)
        return None


# ── Download (fallback when stream URLs fail) ─────────────────────────────────

async def download_audio(url: str) -> Optional[str]:
    """Download audio with yt-dlp; return local file path."""
    opts = {
        **_base_ytdlp_opts(),
        "format": "bestaudio[ext=webm][acodec=opus]/bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s.%(ext)s"),
        "overwrites": False,
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
    """Download video+audio with yt-dlp; return file path."""
    opts = {
        **_base_ytdlp_opts(),
        "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])/best[height<=?720]/best",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s_video.%(ext)s"),
        "merge_output_format": "mp4",
        "overwrites": False,
    }
    return await _run_ytdlp(url, opts)


async def get_video_info(url: str) -> Optional[dict]:
    """Extract metadata without downloading."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _get_info_sync, url)
    except Exception:
        LOG.exception("Video info extraction failed: %s", url)
        return None


def _get_info_sync(url: str) -> Optional[dict]:
    """Synchronous metadata extraction."""
    import yt_dlp

    opts = {
        **_base_ytdlp_opts(),
        "skip_download": True,
    }

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
                "channel": info.get("uploader", info.get("channel", "Unknown")),
                "video_id": info.get("id", ""),
            }
    except Exception:
        LOG.exception("yt-dlp info sync failed: %s", url)
        return None


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _run_ytdlp(url: str, opts: dict) -> Optional[str]:
    """Run yt-dlp download and return file path."""
    import yt_dlp

    loop = asyncio.get_running_loop()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
            if not info:
                LOG.warning("yt-dlp returned no info for: %s", url)
                return None
            path = ydl.prepare_filename(info)
            if os.path.exists(path):
                LOG.info("Downloaded: %s", path)
                return path
            # Post-processing may change the extension
            base = os.path.splitext(path)[0]
            for ext in (".opus", ".m4a", ".webm", ".mp3", ".ogg", ".wav",
                        ".mp4", ".mkv", ".flv"):
                candidate = base + ext
                if os.path.exists(candidate):
                    LOG.info("Post-processed file: %s", candidate)
                    return candidate
            matches = sorted(glob.glob(f"{base}.*"),
                             key=os.path.getmtime, reverse=True)
            if matches:
                LOG.info("Glob-matched: %s", matches[0])
                return matches[0]
            LOG.warning("File NOT found after download: %s", path)
            return None
    except Exception:
        LOG.exception("yt-dlp download failed: %s", url)
        return None


def _parse_duration(duration_str: str) -> int:
    """Parse 'M:SS' or 'H:MM:SS' to seconds."""
    if not duration_str:
        return 0
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
