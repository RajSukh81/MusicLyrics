"""MusicLyrics entry-point — run with ``python -m MusicLyrics``."""

import asyncio
import importlib
import logging
import os
import signal
import sys
from pathlib import Path

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
    except Exception:
        LOG.exception("Could not send startup message to LOG_GROUP_ID.")


async def main():
    """Start the bot, userbot, and py-tgcalls, then idle."""
    _load_plugins()

    await bot.start()
    LOG.info("Bot client started.")

    if Config.STRING_SESSION and userbot and pytgcalls:
        await userbot.start()
        LOG.info("Userbot client started.")
        await pytgcalls.start()
        LOG.info("PyTgCalls started.")

    await _send_startup_message()
    LOG.info("MusicLyrics v%s is running.", __version__)

    # Keep running until interrupted
    stop_event = asyncio.Event()

    def _handle_signal():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    await stop_event.wait()

    # Graceful shutdown
    LOG.info("Shutting down MusicLyrics...")
    if Config.STRING_SESSION and pytgcalls:
        # py-tgcalls 2.x has no stop(); leave all active calls instead
        for chat_id in list(await pytgcalls.calls):
            try:
                await pytgcalls.leave_call(chat_id)
            except Exception:
                pass
    if Config.STRING_SESSION and userbot:
        await userbot.stop()
    await bot.stop()
    LOG.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
