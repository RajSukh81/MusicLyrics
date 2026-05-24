"""Handler for /vplay <query|url> — video playback in voice chat.

Uses stream URL extraction as primary method (no download needed),
with download as fallback. Based on patterns from YukkiMusicBot.
"""

from __future__ import annotations

import logging
import os
import re

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from MusicLyrics.bot import bot
from MusicLyrics.helpers.filters import not_edited
from config import Config

from MusicLyrics.plugins.play.queue import (
    QueueItem,
    add_to_queue,
    get_chat_queue,
    format_duration,
)
from MusicLyrics.plugins.play.stream import stream_video, is_active
from MusicLyrics.plugins.play.platforms.youtube import (
    search_youtube,
    get_video_stream_url,
    download_video,
    get_video_info,
    is_youtube_url,
)
from MusicLyrics.plugins.play.platforms.spotify import (
    is_spotify_url,
    get_spotify_track,
)
from MusicLyrics.plugins.play.platforms.jiosaavn import is_jiosaavn_url
from MusicLyrics.plugins.play.platforms.apple_music import (
    is_apple_music_url,
    get_apple_music_track,
)
from MusicLyrics.plugins.play.play import _detect_platform

LOG = logging.getLogger(__name__)


def _vcontrol_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏸ Pause", callback_data="ctl_pause"),
                InlineKeyboardButton("▶️ Resume", callback_data="ctl_resume"),
                InlineKeyboardButton("⏭ Skip", callback_data="ctl_skip"),
            ],
            [
                InlineKeyboardButton("⏹ Stop", callback_data="ctl_stop"),
                InlineKeyboardButton("📜 Queue", callback_data="ctl_queue"),
                InlineKeyboardButton("🔁 Loop", callback_data="ctl_loop"),
            ],
        ]
    )


async def _get_video_media(url: str) -> tuple[str, bool]:
    """Get media path for video playback.

    Returns (media_path, is_stream_url).
    On cloud servers, download-first is more reliable than streaming URLs
    (avoids IP-restricted googlevideo URLs and proxy failures).
    """
    # Strategy: Download to local file first (most reliable on cloud)
    LOG.info("Downloading video for: %s", url)
    filepath = await download_video(url)
    if filepath and os.path.isfile(filepath):
        return filepath, False

    # Fallback: Try stream URL directly
    LOG.info("Video download failed, trying stream URL for: %s", url)
    stream_url = await get_video_stream_url(url)
    if stream_url:
        return stream_url, True

    return "", False


async def _resolve_video(query: str, platform: str):
    """Resolve query into (info_dict, media_path, is_stream_url) for video."""

    # YouTube URL
    if platform == "youtube":
        info = await get_video_info(query)
        if not info:
            info = {"title": "YouTube Video", "url": query,
                    "duration": 0, "thumbnail": "", "channel": ""}
        if info["duration"] > Config.DURATION_LIMIT_MIN * 60 and info["duration"] > 0:
            raise ValueError(
                f"ভিডিওটি {Config.DURATION_LIMIT_MIN} মিনিটের বেশি।"
            )
        media_path, is_stream = await _get_video_media(query)
        if not media_path:
            raise ValueError("YouTube থেকে video পাওয়া যায়নি।")
        return info, media_path, is_stream

    # Spotify — search YT for video
    if platform == "spotify":
        track = await get_spotify_track(query)
        if not track:
            raise ValueError("Spotify link parse করা যায়নি।")
        yt = await search_youtube(track["query"])
        if not yt:
            raise ValueError("YouTube-এ video পাওয়া যায়নি।")
        media_path, is_stream = await _get_video_media(yt["url"])
        if not media_path:
            raise ValueError("Video stream পাওয়া যায়নি।")
        return {**yt, "platform": "spotify"}, media_path, is_stream

    # Apple Music — search YT for video
    if platform == "apple_music":
        track = await get_apple_music_track(query)
        if not track:
            raise ValueError("Apple Music link parse করা যায়নি।")
        yt = await search_youtube(track["query"])
        if not yt:
            raise ValueError("YouTube-এ video পাওয়া যায়নি।")
        media_path, is_stream = await _get_video_media(yt["url"])
        if not media_path:
            raise ValueError("Video stream পাওয়া যায়নি।")
        return {**yt, "platform": "apple_music"}, media_path, is_stream

    # JioSaavn — video not available, search YT
    if platform == "jiosaavn":
        yt = await search_youtube(query)
        if not yt:
            raise ValueError("JioSaavn video সমর্থন করে না এবং YouTube-এও পাওয়া যায়নি।")
        media_path, is_stream = await _get_video_media(yt["url"])
        if not media_path:
            raise ValueError("Video stream পাওয়া যায়নি।")
        return yt, media_path, is_stream

    # Direct URL
    if platform == "direct_url":
        info = await get_video_info(query)
        if not info:
            info = {"title": "Direct Video", "url": query,
                    "duration": 0, "thumbnail": "", "channel": ""}
        media_path, is_stream = await _get_video_media(query)
        if not media_path:
            raise ValueError("URL থেকে video পাওয়া যায়নি।")
        return info, media_path, is_stream

    # Plain text query
    yt = await search_youtube(query)
    if not yt:
        raise ValueError("কোনো result পাওয়া যায়নি।")
    if yt["duration"] > Config.DURATION_LIMIT_MIN * 60 and yt["duration"] > 0:
        raise ValueError(
            f"ভিডিওটি {Config.DURATION_LIMIT_MIN} মিনিটের বেশি।"
        )
    media_path, is_stream = await _get_video_media(yt["url"])
    if not media_path:
        raise ValueError("Video stream পাওয়া যায়নি।")
    return yt, media_path, is_stream


@bot.on_message(filters.command(["vplay", "vp"]) & not_edited)
async def vplay_command(client: Client, message: Message):
    """Handle /vplay <query|url> — video streaming."""
    chat_id = message.chat.id
    user = message.from_user
    requester = user.mention if user else "Unknown"
    requester_id = user.id if user else 0

    query = ""
    if len(message.command) > 1:
        query = " ".join(message.command[1:])
    elif message.reply_to_message and message.reply_to_message.text:
        query = message.reply_to_message.text

    if not query:
        await message.reply_text(
            "**Usage:** `/vplay <song name or URL>`\n\n"
            "Video সহ voice chat-এ stream করবে।\n\n"
            "Example: `/vplay Arijit Singh live`"
        )
        return

    status_msg = await message.reply_text(
        f"🎬 **Video খুঁজছি:** `{query[:80]}`\n\nঅপেক্ষা করুন..."
    )

    platform = _detect_platform(query)

    try:
        info, media_path, is_stream = await _resolve_video(query, platform)
    except ValueError as exc:
        await status_msg.edit_text(f"❌ **Error:** {exc}")
        return
    except Exception as exc:
        LOG.exception("Unexpected error in /vplay for %s", chat_id)
        await status_msg.edit_text(
            f"❌ কিছু একটা সমস্যা হয়েছে। পরে আবার চেষ্টা করুন।\n"
            f"**Details:** `{type(exc).__name__}: {str(exc)[:200]}`"
        )
        return

    title = info.get("title", "Unknown")
    duration = info.get("duration", 0)
    thumbnail = info.get("thumbnail", "")
    url = info.get("url", query)
    channel = info.get("channel", "")

    item = QueueItem(
        title=title,
        url=url,
        media_path=media_path,
        duration=duration,
        requester=requester,
        requester_id=requester_id,
        thumbnail=thumbnail,
        stream_type="video",
        platform=platform if platform != "query" else "youtube",
        is_stream_url=is_stream,
    )

    position = await add_to_queue(chat_id, item)

    if position > 1 and is_active(chat_id):
        dur = format_duration(duration)
        await status_msg.edit_text(
            f"**🎬 Queue-তে যোগ হয়েছে #{position}** (Video)\n\n"
            f"**Title:** {title}\n"
            f"**Duration:** {dur}\n"
            f"**Requested by:** {requester}",
            reply_markup=_vcontrol_keyboard(),
        )
        return

    try:
        await stream_video(
            chat_id, media_path,
            title=title, duration=duration,
            thumbnail=thumbnail, requester=requester,
        )
    except FileNotFoundError:
        LOG.exception("Media not found for video stream in %s", chat_id)
        await status_msg.edit_text(
            "❌ ভিডিও ফাইল/URL পাওয়া যায়নি।\n"
            "আবার `/vplay` দিয়ে চেষ্টা করুন।"
        )
        return
    except RuntimeError as exc:
        await status_msg.edit_text(
            f"❌ {exc}\n\n"
            "STRING_SESSION সেট করা আছে কিনা চেক করুন।"
        )
        return
    except Exception as exc:
        LOG.exception("Video stream start failed in %s", chat_id)
        await status_msg.edit_text(
            "❌ Voice chat-এ video stream করা যাচ্ছে না।\n"
            "নিশ্চিত করুন voice chat চালু আছে এবং "
            "userbot-কে admin বানানো হয়েছে।\n\n"
            f"**Error:** `{type(exc).__name__}: {str(exc)[:150]}`"
        )
        return

    dur = format_duration(duration)
    text = (
        f"**🎬 Now Playing (Video)**\n\n"
        f"**🎵 Title:** [{title}]({url})\n"
        f"**⏱ Duration:** {dur}\n"
        f"**🎤 Channel:** {channel}\n"
        f"**👤 Requested by:** {requester}"
    )

    try:
        if thumbnail:
            await status_msg.delete()
            await bot.send_photo(
                chat_id,
                photo=thumbnail,
                caption=text,
                reply_markup=_vcontrol_keyboard(),
            )
        else:
            await status_msg.edit_text(text, reply_markup=_vcontrol_keyboard())
    except Exception:
        await status_msg.edit_text(text, reply_markup=_vcontrol_keyboard())
