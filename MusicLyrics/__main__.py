"""MusicLyrics entry-point — run with ``python -m MusicLyrics``."""

import asyncio
import importlib
import logging
import os
import signal
import sys
from pathlib import Path

from pyrogram import idle
from pyrogram.errors import FloodWait

from config import Config
from MusicLyrics import bot, userbot, pytgcalls, __version__

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
)
LOG = logging.getLogger("MusicLyrics")


def _load_plugins():
    """Recursively import every .py module under MusicLyrics/plugins/."""
    plugins_dir = Path(__file__).parent / "plugins"
    if not plugins_dir.is_dir():
        LOG.warning("plugins/ directory not found — skipping plugin load.")
        return

    for py_file in sorted(plugins_dir.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        # Build a dotted module path relative to the repo root
        relative = py_file.relative_to(Path(__file__).parent.parent)
        module_path = ".".join(relative.with_suffix("").parts)
        try:
            importlib.import_module(module_path)
            LOG.info("Loaded plugin: %s", module_path)
        except Exception:
            LOG.exception("Failed to load plugin: %s", module_path)


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
        await bot.send_photo(
            chat_id=Config.LOG_GROUP_ID,
            photo=Config.BRAND_PHOTO_2,
            caption=f"**MusicLyrics v{__version__}** is now online.",
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
                "%s: FloodWait — Telegram requires %d seconds wait. "
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


async def main():
    """Start the bot, userbot, and py-tgcalls, then idle."""

    _load_plugins()

    # Verify handlers were registered
    if not bot.dispatcher.groups:
        LOG.warning("No handlers registered after plugin loading!")

    await _start_with_retry(bot, "Bot")

    if Config.STRING_SESSION and userbot and pytgcalls:
        await _start_with_retry(userbot, "Userbot")
        await pytgcalls.start()
        LOG.info("PyTgCalls started.")

    await _send_startup_message()
    LOG.info("MusicLyrics v%s is running.", __version__)

    # Keep running until interrupted (Pyrogram's official idle)
    await idle()

    # Graceful shutdown
    LOG.info("Shutting down MusicLyrics...")
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
