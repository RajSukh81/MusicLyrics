"""MusicLyrics entry-point -- run with ``python -m MusicLyrics``."""

import asyncio
import importlib
import logging
import os
import signal
import sys
import traceback
from pathlib import Path

from pyrogram import idle, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from config import Config
from MusicLyrics import bot, userbot, pytgcalls, __version__

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
)
LOG = logging.getLogger("MusicLyrics")


# -- Logger helper: send messages to LOG_GROUP_ID --

async def log_to_group(text: str, photo: str = None):
    """Send a log message to the configured LOG_GROUP_ID."""
    if not Config.LOG_GROUP_ID:
        return
    try:
        if photo:
            await bot.send_photo(
                chat_id=Config.LOG_GROUP_ID,
                photo=photo,
                caption=text,
            )
        else:
            await bot.send_message(
                chat_id=Config.LOG_GROUP_ID,
                text=text,
            )
    except FloodWait as e:
        LOG.warning("FloodWait on log message: waiting %d seconds.", e.value)
        await asyncio.sleep(e.value + 2)
    except Exception:
        LOG.exception("Could not send log message to LOG_GROUP_ID.")


def _load_plugins():
    """Recursively import every .py module under MusicLyrics/plugins/."""
    plugins_dir = Path(__file__).parent / "plugins"
    if not plugins_dir.is_dir():
        LOG.warning("plugins/ directory not found -- skipping plugin load.")
        return

    loaded = 0
    failed = 0
    for py_file in sorted(plugins_dir.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        # Build a dotted module path relative to the repo root
        relative = py_file.relative_to(Path(__file__).parent.parent)
        module_path = ".".join(relative.with_suffix("").parts)
        try:
            importlib.import_module(module_path)
            LOG.info("Loaded plugin: %s", module_path)
            loaded += 1
        except Exception:
            LOG.exception("Failed to load plugin: %s", module_path)
            failed += 1

    LOG.info("Plugin loading complete: %d loaded, %d failed.", loaded, failed)
    return loaded, failed


async def _send_startup_message():
    """Send a branded startup notification to LOG_GROUP_ID."""
    if not Config.LOG_GROUP_ID:
        return
    bot_me = await bot.get_me()
    user_info = "N/A (no userbot)"
    if userbot:
        try:
            user_me = await userbot.get_me()
            user_info = user_me.first_name
        except Exception:
            user_info = "N/A"
    text = (
        f"**MusicLyrics v{__version__} Started**\n\n"
        f"Bot  : @{bot_me.username}\n"
        f"User : {user_info}\n\n"
        f"[Support]({Config.SUPPORT_GROUP}) | "
        f"[Channel]({Config.SUPPORT_CHANNEL}) | "
        f"[Owner]({Config.OWNER_LINK})"
    )
    try:
        await bot.send_photo(
            chat_id=Config.LOG_GROUP_ID,
            photo=Config.BRAND_PHOTO,
            caption=text,
        )
    except FloodWait as e:
        LOG.warning("FloodWait on startup message: waiting %d seconds.", e.value)
        await asyncio.sleep(e.value + 2)
    except Exception:
        LOG.exception("Could not send startup message to LOG_GROUP_ID.")


async def _start_with_retry(client, name, max_retries=5):
    """Start a Pyrogram client with FloodWait retry handling."""
    for attempt in range(1, max_retries + 1):
        try:
            await client.start()
            LOG.info("%s client started.", name)
            return
        except FloodWait as e:
            wait = e.value
            LOG.warning(
                "%s: FloodWait -- Telegram requires %d seconds wait. "
                "Attempt %d/%d. Waiting...",
                name, wait, attempt, max_retries,
            )
            await asyncio.sleep(wait + 2)
        except Exception as e:
            LOG.exception("%s: Failed to start (attempt %d/%d): %s", name, attempt, max_retries, e)
            if attempt < max_retries:
                await asyncio.sleep(10)
            else:
                raise


# -- Runtime event logging middleware --

def _setup_event_logging():
    """Register handlers that log important events to LOG_GROUP_ID."""

    @bot.on_message(filters.new_chat_members)
    async def on_bot_added(client, message: Message):
        """Log when bot is added to a new group."""
        me = await client.get_me()
        for member in message.new_chat_members:
            if member.id == me.id:
                added_by = message.from_user.mention if message.from_user else "Unknown"
                text = (
                    f"**Bot Added to New Group**\n\n"
                    f"**Group:** {message.chat.title}\n"
                    f"**Chat ID:** `{message.chat.id}`\n"
                    f"**Added by:** {added_by}\n"
                    f"**Members:** {message.chat.members_count or 'N/A'}"
                )
                await log_to_group(text)
                break

    @bot.on_message(filters.left_chat_member)
    async def on_bot_removed(client, message: Message):
        """Log when bot is removed from a group."""
        me = await client.get_me()
        if message.left_chat_member and message.left_chat_member.id == me.id:
            removed_by = message.from_user.mention if message.from_user else "Unknown"
            text = (
                f"**Bot Removed from Group**\n\n"
                f"**Group:** {message.chat.title}\n"
                f"**Chat ID:** `{message.chat.id}`\n"
                f"**Removed by:** {removed_by}"
            )
            await log_to_group(text)

    @bot.on_message(filters.command("play") & ~filters.edited, group=99)
    async def log_play_cmd(client, message: Message):
        """Log /play usage to LOG_GROUP_ID."""
        user = message.from_user
        query = " ".join(message.command[1:]) if len(message.command) > 1 else "(empty)"
        text = (
            f"**Play Command Used**\n\n"
            f"**User:** {user.mention if user else 'Unknown'}\n"
            f"**Chat:** {message.chat.title or 'Private'} (`{message.chat.id}`)\n"
            f"**Query:** `{query[:100]}`"
        )
        await log_to_group(text)
        # Don't stop propagation - let the main handler process it
        message.continue_propagation()

    LOG.info("Event logging handlers registered.")


async def main():
    """Start the bot, userbot, and py-tgcalls, then idle."""

    result = _load_plugins()

    # Verify handlers were registered
    handler_count = sum(len(handlers) for handlers in bot.dispatcher.groups.values())
    LOG.info("Total handlers registered: %d", handler_count)
    if handler_count == 0:
        LOG.warning("No handlers registered after plugin loading!")

    await _start_with_retry(bot, "Bot")

    if Config.STRING_SESSION and userbot and pytgcalls:
        await _start_with_retry(userbot, "Userbot")
        try:
            await pytgcalls.start()
            LOG.info("PyTgCalls started.")
        except Exception:
            LOG.exception("PyTgCalls failed to start -- music features may not work.")
            await log_to_group(
                "**Warning:** PyTgCalls failed to start.\n"
                "Music streaming features may not work.\n\n"
                f"```{traceback.format_exc()[-500:]}```"
            )

    # Setup event logging
    _setup_event_logging()

    await _send_startup_message()

    # Log plugin loading results
    if result:
        loaded, failed = result
        if failed > 0:
            await log_to_group(
                f"**Plugin Loading Report**\n\n"
                f"Loaded: {loaded}\n"
                f"Failed: {failed}\n\n"
                "Check server logs for details."
            )

    LOG.info("MusicLyrics v%s is running.", __version__)

    # Keep running until interrupted (Pyrogram's official idle)
    await idle()

    # Graceful shutdown
    LOG.info("Shutting down MusicLyrics...")
    await log_to_group("**MusicLyrics is shutting down...**")
    try:
        if Config.STRING_SESSION and pytgcalls:
            try:
                calls = pytgcalls.calls
                if asyncio.iscoroutine(calls):
                    calls = await calls
                for chat_id in list(calls):
                    try:
                        await pytgcalls.leave_call(chat_id)
                    except Exception:
                        pass
            except Exception:
                LOG.warning("Could not leave active calls during shutdown.")
        if Config.STRING_SESSION and userbot:
            await userbot.stop()
        await bot.stop()
    except Exception:
        LOG.exception("Error during shutdown.")
    LOG.info("Goodbye.")


if __name__ == "__main__":
    # Use get_event_loop().run_until_complete() instead of asyncio.run()
    # because asyncio.run() creates a NEW event loop, but pyrofork's Client
    # and Dispatcher (created at import time in bot.py) hold a reference to
    # the loop from asyncio.get_event_loop(). Using the same loop ensures
    # add_handler(), dispatcher.start(), and handler workers all operate
    # on the correct loop. This matches pyrofork's own Client.run() behavior.
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
