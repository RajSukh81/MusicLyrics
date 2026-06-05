"""Background prefetch for the next queued track.

While the current track plays, we resolve the next QueueItem in the background
(download or fetch a fresh stream URL).  When the current track ends or the
user skips, the next track is already prepared, so playback transitions
almost instantly with no extractor/download wait time.

Design
------
* One in-flight prefetch task per chat_id.
* Cache stores the resolved data plus a "key" (url + title + stream_type)
  so we can verify the cache still matches the upcoming track before using it.
* If the queue is mutated (shuffle, jump, manual reorder) the cache is
  invalidated to avoid playing the wrong file.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable

from MusicLyrics.utils.downloader import cleanup as _cleanup_file

LOG = logging.getLogger(__name__)


# ─── Cache structures ────────────────────────────────────────────────────────
@dataclass
class _Prefetched:
    key: str                # identity of the QueueItem (url|title|stream_type)
    media_path: str         # local file path OR direct stream URL
    is_stream_url: bool
    platform: str


_cache: dict[int, _Prefetched] = {}
_tasks: dict[int, asyncio.Task] = {}
_lock = asyncio.Lock()

# Resolver injected by stream.py to avoid a circular import.
# Signature: async def resolver(item) -> tuple[str|None, bool, str]
_resolver: Optional[Callable[[object], Awaitable[tuple]]] = None


def register_resolver(fn: Callable[[object], Awaitable[tuple]]) -> None:
    """Called once from stream.py to wire up the resolution function."""
    global _resolver
    _resolver = fn


def _item_key(item) -> str:
    """Stable identity for a QueueItem."""
    url = getattr(item, "url", "") or ""
    title = getattr(item, "title", "") or ""
    stype = getattr(item, "stream_type", "audio") or "audio"
    return f"{url}|{title}|{stype}"


# ─── Public API ──────────────────────────────────────────────────────────────
async def schedule_prefetch(chat_id: int) -> None:
    """Kick off (or replace) the prefetch task for the next item in *chat_id*'s queue.

    The "next" item is the one at index 1 — index 0 is what's currently playing.
    Non-blocking: returns immediately, work happens in the background.
    """
    if _resolver is None:
        return

    # Import here to avoid circular import at module load.
    try:
        from MusicLyrics.plugins.play.queue import get_chat_queue
    except Exception:  # pragma: no cover
        return

    cq = await get_chat_queue(chat_id)
    if len(cq.items) < 2:
        # Nothing to prefetch — only current track (or empty).
        return

    next_item = cq.items[1]
    key = _item_key(next_item)

    async with _lock:
        # Already cached & matches? Nothing to do.
        cached = _cache.get(chat_id)
        if cached and cached.key == key:
            return

        # In-flight task for the same item? Let it finish.
        old_task = _tasks.get(chat_id)
        if old_task and not old_task.done():
            # If it's prefetching a different item, cancel it.
            if getattr(old_task, "_prefetch_key", None) == key:
                return
            old_task.cancel()

        task = asyncio.create_task(_prefetch_worker(chat_id, next_item, key))
        # Stash the key so we can compare on rapid reschedules.
        task._prefetch_key = key  # type: ignore[attr-defined]
        _tasks[chat_id] = task


async def pop_prefetched(chat_id: int, item) -> Optional[tuple[str, bool, str]]:
    """Return cached resolution `(media_path, is_stream_url, platform)` if it
    matches *item*.  The entry is removed from the cache on hit.
    """
    key = _item_key(item)
    async with _lock:
        cached = _cache.get(chat_id)
        if cached and cached.key == key:
            del _cache[chat_id]
            LOG.info("Prefetch HIT for chat %s: '%s' (%s)",
                     chat_id, getattr(item, "title", "?"), cached.platform)
            return cached.media_path, cached.is_stream_url, cached.platform

        # If a prefetch is currently in-flight for this key, wait briefly for it.
        task = _tasks.get(chat_id)
        if task and not task.done() and getattr(task, "_prefetch_key", None) == key:
            LOG.info("Prefetch in-flight for '%s' — awaiting up to 12s",
                     getattr(item, "title", "?"))
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=12.0)
            except (asyncio.TimeoutError, Exception):
                pass
            cached = _cache.get(chat_id)
            if cached and cached.key == key:
                del _cache[chat_id]
                LOG.info("Prefetch HIT (after wait) for chat %s", chat_id)
                return cached.media_path, cached.is_stream_url, cached.platform

    LOG.info("Prefetch MISS for chat %s: '%s'", chat_id, getattr(item, "title", "?"))
    return None


async def clear_prefetch(chat_id: int) -> None:
    """Cancel any in-flight prefetch and drop cached entry (deleting any
    downloaded file).  Call this on stop / queue-clear / leave-VC.
    """
    async with _lock:
        task = _tasks.pop(chat_id, None)
        cached = _cache.pop(chat_id, None)

    if task and not task.done():
        task.cancel()
    if cached and not cached.is_stream_url and cached.media_path:
        try:
            _cleanup_file(cached.media_path)
        except Exception:
            pass


async def invalidate_if_changed(chat_id: int) -> None:
    """Drop the cache if the upcoming track no longer matches what we prefetched.

    Useful after shuffle / queue mutation.  Safer than blindly clearing —
    keeps the cache when it's still valid.
    """
    try:
        from MusicLyrics.plugins.play.queue import get_chat_queue
    except Exception:
        return
    cq = await get_chat_queue(chat_id)
    expected_key: Optional[str] = None
    if len(cq.items) >= 2:
        expected_key = _item_key(cq.items[1])

    async with _lock:
        cached = _cache.get(chat_id)
        task = _tasks.get(chat_id)

    if cached and cached.key != expected_key:
        await clear_prefetch(chat_id)
        if expected_key:  # something else to prefetch now
            await schedule_prefetch(chat_id)
        return

    if task and not task.done():
        if getattr(task, "_prefetch_key", None) != expected_key:
            await clear_prefetch(chat_id)
            if expected_key:
                await schedule_prefetch(chat_id)


# ─── Internal worker ─────────────────────────────────────────────────────────
async def _prefetch_worker(chat_id: int, item, key: str) -> None:
    """Resolve *item* via the registered resolver and store the result."""
    LOG.info("Prefetch START for chat %s: '%s'", chat_id, getattr(item, "title", "?"))
    try:
        result = await _resolver(item)  # type: ignore[misc]
    except asyncio.CancelledError:
        LOG.debug("Prefetch cancelled for chat %s", chat_id)
        raise
    except Exception as e:
        LOG.warning("Prefetch failed for chat %s ('%s'): %s",
                    chat_id, getattr(item, "title", "?"), e)
        return

    if not result:
        return
    media_path, is_stream_url, platform = result
    if not media_path:
        LOG.debug("Prefetch: resolver returned no media for chat %s", chat_id)
        return

    # Sanity: local path must exist
    if not is_stream_url and not os.path.isfile(str(media_path)):
        LOG.debug("Prefetch: local file missing after resolve: %s", media_path)
        return

    async with _lock:
        # Re-check key in case the queue moved on while we were resolving.
        try:
            from MusicLyrics.plugins.play.queue import get_chat_queue
            cq = await get_chat_queue(chat_id)
            current_next_key = _item_key(cq.items[1]) if len(cq.items) >= 2 else None
        except Exception:
            current_next_key = key

        if current_next_key != key:
            LOG.info("Prefetch: queue changed while resolving, discarding result for chat %s", chat_id)
            if not is_stream_url:
                try:
                    _cleanup_file(media_path)
                except Exception:
                    pass
            return

        _cache[chat_id] = _Prefetched(
            key=key,
            media_path=media_path,
            is_stream_url=is_stream_url,
            platform=platform,
        )
    LOG.info("Prefetch DONE for chat %s: '%s' via %s (stream_url=%s)",
             chat_id, getattr(item, "title", "?"), platform, is_stream_url)
