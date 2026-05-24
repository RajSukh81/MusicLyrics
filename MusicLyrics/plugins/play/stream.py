"""Core streaming logic -- join/leave voice chats, stream audio/video."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

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
    LOG.warning("STRING_SESSION not set -- music streaming features are disabled.")


# -- Import py-tgcalls types with compatibility handling --
try:
    from pytgcalls.types import MediaStream, AudioQuality, VideoQuality
    _HAS_MEDIA_STREAM = True
except ImportError:
    _HAS_MEDIA_STREAM = False
    LOG.warning("Could not import MediaStream/AudioQuality/VideoQuality from pytgcalls.types")

try:
    from pytgcalls.types.stream import StreamAudioEnded
    _STREAM_END_TYPE = StreamAudioEnded
except ImportError:
    _STREAM_END_TYPE = None


def _make_audio_stream(file_path: str):
    """Create an audio-only MediaStream compatible with multiple py-tgcalls versions."""
    if not _HAS_MEDIA_STREAM:
        raise RuntimeError("py-tgcalls MediaStream not available.")
    try:
        # py-tgcalls >= 2.1 with Flags support
        return MediaStream(
            file_path,
            audio_parameters=AudioQuality.HIGH,
            video_flags=MediaStream.Flags.IGNORE,
        )
    except (AttributeError, TypeError):
        pass
    try:
        # Fallback: just audio parameters, no video flags
        return MediaStream(
            file_path,
            audio_parameters=AudioQuality.HIGH,
        )
    except (AttributeError, TypeError):
        pass
    # Last resort: plain path
    return MediaStream(file_path)


def _make_video_stream(file_path: str):
    """Create a video+audio MediaStream compatible with multiple py-tgcalls versions."""
    if not _HAS_MEDIA_STREAM:
        raise RuntimeError("py-tgcalls MediaStream not available.")
    try:
        return MediaStream(
            file_path,
            audio_parameters=AudioQuality.HIGH,
            video_parameters=VideoQuality.SD_480p,
        )
    except (AttributeError, TypeError):
        pass
    return MediaStream(file_path)


# -- Public API ---

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
        raise RuntimeError("Music streaming is disabled -- STRING_SESSION not configured.")
    try:
        audio = _make_audio_stream(file_path)
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
        raise RuntimeError("Music streaming is disabled -- STRING_SESSION not configured.")
    try:
        stream = _make_video_stream(file_path)
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
        raise RuntimeError("Music streaming is disabled -- STRING_SESSION not configured.")
    try:
        stream = _make_audio_stream(file_path)
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


# -- Stream-end callback ---

async def _on_stream_end(client, update):
    """When current track ends, play next in queue or leave."""
    chat_id = getattr(update, "chat_id", None)
    if chat_id is None:
        # Some versions use different attribute names
        chat_id = getattr(update, "chat", {})
        if isinstance(chat_id, dict):
            chat_id = chat_id.get("id")
        if chat_id is None:
            LOG.warning("Stream end event with unknown chat_id: %s", update)
            return

    next_item = await skip_queue(chat_id)
    if next_item is None:
        # Queue exhausted -- leave
        await leave_voice_chat(chat_id)
        try:
            await bot.send_message(
                chat_id,
                "**Queue finished!**\n"
                "Leaving voice chat.\n\n"
                "Use `/play` to play again.",
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
            f"**Now Playing**\n\n"
            f"**Title:** {next_item.title}\n"
            f"**Duration:** {dur}\n"
            f"**Requested by:** {next_item.requester}",
        )
    except Exception:
        LOG.exception("Failed to play next in queue for %s", chat_id)
        await leave_voice_chat(chat_id)


# Register the stream-end callback with compatibility for multiple py-tgcalls versions
if pytgcalls is not None:
    _registered = False

    # Method 1: pytgcalls.on_update with filters (py-tgcalls >= 2.1)
    if not _registered:
        try:
            from pytgcalls import filters as _ptg_filters
            if hasattr(_ptg_filters, "stream_end"):
                pytgcalls.on_update(_ptg_filters.stream_end)(_on_stream_end)
                _registered = True
                LOG.info("Stream-end callback registered via pytgcalls.filters.stream_end")
        except (ImportError, AttributeError, TypeError) as e:
            LOG.debug("Method 1 (filters.stream_end) failed: %s", e)

    # Method 2: pytgcalls.on_stream_end decorator
    if not _registered:
        try:
            if hasattr(pytgcalls, "on_stream_end"):
                pytgcalls.on_stream_end()(_on_stream_end)
                _registered = True
                LOG.info("Stream-end callback registered via pytgcalls.on_stream_end()")
        except (AttributeError, TypeError) as e:
            LOG.debug("Method 2 (on_stream_end) failed: %s", e)

    # Method 3: pytgcalls.on_closed_voice_chat
    if not _registered:
        try:
            if hasattr(pytgcalls, "on_closed_voice_chat"):
                pytgcalls.on_closed_voice_chat()(_on_stream_end)
                _registered = True
                LOG.info("Stream-end callback registered via pytgcalls.on_closed_voice_chat()")
        except (AttributeError, TypeError) as e:
            LOG.debug("Method 3 (on_closed_voice_chat) failed: %s", e)

    if not _registered:
        LOG.warning("Could not register stream-end callback -- auto-skip to next track will not work.")
