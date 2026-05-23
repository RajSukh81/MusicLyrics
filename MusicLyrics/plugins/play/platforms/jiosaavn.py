"""JioSaavn integration via the public saavn.dev API."""

from __future__ import annotations

import logging
import re
from typing import Optional

import aiohttp

LOG = logging.getLogger(__name__)

_API_BASE = "https://saavn.dev/api"


def is_jiosaavn_url(url: str) -> bool:
    return bool(re.match(r"https?://(www\.)?jiosaavn\.com/", url))


async def search_jiosaavn(query: str) -> Optional[dict]:
    """Search JioSaavn; return first result or None.

    Keys: title, url, duration (sec), thumbnail, artist, download_url.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_API_BASE}/search/songs",
                params={"query": query, "limit": 1},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        results = data.get("data", {}).get("results", [])
        if not results:
            return None
        song = results[0]
        return _parse_song(song)
    except Exception:
        LOG.exception("JioSaavn search failed: %s", query)
        return None


async def get_jiosaavn_song(url: str) -> Optional[dict]:
    """Get song details and direct download URL from a JioSaavn link."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_API_BASE}/songs", params={"link": url}
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        songs = data.get("data", [])
        if not songs:
            return None
        return _parse_song(songs[0])
    except Exception:
        LOG.exception("JioSaavn song fetch failed: %s", url)
        return None


async def download_jiosaavn(url: str) -> Optional[str]:
    """Download song from JioSaavn direct URL and return local path."""
    import os, aiofiles
    from config import Config

    song = await get_jiosaavn_song(url)
    if not song or not song.get("download_url"):
        return None

    os.makedirs(Config.DOWNLOADS_DIR, exist_ok=True)
    # sanitise filename
    safe = re.sub(r'[^\w\s-]', '', song["title"])[:60].strip()
    filepath = os.path.join(Config.DOWNLOADS_DIR, f"{safe}.m4a")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(song["download_url"]) as resp:
                if resp.status != 200:
                    return None
                async with aiofiles.open(filepath, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 64):
                        await f.write(chunk)
        return filepath
    except Exception:
        LOG.exception("JioSaavn download failed: %s", url)
        return None


def _parse_song(song: dict) -> dict:
    """Normalise a JioSaavn API song object."""
    download_urls = song.get("downloadUrl", [])
    # pick highest quality
    dl_url = ""
    if download_urls:
        dl_url = download_urls[-1].get("url", "")

    images = song.get("image", [])
    thumb = images[-1].get("url", "") if images else ""

    artists = song.get("artists", {}).get("primary", [])
    artist_str = ", ".join(a.get("name", "") for a in artists) if artists else (
        song.get("primaryArtists", "Unknown")
    )

    return {
        "title": song.get("name", "Unknown"),
        "url": song.get("url", ""),
        "duration": int(song.get("duration", 0)),
        "thumbnail": thumb,
        "artist": artist_str,
        "download_url": dl_url,
    }
