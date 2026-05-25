"""Core streaming logic -- join/leave voice chats, stream audio/video.

Supports both local file paths and direct stream URLs (e.g. from YouTube).
Uses the py-tgcalls 2.x MediaStream API with proper flags (based on AnonXMusic).
"""

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
_HAS_MEDIA_STREAM = False
_HAS_FLAGS = False
_HAS_GROUP_CALL_CONFIG = False
MediaStream = None
AudioQuality = None
VideoQuality = None

try:
    from pytgcalls.types import MediaStream as _MS, AudioQuality as _AQ, VideoQuality as _VQ
    MediaStream = _MS
    AudioQuality = _AQ
    VideoQuality = _VQ
    _HAS_MEDIA_STREAM = True
except ImportError:
    LOG.warning("Could not import MediaStream/AudioQuality/VideoQuality from pytgcalls.types")

# Check for Flags support (py-tgcalls >= 2.1)
if _HAS_MEDIA_STREAM:
    try:
        _ = MediaStream.Flags.IGNORE
        _HAS_FLAGS = True
    except AttributeError:
        _HAS_FLAGS = False
        LOG.info("MediaStream.Flags not available (older py-tgcalls version)")

# Check for GroupCallConfig
try:
    from pytgcalls.types import GroupCallConfig
    _HAS_GROUP_CALL_CONFIG = True
except ImportError:
    GroupCallConfig = None
    _HAS_GROUP_CALL_CONFIG = False

try:
    from pytgcalls.types.stream import StreamAudioEnded
    _STREAM_END_TYPE = StreamAudioEnded
except ImportError:
    _STREAM_END_TYPE = None


def _is_url(path: str) -> bool:
    """Check if path is a URL (not a local file)."""
    return path.startswith("http://") or path.startswith("https://")


def _validate_media(media_path: str) -> None:
    """Validate media path -- file must exist or be a URL."""
    if not media_path:
        raise FileNotFoundError("No media path provided.")
    if _is_url(media_path):
        return  # URLs are always accepted
    if not os.path.isfile(media_path):
        raise FileNotFoundError(f"File not found: {media_path}")


def _make_audio_stream(media_path: str):
    """Create an audio-only MediaStream (file or URL).

    Uses video_flags=IGNORE for audio-only mode, matching
    the approach used by AnonXMusic.
    """
    if not _HAS_MEDIA_STREAM:
        raise RuntimeError("py-tgcalls MediaStream not available.")

    if _HAS_FLAGS:
        try:
            return MediaStream(
                media_path,
                audio_parameters=AudioQuality.HIGH,
                video_flags=MediaStream.Flags.IGNORE,
            )
        except (AttributeError, TypeError) as e:
            LOG.debug("MediaStream with Flags failed: %s", e)

    # Fallback: just audio parameters
    try:
        return MediaStream(
            media_path,
            audio_parameters=AudioQuality.HIGH,
        )
    except (AttributeError, TypeError) as e:
        LOG.debug("MediaStream with AudioQuality failed: %s", e)

    # Last resort
    return MediaStream(media_path)


def _make_video_stream(media_path: str):
    """Create a video+audio MediaStream (file or URL).

    Uses video_flags=AUTO_DETECT for video mode, matching
    the approach used by AnonXMusic.
    """
    if not _HAS_MEDIA_STREAM:
        raise RuntimeError("py-tgcalls MediaStream not available.")

    if _HAS_FLAGS:
        try:
            return MediaStream(
                media_path,
                audio_parameters=AudioQuality.HIGH,
                video_parameters=VideoQuality.HD_720p,
                video_flags=MediaStream.Flags.AUTO_DETECT,
            )
        except (AttributeError, TypeError) as e:
            LOG.debug("MediaStream video with Flags failed: %s", e)

    # Fallback
    try:
        return MediaStream(
            media_path,
            audio_parameters=AudioQuality.HIGH,
            video_parameters=VideoQuality.SD_480p,
        )
    except (AttributeError, TypeError) as e:
        LOG.debug("MediaStream video fallback failed: %s", e)

    return MediaStream(media_path)


async def _do_play(chat_id: int, stream):
    """Call pytgcalls.play with GroupCallConfig if available.

    Handles ProcessLookupError (ffprobe race condition on cloud servers)
    by retrying once after a short delay.
    """
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            if _HAS_GROUP_CALL_CONFIG:
                try:
                    await pytgcalls.play(
                        chat_id, stream,
                        config=GroupCallConfig(auto_start=True),
                    )
                    return
                except (TypeError, AttributeError) as e:
                    LOG.debug("play() with GroupCallConfig failed: %s", e)
            await pytgcalls.play(chat_id, stream)
            return
        except ProcessLookupError:
            if attempt < max_attempts:
                LOG.warning(
                    "ProcessLookupError in play() (attempt %d/%d) — "
                    "ffprobe subprocess race condition. Retrying after 2s...",
                    attempt, max_attempts,
                )
                await asyncio.sleep(2)
                continue
            LOG.error("ProcessLookupError persists after %d attempts", max_attempts)
            raise


# -- Public API ---

async def stream_audio(
    chat_id: int,
    media_path: str,
    title: str = "",
    duration: int = 0,
    thumbnail: str = "",
    requester: str = "",
) -> None:
    """Join voice chat (if needed) and start audio stream.

    media_path can be a local file path or a direct stream URL.
    If streaming a URL fails with ProcessLookupError (ffprobe race
    condition on cloud servers), automatically downloads the file
    and retries with the local path.
    """
    if pytgcalls is None:
        raise RuntimeError("Music streaming is disabled -- STRING_SESSION not configured.")
    _validate_media(media_path)
    try:
        audio = _make_audio_stream(media_path)
        await _do_play(chat_id, audio)
        _active_chats.add(chat_id)
        LOG.info("Streaming audio in %s: %s (%s)",
                 chat_id, title, media_path[:100])
    except ProcessLookupError:
        # ffprobe failed on stream URL — try downloading and playing local file
        if _is_url(media_path):
            LOG.warning(
                "ProcessLookupError with stream URL in %s — "
                "downloading file and retrying...", chat_id
            )
            try:
                from MusicLyrics.plugins.play.platforms.youtube import (
                    download_audio, _extract_video_id
                )
                local_path = await download_audio(media_path)
                if local_path and os.path.isfile(local_path):
                    audio = _make_audio_stream(local_path)
                    await _do_play(chat_id, audio)
                    _active_chats.add(chat_id)
                    LOG.info("Streaming audio (downloaded) in %s: %s (%s)",
                             chat_id, title, local_path[:100])
                    return
            except Exception as dl_exc:
                LOG.exception("Download fallback also failed in %s: %s",
                             chat_id, dl_exc)
        raise ProcessLookupError(
            "ffprobe subprocess failed. Ensure ffmpeg/ffprobe "
            "is installed and accessible."
        )
    except Exception as exc:
        LOG.exception("Failed to stream audio in %s: %s", chat_id, exc)
        raise


async def stream_video(
    chat_id: int,
    media_path: str,
    title: str = "",
    duration: int = 0,
    thumbnail: str = "",
    requester: str = "",
) -> None:
    """Join voice chat (if needed) and start video stream.

    media_path can be a local file path or a direct stream URL.
    If streaming a URL fails with ProcessLookupError (ffprobe race
    condition on cloud servers), automatically downloads the file
    and retries with the local path.
    """
    if pytgcalls is None:
        raise RuntimeError("Music streaming is disabled -- STRING_SESSION not configured.")
    _validate_media(media_path)
    try:
        stream = _make_video_stream(media_path)
        await _do_play(chat_id, stream)
        _active_chats.add(chat_id)
        LOG.info("Streaming video in %s: %s (%s)",
                 chat_id, title, media_path[:100])
    except ProcessLookupError:
        # ffprobe failed on stream URL — try downloading and playing local file
        if _is_url(media_path):
            LOG.warning(
                "ProcessLookupError with video stream URL in %s — "
                "downloading file and retrying...", chat_id
            )
            try:
                from MusicLyrics.plugins.play.platforms.youtube import (
                    download_video, _extract_video_id
                )
                local_path = await download_video(media_path)
                if local_path and os.path.isfile(local_path):
                    stream = _make_video_stream(local_path)
                    await _do_play(chat_id, stream)
                    _active_chats.add(chat_id)
                    LOG.info("Streaming video (downloaded) in %s: %s (%s)",
                             chat_id, title, local_path[:100])
                    return
            except Exception as dl_exc:
                LOG.exception("Video download fallback also failed in %s: %s",
                             chat_id, dl_exc)
        raise ProcessLookupError(
            "ffprobe subprocess failed. Ensure ffmpeg/ffprobe "
            "is installed and accessible."
        )
    except Exception as exc:
        LOG.exception("Failed to stream video in %s: %s", chat_id, exc)
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
    _validate_media(file_path)
    try:
        stream = _make_audio_stream(file_path)
        await _do_play(chat_id, stream)
        _active_chats.add(chat_id)
        LOG.info("Streaming audio+image in %s: %s", chat_id, title)
    except Exception as exc:
        LOG.exception("Failed to stream audio+image in %s: %s", chat_id, exc)
        raise


async def pause_stream(chat_id: int) -> bool:
    try:
        await pytgcalls.pause_stream(chat_id)
        return True
    except Exception:
        LOG.exception("Pause failed: %s", chat_id)
        return False


async def resume_stream(chat_id: int) -> bool:
    try:
        await pytgcalls.resume_stream(chat_id)
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
        await pytgcalls.change_volume(chat_id, volume)
        return True
    except Exception:
        LOG.exception("Volume change failed: %s", chat_id)
        return False


async def leave_voice_chat(chat_id: int) -> None:
    """Leave the voice chat and clean up."""
    try:
        await pytgcalls.leave_group_call(chat_id)
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
        chat_id = getattr(update, "chat", {})
        if isinstance(chat_id, dict):
            chat_id = chat_id.get("id")
        if chat_id is None:
            LOG.warning("Stream end event with unknown chat_id: %s", update)
            return

    # Clean up the finished track's file (if it was a local download)
    finished = await get_current(chat_id)
    if finished and not finished.is_stream_url and finished.media_path:
        cleanup(finished.media_path)

    next_item = await skip_queue(chat_id)
    if next_item is None:
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
        # Re-fetch stream URL if it was a stream URL (they expire)
        if next_item.is_stream_url:
            try:
                from MusicLyrics.plugins.play.platforms.youtube import (
                    get_audio_stream_url, get_video_stream_url, is_youtube_url
                )
                if is_youtube_url(next_item.url):
                    if next_item.stream_type == "video":
                        new_url = await get_video_stream_url(next_item.url)
                    else:
                        new_url = await get_audio_stream_url(next_item.url)
                    if new_url:
                        next_item.media_path = new_url
                        LOG.info("Re-fetched stream URL for queued track: %s", next_item.title)
            except Exception:
                LOG.warning("Failed to re-fetch stream URL for: %s", next_item.title)

        if next_item.stream_type == "video":
            await stream_video(
                chat_id, next_item.media_path,
                title=next_item.title, duration=next_item.duration,
            )
        else:
            await stream_audio(
                chat_id, next_item.media_path,
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
        LOG.warning("Could not register stream-end callback -- auto-skip will not work.")
