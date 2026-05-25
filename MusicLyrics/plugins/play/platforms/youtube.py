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

class _YtDlpLogger:
    """Custom logger for yt-dlp that suppresses noisy warnings."""
    def debug(self, msg): LOG.debug("[yt-dlp] %s", msg)
    def info(self, msg): LOG.debug("[yt-dlp] %s", msg)
    def warning(self, msg):
        if "is not a valid URL" in str(msg):
            return  # Suppress noisy generic extractor warnings
        LOG.warning("[yt-dlp] %s", msg)
    def error(self, msg): LOG.warning("[yt-dlp] %s", msg)

_ytdlp_logger = _YtDlpLogger()

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
    "https://api.piped.yt",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.darkness.services",
    "https://pipedapi.drgns.space",
    "https://pipedapi.in.projectsegfau.lt",
    "https://pipedapi.us.projectsegfau.lt",
    "https://pipedapi.frontendfriendly.xyz",
    "https://api.piped.privacydev.net",
    "https://pipedapi.ngn.tf",
]

# Invidious instances as additional fallback (updated May 2026)
_INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.fdn.fr",
    "https://invidious.protokolla.fi",
    "https://iv.datura.network",
    "https://vid.puffyan.us",
    "https://invidious.nerdvpn.de",
    "https://inv.tux.pizza",
    "https://invidious.privacyredirect.com",
    "https://inv.n8pjl.ca",
    "https://invidious.lunar.icu",
    "https://invidious.perennialte.ch",
    "https://inv.us.projectsegfau.lt",
]

# Cobalt API — reliable cloud-friendly YouTube proxy
# Requires API key since late 2024 — set COBALT_API_KEY env var
_COBALT_INSTANCES = [
    "https://api.cobalt.tools",
]
# Allow custom Cobalt instance via env var (e.g., self-hosted)
_cobalt_custom_url = os.environ.get("COBALT_API_URL", "").strip().rstrip("/")
if _cobalt_custom_url:
    _COBALT_INSTANCES.insert(0, _cobalt_custom_url)
_COBALT_API_KEY = os.environ.get("COBALT_API_KEY", "").strip()

# Validate Cobalt API key format — real keys are long hex/alphanumeric strings
if _COBALT_API_KEY and len(_COBALT_API_KEY) < 20:
    LOG.warning(
        "COBALT_API_KEY looks invalid (too short: %d chars). "
        "Real Cobalt API keys are typically 32+ character hex strings. "
        "Get a valid key from https://cobalt.tools — current value may cause 401 errors.",
        len(_COBALT_API_KEY),
    )

_PROXY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
}

# ── Proxy support for cloud deployments ──────────────────────────────────────
# Track proxy health — auto-disable proxies that return 402/403/407
_proxy_dead = False
_proxy_fail_count = 0
_proxy_dead_since: float = 0.0  # timestamp when proxy was disabled
_PROXY_FAIL_THRESHOLD = 2  # Disable after 2 consecutive failures
_PROXY_RECOVERY_SECONDS = 300  # Re-try proxy every 5 minutes


def _mark_proxy_failed():
    """Mark the proxy as potentially dead after a failure."""
    global _proxy_fail_count, _proxy_dead, _proxy_dead_since
    _proxy_fail_count += 1
    if _proxy_fail_count >= _PROXY_FAIL_THRESHOLD:
        _proxy_dead = True
        import time as _t
        _proxy_dead_since = _t.time()
        LOG.warning("Proxy disabled after %d consecutive failures. "
                    "Will auto-retry in %d seconds. "
                    "Check your YOUTUBE_PROXY subscription.",
                    _proxy_fail_count, _PROXY_RECOVERY_SECONDS)


def _mark_proxy_ok():
    """Reset proxy failure counter on success."""
    global _proxy_fail_count, _proxy_dead, _proxy_dead_since
    _proxy_fail_count = 0
    _proxy_dead = False
    _proxy_dead_since = 0.0


def _check_proxy_recovery():
    """Periodically re-enable proxy for retry (subscription might have been renewed)."""
    global _proxy_dead, _proxy_fail_count, _proxy_dead_since
    if _proxy_dead and _proxy_dead_since > 0:
        import time as _t
        elapsed = _t.time() - _proxy_dead_since
        if elapsed >= _PROXY_RECOVERY_SECONDS:
            LOG.info("Proxy recovery: re-enabling proxy for retry after %d seconds", int(elapsed))
            _proxy_dead = False
            _proxy_fail_count = 0
            _proxy_dead_since = 0.0


def _get_proxy() -> Optional[str]:
    """Get a random proxy URL from the proxy list or single proxy config.

    Returns None if the proxy has been auto-disabled due to failures
    (e.g., 402 Payment Required = expired subscription).
    Always ensures the returned proxy is a valid URL (http://user:pass@host:port).
    """
    _check_proxy_recovery()  # Re-enable proxy periodically for retry
    if _proxy_dead:
        return None  # Proxy is dead, go direct

    proxy = None
    # Priority 1: Proxy list (rotation)
    if Config.YOUTUBE_PROXIES:
        proxy = random.choice(Config.YOUTUBE_PROXIES)
    else:
        # Priority 2: Single proxy
        proxy = Config.YOUTUBE_PROXY or os.environ.get("YOUTUBE_PROXY", "") or None

    # Safety: ensure proxy is a valid URL, not raw ip:port:user:pass format
    if proxy and not proxy.startswith(("http://", "https://", "socks")):
        parts = proxy.split(":")
        if len(parts) == 4:
            # Webshare format: ip:port:user:pass
            ip, port, user, pw = parts
            proxy = f"http://{user}:{pw}@{ip}:{port}"
            LOG.info("Auto-converted Webshare proxy format to URL: %s", proxy[:40])
        elif "@" in proxy:
            proxy = f"http://{proxy}"
        else:
            proxy = f"http://{proxy}"

    return proxy if proxy else None


def _aio_session_kwargs() -> dict:
    """Return kwargs for aiohttp.ClientSession with proxy support."""
    return {}


def _aio_request_kwargs() -> dict:
    """Return kwargs for aiohttp request methods (get/post) with proxy.

    ONLY use this for direct YouTube API calls (Innertube).
    Do NOT use for third-party APIs (Piped, Invidious, Cobalt).
    """
    proxy = _get_proxy()
    if proxy:
        return {"proxy": proxy}
    return {}


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
    """Get stream info from Piped API. Returns dict with audioStreams, videoStreams, etc.

    NOTE: Does NOT use YOUTUBE_PROXY — Piped instances ARE the proxy.
    Sending requests to Piped through another proxy just adds failure points.
    """
    instances = list(_PIPED_INSTANCES)
    random.shuffle(instances)

    for base_url in instances:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base_url}/streams/{video_id}",
                    headers=_PROXY_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and (data.get("audioStreams") or data.get("videoStreams")):
                            LOG.info("Piped stream obtained from %s for %s", base_url, video_id)
                            return data
                    else:
                        LOG.debug("Piped %s returned HTTP %d for %s", base_url, resp.status, video_id)
        except asyncio.TimeoutError:
            LOG.debug("Piped %s timed out for %s", base_url, video_id)
            continue
        except Exception as e:
            LOG.debug("Piped %s failed for %s: %s", base_url, video_id, e)
            continue

    return None


async def _invidious_get_streams(video_id: str) -> Optional[dict]:
    """Get stream info from Invidious API.

    NOTE: Does NOT use YOUTUBE_PROXY — Invidious instances ARE the proxy.
    """
    instances = list(_INVIDIOUS_INSTANCES)
    random.shuffle(instances)

    for base_url in instances:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base_url}/api/v1/videos/{video_id}",
                    headers=_PROXY_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and (data.get("adaptiveFormats") or data.get("formatStreams")):
                            LOG.info("Invidious stream obtained from %s for %s", base_url, video_id)
                            return data
                    else:
                        LOG.debug("Invidious %s returned HTTP %d for %s", base_url, resp.status, video_id)
        except asyncio.TimeoutError:
            LOG.debug("Invidious %s timed out for %s", base_url, video_id)
            continue
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
    """Get stream URL via Cobalt API. Works reliably on cloud servers.

    Requires a VALID COBALT_API_KEY env var since Cobalt v10+ (late 2024).
    If COBALT_API_KEY is not set but COBALT_API_URL is configured
    (e.g., a self-hosted instance), tries without auth header.
    NOTE: Does NOT use YOUTUBE_PROXY — Cobalt IS the proxy to YouTube.
    """
    if not _COBALT_API_KEY and not _cobalt_custom_url:
        LOG.debug("No Cobalt API key or custom URL set, skipping Cobalt.")
        return None

    yt_url = f"https://www.youtube.com/watch?v={video_id}"

    # Try both v10+ endpoint (POST /) and legacy endpoint (POST /api/json)
    _endpoints = ["/", "/api/json"]

    for instance in _COBALT_INSTANCES:
        for endpoint in _endpoints:
            try:
                # v10+ payload format
                if endpoint == "/":
                    payload = {
                        "url": yt_url,
                        "downloadMode": "audio" if audio_only else "auto",
                        "audioFormat": "opus",
                        "youtubeVideoCodec": "h264",
                        "videoQuality": "720",
                    }
                else:
                    # Legacy /api/json format
                    payload = {
                        "url": yt_url,
                        "isAudioOnly": audio_only,
                        "aFormat": "opus",
                        "vCodec": "h264",
                        "vQuality": "720",
                        "filenamePattern": "basic",
                    }

                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": _PROXY_HEADERS["User-Agent"],
                }
                if _COBALT_API_KEY:
                    headers["Authorization"] = f"Api-Key {_COBALT_API_KEY}"
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{instance}{endpoint}",
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            # v10+ format: {"url": "..."}
                            stream_url = data.get("url")
                            if stream_url:
                                LOG.info("Cobalt stream obtained from %s%s for %s (audio=%s)",
                                         instance, endpoint, video_id, audio_only)
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
                            # Legacy format: {"status": "stream", "url": "..."}
                            if data.get("status") in ("stream", "redirect", "success"):
                                stream_url = data.get("url")
                                if stream_url:
                                    LOG.info("Cobalt legacy stream from %s for %s",
                                             instance, video_id)
                                    return stream_url
                        else:
                            body = ""
                            try:
                                body = await resp.text()
                            except Exception:
                                pass
                            if resp.status in (401, 403):
                                LOG.warning(
                                    "Cobalt %s%s returned HTTP %d (auth error) for %s. "
                                    "Your COBALT_API_KEY may be invalid or expired. "
                                    "Get a valid key from https://cobalt.tools",
                                    instance, endpoint, resp.status, video_id,
                                )
                                break  # Don't try legacy endpoint with same bad key
                            else:
                                LOG.debug("Cobalt %s%s returned HTTP %d for %s: %s",
                                          instance, endpoint, resp.status, video_id, body[:100])
            except Exception as e:
                LOG.debug("Cobalt %s%s failed for %s: %s", instance, endpoint, video_id, e)
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
# NOTE: YouTube aggressively blocks cloud IPs. Order matters — most reliable first.
_PLAYER_CLIENTS = [
    # ANDROID_TESTSUITE — least blocked, returns direct URLs without cipher
    {
        "name": "ANDROID_TESTSUITE",
        "context": {
            "client": {
                "clientName": "ANDROID_TESTSUITE",
                "clientVersion": "1.9",
                "androidSdkVersion": 34,
                "hl": "en",
                "gl": "US",
                "osName": "Android",
                "osVersion": "14",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w",
        "ua": "com.google.android.youtube/1.9 (Linux; U; Android 14; en_US) gzip",
    },
    # IOS — reliable for direct stream URLs
    {
        "name": "IOS",
        "context": {
            "client": {
                "clientName": "IOS",
                "clientVersion": "20.25.2",
                "deviceMake": "Apple",
                "deviceModel": "iPhone16,2",
                "hl": "en",
                "gl": "US",
                "osName": "iOS",
                "osVersion": "18.5.1",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyB-63vPrdThhKuerbB2N_l7Kwwcxj6yUAc",
        "ua": "com.google.ios.youtube/20.25.2 (iPhone16,2; U; CPU iOS 18_5_1 like Mac OS X)",
    },
    # IOS_MUSIC — good for audio streams
    {
        "name": "IOS_MUSIC",
        "context": {
            "client": {
                "clientName": "IOS_MUSIC",
                "clientVersion": "7.45.1",
                "deviceMake": "Apple",
                "deviceModel": "iPhone16,2",
                "hl": "en",
                "gl": "US",
                "osName": "iOS",
                "osVersion": "18.5.1",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyBAETezhkwP0ZWA02RsqT1zu78Fpt0bC_s",
        "ua": "com.google.ios.youtubemusic/7.45.1 (iPhone16,2; U; CPU iOS 18_5_1 like Mac OS X)",
    },
    # ANDROID_MUSIC — alternative mobile music client
    {
        "name": "ANDROID_MUSIC",
        "context": {
            "client": {
                "clientName": "ANDROID_MUSIC",
                "clientVersion": "7.40.51",
                "androidSdkVersion": 34,
                "hl": "en",
                "gl": "US",
                "osName": "Android",
                "osVersion": "14",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyAOghZGza2MQSZkY_zfZ370N-PUdXEo8AI",
        "ua": "com.google.android.apps.youtube.music/7.40.51 (Linux; U; Android 14) gzip",
    },
    # ANDROID_VR — low detection rate (deprioritized — YouTube started blocking mid-2025)
    {
        "name": "ANDROID_VR",
        "context": {
            "client": {
                "clientName": "ANDROID_VR",
                "clientVersion": "1.68.05",
                "androidSdkVersion": 34,
                "hl": "en",
                "gl": "US",
                "osName": "Android",
                "osVersion": "14",
                "platform": "MOBILE",
            }
        },
        "key": "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w",
        "ua": "com.google.android.apps.youtube.vr.oculus/1.68.05 (Linux; U; Android 14) gzip",
    },
    # TV_EMBEDDED — works for some videos, no cipher needed
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
    # MEDIACONNECT — newer client, often bypasses restrictions
    {
        "name": "MEDIACONNECT",
        "context": {
            "client": {
                "clientName": "MEDIA_CONNECT_FRONTEND",
                "clientVersion": "0.1",
                "hl": "en",
                "gl": "US",
            }
        },
        "key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    },
]


def _parse_netscape_cookies(cookie_file: str) -> dict[str, str]:
    """Parse Netscape cookie file into a dict of name->value for youtube.com."""
    cookies = {}
    try:
        with open(cookie_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    # But parse #HttpOnly_ lines
                    if line.startswith("#HttpOnly_"):
                        line = line[len("#HttpOnly_"):]
                    else:
                        continue
                parts = line.split("\t")
                if len(parts) >= 7 and "youtube" in parts[0]:
                    cookies[parts[5]] = parts[6]
    except Exception:
        pass
    return cookies


async def _innertube_web_with_cookies(video_id: str, cookie_file: str) -> Optional[dict]:
    """Try WEB client InnerTube with cookie authentication.

    This is the most reliable method for cloud servers when cookies are available.
    YouTube trusts WEB client requests with valid login cookies even from cloud IPs.
    """
    cookies = _parse_netscape_cookies(cookie_file)
    if not cookies.get("SID") and not cookies.get("__Secure-1PSID"):
        return None  # No valid login cookies

    # Build cookie header string
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())

    # Generate SAPISIDHASH for authenticated requests
    sapisid = cookies.get("SAPISID") or cookies.get("__Secure-3PAPISID", "")
    import hashlib
    import time as _time
    origin = "https://www.youtube.com"
    timestamp = str(int(_time.time()))
    hash_input = f"{timestamp} {sapisid} {origin}"
    sapisidhash = hashlib.sha1(hash_input.encode()).hexdigest()
    auth_header = f"SAPISIDHASH {timestamp}_{sapisidhash}"

    _web_version = "2.20250523.06.00"
    # Calculate dynamic signatureTimestamp (days since YouTube epoch 2025-01-01
    # approx — this prevents stale hardcoded values from being rejected)
    import math, time as _time2
    _sts = math.floor(_time2.time() / 86400)  # daily rotating timestamp
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": _web_version,
                "hl": "en",
                "gl": "US",
            }
        },
        "videoId": video_id,
        "playbackContext": {
            "contentPlaybackContext": {
                "html5Preference": "HTML5_PREF_WANTS",
                "signatureTimestamp": _sts,
            }
        },
        "contentCheckOk": True,
        "racyCheckOk": True,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "Origin": origin,
        "Referer": f"{origin}/",
        "Cookie": cookie_header,
        "Authorization": auth_header,
        "X-Youtube-Client-Name": "1",
        "X-Youtube-Client-Version": _web_version,
        "X-Goog-Visitor-Id": cookies.get("VISITOR_INFO1_LIVE", ""),
        "X-Goog-AuthUser": "0",
    }

    api_url = f"{_INNERTUBE_PLAYER_URL}?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
                **_aio_request_kwargs(),
            ) as resp:
                if resp.status != 200:
                    LOG.debug("Innertube WEB+cookies HTTP %d for %s", resp.status, video_id)
                    return None
                data = await resp.json()

                ps = data.get("playabilityStatus", {})
                if ps.get("status") != "OK":
                    LOG.debug("Innertube WEB+cookies: status=%s for %s",
                             ps.get("status"), video_id)
                    return None

                sd = data.get("streamingData", {})
                all_fmts = sd.get("adaptiveFormats", []) + sd.get("formats", [])

                # With WEB client, formats may have signatureCipher — we accept both
                if all_fmts:
                    # Prefer direct URLs
                    direct = [f for f in all_fmts
                              if f.get("url") and not f.get("signatureCipher")]
                    if direct:
                        LOG.info("Innertube WEB+cookies: %d direct formats for %s",
                                len(direct), video_id)
                        return data
                    else:
                        LOG.debug("Innertube WEB+cookies: %d formats but all cipher for %s",
                                 len(all_fmts), video_id)
    except Exception as e:
        LOG.debug("Innertube WEB+cookies error for %s: %s", video_id, e)

    return None


async def _innertube_player(video_id: str) -> Optional[dict]:
    """Get player response from YouTube Innertube Player API directly.

    Tries WEB client with cookies first (best for cloud servers),
    then mobile clients for direct stream URLs without signature cipher.
    Auto-detects and disables broken proxies (402 Payment Required).
    """
    # Try WEB client with cookies first (works on cloud IPs when authenticated)
    cookie_file = _get_cookie()
    if cookie_file:
        try:
            result = await _innertube_web_with_cookies(video_id, cookie_file)
            if result:
                return result
        except Exception:
            LOG.debug("Innertube WEB+cookies failed for %s", video_id)

    # Try all mobile/TV clients (no cookies needed — direct stream URLs)
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
                    **_aio_request_kwargs(),
                ) as resp:
                    if resp.status != 200:
                        if resp.status in (402, 407):
                            _mark_proxy_failed()
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

                    # Check for HLS manifest URL first (no signature needed)
                    sd = data.get("streamingData", {})
                    hls_url = sd.get("hlsManifestUrl")
                    if hls_url:
                        LOG.info("Innertube %s: HLS manifest available for %s",
                                 client["name"], video_id)
                        # Store HLS URL in data for extraction
                        data["_hls_manifest_url"] = hls_url
                        return data

                    all_fmts = sd.get("adaptiveFormats", []) + sd.get("formats", [])

                    # Only use formats with direct URL (no signatureCipher)
                    direct = [f for f in all_fmts
                              if f.get("url") and not f.get("signatureCipher")]
                    if direct:
                        LOG.info("Innertube player: %d direct formats via %s for %s",
                                len(direct), client["name"], video_id)
                        return data
                    else:
                        # Store cipher format count for logging
                        cipher_count = len([f for f in all_fmts if f.get("signatureCipher")])
                        LOG.debug("Innertube %s: %d formats but all cipher for %s "
                                 "(cipher=%d, total=%d)",
                                 client["name"], len(all_fmts), video_id,
                                 cipher_count, len(all_fmts))
                        # NOTE: We don't return cipher formats here because
                        # they need signature decryption which Innertube alone
                        # can't do. yt-dlp handles this in its fallback path.

        except Exception as e:
            LOG.debug("Innertube player %s error for %s: %s",
                     client["name"], video_id, e)
            continue
    return None


def _best_innertube_audio(data: dict) -> Optional[str]:
    """Extract best direct audio URL from Innertube player response."""
    # Check for HLS manifest first (works without signature decryption)
    hls_url = data.get("_hls_manifest_url")
    if hls_url:
        LOG.info("Using HLS manifest URL for audio")
        return hls_url

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
    # Check for HLS manifest first (works without signature decryption)
    hls_url = data.get("_hls_manifest_url")
    if hls_url:
        LOG.info("Using HLS manifest URL for video")
        return hls_url

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
    # Ensure the cookie file starts with proper Netscape header
    if not raw.startswith("# Netscape HTTP Cookie File") and not raw.startswith("# HTTP Cookie File"):
        raw = "# Netscape HTTP Cookie File\n# https://curl.haxx.se/docs/http-cookies.html\n# This file was generated automatically.\n\n" + raw
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
# Updated May 2026 — with cookies, "web" client works best on cloud.
# Without cookies, mobile/TV clients are tried.
_CLIENT_COMBOS_WITH_COOKIES: list[list[str]] = [
    ["web"],                           # Web client — BEST with cookies on cloud
    ["web_creator"],                   # Creator Studio client — good for restricted
    ["web_music"],                     # YouTube Music web — good with cookies
    ["web_safari"],                    # Safari — cookies help
    ["ios"],                           # iOS client
    ["ios_music"],                     # iOS Music fallback
    ["mediaconnect"],                  # MediaConnect — newer, less blocked
    ["tv"],                            # Smart TV — fewer restrictions
]

_CLIENT_COMBOS_NO_COOKIES: list[list[str]] = [
    ["ios"],                           # iOS — best without cookies
    ["ios_music"],                     # iOS Music — rarely blocked
    ["mediaconnect"],                  # MediaConnect — newer client
    ["tv"],                            # Smart TV — fewer restrictions
    ["web_music"],                     # YouTube Music web client
    ["web_creator"],                   # Creator Studio — works without cookies too
    ["tv_embedded"],                   # TV embedded player
    ["mweb"],                          # Mobile web fallback
]


def _get_client_combos() -> list[list[str]]:
    """Return appropriate client combos based on cookie availability."""
    if _get_cookie():
        return _CLIENT_COMBOS_WITH_COOKIES
    return _CLIENT_COMBOS_NO_COOKIES


def _base_ytdlp_opts(client_combo: Optional[list[str]] = None) -> dict:
    combos = _get_client_combos()
    if client_combo is None:
        client_combo = combos[0]
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
        "no_color": True,
        "noprogress": True,
        "logger": _ytdlp_logger,
        "check_formats": "selected",    # Only verify the selected format, not all
        "allow_unplayable_formats": True,  # Accept formats yt-dlp can't verify on cloud
        "format_sort": [
            "proto:https",             # prefer HTTPS streams
            "proto:m3u8_native",       # prefer HLS (no signature needed)
            "hasaud",                  # prefer formats with audio
            "source",                  # prefer higher quality source
        ],
        "extractor_args": {
            "youtube": {
                "player_client": client_combo,
                # NOTE: do NOT set player_skip: ["webpage"] here —
                # on cloud IPs (Heroku) YouTube returns cipher-protected
                # stream URLs even for mobile clients. Without the webpage
                # player yt-dlp cannot decrypt signatures, causing
                # "Requested format is not available" errors.
            },
        },
        "hls_prefer_native": True,  # Use native HLS downloader (more reliable)
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        # Workaround: skip signature decryption issues
        "extractor_retries": 3,
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

    # Proxy support — essential for Heroku/cloud deployments
    proxy = _get_proxy()
    if proxy:
        opts["proxy"] = proxy
        LOG.debug("Using proxy for yt-dlp: %s", proxy[:30])

    return opts


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH — YouTube Innertube API (direct, no external library)
# ══════════════════════════════════════════════════════════════════════════════

_INNERTUBE_SEARCH_URL = "https://www.youtube.com/youtubei/v1/search"

_INNERTUBE_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20250520.01.00",
        "hl": "en",
        "gl": "US",
    }
}

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
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
    """Call YouTube innertube search API and parse results.

    NOTE: Does NOT use proxy — YouTube search API works fine from cloud IPs.
    Only the player/stream API blocks cloud IPs.
    """
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

    Priority: Cobalt -> Innertube Player API -> Piped -> Invidious -> yt-dlp.
    Cobalt is tried first because it's most reliable on cloud servers.
    """
    video_id = _extract_video_id(url)

    if video_id:
        # Try 1: Cobalt API (most reliable on cloud servers — bypasses YouTube blocks)
        try:
            stream_url = await _cobalt_get_stream(video_id, audio_only=True)
            if stream_url:
                LOG.info("Audio stream via Cobalt for %s", video_id)
                return stream_url
        except Exception:
            LOG.debug("Cobalt audio failed for %s", video_id)

        # Try 2: Innertube Player API (direct, no yt-dlp)
        try:
            data = await _innertube_player(video_id)
            if data:
                stream_url = _best_innertube_audio(data)
                if stream_url:
                    LOG.info("Audio stream via Innertube for %s", video_id)
                    return stream_url
        except Exception:
            LOG.debug("Innertube audio failed for %s", video_id)

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

    Priority: Cobalt -> Innertube Player API -> Piped -> Invidious -> yt-dlp.
    Cobalt is tried first because it's most reliable on cloud servers.
    """
    video_id = _extract_video_id(url)

    if video_id:
        # Try 1: Cobalt API (most reliable on cloud servers)
        try:
            stream_url = await _cobalt_get_stream(video_id, audio_only=False)
            if stream_url:
                LOG.info("Video stream via Cobalt for %s", video_id)
                return stream_url
        except Exception:
            LOG.debug("Cobalt video failed for %s", video_id)

        # Try 2: Innertube Player API
        try:
            data = await _innertube_player(video_id)
            if data:
                stream_url = _best_innertube_video(data)
                if stream_url:
                    LOG.info("Video stream via Innertube for %s", video_id)
                    return stream_url
        except Exception:
            LOG.debug("Innertube video failed for %s", video_id)

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

    # Try 5: yt-dlp (last resort)
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

    # Check for HLS manifest URL first (works without signature decryption)
    manifest_url = info.get("manifest_url")
    if manifest_url and "manifest/hls" in manifest_url:
        LOG.info("Using HLS manifest URL from yt-dlp info")
        return manifest_url

    # Direct URL
    stream_url = info.get("url")
    if stream_url:
        return stream_url

    # From requested_formats
    for fmt_info in info.get("requested_formats", []):
        fmt_url = fmt_info.get("url")
        if fmt_url:
            if audio_only and fmt_info.get("acodec", "none") != "none":
                return fmt_url
            if not audio_only and fmt_info.get("vcodec", "none") != "none":
                return fmt_url

    # From all formats — be very permissive
    formats = info.get("formats", [])
    if audio_only:
        audio_fmts = [f for f in formats
                      if f.get("url")
                      and f.get("acodec", "none") != "none"]
        if audio_fmts:
            best = max(audio_fmts, key=lambda f: f.get("abr", 0) or f.get("tbr", 0) or 0)
            return best.get("url")
    else:
        video_fmts = [f for f in formats
                      if f.get("url")
                      and f.get("vcodec", "none") != "none"]
        if video_fmts:
            best = max(video_fmts, key=lambda f: f.get("height", 0) or 0)
            return best.get("url")

    # Last resort: ANY format with a URL
    any_fmt = [f for f in formats if f.get("url")]
    if any_fmt:
        LOG.info("Using fallback format (any available) for stream")
        return any_fmt[-1].get("url")

    return None


def _get_stream_url_sync(url: str, audio_only: bool) -> Optional[str]:
    import yt_dlp

    if audio_only:
        fmt = "ba*/b"  # most permissive: best audio (any), fallback to best anything
    else:
        fmt = "bv*[height<=720]+ba*/bv*+ba*/b"  # very permissive video+audio

    last_err = None
    for combo in _get_client_combos():
        opts = {**_base_ytdlp_opts(client_combo=combo), "format": fmt}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                result = _extract_stream_from_info(info, audio_only)
                if result:
                    LOG.info("Stream URL obtained for %s (client: %s)", url, combo)
                    _mark_proxy_ok()
                    return result
        except Exception as exc:
            last_err = exc
            exc_str = str(exc)
            # Detect proxy payment/auth failures and auto-disable
            if "402" in exc_str or "Payment Required" in exc_str or \
               "407" in exc_str or "Proxy Authentication" in exc_str:
                _mark_proxy_failed()
                LOG.warning("Proxy payment/auth error detected: %s", exc_str[:100])
            LOG.warning("Stream URL attempt failed with client %s: %s", combo, exc)
            continue

    # Ultimate fallback: try without proxy if proxy was being used
    if last_err and _get_proxy():
        LOG.info("Retrying stream URL WITHOUT proxy for: %s", url)
        try:
            no_proxy_opts = _base_ytdlp_opts()
            no_proxy_opts.pop("proxy", None)  # Force no proxy
            no_proxy_opts["format"] = "ba*/b" if audio_only else "bv*+ba*/b"
            no_proxy_opts["allow_unplayable_formats"] = True
            no_proxy_opts["check_formats"] = False
            with yt_dlp.YoutubeDL(no_proxy_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                result = _extract_stream_from_info(info, audio_only)
                if result:
                    LOG.info("Stream URL obtained WITHOUT proxy for %s", url)
                    _mark_proxy_failed()  # Mark proxy as bad
                    return result
        except Exception as exc2:
            LOG.warning("No-proxy fallback also failed: %s", exc2)

    # Ultimate fallback 1: try "b" (best anything) with default client
    if last_err:
        LOG.info("Retrying with permissive format 'b' for: %s", url)
        try:
            fb_opts = _base_ytdlp_opts()
            fb_opts["format"] = "b"
            fb_opts["allow_unplayable_formats"] = True
            fb_opts.pop("proxy", None)  # Try without proxy for broader compatibility
            with yt_dlp.YoutubeDL(fb_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                result = _extract_stream_from_info(info, audio_only)
                if result:
                    LOG.info("Stream URL obtained via permissive fallback for %s", url)
                    return result
        except Exception as exc2:
            LOG.warning("Permissive fallback also failed: %s", exc2)

    # Ultimate fallback 2: try with NO format restriction (accept anything)
    if last_err:
        LOG.info("Retrying with no format restriction for: %s", url)
        try:
            worst_opts = _base_ytdlp_opts()
            worst_opts.pop("proxy", None)  # No proxy
            worst_opts["format"] = "worst"  # even worst quality is better than nothing
            worst_opts["allow_unplayable_formats"] = True
            worst_opts["check_formats"] = False  # Skip ALL format verification
            with yt_dlp.YoutubeDL(worst_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                result = _extract_stream_from_info(info, audio_only)
                if result:
                    LOG.info("Stream URL obtained via worst-format fallback for %s", url)
                    return result
        except Exception as exc3:
            LOG.warning("Worst-format fallback also failed: %s", exc3)

    # Ultimate fallback 3: try "default" client (yt-dlp auto-selects best)
    if last_err:
        LOG.info("Retrying with default yt-dlp client for: %s", url)
        try:
            default_opts = _base_ytdlp_opts()
            default_opts.pop("proxy", None)
            default_opts["format"] = "ba*/b" if audio_only else "bv*+ba*/b"
            default_opts["allow_unplayable_formats"] = True
            default_opts["check_formats"] = False
            # Remove player_client restriction — let yt-dlp decide
            default_opts.get("extractor_args", {}).get("youtube", {}).pop("player_client", None)
            with yt_dlp.YoutubeDL(default_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                result = _extract_stream_from_info(info, audio_only)
                if result:
                    LOG.info("Stream URL obtained via default-client fallback for %s", url)
                    return result
        except Exception as exc4:
            LOG.warning("Default-client fallback also failed: %s", exc4)

    if last_err:
        LOG.error("All yt-dlp stream URL attempts failed: %s — %s", url, last_err)
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
    """Download audio. Tries Cobalt/Innertube/Piped stream + download, yt-dlp fallback."""
    video_id = _extract_video_id(url)

    if video_id:
        # Try 1: Cobalt API -> download stream (most reliable on cloud)
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

        # Try 2: Innertube Player API -> download stream
        try:
            data = await _innertube_player(video_id)
            if data:
                stream_url = _best_innertube_audio(data)
                if stream_url:
                    filepath = os.path.join(_DOWNLOADS, f"{video_id}_innertube.m4a")
                    # Innertube URLs are YouTube CDN — NEED proxy on cloud
                    downloaded = await _download_stream(stream_url, filepath,
                                                        use_proxy=True)
                    if downloaded:
                        LOG.info("Audio downloaded via Innertube for %s", video_id)
                        return downloaded
        except Exception:
            LOG.debug("Innertube audio download failed for %s", video_id)

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

    # Try 4: yt-dlp (last resort)
    LOG.info("Direct download failed, trying yt-dlp for audio: %s", url)
    opts = {
        **_base_ytdlp_opts(),
        "format": "ba*/b",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s.%(ext)s"),
        "overwrites": False,
    }
    return await _run_ytdlp(url, opts)


async def download_video(url: str) -> Optional[str]:
    """Download video. Tries Cobalt/Innertube/Piped stream + download, yt-dlp fallback."""
    video_id = _extract_video_id(url)

    if video_id:
        # Try 1: Cobalt API -> download stream (most reliable on cloud)
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

        # Try 2: Innertube Player API -> download stream
        try:
            data = await _innertube_player(video_id)
            if data:
                stream_url = _best_innertube_video(data)
                if stream_url:
                    filepath = os.path.join(_DOWNLOADS, f"{video_id}_innertube_video.mp4")
                    # Innertube URLs are YouTube CDN — NEED proxy on cloud
                    downloaded = await _download_stream(stream_url, filepath,
                                                        use_proxy=True)
                    if downloaded:
                        LOG.info("Video downloaded via Innertube for %s", video_id)
                        return downloaded
        except Exception:
            LOG.debug("Innertube video download failed for %s", video_id)

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

    # Try 4: yt-dlp (last resort)
    LOG.info("Direct download failed, trying yt-dlp for video: %s", url)
    opts = {
        **_base_ytdlp_opts(),
        "format": "bv*[height<=720]+ba*/bv*+ba*/b",
        "outtmpl": os.path.join(_DOWNLOADS, "%(id)s_video.%(ext)s"),
        "merge_output_format": "mp4",
        "overwrites": False,
    }
    return await _run_ytdlp(url, opts)


async def search_and_download_audio(query: str) -> tuple[Optional[str], Optional[dict]]:
    """Search YouTube and download audio in one step using yt-dlp's ytsearch.

    This is the most reliable fallback for cloud servers where separate
    search -> extract URL -> download flow fails due to IP blocking.
    yt-dlp handles search + download atomically.

    Returns (filepath, info_dict) or (None, None).
    """
    import yt_dlp
    loop = asyncio.get_running_loop()

    for combo in _get_client_combos():
        opts = {
            **_base_ytdlp_opts(client_combo=combo),
            "format": "ba*/b",
            "outtmpl": os.path.join(_DOWNLOADS, "%(id)s.%(ext)s"),
            "default_search": "ytsearch",
            "noplaylist": True,
            "overwrites": False,
        }
        try:
            def _do_search_dl():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(query, download=True)
                    if not info:
                        return None, None
                    # ytsearch returns a playlist-like result
                    entries = info.get("entries")
                    item = entries[0] if entries else info
                    if not item:
                        return None, None
                    path = ydl.prepare_filename(item)
                    if not os.path.exists(path):
                        base = os.path.splitext(path)[0]
                        for ext in (".opus", ".m4a", ".webm", ".mp3", ".ogg", ".mp4"):
                            candidate = base + ext
                            if os.path.exists(candidate):
                                path = candidate
                                break
                        else:
                            matches = sorted(glob.glob(f"{base}.*"),
                                             key=os.path.getmtime, reverse=True)
                            if matches:
                                path = matches[0]
                    if not os.path.exists(path):
                        return None, None
                    result_info = {
                        "title": item.get("title", "Unknown"),
                        "url": item.get("webpage_url") or item.get("url", ""),
                        "duration": int(item.get("duration") or 0),
                        "thumbnail": item.get("thumbnail", ""),
                        "channel": item.get("uploader") or item.get("channel", "Unknown"),
                        "video_id": item.get("id", ""),
                    }
                    return path, result_info

            filepath, info = await loop.run_in_executor(None, _do_search_dl)
            if filepath and os.path.isfile(filepath):
                LOG.info("search_and_download_audio succeeded (client: %s): %s", combo, query)
                return filepath, info
        except Exception as exc:
            LOG.warning("search_and_download_audio failed (client %s): %s", combo, exc)
            continue

    LOG.error("search_and_download_audio: all attempts failed for: %s", query)

    # Last resort: retry first client combo without proxy
    if _get_proxy() or _proxy_dead:
        LOG.info("search_and_download_audio: retrying WITHOUT proxy for: %s", query)
        import yt_dlp as _yt_dlp
        combos = _get_client_combos()
        opts = {
            **_base_ytdlp_opts(client_combo=combos[0]),
            "format": "ba*/b",
            "outtmpl": os.path.join(_DOWNLOADS, "%(id)s.%(ext)s"),
            "default_search": "ytsearch",
            "noplaylist": True,
            "overwrites": False,
        }
        opts.pop("proxy", None)  # Force no proxy
        try:
            def _do_noproxy_dl():
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(query, download=True)
                    if not info:
                        return None, None
                    entries = info.get("entries")
                    item = entries[0] if entries else info
                    if not item:
                        return None, None
                    path = ydl.prepare_filename(item)
                    if not os.path.exists(path):
                        base = os.path.splitext(path)[0]
                        for ext in (".opus", ".m4a", ".webm", ".mp3", ".ogg", ".mp4"):
                            candidate = base + ext
                            if os.path.exists(candidate):
                                path = candidate
                                break
                        else:
                            matches = sorted(glob.glob(f"{base}.*"),
                                             key=os.path.getmtime, reverse=True)
                            if matches:
                                path = matches[0]
                    if not os.path.exists(path):
                        return None, None
                    result_info = {
                        "title": item.get("title", "Unknown"),
                        "url": item.get("webpage_url") or item.get("url", ""),
                        "duration": int(item.get("duration") or 0),
                        "thumbnail": item.get("thumbnail", ""),
                        "channel": item.get("uploader") or item.get("channel", "Unknown"),
                        "video_id": item.get("id", ""),
                    }
                    return path, result_info
            filepath, info = await loop.run_in_executor(None, _do_noproxy_dl)
            if filepath and os.path.isfile(filepath):
                LOG.info("search_and_download_audio succeeded WITHOUT proxy: %s", query)
                _mark_proxy_failed()
                return filepath, info
        except Exception as exc:
            LOG.warning("search_and_download_audio no-proxy also failed: %s", exc)

    return None, None


async def search_and_download_video(query: str) -> tuple[Optional[str], Optional[dict]]:
    """Search YouTube and download video in one step using yt-dlp's ytsearch.

    Returns (filepath, info_dict) or (None, None).
    """
    import yt_dlp
    loop = asyncio.get_running_loop()

    for combo in _get_client_combos():
        opts = {
            **_base_ytdlp_opts(client_combo=combo),
            "format": "bv*[height<=720]+ba*/bv*+ba*/b",
            "outtmpl": os.path.join(_DOWNLOADS, "%(id)s_video.%(ext)s"),
            "merge_output_format": "mp4",
            "default_search": "ytsearch",
            "noplaylist": True,
            "overwrites": False,
        }
        try:
            def _do_search_dl():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(query, download=True)
                    if not info:
                        return None, None
                    entries = info.get("entries")
                    item = entries[0] if entries else info
                    if not item:
                        return None, None
                    path = ydl.prepare_filename(item)
                    if not os.path.exists(path):
                        base = os.path.splitext(path)[0]
                        for ext in (".mp4", ".mkv", ".webm", ".flv"):
                            candidate = base + ext
                            if os.path.exists(candidate):
                                path = candidate
                                break
                        else:
                            matches = sorted(glob.glob(f"{base}.*"),
                                             key=os.path.getmtime, reverse=True)
                            if matches:
                                path = matches[0]
                    if not os.path.exists(path):
                        return None, None
                    result_info = {
                        "title": item.get("title", "Unknown"),
                        "url": item.get("webpage_url") or item.get("url", ""),
                        "duration": int(item.get("duration") or 0),
                        "thumbnail": item.get("thumbnail", ""),
                        "channel": item.get("uploader") or item.get("channel", "Unknown"),
                        "video_id": item.get("id", ""),
                    }
                    return path, result_info

            filepath, info = await loop.run_in_executor(None, _do_search_dl)
            if filepath and os.path.isfile(filepath):
                LOG.info("search_and_download_video succeeded (client: %s): %s", combo, query)
                return filepath, info
        except Exception as exc:
            LOG.warning("search_and_download_video failed (client %s): %s", combo, exc)
            continue

    LOG.error("search_and_download_video: all attempts failed for: %s", query)

    # Last resort: retry first client combo without proxy
    if _get_proxy() or _proxy_dead:
        LOG.info("search_and_download_video: retrying WITHOUT proxy for: %s", query)
        import yt_dlp as _yt_dlp
        combos = _get_client_combos()
        opts = {
            **_base_ytdlp_opts(client_combo=combos[0]),
            "format": "bv*[height<=720]+ba*/bv*+ba*/b",
            "outtmpl": os.path.join(_DOWNLOADS, "%(id)s_video.%(ext)s"),
            "merge_output_format": "mp4",
            "default_search": "ytsearch",
            "noplaylist": True,
            "overwrites": False,
        }
        opts.pop("proxy", None)  # Force no proxy
        try:
            def _do_noproxy_dl():
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(query, download=True)
                    if not info:
                        return None, None
                    entries = info.get("entries")
                    item = entries[0] if entries else info
                    if not item:
                        return None, None
                    path = ydl.prepare_filename(item)
                    if not os.path.exists(path):
                        base = os.path.splitext(path)[0]
                        for ext in (".mp4", ".mkv", ".webm", ".flv"):
                            candidate = base + ext
                            if os.path.exists(candidate):
                                path = candidate
                                break
                        else:
                            matches = sorted(glob.glob(f"{base}.*"),
                                             key=os.path.getmtime, reverse=True)
                            if matches:
                                path = matches[0]
                    if not os.path.exists(path):
                        return None, None
                    result_info = {
                        "title": item.get("title", "Unknown"),
                        "url": item.get("webpage_url") or item.get("url", ""),
                        "duration": int(item.get("duration") or 0),
                        "thumbnail": item.get("thumbnail", ""),
                        "channel": item.get("uploader") or item.get("channel", "Unknown"),
                        "video_id": item.get("id", ""),
                    }
                    return path, result_info
            filepath, info = await loop.run_in_executor(None, _do_noproxy_dl)
            if filepath and os.path.isfile(filepath):
                LOG.info("search_and_download_video succeeded WITHOUT proxy: %s", query)
                _mark_proxy_failed()
                return filepath, info
        except Exception as exc:
            LOG.warning("search_and_download_video no-proxy also failed: %s", exc)

    return None, None


async def _download_stream(stream_url: str, filepath: str,
                           use_proxy: bool = False) -> Optional[str]:
    """Download a stream URL directly via aiohttp.

    use_proxy=True for Innertube/YouTube CDN URLs (need proxy on cloud).
    use_proxy=False for Piped/Invidious/Cobalt URLs (already proxied).
    """
    try:
        req_kwargs = {}
        if use_proxy:
            proxy = _get_proxy()
            if proxy:
                req_kwargs["proxy"] = proxy
                LOG.debug("Downloading stream via proxy: %s", proxy[:30])

        async with aiohttp.ClientSession() as session:
            async with session.get(
                stream_url,
                headers=_PROXY_HEADERS,
                timeout=aiohttp.ClientTimeout(total=180),
                **req_kwargs,
            ) as resp:
                if resp.status != 200:
                    LOG.debug("Stream download HTTP %d for: %s", resp.status, stream_url[:80])
                    return None
                import aiofiles
                total_bytes = 0
                async with aiofiles.open(filepath, "wb") as fp:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        await fp.write(chunk)
                        total_bytes += len(chunk)
        if os.path.exists(filepath) and total_bytes > 1000:
            LOG.info("Downloaded %d bytes to %s", total_bytes, filepath)
            return filepath
        LOG.warning("Downloaded file is empty or too small (%d bytes): %s",
                   total_bytes, stream_url[:80])
        # Clean up empty file
        if os.path.exists(filepath):
            os.remove(filepath)
        return None
    except Exception:
        LOG.debug("Direct stream download failed: %s", stream_url[:80])
        return None


def _get_info_sync(url: str) -> Optional[dict]:
    import yt_dlp

    last_err = None
    for combo in _get_client_combos():
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
        LOG.error("All yt-dlp info attempts failed: %s — %s", url, last_err)
    return None


async def _run_ytdlp(url: str, opts: dict) -> Optional[str]:
    import yt_dlp
    loop = asyncio.get_running_loop()

    last_err = None
    for combo in _get_client_combos():
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
            exc_str = str(exc)
            if "402" in exc_str or "Payment Required" in exc_str or \
               "407" in exc_str or "Proxy Authentication" in exc_str:
                _mark_proxy_failed()
            LOG.warning("yt-dlp download attempt failed (client %s): %s", combo, exc)
            continue

    # Retry without proxy if proxy was being used
    if last_err and not _proxy_dead and _get_proxy():
        LOG.info("Retrying download WITHOUT proxy for: %s", url)
        try:
            no_proxy_opts = {**opts}
            no_proxy_opts.pop("proxy", None)
            # Also remove proxy from any nested opts that _base_ytdlp_opts may have added
            cookie = _get_cookie()
            if cookie:
                no_proxy_opts["cookiefile"] = cookie
            no_proxy_opts["format"] = "b"  # Most permissive format
            with yt_dlp.YoutubeDL(no_proxy_opts) as ydl:
                info = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(url, download=True)
                )
                if info:
                    path = ydl.prepare_filename(info)
                    if os.path.exists(path):
                        _mark_proxy_failed()
                        return path
                    base = os.path.splitext(path)[0]
                    matches = sorted(glob.glob(f"{base}.*"),
                                     key=os.path.getmtime, reverse=True)
                    if matches:
                        _mark_proxy_failed()
                        return matches[0]
        except Exception as exc_np:
            LOG.warning("No-proxy download fallback also failed: %s", exc_np)

    # Ultimate fallback: try "b" format with default client
    if last_err:
        LOG.info("Retrying download with permissive format 'b' for: %s", url)
        try:
            fallback_opts = {**opts, "format": "b",
                             "allow_unplayable_formats": True,
                             "check_formats": False}
            cookie = _get_cookie()
            if cookie:
                fallback_opts["cookiefile"] = cookie
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                info = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(url, download=True)
                )
                if info:
                    path = ydl.prepare_filename(info)
                    if os.path.exists(path):
                        return path
                    base = os.path.splitext(path)[0]
                    matches = sorted(glob.glob(f"{base}.*"),
                                     key=os.path.getmtime, reverse=True)
                    if matches:
                        return matches[0]
        except Exception as exc2:
            LOG.warning("Permissive download fallback also failed: %s", exc2)

    # Ultimate fallback 2: try "worst" format (any quality, just get something)
    if last_err:
        LOG.info("Retrying download with 'worst' format for: %s", url)
        try:
            worst_opts = {**opts, "format": "worst",
                          "allow_unplayable_formats": True,
                          "check_formats": False}
            cookie = _get_cookie()
            if cookie:
                worst_opts["cookiefile"] = cookie
            with yt_dlp.YoutubeDL(worst_opts) as ydl:
                info = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(url, download=True)
                )
                if info:
                    path = ydl.prepare_filename(info)
                    if os.path.exists(path):
                        return path
                    base = os.path.splitext(path)[0]
                    matches = sorted(glob.glob(f"{base}.*"),
                                     key=os.path.getmtime, reverse=True)
                    if matches:
                        return matches[0]
        except Exception as exc3:
            LOG.warning("Worst-format download fallback also failed: %s", exc3)

    # Ultimate fallback 3: default yt-dlp client with no restrictions
    if last_err:
        LOG.info("Retrying download with default client for: %s", url)
        try:
            default_opts = {**opts, "format": "b",
                            "allow_unplayable_formats": True,
                            "check_formats": False}
            default_opts.pop("proxy", None)
            cookie = _get_cookie()
            if cookie:
                default_opts["cookiefile"] = cookie
            # Remove player_client restriction
            default_opts.get("extractor_args", {}).get("youtube", {}).pop("player_client", None)
            with yt_dlp.YoutubeDL(default_opts) as ydl:
                info = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(url, download=True)
                )
                if info:
                    path = ydl.prepare_filename(info)
                    if os.path.exists(path):
                        return path
                    base = os.path.splitext(path)[0]
                    matches = sorted(glob.glob(f"{base}.*"),
                                     key=os.path.getmtime, reverse=True)
                    if matches:
                        return matches[0]
        except Exception as exc4:
            LOG.warning("Default-client download fallback also failed: %s", exc4)

    if last_err:
        LOG.error("All yt-dlp download attempts failed: %s — %s", url, last_err)
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
