"""Core streaming logic — join/leave voice chats, stream audio/video."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from pytgcalls.types import MediaStream, AudioQuality, VideoQuality

from MusicLyrics.bot import bot
from MusicLyrics.userbot import pytgcalls, userbot

from MusicLyrics.plugins.play.queue import (
    get_current,
    skip_queue,
    clear_queue,
    format_duration,
)
from MusicLyrics.utils.downloader import cleanup

LOG = logging.getLogger(__name__)

# Track active chats so we know whether to join or change stream
_active_chats: set[int] = set()

# Guard: if pytgcalls is None (no STRING_SESSION), music features are disabled
if pytgcalls is None:
    LOG.warning("STRING_SESSION not set — music streaming features are disabled.")


# ── Public API ───────────────────────────────────────────────────────────────

async def stream_audio(
    chat_id: int,
    file_path: str,
    title: str = "",
    duration: int = 0,
    thumbnail: str = "",
    requester: str = "",
) -> None:
    """Join voice chat (if needed) and start audio stream."""
    if pytgcalls is None:
        raise RuntimeError("Music streaming is disabled — STRING_SESSION not configured.")
    try:
        audio = MediaStream(
            file_path,
            audio_parameters=AudioQuality.HIGH,
            video_flags=MediaStream.Flags.IGNORE,
        )

        await pytgcalls.play(chat_id, audio)
        _active_chats.add(chat_id)

        LOG.info("Streaming audio in %s: %s", chat_id, title)
    except Exception:
        LOG.exception("Failed to stream audio in %s", chat_id)
        raise


async def stream_video(
    chat_id: int,
    file_path: str,
    title: str = "",
    duration: int = 0,
    thumbnail: str = "",
    requester: str = "",
) -> None:
    """Join voice chat (if needed) and start video stream."""
    if pytgcalls is None:
        raise RuntimeError("Music streaming is disabled — STRING_SESSION not configured.")
    try:
        stream = MediaStream(
            file_path,
            audio_parameters=AudioQuality.HIGH,
            video_parameters=VideoQuality.SD_480p,
        )

        await pytgcalls.play(chat_id, stream)
        _active_chats.add(chat_id)

        LOG.info("Streaming video in %s: %s", chat_id, title)
    except Exception:
        LOG.exception("Failed to stream video in %s", chat_id)
        raise


async def stream_audio_with_image(
    chat_id: int,
    file_path: str,
    image_path: str,
    title: str = "",
) -> None:
    """Stream audio with a static thumbnail image in video chat."""
    if pytgcalls is None:
        raise RuntimeError("Music streaming is disabled — STRING_SESSION not configured.")
    try:
        stream = MediaStream(
            file_path,
            audio_parameters=AudioQuality.HIGH,
            video_flags=MediaStream.Flags.IGNORE,
        )

        await pytgcalls.play(chat_id, stream)
        _active_chats.add(chat_id)

        LOG.info("Streaming audio+image in %s: %s", chat_id, title)
    except Exception:
        LOG.exception("Failed to stream audio+image in %s", chat_id)
        raise


async def pause_stream(chat_id: int) -> bool:
    try:
        await pytgcalls.pause(chat_id)
        return True
    except Exception:
        LOG.exception("Pause failed: %s", chat_id)
        return False


async def resume_stream(chat_id: int) -> bool:
    try:
        await pytgcalls.resume(chat_id)
        return True
    except Exception:
        LOG.exception("Resume failed: %s", chat_id)
        return False


async def seek_stream(chat_id: int, seconds: int) -> bool:
    """Seek is not natively supported by all py-tgcalls versions."""
    try:
        LOG.warning("Seek requested but not natively supported in this version.")
        return False
    except Exception:
        LOG.exception("Seek failed: %s", chat_id)
        return False


async def set_volume(chat_id: int, volume: int) -> bool:
    """Set playback volume (1-200)."""
    volume = max(1, min(200, volume))
    try:
        await pytgcalls.change_volume_call(chat_id, volume)
        return True
    except Exception:
        LOG.exception("Volume change failed: %s", chat_id)
        return False


async def leave_voice_chat(chat_id: int) -> None:
    """Leave the voice chat and clean up."""
    try:
        await pytgcalls.leave_call(chat_id)
    except Exception:
        LOG.exception("Leave VC failed: %s", chat_id)
    _active_chats.discard(chat_id)
    await clear_queue(chat_id)


def is_active(chat_id: int) -> bool:
    return chat_id in _active_chats


# ── Stream-end callback ─────────────────────────────────────────────────────

async def _on_stream_end(client, update):
    """When current track ends, play next in queue or leave."""
    chat_id = update.chat_id

    next_item = await skip_queue(chat_id)
    if next_item is None:
        # Queue exhausted — leave
        await leave_voice_chat(chat_id)
        try:
            await bot.send_message(
                chat_id,
                "**Queue শেষ হয়ে গেছে!** 🎵\n"
                "Voice chat থেকে বের হয়ে যাচ্ছি।\n\n"
                "আবার গান শুনতে `/play` command দিন।",
            )
        except Exception:
            pass
        return

    # Play next track
    try:
        if next_item.stream_type == "video":
            await stream_video(
                chat_id, next_item.file_path,
                title=next_item.title, duration=next_item.duration,
            )
        else:
            await stream_audio(
                chat_id, next_item.file_path,
                title=next_item.title, duration=next_item.duration,
            )

        dur = format_duration(next_item.duration)
        await bot.send_message(
            chat_id,
            f"**▶️ Now Playing**\n\n"
            f"**🎵 Title:** {next_item.title}\n"
            f"**⏱ Duration:** {dur}\n"
            f"**👤 Requested by:** {next_item.requester}",
        )
    except Exception:
        LOG.exception("Failed to play next in queue for %s", chat_id)
        await leave_voice_chat(chat_id)


# Register the callback only if pytgcalls is available
if pytgcalls is not None:
    from pytgcalls import filters as _ptg_filters
    pytgcalls.on_update(_ptg_filters.stream_end)(_on_stream_end)
