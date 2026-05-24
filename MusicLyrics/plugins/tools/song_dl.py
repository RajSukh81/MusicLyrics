"""Song/video download plugin for MusicLyrics bot."""

import os
import asyncio
import re
import tempfile
import logging

from pyrogram import filters
from pyrogram.types import Message

from MusicLyrics.bot import bot
from config import Config

LOG = logging.getLogger(__name__)


async def _yt_search(query: str) -> dict | None:
    """Search YouTube and return first result info dict."""
    try:
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "default_search": "ytsearch1",
            "noplaylist": True,
            "socket_timeout": 15,
        }
        loop = asyncio.get_running_loop()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: ydl.extract_info(query, download=False)
                ),
                timeout=60,
            )
        if not info:
            return None
        # If search result, get first entry
        entries = info.get("entries")
        if entries:
            return entries[0] if entries else None
        return info
    except asyncio.TimeoutError:
        LOG.error("yt-dlp search timed out for: %s", query)
        return None
    except Exception as e:
        LOG.exception("yt-dlp search error: %s", e)
        return None


async def _download(query: str, audio_only: bool = True) -> tuple[str | None, dict | None]:
    """Download audio/video and return (filepath, info)."""
    info = await _yt_search(query)
    if not info:
        return None, None

    url = info.get("webpage_url") or info.get("url", query)
    title = info.get("title", "Unknown")
    # Sanitize filename
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)[:50]

    dl_dir = Config.DOWNLOADS_DIR
    os.makedirs(dl_dir, exist_ok=True)

    if audio_only:
        out_path = os.path.join(dl_dir, f"{safe_title}.%(ext)s")
        opts = {
            "format": "bestaudio/best",
            "outtmpl": out_path,
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "noplaylist": True,
            "socket_timeout": 30,
            "retries": 3,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "128",
                }
            ],
        }
    else:
        out_path = os.path.join(dl_dir, f"{safe_title}.%(ext)s")
        opts = {
            "format": "best[ext=mp4][height<=720]/best[height<=720]/best",
            "outtmpl": out_path,
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "noplaylist": True,
            "socket_timeout": 30,
            "retries": 3,
            "merge_output_format": "mp4",
        }

    import yt_dlp
    import glob

    loop = asyncio.get_running_loop()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: ydl.extract_info(url, download=True)
                ),
                timeout=120,
            )
    except asyncio.TimeoutError:
        LOG.error("yt-dlp download timed out for: %s", url)
        return None, info
    except Exception:
        LOG.exception("yt-dlp download failed for: %s", url)
        return None, info

    # Find the downloaded file
    base_pattern = os.path.join(dl_dir, f"{safe_title}.*")
    matches = glob.glob(base_pattern)
    if matches:
        return matches[0], info

    return None, info


@bot.on_message(filters.command("song"))
async def song_cmd(client, message: Message):
    """Search and download a song, send as audio."""
    args = message.text.split(None, 1)
    if len(args) < 2:
        return await message.reply_text(
            "**Usage:** `/song <query>`\n\n"
            "Example: `/song Arijit Singh Tum Hi Ho`"
        )

    query = args[1].strip()
    status = await message.reply_text(
        f"🔍 খুঁজছি: **{query}**\nSearching..."
    )

    try:
        await status.edit_text(f"⬇️ ডাউনলোড হচ্ছে... / Downloading: **{query}**")
        path, info = await _download(query, audio_only=True)

        if not path:
            return await status.edit_text(
                "❌ গান খুঁজে পাওয়া যায়নি বা ডাউনলোড ব্যর্থ।\n"
                "Song not found or download failed.\n\n"
                "Tips: Try a different search query or use a direct YouTube URL."
            )

        title = info.get("title", "Unknown") if info else "Unknown"
        duration = info.get("duration", 0) if info else 0
        performer = info.get("uploader", "Unknown") if info else "Unknown"

        # Check file size (Telegram limit ~50MB for bots)
        file_size = os.path.getsize(path)
        if file_size > 50 * 1024 * 1024:
            os.remove(path)
            return await status.edit_text(
                "❌ ফাইল সাইজ 50MB এর বেশি। Telegram limit exceeded.\n"
                "Try a shorter song."
            )

        await status.edit_text(f"📤 আপলোড হচ্ছে... / Uploading: **{title}**")
        await message.reply_audio(
            audio=path,
            title=title,
            performer=performer,
            duration=int(duration),
            caption=f"🎵 **{title}**\n🎤 {performer}\n⏱ {int(duration // 60)}:{int(duration % 60):02d}",
        )
        await status.delete()
    except Exception as e:
        LOG.exception("Error in /song command")
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if "path" in locals() and path and os.path.exists(path):
            os.remove(path)


@bot.on_message(filters.command("vsong"))
async def vsong_cmd(client, message: Message):
    """Search and download a video, send as video file."""
    args = message.text.split(None, 1)
    if len(args) < 2:
        return await message.reply_text(
            "**Usage:** `/vsong <query>`\n\n"
            "Example: `/vsong Arijit Singh live concert`"
        )

    query = args[1].strip()
    status = await message.reply_text(
        f"🔍 খুঁজছি: **{query}**\nSearching..."
    )

    try:
        await status.edit_text(f"⬇️ ডাউনলোড হচ্ছে... / Downloading: **{query}**")
        path, info = await _download(query, audio_only=False)

        if not path:
            return await status.edit_text(
                "❌ ভিডিও খুঁজে পাওয়া যায়নি বা ডাউনলোড ব্যর্থ।\n"
                "Video not found or download failed.\n\n"
                "Tips: Try a different search query or use a direct YouTube URL."
            )

        title = info.get("title", "Unknown") if info else "Unknown"
        duration = info.get("duration", 0) if info else 0

        # Check file size
        file_size = os.path.getsize(path)
        if file_size > 50 * 1024 * 1024:
            os.remove(path)
            return await status.edit_text(
                "❌ ফাইল সাইজ 50MB এর বেশি। Telegram limit exceeded.\n"
                "Try `/vsong` with a shorter video."
            )

        await status.edit_text(f"📤 আপলোড হচ্ছে... / Uploading: **{title}**")
        await message.reply_video(
            video=path,
            duration=int(duration),
            caption=f"🎬 **{title}**\n⏱ {int(duration // 60)}:{int(duration % 60):02d}",
        )
        await status.delete()
    except Exception as e:
        LOG.exception("Error in /vsong command")
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if "path" in locals() and path and os.path.exists(path):
            os.remove(path)
