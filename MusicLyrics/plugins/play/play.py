"""Handler for /play <query|url> — audio playback in voice chat."""

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
    get_current,
    get_chat_queue,
    format_duration,
)
from MusicLyrics.plugins.play.stream import stream_audio, is_active
from MusicLyrics.plugins.play.platforms.youtube import (
    search_youtube,
    download_audio,
    get_video_info,
    is_youtube_url,
)
from MusicLyrics.plugins.play.platforms.spotify import (
    is_spotify_url,
    get_spotify_track,
    get_spotify_playlist,
)
from MusicLyrics.plugins.play.platforms.jiosaavn import (
    is_jiosaavn_url,
    get_jiosaavn_song,
    download_jiosaavn,
)
from MusicLyrics.plugins.play.platforms.apple_music import (
    is_apple_music_url,
    get_apple_music_track,
)

LOG = logging.getLogger(__name__)


def _control_keyboard() -> InlineKeyboardMarkup:
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


def _detect_platform(text: str) -> str:
    """Return platform name from a URL or 'query' for plain text."""
    if is_youtube_url(text):
        return "youtube"
    if is_spotify_url(text):
        return "spotify"
    if is_jiosaavn_url(text):
        return "jiosaavn"
    if is_apple_music_url(text):
        return "apple_music"
    if re.match(r"https?://", text):
        return "direct_url"
    return "query"


async def _resolve_query(query: str, platform: str, msg: Message):
    """Resolve the user query into (info_dict, file_path) or raise."""

    # -- YouTube URL
    if platform == "youtube":
        info = await get_video_info(query)
        if not info:
            raise ValueError("YouTube link থেকে তথ্য পাওয়া যায়নি।")
        if info["duration"] > Config.DURATION_LIMIT_MIN * 60:
            raise ValueError(
                f"গানটি {Config.DURATION_LIMIT_MIN} মিনিটের বেশি, "
                "play করা যাবে না।"
            )
        filepath = await download_audio(query)
        if not filepath:
            raise ValueError("Download ব্যর্থ হয়েছে। আবার চেষ্টা করুন।")
        return info, filepath

    # -- Spotify
    if platform == "spotify":
        track = await get_spotify_track(query)
        if not track:
            raise ValueError("Spotify link parse করা যায়নি।")
        yt = await search_youtube(track["query"])
        if not yt:
            raise ValueError("YouTube-এ গানটি পাওয়া যায়নি।")
        filepath = await download_audio(yt["url"])
        if not filepath:
            raise ValueError("Download ব্যর্থ হয়েছে।")
        info = {**yt, "platform": "spotify"}
        return info, filepath

    # -- JioSaavn
    if platform == "jiosaavn":
        song = await get_jiosaavn_song(query)
        if not song:
            raise ValueError("JioSaavn link থেকে তথ্য পাওয়া যায়নি।")
        filepath = await download_jiosaavn(query)
        if not filepath:
            yt = await search_youtube(f"{song['title']} {song.get('artist','')}")
            if not yt:
                raise ValueError("গানটি download করা যায়নি।")
            filepath = await download_audio(yt["url"])
            if not filepath:
                raise ValueError("Download ব্যর্থ হয়েছে।")
        info = {
            "title": song["title"],
            "url": song["url"],
            "duration": song["duration"],
            "thumbnail": song.get("thumbnail", ""),
            "channel": song.get("artist", ""),
            "platform": "jiosaavn",
        }
        return info, filepath

    # -- Apple Music
    if platform == "apple_music":
        track = await get_apple_music_track(query)
        if not track:
            raise ValueError("Apple Music link parse করা যায়নি।")
        yt = await search_youtube(track["query"])
        if not yt:
            raise ValueError("YouTube-এ গানটি পাওয়া যায়নি।")
        filepath = await download_audio(yt["url"])
        if not filepath:
            raise ValueError("Download ব্যর্থ হয়েছে।")
        info = {**yt, "platform": "apple_music"}
        return info, filepath

    # -- Direct URL (try yt-dlp)
    if platform == "direct_url":
        info = await get_video_info(query)
        if not info:
            info = {"title": "Direct Stream", "url": query,
                    "duration": 0, "thumbnail": "", "channel": ""}
        filepath = await download_audio(query)
        if not filepath:
            raise ValueError("URL থেকে download করা যায়নি।")
        return info, filepath

    # -- Plain text query: YouTube search
    yt = await search_youtube(query)
    if not yt:
        raise ValueError("কোনো result পাওয়া যায়নি। অন্য keyword দিয়ে চেষ্টা করুন।")
    if yt["duration"] > Config.DURATION_LIMIT_MIN * 60:
        raise ValueError(
            f"গানটি {Config.DURATION_LIMIT_MIN} মিনিটের বেশি, play করা যাবে না।"
        )
    filepath = await download_audio(yt["url"])
    if not filepath:
        raise ValueError("Download ব্যর্থ হয়েছে। আবার চেষ্টা করুন।")
    return yt, filepath


@bot.on_message(filters.command(["play", "p"]) & not_edited)
async def play_command(client: Client, message: Message):
    """Handle /play <query|url>."""
    chat_id = message.chat.id
    user = message.from_user
    requester = user.mention if user else "Unknown"
    requester_id = user.id if user else 0

    # Extract query
    query = ""
    if len(message.command) > 1:
        query = " ".join(message.command[1:])
    elif message.reply_to_message and message.reply_to_message.text:
        query = message.reply_to_message.text
    elif message.reply_to_message and message.reply_to_message.audio:
        pass  # audio file reply not handled here

    if not query:
        await message.reply_text(
            "**Usage:** `/play <song name or URL>`\n\n"
            "Example:\n"
            "`/play Arijit Singh Tum Hi Ho`\n"
            "`/play https://youtu.be/...`"
        )
        return

    status_msg = await message.reply_text(
        f"🔍 **খুঁজছি:** `{query[:80]}`\n\nঅপেক্ষা করুন..."
    )

    platform = _detect_platform(query)

    try:
        info, filepath = await _resolve_query(query, platform, message)
    except ValueError as exc:
        await status_msg.edit_text(f"❌ **Error:** {exc}")
        return
    except Exception:
        LOG.exception("Unexpected error in /play for %s", chat_id)
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
        stream_type="audio",
        platform=platform if platform != "query" else "youtube",
    )

    cq = await get_chat_queue(chat_id)
    position = await add_to_queue(chat_id, item)

    # If something is already playing, just queue it
    if position > 1 and is_active(chat_id):
        dur = format_duration(duration)
        await status_msg.edit_text(
            f"**🎵 Queue-তে যোগ হয়েছে #{position}**\n\n"
            f"**Title:** {title}\n"
            f"**Duration:** {dur}\n"
            f"**Requested by:** {requester}",
            reply_markup=_control_keyboard(),
        )
        return

    # Start streaming
    try:
        await stream_audio(
            chat_id, filepath,
            title=title, duration=duration,
            thumbnail=thumbnail, requester=requester,
        )
    except Exception:
        LOG.exception("Stream start failed in %s", chat_id)
        await status_msg.edit_text(
            "❌ Voice chat-এ connect করা যাচ্ছে না।\n"
            "নিশ্চিত করুন voice chat চালু আছে এবং "
            "userbot-কে admin বানানো হয়েছে।"
        )
        return

    dur = format_duration(duration)
    text = (
        f"**▶️ Now Playing**\n\n"
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
                reply_markup=_control_keyboard(),
            )
        else:
            await status_msg.edit_text(text, reply_markup=_control_keyboard())
    except Exception:
        await status_msg.edit_text(text, reply_markup=_control_keyboard())
