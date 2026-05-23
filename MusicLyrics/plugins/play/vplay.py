"""Handler for /vplay <query|url> — video playback in voice chat."""

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from MusicLyrics.bot import bot
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


async def _resolve_video(query: str, platform: str):
    """Resolve query into (info_dict, file_path) for video streaming."""

    # YouTube URL
    if platform == "youtube":
        info = await get_video_info(query)
        if not info:
            raise ValueError("YouTube link থেকে তথ্য পাওয়া যায়নি।")
        if info["duration"] > Config.DURATION_LIMIT_MIN * 60:
            raise ValueError(
                f"ভিডিওটি {Config.DURATION_LIMIT_MIN} মিনিটের বেশি।"
            )
        filepath = await download_video(query)
        if not filepath:
            raise ValueError("Video download ব্যর্থ হয়েছে।")
        return info, filepath

    # Spotify — search YT for video
    if platform == "spotify":
        track = await get_spotify_track(query)
        if not track:
            raise ValueError("Spotify link parse করা যায়নি।")
        yt = await search_youtube(track["query"])
        if not yt:
            raise ValueError("YouTube-এ video পাওয়া যায়নি।")
        filepath = await download_video(yt["url"])
        if not filepath:
            raise ValueError("Download ব্যর্থ হয়েছে।")
        return {**yt, "platform": "spotify"}, filepath

    # Apple Music — search YT for video
    if platform == "apple_music":
        track = await get_apple_music_track(query)
        if not track:
            raise ValueError("Apple Music link parse করা যায়নি।")
        yt = await search_youtube(track["query"])
        if not yt:
            raise ValueError("YouTube-এ video পাওয়া যায়নি।")
        filepath = await download_video(yt["url"])
        if not filepath:
            raise ValueError("Download ব্যর্থ হয়েছে।")
        return {**yt, "platform": "apple_music"}, filepath

    # JioSaavn — video not available, search YT
    if platform == "jiosaavn":
        yt = await search_youtube(query)
        if not yt:
            raise ValueError("JioSaavn video সমর্থন করে না এবং YouTube-এও পাওয়া যায়নি।")
        filepath = await download_video(yt["url"])
        if not filepath:
            raise ValueError("Download ব্যর্থ হয়েছে।")
        return yt, filepath

    # Direct URL
    if platform == "direct_url":
        info = await get_video_info(query)
        if not info:
            info = {"title": "Direct Video", "url": query,
                    "duration": 0, "thumbnail": "", "channel": ""}
        filepath = await download_video(query)
        if not filepath:
            raise ValueError("URL থেকে video download করা যায়নি।")
        return info, filepath

    # Plain text query
    yt = await search_youtube(query)
    if not yt:
        raise ValueError("কোনো result পাওয়া যায়নি।")
    if yt["duration"] > Config.DURATION_LIMIT_MIN * 60:
        raise ValueError(
            f"ভিডিওটি {Config.DURATION_LIMIT_MIN} মিনিটের বেশি।"
        )
    filepath = await download_video(yt["url"])
    if not filepath:
        raise ValueError("Video download ব্যর্থ হয়েছে।")
    return yt, filepath


@bot.on_message(filters.command(["vplay", "vp"]) & ~filters.edited)
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
        info, filepath = await _resolve_video(query, platform)
    except ValueError as exc:
        await status_msg.edit_text(f"❌ **Error:** {exc}")
        return
    except Exception:
        LOG.exception("Unexpected error in /vplay for %s", chat_id)
        await status_msg.edit_text(
            "❌ কিছু একটা সমস্যা হয়েছে। পরে আবার চেষ্টা করুন।"
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
        file_path=filepath,
        duration=duration,
        requester=requester,
        requester_id=requester_id,
        thumbnail=thumbnail,
        stream_type="video",
        platform=platform if platform != "query" else "youtube",
    )

    cq = await get_chat_queue(chat_id)
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
            chat_id, filepath,
            title=title, duration=duration,
            thumbnail=thumbnail, requester=requester,
        )
    except Exception:
        LOG.exception("Video stream start failed in %s", chat_id)
        await status_msg.edit_text(
            "❌ Voice chat-এ video stream করা যাচ্ছে না।\n"
            "নিশ্চিত করুন voice chat চালু আছে এবং "
            "userbot-কে admin বানানো হয়েছে।"
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
