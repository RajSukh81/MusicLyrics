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
from MusicLyrics.utils.autodelete import auto_delete_service, auto_delete_playing

# SoundCloud & JioSaavn fallbacks — ultimate last resort when all other methods fail
from MusicLyrics.plugins.play.platforms.soundcloud import (
    search_and_download_soundcloud,
    get_soundcloud_stream_url,
    is_soundcloud_url,
)
from MusicLyrics.plugins.play.platforms.jiosaavn import (
    search_and_download_jiosaavn,
)

LOG = logging.getLogger(__name__)

# Track active chats so we know whether to join or change stream
_active_chats: set[int] = set()

# Track "Now Playing" messages for each chat so we can delete them when track ends
_now_playing_messages: dict[int, list] = {}

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


async def _check_stream_url(url: str) -> bool:
    """Quick HEAD/GET check to see if a stream URL is still valid.

    Returns True if URL is reachable (2xx/3xx), False otherwise.
    This prevents passing expired/dead URLs to py-tgcalls/ffprobe
    which causes JSONDecodeError crashes.
    
    NOTE: This check is intentionally lenient — we prefer false positives
    (passing a maybe-dead URL to py-tgcalls) over false negatives
    (rejecting a working URL because of a transient network issue).
    """
    if not _is_url(url):
        return True  # Not a URL, skip check
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # Try HEAD first (faster), then GET if HEAD fails
            for method in [session.head, session.get]:
                try:
                    async with method(
                        url,
                        timeout=aiohttp.ClientTimeout(total=5, connect=3),
                        allow_redirects=True,
                    ) as resp:
                        if resp.status < 400:
                            return True
                        # Only reject URLs that definitively don't exist
                        # 404 = not found, 410 = gone permanently
                        if resp.status in (404, 410):
                            LOG.warning("Stream URL not found (HTTP %d): %s", resp.status, url[:80])
                            return False
                        # For 403/451/etc — the URL might still work with py-tgcalls
                        # because it uses different HTTP headers/cookies.
                        # Don't reject it here.
                        LOG.debug("Stream URL returned HTTP %d (not rejecting): %s", resp.status, url[:80])
                        return True
                except asyncio.TimeoutError:
                    LOG.debug("Stream URL check timed out (not rejecting): %s", url[:80])
                    return True  # Timeout doesn't mean URL is dead
                except Exception:
                    continue
    except Exception:
        pass
    # If we can't check at all, assume the URL is OK — let py-tgcalls try it
    return True


def _validate_media(media_path: str) -> None:
    """Validate media path -- file must exist or be a URL."""
    if not media_path:
        raise FileNotFoundError("No media path provided.")
    if _is_url(media_path):
        # Basic URL sanity checks
        if len(media_path) < 10:
            raise FileNotFoundError(f"Invalid media URL: {media_path}")
        return  # URLs are accepted
    if not os.path.isfile(media_path):
        raise FileNotFoundError(f"File not found: {media_path}")
    # Reject empty files (corrupted downloads)
    if os.path.getsize(media_path) < 1000:
        raise FileNotFoundError(f"File too small (likely corrupted): {media_path}")


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
    """Call pytgcalls.play with GroupCallConfig if available."""
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
    If streaming a URL fails, automatically downloads the file
    and retries with the local path.
    """
    if pytgcalls is None:
        raise RuntimeError("Music streaming is disabled -- STRING_SESSION not configured.")
    _validate_media(media_path)

    # Pre-check stream URL validity to prevent ffprobe/JSONDecodeError crashes
    if _is_url(media_path):
        url_ok = await _check_stream_url(media_path)
        if not url_ok:
            LOG.warning("Stream URL pre-check failed in %s — going directly to fallback", chat_id)
            # Directly attempt download instead of passing dead URL to py-tgcalls
            try:
                from MusicLyrics.plugins.play.platforms.youtube import download_audio, search_and_download_audio
                local_path = None
                if title:
                    local_path, _ = await search_and_download_audio(title)
                if local_path and os.path.isfile(str(local_path)):
                    audio = _make_audio_stream(local_path)
                    await _do_play(chat_id, audio)
                    _active_chats.add(chat_id)
                    LOG.info("Streaming audio (URL pre-check recovery) in %s: %s", chat_id, title)
                    return
            except Exception:
                LOG.debug("URL pre-check recovery download failed for %s", chat_id)
            # Try JioSaavn
            if title:
                try:
                    js_path, js_info = await search_and_download_jiosaavn(title)
                    if js_path and os.path.isfile(str(js_path)):
                        audio = _make_audio_stream(js_path)
                        await _do_play(chat_id, audio)
                        _active_chats.add(chat_id)
                        LOG.info("Streaming audio (JioSaavn pre-check recovery) in %s: %s", chat_id, title)
                        return
                except Exception:
                    pass
            # Try SoundCloud
            if title:
                try:
                    sc_path, sc_info = await search_and_download_soundcloud(title)
                    if sc_path and (os.path.isfile(str(sc_path)) or (sc_info and sc_info.get("_is_stream_url"))):
                        audio = _make_audio_stream(sc_path)
                        await _do_play(chat_id, audio)
                        _active_chats.add(chat_id)
                        LOG.info("Streaming audio (SoundCloud pre-check recovery) in %s: %s", chat_id, title)
                        return
                except Exception:
                    pass
            raise FileNotFoundError(f"Stream URL expired and all fallbacks failed for: {title or media_path[:80]}")

    try:
        audio = _make_audio_stream(media_path)
        await _do_play(chat_id, audio)
        _active_chats.add(chat_id)
        LOG.info("Streaming audio in %s: %s (%s)",
                 chat_id, title, media_path[:100])
    except Exception as exc:
        # If stream URL failed, try downloading and playing local file
        if _is_url(media_path):
            LOG.warning(
                "%s with stream URL in %s — downloading file and retrying...",
                type(exc).__name__, chat_id,
            )
            try:
                from MusicLyrics.plugins.play.platforms.youtube import download_audio, search_and_download_audio
                local_path = await download_audio(media_path)
                if not local_path or not os.path.isfile(str(local_path)):
                    # download_audio with URL failed, try search+download with title
                    if title:
                        LOG.info("URL download failed, trying search+download for: %s", title)
                        local_path, _ = await search_and_download_audio(title)
                if local_path and os.path.isfile(str(local_path)):
                    audio = _make_audio_stream(local_path)
                    await _do_play(chat_id, audio)
                    _active_chats.add(chat_id)
                    LOG.info("Streaming audio (downloaded) in %s: %s (%s)",
                             chat_id, title, str(local_path)[:100])
                    return
            except Exception as dl_exc:
                LOG.exception("Download fallback also failed in %s: %s",
                             chat_id, dl_exc)

            # Try JioSaavn before SoundCloud
            if title:
                try:
                    LOG.info("YouTube download failed, trying JioSaavn for: %s", title)
                    js_path, js_info = await search_and_download_jiosaavn(title)
                    if js_path and os.path.isfile(str(js_path)):
                        audio = _make_audio_stream(js_path)
                        await _do_play(chat_id, audio)
                        _active_chats.add(chat_id)
                        LOG.info("Streaming audio (JioSaavn fallback) in %s: %s", chat_id, title)
                        return
                except Exception:
                    LOG.debug("JioSaavn fallback failed in %s", chat_id)

            # LAST RESORT: SoundCloud fallback when all YouTube methods fail
            if title:
                try:
                    LOG.info("All YouTube methods failed, trying SoundCloud fallback for: %s", title)
                    sc_path, sc_info = await search_and_download_soundcloud(title)
                    if sc_path:
                        if sc_info and sc_info.get("_is_stream_url"):
                            audio = _make_audio_stream(sc_path)
                        elif os.path.isfile(str(sc_path)):
                            audio = _make_audio_stream(sc_path)
                        else:
                            audio = None
                        if audio:
                            await _do_play(chat_id, audio)
                            _active_chats.add(chat_id)
                            LOG.info("Streaming audio (SoundCloud fallback) in %s: %s (%s)",
                                     chat_id, title, str(sc_path)[:100])
                            return
                except Exception as sc_exc:
                    LOG.exception("SoundCloud fallback also failed in %s: %s",
                                 chat_id, sc_exc)

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
    If streaming a URL fails, automatically downloads the file
    and retries with the local path.
    """
    if pytgcalls is None:
        raise RuntimeError("Music streaming is disabled -- STRING_SESSION not configured.")
    _validate_media(media_path)

    # Pre-check stream URL validity to prevent ffprobe/JSONDecodeError crashes
    if _is_url(media_path):
        url_ok = await _check_stream_url(media_path)
        if not url_ok:
            LOG.warning("Video stream URL pre-check failed in %s — going directly to fallback", chat_id)
            try:
                from MusicLyrics.plugins.play.platforms.youtube import download_video, search_and_download_video
                local_path = None
                if title:
                    local_path, _ = await search_and_download_video(title)
                if local_path and os.path.isfile(str(local_path)):
                    stream = _make_video_stream(local_path)
                    await _do_play(chat_id, stream)
                    _active_chats.add(chat_id)
                    LOG.info("Streaming video (URL pre-check recovery) in %s: %s", chat_id, title)
                    return
            except Exception:
                LOG.debug("Video URL pre-check recovery download failed for %s", chat_id)
            # Try SoundCloud
            if title:
                try:
                    sc_path, sc_info = await search_and_download_soundcloud(title)
                    if sc_path and (os.path.isfile(str(sc_path)) or (sc_info and sc_info.get("_is_stream_url"))):
                        stream = _make_audio_stream(sc_path)
                        await _do_play(chat_id, stream)
                        _active_chats.add(chat_id)
                        LOG.info("Streaming audio via SoundCloud (video pre-check recovery) in %s: %s", chat_id, title)
                        return
                except Exception:
                    pass
            raise FileNotFoundError(f"Video stream URL expired and all fallbacks failed for: {title or media_path[:80]}")

    try:
        stream = _make_video_stream(media_path)
        await _do_play(chat_id, stream)
        _active_chats.add(chat_id)
        LOG.info("Streaming video in %s: %s (%s)",
                 chat_id, title, media_path[:100])
    except Exception as exc:
        # If stream URL failed, try downloading and playing local file
        if _is_url(media_path):
            LOG.warning(
                "%s with video stream URL in %s — downloading file and retrying...",
                type(exc).__name__, chat_id,
            )
            try:
                from MusicLyrics.plugins.play.platforms.youtube import download_video, search_and_download_video
                local_path = await download_video(media_path)
                if not local_path or not os.path.isfile(str(local_path)):
                    # URL download failed, try search+download with title
                    if title:
                        LOG.info("Video URL download failed, trying search+download for: %s", title)
                        local_path, _ = await search_and_download_video(title)
                if local_path and os.path.isfile(str(local_path)):
                    stream = _make_video_stream(local_path)
                    await _do_play(chat_id, stream)
                    _active_chats.add(chat_id)
                    LOG.info("Streaming video (downloaded) in %s: %s (%s)",
                             chat_id, title, str(local_path)[:100])
                    return
            except Exception as dl_exc:
                LOG.exception("Video download fallback also failed in %s: %s",
                             chat_id, dl_exc)

            # LAST RESORT: SoundCloud fallback for video too (plays audio)
            if title:
                try:
                    LOG.info("All video methods failed, trying SoundCloud fallback for: %s", title)
                    sc_path, sc_info = await search_and_download_soundcloud(title)
                    if sc_path:
                        if sc_info and sc_info.get("_is_stream_url"):
                            stream = _make_audio_stream(sc_path)
                        elif os.path.isfile(str(sc_path)):
                            stream = _make_audio_stream(sc_path)
                        else:
                            stream = None
                        if stream:
                            await _do_play(chat_id, stream)
                            _active_chats.add(chat_id)
                            LOG.info("Streaming audio via SoundCloud (video fallback) in %s: %s (%s)",
                                     chat_id, title, str(sc_path)[:100])
                            return
                except Exception as sc_exc:
                    LOG.exception("SoundCloud video fallback also failed in %s: %s",
                                 chat_id, sc_exc)

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
        LOG.info("Left voice chat: %s", chat_id)
    except Exception:
        LOG.exception("Leave VC failed: %s", chat_id)
    _active_chats.discard(chat_id)
    # Clear now playing messages tracking
    if chat_id in _now_playing_messages:
        del _now_playing_messages[chat_id]
    await clear_queue(chat_id)


def is_active(chat_id: int) -> bool:
    return chat_id in _active_chats


# -- Stream-end callback ---

async def _on_stream_end(client, update):
    """When current track ends, play next in queue or leave."""
    chat_id = None

    # Try various ways to get chat_id from the update object
    if hasattr(update, "chat_id"):
        chat_id = update.chat_id
    elif hasattr(update, "chat"):
        chat_obj = update.chat
        if isinstance(chat_obj, dict):
            chat_id = chat_obj.get("id")
        elif isinstance(chat_obj, int):
            chat_id = chat_obj
        elif hasattr(chat_obj, "id"):
            chat_id = chat_obj.id
    elif isinstance(update, int):
        chat_id = update
    elif isinstance(update, dict):
        chat_id = update.get("chat_id") or update.get("chat", {}).get("id")

    if chat_id is None:
        LOG.warning("Stream end event with unknown chat_id: %s (type: %s)", update, type(update).__name__)
        return

    LOG.info("Stream end event for chat %s", chat_id)

    # Get the finished track info BEFORE cleaning up
    finished = await get_current(chat_id)
    finished_title = finished.title if finished else "Unknown"
    finished_requester = finished.requester if finished else ""

    # Clean up the finished track's file (if it was a local download)
    if finished and not finished.is_stream_url and finished.media_path:
        cleanup(finished.media_path)

    # Delete previous "Now Playing" / thumbnail messages for this chat
    if chat_id in _now_playing_messages:
        for old_msg in _now_playing_messages[chat_id]:
            try:
                await old_msg.delete()
                LOG.debug("Deleted previous Now Playing message in %s", chat_id)
            except Exception:
                pass
        _now_playing_messages[chat_id].clear()

    next_item = await skip_queue(chat_id)
    if next_item is None:
        # Queue is empty — send a nice "song finished" message, then leave VC
        try:
            finish_msg = await bot.send_message(
                chat_id,
                f"✅ **গান শেষ হয়ে গেছে!**\n\n"
                f"🎵 **শেষ গান:** {finished_title}\n"
                f"👤 **শুনিয়েছিলেন:** {finished_requester}\n\n"
                f"🔄 আবার গান শুনতে `/play` কমান্ড দিন।\n"
                f"📜 গানের তালিকা দেখতে `/queue` দিন।",
            )
            await auto_delete_service(finish_msg)
        except Exception:
            pass
        # Now leave the voice chat
        await leave_voice_chat(chat_id)
        LOG.info("Queue empty, left voice chat in %s", chat_id)
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
                elif is_soundcloud_url(next_item.url):
                    new_url = await get_soundcloud_stream_url(next_item.url)
                    if new_url:
                        next_item.media_path = new_url
                        LOG.info("Re-fetched SoundCloud stream URL for: %s", next_item.title)
            except Exception:
                LOG.warning("Failed to re-fetch stream URL for: %s — will try playing with existing URL", next_item.title)

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
        np_msg = await bot.send_message(
            chat_id,
            f"▶️ **এখন চলছে**\n\n"
            f"🎵 **Title:** {next_item.title}\n"
            f"⏱ **Duration:** {dur}\n"
            f"👤 **Requested by:** {next_item.requester}",
        )
        # Track this message so we can delete it when this track ends
        if chat_id not in _now_playing_messages:
            _now_playing_messages[chat_id] = []
        _now_playing_messages[chat_id].append(np_msg)
        # Don't auto-delete — we'll manually delete when track ends
    except Exception:
        LOG.exception("Failed to play next in queue for %s", chat_id)
        # Send error message before leaving
        try:
            err_msg = await bot.send_message(
                chat_id,
                f"❌ **পরের গানটি চলানো যায়নি:** {next_item.title}\n\n"
                "Voice chat থেকে বের হচ্ছে। আবার `/play` দিন।",
            )
            await auto_delete_service(err_msg)
        except Exception:
            pass
        await leave_voice_chat(chat_id)


# Register the stream-end callback with compatibility for multiple py-tgcalls versions
if pytgcalls is not None:
    _registered = False

    # Method 1: pytgcalls.on_update with filters (py-tgcalls >= 2.1)
    if not _registered:
        try:
            from pytgcalls import filters as _ptg_filters
            if hasattr(_ptg_filters, "stream_end"):
                @pytgcalls.on_update(_ptg_filters.stream_end)
                async def _stream_end_handler(client, update):
                    await _on_stream_end(client, update)
                _registered = True
                LOG.info("Stream-end callback registered via pytgcalls.filters.stream_end")
        except (ImportError, AttributeError, TypeError) as e:
            LOG.debug("Method 1 (filters.stream_end) failed: %s", e)

    # Method 2: pytgcalls.on_stream_end decorator
    if not _registered:
        try:
            if hasattr(pytgcalls, "on_stream_end"):
                @pytgcalls.on_stream_end()
                async def _stream_end_handler2(client, update):
                    await _on_stream_end(client, update)
                _registered = True
                LOG.info("Stream-end callback registered via pytgcalls.on_stream_end()")
        except (AttributeError, TypeError) as e:
            LOG.debug("Method 2 (on_stream_end) failed: %s", e)

    # Method 3: pytgcalls.on_closed_voice_chat
    if not _registered:
        try:
            if hasattr(pytgcalls, "on_closed_voice_chat"):
                @pytgcalls.on_closed_voice_chat()
                async def _stream_end_handler3(client, update):
                    await _on_stream_end(client, update)
                _registered = True
                LOG.info("Stream-end callback registered via pytgcalls.on_closed_voice_chat()")
        except (AttributeError, TypeError) as e:
            LOG.debug("Method 3 (on_closed_voice_chat) failed: %s", e)

    # Method 4: py-tgcalls >= 2.1 raw on_update without filter
    if not _registered:
        try:
            @pytgcalls.on_update()
            async def _raw_update_handler(client, update):
                # Only handle stream-end type events
                update_type = type(update).__name__.lower()
                if "end" in update_type or "stream" in update_type:
                    await _on_stream_end(client, update)
            _registered = True
            LOG.info("Stream-end callback registered via raw pytgcalls.on_update()")
        except (AttributeError, TypeError) as e:
            LOG.debug("Method 4 (raw on_update) failed: %s", e)

    if not _registered:
        LOG.warning("Could not register stream-end callback -- auto-skip will not work.")
