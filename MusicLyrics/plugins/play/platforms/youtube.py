"""YouTube search and download helpers.

Search uses YouTube's innertube API directly via aiohttp (no external
library needed — avoids youtube-search-python httpx compatibility issues).

Stream URL extraction uses Piped/Invidious API proxies as PRIMARY method
(works on cloud servers without cookies), with yt-dlp as fallback.
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

# ══════════════════════════════════════════════════════════════════════════════
# PIPED / INVIDIOUS API — cookie-free YouTube proxy (PRIMARY method)
# ══════════════════════════════════════════════════════════════════════════════

# Multiple public Piped API instances for redundancy (updated May 2026)
_PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.r4fo.com",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.moomoo.me",
    "https://pipedapi.syncpundit.io",
    "https://api.piped.yt",
    "https://pipedapi.ngn.tf",
]

# Invidious instances as additional fallback (updated May 2026)
_INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.fdn.fr",
    "https://invidious.protokolla.fi",
    "https://invidious.nerdvpn.de",
    "https://inv.tux.pizza",
    "https://invidious.perennialte.ch",
    "https://invidious.privacyredirect.com",
    "https://iv.datura.network",
]

# Cobalt API — reliable cloud-friendly YouTube proxy (no auth needed)
_COBALT_INSTANCES = [
    "https://api.cobalt.tools",
]

_PROXY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


def _extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/|shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


async def _piped_get_streams(video_id: str) -> Optional[dict]:
    """Get stream info from Piped API. Returns dict with audioStreams, videoStreams, etc."""
    instances = list(_PIPED_INSTANCES)
    random.shuffle(instances)

    for base_url in instances:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base_url}/streams/{video_id}",
                    headers=_PROXY_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and data.get("audioStreams"):
                            LOG.info("Piped stream obtained from %s for %s", base_url, video_id)
                            return data
                    else:
                        LOG.debug("Piped %s returned HTTP %d for %s", base_url, resp.status, video_id)
        except Exception as e:
            LOG.debug("Piped %s failed for %s: %s", base_url, video_id, e)
            continue

    return None


async def _invidious_get_streams(video_id: str) -> Optional[dict]:
    """Get stream info from Invidious API."""
    instances = list(_INVIDIOUS_INSTANCES)
    random.shuffle(instances)

    for base_url in instances:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base_url}/api/v1/videos/{video_id}",
                    headers=_PROXY_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and data.get("adaptiveFormats"):
                            LOG.info("Invidious stream obtained from %s for %s", base_url, video_id)
                            return data
                    else:
                        LOG.debug("Invidious %s returned HTTP %d for %s", base_url, resp.status, video_id)
        except Exception as e:
            LOG.debug("Invidious %s failed for %s: %s", base_url, video_id, e)
            continue

    return None


def _best_piped_audio_url(data: dict) -> Optional[str]:
    """Pick the best audio stream URL from Piped response."""
    streams = data.get("audioStreams", [])
    if not streams:
        return None
    # Prefer opus/webm, then m4a, sorted by bitrate
    opus = [s for s in streams if s.get("codec", "").startswith("opus")]
    if opus:
        best = max(opus, key=lambda s: s.get("bitrate", 0))
        return best.get("url")
    # Fallback: any audio stream with highest bitrate
    best = max(streams, key=lambda s: s.get("bitrate", 0))
    return best.get("url")


def _best_piped_video_url(data: dict) -> Optional[str]:
    """Pick the best video stream URL from Piped response (with audio)."""
    # First try videoStreams (muxed — has both audio+video)
    streams = data.get("videoStreams", [])
    if streams:
        # Prefer mp4, max 720p
        candidates = [s for s in streams
                      if s.get("videoOnly") is not True
                      and (s.get("height", 0) or 0) <= 720]
        if not candidates:
            candidates = [s for s in streams if s.get("videoOnly") is not True]
        if candidates:
            best = max(candidates, key=lambda s: s.get("height", 0) or 0)
            return best.get("url")
    # Fallback: audio-only stream for video player
    return _best_piped_audio_url(data)


def _best_invidious_audio_url(data: dict) -> Optional[str]:
    """Pick best audio URL from Invidious response."""
    formats = data.get("adaptiveFormats", [])
    audio = [f for f in formats if f.get("type", "").startswith("audio/")]
    if not audio:
        return None
    # Prefer opus
    opus = [f for f in audio if "opus" in f.get("type", "")]
    if opus:
        best = max(opus, key=lambda f: int(f.get("bitrate", "0") or 0))
        return best.get("url")
    best = max(audio, key=lambda f: int(f.get("bitrate", "0") or 0))
    return best.get("url")


# ══════════════════════════════════════════════════════════════════════════════
# COBALT API — Reliable cloud-friendly YouTube proxy (no auth needed)
# ══════════════════════════════════════════════════════════════════════════════

async def _cobalt_get_stream(video_id: str, audio_only: bool = True) -> Optional[str]:
    """Get stream URL via Cobalt API. Works reliably on cloud servers."""
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    for instance in _COBALT_INSTANCES:
        try:
            payload = {
                "url": yt_url,
                "downloadMode": "audio" if audio_only else "auto",
                "audioFormat": "opus",
                "youtubeVideoCodec": "h264",
                "videoQuality": "720",
            }
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": _PROXY_HEADERS["User-Agent"],
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{instance}/",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        stream_url = data.get("url")
                        if stream_url:
                            LOG.info("Cobalt stream obtained from %s for %s (audio=%s)",
                                     instance, video_id, audio_only)
                            return stream_url
                        # Cobalt may return a picker for videos with separate streams
                        picker = data.get("picker")
                        if picker and isinstance(picker, list):
                            for p_item in picker:
                                if audio_only and p_item.get("type") == "audio":
                                    return p_item.get("url")
                                if not audio_only and p_item.get("type") == "video":
                                    return p_item.get("url")
                            # Fallback: first item
                            if picker:
                                return picker[0].get("url")
                    else:
                        LOG.debug("Cobalt %s returned HTTP %d for %s",
                                  instance, resp.status, video_id)
        except Exception as e:
            LOG.debug("Cobalt %s failed for %s: %s", instance, video_id, e)
            continue
    return None


def _best_invidious_video_url(data: dict) -> Optional[str]:
    """Pick best video URL from Invidious response."""
    formats = data.get("formatStreams", [])
    if formats:
        candidates = [f for f in formats if (int(f.get("resolution", "0p").rstrip("p") or 0)) <= 720]
        if not candidates:
            candidates = formats
        if candidates:
            best = max(candidates, key=lambda f: int(f.get("resolution", "0p").rstrip("p") or 0))
            return best.get("url")
    return _best_invidious_audio_url(data)


def _piped_video_info(data: dict, video_id: str) -> dict:
    """Extract video info dict from Piped response."""
    return {
        "title": data.get("title", "Unknown"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration": data.get("duration", 0),
        "thumbnail": data.get("thumbnailUrl", ""),
        "channel": data.get("uploader", "Unknown"),
        "video_id": video_id,
    }


def _invidious_video_info(data: dict, video_id: str) -> dict:
    """Extract video info dict from Invidious response."""
    thumbs = data.get("videoThumbnails", [])
    thumbnail = ""
    for t in thumbs:
        if t.get("quality") == "maxresdefault":
            thumbnail = t.get("url", "")
            break
    if not thumbnail and thumbs:
        thumbnail = thumbs[0].get("url", "")

    return {
        "title": data.get("title", "Unknown"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration": data.get("lengthSeconds", 0),
        "thumbnail": thumbnail,
        "channel": data.get("author", "Unknown"),
        "video_id": video_id,
    }

# ══════════════════════════════════════════════════════════════════════════════
# INNERTUBE PLAYER API — Direct YouTube stream extraction (no yt-dlp needed)
# Mobile clients return direct stream URLs without signature cipher.
# This is the same method NewPipe and Piped use internally.
# ══════════════════════════════════════════════════════════════════════════════

_INNERTUBE_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player"

# Mobile/TV/Web clients that return direct (non-cipher) stream URLs
# Updated May 2026 with latest client versions to avoid 403/bot detection
_PLAYER_CLIENTS = [
    {
        "name": "IOS_MUSIC",
        "context": {
            "client": {
                "clientName": "IOS_MUSIC",
                "clientVersion": "7.36.1",
                "deviceMake": "Apple",
                "deviceModel": "iPhone16,2",
                "hl": "en",
                "gl": "US",
                "osName": "iOS",
                "osVersion": "18.2",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyBAETezhkwP0ZWA02RsqT1zu78Fpt0bC_s",
        "ua": "com.google.ios.youtubemusic/7.36.1 (iPhone16,2; U; CPU iOS 18_2 like Mac OS X)",
    },
    {
        "name": "ANDROID_VR",
        "context": {
            "client": {
                "clientName": "ANDROID_VR",
                "clientVersion": "1.62.27",
                "androidSdkVersion": 34,
                "hl": "en",
                "gl": "US",
                "osName": "Android",
                "osVersion": "14",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w",
        "ua": "com.google.android.apps.youtube.vr.oculus/1.62.27 (Linux; U; Android 14) gzip",
    },
    {
        "name": "IOS",
        "context": {
            "client": {
                "clientName": "IOS",
                "clientVersion": "20.05.1",
                "deviceMake": "Apple",
                "deviceModel": "iPhone16,2",
                "hl": "en",
                "gl": "US",
                "osName": "iOS",
                "osVersion": "18.2",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyB-63vPrdThhKuerbB2N_l7Kwwcxj6yUAc",
        "ua": "com.google.ios.youtube/20.05.1 (iPhone16,2; U; CPU iOS 18_2 like Mac OS X)",
    },
    {
        "name": "ANDROID_MUSIC",
        "context": {
            "client": {
                "clientName": "ANDROID_MUSIC",
                "clientVersion": "7.31.52",
                "androidSdkVersion": 34,
                "hl": "en",
                "gl": "US",
                "osName": "Android",
                "osVersion": "14",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyAOghZGza2MQSZkY_zfZ370N-PUdXEo8AI",
        "ua": "com.google.android.apps.youtube.music/7.31.52 (Linux; U; Android 14) gzip",
    },
    {
        "name": "TV_EMBEDDED",
        "context": {
            "client": {
                "clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
                "clientVersion": "2.0",
                "hl": "en",
                "gl": "US",
                "platform": "TV",
            }
        },
        "key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "ua": "Mozilla/5.0 (SMART-TV; Linux; Tizen 7.0)",
        "embed": True,
    },
]


async def _innertube_player(video_id: str) -> Optional[dict]:
    """Get player response from YouTube Innertube Player API directly.

    Tries multiple client contexts. Mobile clients (ANDROID, IOS) typically
    return direct stream URLs without signature cipher — no yt-dlp needed.
    """
    for client in _PLAYER_CLIENTS:
        try:
            payload = {
                "context": client["context"],
                "videoId": video_id,
                "playbackContext": {
                    "contentPlaybackContext": {
                        "html5Preference": "HTML5_PREF_WANTS",
                    }
                },
                "contentCheckOk": True,
                "racyCheckOk": True,
            }
            # TV_EMBEDDED needs thirdParty.embedUrl
            if client.get("embed"):
                payload["thirdParty"] = {"embedUrl": "https://www.google.com"}

            headers = {
                "Content-Type": "application/json",
                "User-Agent": client["ua"],
                "Origin": "https://www.youtube.com",
                "Referer": "https://www.youtube.com/",
            }
            api_url = f"{_INNERTUBE_PLAYER_URL}?key={client['key']}"

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        LOG.debug("Innertube player %s HTTP %d for %s",
                                 client["name"], resp.status, video_id)
                        continue
                    data = await resp.json()

                    # Check playability
                    ps = data.get("playabilityStatus", {})
                    if ps.get("status") != "OK":
                        LOG.debug("Innertube %s: status=%s for %s (reason: %s)",
                                 client["name"], ps.get("status"), video_id,
                                 ps.get("reason", "unknown"))
                        continue

                    # Check for stream URLs
                    sd = data.get("streamingData", {})
                    all_fmts = sd.get("adaptiveFormats", []) + sd.get("formats", [])

                    # Only use formats with direct URL (no signatureCipher)
                    direct = [f for f in all_fmts
                              if f.get("url") and not f.get("signatureCipher")]
                    if direct:
                        LOG.info("Innertube player: %d direct formats via %s for %s",
                                len(direct), client["name"], video_id)
                        return data
                    else:
                        LOG.debug("Innertube %s: %d formats but all cipher for %s",
                                 client["name"], len(all_fmts), video_id)

        except Exception as e:
            LOG.debug("Innertube player %s error for %s: %s",
                     client["name"], video_id, e)
            continue
    return None


def _best_innertube_audio(data: dict) -> Optional[str]:
    """Extract best direct audio URL from Innertube player response."""
    sd = data.get("streamingData", {})

    # Adaptive audio-only formats
    adaptive = sd.get("adaptiveFormats", [])
    audio = [f for f in adaptive
             if f.get("url") and not f.get("signatureCipher")
             and f.get("mimeType", "").startswith("audio/")]
    if audio:
        # Prefer opus/webm
        opus = [f for f in audio if "opus" in f.get("mimeType", "")]
        pool = opus if opus else audio
        best = max(pool, key=lambda f: int(f.get("bitrate", 0)))
        return best.get("url")

    # Fallback: combined formats (audio+video)
    combined = sd.get("formats", [])
    direct = [f for f in combined
              if f.get("url") and not f.get("signatureCipher")]
    if direct:
        return direct[0].get("url")

    return None


def _best_innertube_video(data: dict) -> Optional[str]:
    """Extract best direct video URL from Innertube player response."""
    sd = data.get("streamingData", {})

    # Combined formats first (has audio+video — best for VC streaming)
    combined = sd.get("formats", [])
    direct = [f for f in combined
              if f.get("url") and not f.get("signatureCipher")]
    if direct:
        candidates = [f for f in direct if (f.get("height", 0) or 0) <= 720]
        if not candidates:
            candidates = direct
        best = max(candidates, key=lambda f: f.get("height", 0) or 0)
        return best.get("url")

    # Adaptive video-only
    adaptive = sd.get("adaptiveFormats", [])
    video = [f for f in adaptive
             if f.get("url") and not f.get("signatureCipher")
             and f.get("mimeType", "").startswith("video/")]
    if video:
        candidates = [f for f in video if (f.get("height", 0) or 0) <= 720]
        if not candidates:
            candidates = video
        best = max(candidates, key=lambda f: f.get("height", 0) or 0)
        return best.get("url")

    # Last resort: audio
    return _best_innertube_audio(data)


def _innertube_video_info(data: dict, video_id: str) -> dict:
    """Extract video info from Innertube player response."""
    vd = data.get("videoDetails", {})
    thumbs = vd.get("thumbnail", {}).get("thumbnails", [])
    thumbnail = thumbs[-1].get("url", "") if thumbs else ""
    return {
        "title": vd.get("title", "Unknown"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration": int(vd.get("lengthSeconds", 0)),
        "thumbnail": thumbnail,
        "channel": vd.get("author", "Unknown"),
        "video_id": video_id,
    }

# ── Cookie support ────────────────────────────────────────────────────────────
_COOKIES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "..", "..", "cookies")
os.makedirs(_COOKIES_DIR, exist_ok=True)

_cookie_files: list[str] = []
_cookies_loaded = False


def _write_env_cookies():
    """Write cookies from COOKIES_TXT env var to a file (for cloud deploys
    like Heroku where cookie files can't be committed to git).

    Supports both single-line (escaped \\n) and multi-line env var values.
    Always rewrites the file to pick up env var changes on dyno restart.
    """
    raw = os.environ.get("COOKIES_TXT", "").strip()
    if not raw:
        return
    # Handle escaped newlines (common when setting env vars via CLI)
    if "\\n" in raw and "\n" not in raw:
        raw = raw.replace("\\n", "\n")
    env_cookie_path = os.path.join(_COOKIES_DIR, "_env_cookies.txt")
    try:
        with open(env_cookie_path, "w") as fp:
            fp.write(raw)
        LOG.info("Wrote COOKIES_TXT env var to %s (%d bytes)", env_cookie_path, len(raw))
    except Exception:
        LOG.exception("Failed to write COOKIES_TXT env var to file")


def _load_cookie_files():
    global _cookies_loaded
    if _cookies_loaded:
        return
    _cookies_loaded = True
    # First, materialise cookies from env var (if set)
    _write_env_cookies()
    for f in os.listdir(_COOKIES_DIR):
        if f.endswith(".txt"):
            _cookie_files.append(os.path.join(_COOKIES_DIR, f))
    if _cookie_files:
        LOG.info("Loaded %d cookie file(s)", len(_cookie_files))
    else:
        LOG.warning(
            "No cookie files found. YouTube may block requests on cloud servers. "
            "Set the COOKIES_TXT env var or add .txt files to %s",
            _COOKIES_DIR,
        )


def _get_cookie() -> Optional[str]:
    _load_cookie_files()
    return random.choice(_cookie_files) if _cookie_files else None


# ── yt-dlp player client rotation ────────────────────────────────────────────
# YouTube aggressively blocks certain clients on cloud IPs.
# Updated May 2026 — prioritize clients that work on Heroku/cloud.
# Valid clients (yt-dlp 2026.x): web, web_safari, web_embedded, web_music,
#   web_creator, android, android_vr, ios, ios_music, mweb, tv, tv_embedded
_CLIENT_COMBOS: list[list[str]] = [
    ["ios_music"],                     # Best for cloud — rarely blocked
    ["android_vr"],                    # VR client — low detection rate
    ["ios"],                           # iOS client — good for music
    ["web_music"],                     # YouTube Music web client
    ["tv_embedded"],                   # TV embedded player
    ["mweb"],                          # Mobile web fallback
    ["web_safari"],                    # Safari web client
]


def _base_ytdlp_opts(client_combo: Optional[list[str]] = None) -> dict:
    if client_combo is None:
        client_combo = _CLIENT_COMBOS[0]
    opts = {
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "geo_bypass_country": "US",
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": client_combo,
            },
        },
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    # PO token support (if set via env var)
    # Format: "web+VISITOR_DATA:PO_TOKEN" (yt-dlp 2024.09+ format)
    po_token = os.environ.get("YT_PO_TOKEN", "").strip()
    if po_token:
        opts["extractor_args"]["youtube"]["po_token"] = [po_token]

    # Visitor data support (optional, used with PO token)
    visitor_data = os.environ.get("YT_VISITOR_DATA", "").strip()
    if visitor_data:
        opts["extractor_args"]["youtube"]["visitor_data"] = [visitor_data]

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
# STREAM URL EXTRACTION — Piped/Invidious first, yt-dlp fallback
# ══════════════════════════════════════════════════════════════════════════════

async def get_audio_stream_url(url: str) -> Optional[str]:
    """Extract direct audio stream URL (no download).

    Priority: Innertube Player API -> Cobalt -> Piped -> Invidious -> yt-dlp.
    """
    video_id = _extract_video_id(url)

    if video_id:
        # Try 1: Innertube Player API (direct, no yt-dlp, works on cloud IPs)
        try:
            data = await _innertube_player(video_id)
            if data:
                stream_url = _best_innertube_audio(data)
                if stream_url:
                    LOG.info("Audio stream via Innertube for %s", video_id)
                    return stream_url
        except Exception:
            LOG.debug("Innertube audio failed for %s", video_id)

        # Try 2: Cobalt API (reliable on cloud servers)
        try:
            stream_url = await _cobalt_get_stream(video_id, audio_only=True)
            if stream_url:
                LOG.info("Audio stream via Cobalt for %s", video_id)
                return stream_url
        except Exception:
            LOG.debug("Cobalt audio failed for %s", video_id)

        # Try 3: Piped proxy
        try:
            data = await _piped_get_streams(video_id)
            if data:
                stream_url = _best_piped_audio_url(data)
                if stream_url:
                    LOG.info("Audio stream via Piped for %s", video_id)
                    return stream_url
        except Exception:
            LOG.debug("Piped audio failed for %s", video_id)

        # Try 4: Invidious proxy
        try:
            data = await _invidious_get_streams(video_id)
            if data:
                stream_url = _best_invidious_audio_url(data)
                if stream_url:
                    LOG.info("Audio stream via Invidious for %s", video_id)
                    return stream_url
        except Exception:
            LOG.debug("Invidious audio failed for %s", video_id)

    # Try 5: yt-dlp (last resort)
    LOG.info("All direct APIs failed, trying yt-dlp for audio: %s", url)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, _get_stream_url_sync, url, True
        )
    except Exception:
        LOG.exception("Audio stream URL extraction failed: %s", url)
        return None


async def get_video_stream_url(url: str) -> Optional[str]:
    """Extract direct video stream URL (no download).

    Priority: Innertube Player API -> Cobalt -> Piped -> Invidious -> yt-dlp.
    """
    video_id = _extract_video_id(url)

    if video_id:
        # Try 1: Innertube Player API
        try:
            data = await _innertube_player(video_id)
            if data:
                stream_url = _best_innertube_video(data)
                if stream_url:
                    LOG.info("Video stream via Innertube for %s", video_id)
                    return stream_url
        except Exception:
            LOG.debug("Innertube video failed for %s", video_id)

        # Try 2: Cobalt API (reliable on cloud servers)
        try:
            stream_url = await _cobalt_get_stream(video_id, audio_only=False)
            if stream_url:
                LOG.info("Video stream via Cobalt for %s", video_id)
                return stream_url
        except Exception:
            LOG.debug("Cobalt video failed for %s", video_id)

        # Try 3: Piped proxy
        try:
            data = await _piped_get_streams(video_id)
            if data:
                stream_url = _best_piped_video_url(data)
                if stream_url:
                    LOG.info("Video stream via Piped for %s", video_id)
                    return stream_url
        except Exception:
            LOG.debug("Piped video failed for %s", video_id)

        # Try 4: Invidious proxy
        try:
            data = await _invidious_get_streams(video_id)
            if data:
                stream_url = _best_invidious_video_url(data)
                if stream_url:
                    LOG.info("Video stream via Invidious for %s", video_id)
                    return stream_url
        except Exception:
            LOG.debug("Invidious video failed for %s", video_id)

    # Try 4: yt-dlp (last resort)
    LOG.info("All direct APIs failed, trying yt-dlp for video: %s", url)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, _get_stream_url_sync, url, False
        )
    except Exception:
        LOG.exception("Video stream URL extraction failed: %s", url)
        return None


async def get_video_info(url: str) -> Optional[dict]:
    """Get video metadata. Tries Innertube/Piped/Invidious first, yt-dlp fallback."""
    video_id = _extract_video_id(url)

    if video_id:
        # Try Innertube Player API
        try:
            data = await _innertube_player(video_id)
            if data and data.get("videoDetails", {}).get("title"):
                return _innertube_video_info(data, video_id)
        except Exception:
            LOG.debug("Innertube info failed for %s", video_id)

        # Try Piped
        try:
            data = await _piped_get_streams(video_id)
            if data and data.get("title"):
                return _piped_video_info(data, video_id)
        except Exception:
            LOG.debug("Piped info failed for %s", video_id)

        # Try Invidious
        try:
            data = await _invidious_get_streams(video_id)
            if data and data.get("title"):
                return _invidious_video_info(data, video_id)
        except Exception:
            LOG.debug("Invidious info failed for %s", video_id)

    # Fallback: yt-dlp
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _get_info_sync, url)
    except Exception:
        LOG.exception("Video info extraction failed: %s", url)
        return None


def _extract_stream_from_info(info: dict, audio_only: bool) -> Optional[str]:
    """Extract the best stream URL from yt-dlp info dict."""
    if not info:
        return None

    # Direct URL
    stream_url = info.get("url")
    if stream_url:
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


def _get_stream_url_sync(url: str, audio_only: bool) -> Optional[str]:
    import yt_dlp

    if audio_only:
        fmt = "ba*/b"  # most permissive: best audio (any), fallback to best anything
    else:
        fmt = "bv*[height<=720]+ba*/bv*+ba*/b"  # very permissive video+audio

    last_err = None
    for combo in _CLIENT_COMBOS:
        opts = {**_base_ytdlp_opts(client_combo=combo), "format": fmt}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                result = _extract_stream_from_info(info, audio_only)
                if result:
                    LOG.info("Stream URL obtained for %s (client: %s)", url, combo)
                    return result
        except Exception as exc:
            last_err = exc
            LOG.warning("Stream URL attempt failed with client %s: %s", combo, exc)
            continue

    if last_err:
        LOG.exception("All yt-dlp stream URL attempts failed: %s — %s", url, last_err)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD — Innertube/Piped stream download, yt-dlp fallback
# ══════════════════════════════════════════════════════════════════════════════


async def _piped_or_invidious_audio(video_id: str) -> Optional[str]:
    """Try Piped then Invidious for audio stream URL."""
    try:
        data = await _piped_get_streams(video_id)
        if data:
            url = _best_piped_audio_url(data)
            if url:
                return url
    except Exception:
        pass
    try:
        data = await _invidious_get_streams(video_id)
        if data:
            url = _best_invidious_audio_url(data)
            if url:
                return url
    except Exception:
        pass
    return None


async def _piped_or_invidious_video(video_id: str) -> Optional[str]:
    """Try Piped then Invidious for video stream URL."""
    try:
        data = await _piped_get_streams(video_id)
        if data:
            url = _best_piped_video_url(data)
            if url:
                return url
    except Exception:
        pass
    try:
        data = await _invidious_get_streams(video_id)
        if data:
            url = _best_invidious_video_url(data)
            if url:
                return url
    except Exception:
        pass
    return None


async def download_audio(url: str) -> Optional[str]:
    """Download audio. Tries Innertube/Cobalt/Piped stream + download, yt-dlp fallback."""
    video_id = _extract_video_id(url)

    if video_id:
        # Try 1: Innertube Player API -> download stream
        try:
            data = await _innertube_player(video_id)
            if data:
                stream_url = _best_innertube_audio(data)
                if stream_url:
                    filepath = os.path.join(_DOWNLOADS, f"{video_id}_innertube.m4a")
                    downloaded = await _download_stream(stream_url, filepath)
                    if downloaded:
                        LOG.info("Audio downloaded via Innertube for %s", video_id)
                        return downloaded
        except Exception:
            LOG.debug("Innertube audio download failed for %s", video_id)

        # Try 2: Cobalt API -> download stream
        try:
            stream_url = await _cobalt_get_stream(video_id, audio_only=True)
            if stream_url:
                filepath = os.path.join(_DOWNLOADS, f"{video_id}_cobalt.opus")
                downloaded = await _download_stream(stream_url, filepath)
                if downloaded:
                    LOG.info("Audio downloaded via Cobalt for %s", video_id)
                    return downloaded
        except Exception:
            LOG.debug("Cobalt audio download failed for %s", video_id)

        # Try 3: Piped/Invidious stream URL + download
        try:
            stream_url = await _piped_or_invidious_audio(video_id)
            if stream_url:
                filepath = os.path.join(_DOWNLOADS, f"{video_id}.opus")
                downloaded = await _download_stream(stream_url, filepath)
                if downloaded:
                    LOG.info("Audio downloaded via proxy for %s", video_id)
                    return downloaded
        except Exception:
            LOG.debug("Proxy audio download failed for %s", video_id)

    # Try 3: yt-dlp (last resort)
    LOG.info("Direct download failed, trying yt-dlp for audio: %s", url)
    opts = {
        **_base_ytdlp_opts(),
        "format": "ba*/b",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s.%(ext)s"),
        "overwrites": False,
    }
    return await _run_ytdlp(url, opts)


async def download_video(url: str) -> Optional[str]:
    """Download video. Tries Innertube/Cobalt/Piped stream + download, yt-dlp fallback."""
    video_id = _extract_video_id(url)

    if video_id:
        # Try 1: Innertube Player API -> download stream
        try:
            data = await _innertube_player(video_id)
            if data:
                stream_url = _best_innertube_video(data)
                if stream_url:
                    filepath = os.path.join(_DOWNLOADS, f"{video_id}_innertube_video.mp4")
                    downloaded = await _download_stream(stream_url, filepath)
                    if downloaded:
                        LOG.info("Video downloaded via Innertube for %s", video_id)
                        return downloaded
        except Exception:
            LOG.debug("Innertube video download failed for %s", video_id)

        # Try 2: Cobalt API -> download stream
        try:
            stream_url = await _cobalt_get_stream(video_id, audio_only=False)
            if stream_url:
                filepath = os.path.join(_DOWNLOADS, f"{video_id}_cobalt_video.mp4")
                downloaded = await _download_stream(stream_url, filepath)
                if downloaded:
                    LOG.info("Video downloaded via Cobalt for %s", video_id)
                    return downloaded
        except Exception:
            LOG.debug("Cobalt video download failed for %s", video_id)

        # Try 3: Piped/Invidious stream URL + download
        try:
            stream_url = await _piped_or_invidious_video(video_id)
            if stream_url:
                filepath = os.path.join(_DOWNLOADS, f"{video_id}_video.mp4")
                downloaded = await _download_stream(stream_url, filepath)
                if downloaded:
                    LOG.info("Video downloaded via proxy for %s", video_id)
                    return downloaded
        except Exception:
            LOG.debug("Proxy video download failed for %s", video_id)

    # Try 3: yt-dlp (last resort)
    LOG.info("Direct download failed, trying yt-dlp for video: %s", url)
    opts = {
        **_base_ytdlp_opts(),
        "format": "bv*[height<=720]+ba*/bv*+ba*/b",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s_video.%(ext)s"),
        "merge_output_format": "mp4",
        "overwrites": False,
    }
    return await _run_ytdlp(url, opts)


async def _download_stream(stream_url: str, filepath: str) -> Optional[str]:
    """Download a stream URL directly via aiohttp."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                stream_url,
                headers=_PROXY_HEADERS,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                if resp.status != 200:
                    return None
                import aiofiles
                async with aiofiles.open(filepath, "wb") as fp:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        await fp.write(chunk)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
            return filepath
        return None
    except Exception:
        LOG.debug("Direct stream download failed: %s", stream_url[:80])
        return None


def _get_info_sync(url: str) -> Optional[dict]:
    import yt_dlp

    last_err = None
    for combo in _CLIENT_COMBOS:
        opts = {**_base_ytdlp_opts(client_combo=combo), "skip_download": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    continue
                return {
                    "title": info.get("title", "Unknown"),
                    "url": info.get("webpage_url", url),
                    "duration": int(info.get("duration") or 0),
                    "thumbnail": info.get("thumbnail", ""),
                    "channel": info.get("uploader") or info.get("channel", "Unknown"),
                    "video_id": info.get("id", ""),
                }
        except Exception as exc:
            last_err = exc
            LOG.warning("yt-dlp info attempt failed (client %s): %s", combo, exc)
            continue

    if last_err:
        LOG.exception("All yt-dlp info attempts failed: %s — %s", url, last_err)
    return None


async def _run_ytdlp(url: str, opts: dict) -> Optional[str]:
    import yt_dlp
    loop = asyncio.get_running_loop()

    last_err = None
    for combo in _CLIENT_COMBOS:
        run_opts = {**opts}
        # Build extractor_args preserving PO token and visitor data
        yt_args = {"player_client": combo}
        po_token = os.environ.get("YT_PO_TOKEN", "").strip()
        if po_token:
            yt_args["po_token"] = [po_token]
        visitor_data = os.environ.get("YT_VISITOR_DATA", "").strip()
        if visitor_data:
            yt_args["visitor_data"] = [visitor_data]
        run_opts["extractor_args"] = {"youtube": yt_args}
        # Preserve cookies
        cookie = _get_cookie()
        if cookie:
            run_opts["cookiefile"] = cookie

        try:
            with yt_dlp.YoutubeDL(run_opts) as ydl:
                info = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(url, download=True)
                )
                if not info:
                    continue
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
        except Exception as exc:
            last_err = exc
            LOG.warning("yt-dlp download attempt failed (client %s): %s", combo, exc)
            continue

    if last_err:
        LOG.exception("All yt-dlp download attempts failed: %s — %s", url, last_err)
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
