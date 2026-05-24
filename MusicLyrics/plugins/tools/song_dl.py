"""Song/video download plugin for MusicLyrics bot."""

import json
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
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--default-search", "ytsearch1",
            "--no-playlist", "-f", "bestaudio/best",
            "--geo-bypass", "--no-check-certificates",
            query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0 or not stdout:
            LOG.warning("yt-dlp search failed: %s", stderr.decode()[:200] if stderr else "no output")
            return None
        return json.loads(stdout)
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
    # Sanitize filename — remove path separators and special chars
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)[:50]
    duration = info.get("duration", 0)

    dl_dir = Config.DOWNLOADS_DIR
    os.makedirs(dl_dir, exist_ok=True)

    if audio_only:
        out_path = os.path.join(dl_dir, f"{safe_title}.mp3")
        cmd = [
            "yt-dlp", "-x", "--audio-format", "mp3",
            "--audio-quality", "128K",
            "--geo-bypass", "--no-check-certificates",
            "-o", out_path, "--no-playlist", url,
        ]
    else:
        out_path = os.path.join(dl_dir, f"{safe_title}.mp4")
        cmd = [
            "yt-dlp", "-f", "best[ext=mp4][height<=720]/best[height<=720]/best",
            "--geo-bypass", "--no-check-certificates",
            "-o", out_path, "--no-playlist", url,
        ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            LOG.warning("yt-dlp download failed: %s", stderr.decode()[:200] if stderr else "unknown")
    except asyncio.TimeoutError:
        LOG.error("yt-dlp download timed out for: %s", url)
        return None, info

    if os.path.exists(out_path):
        return out_path, info

    # yt-dlp may append extension — try glob
    import glob
    pattern = os.path.join(dl_dir, f"{safe_title}.*")
    matches = glob.glob(pattern)
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
