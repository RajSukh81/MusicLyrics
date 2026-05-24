"""MusicLyrics entry-point -- run with ``python -m MusicLyrics``."""

import asyncio
import importlib
import logging
import os
import signal
import sys
import traceback
from pathlib import Path

import aiohttp
from pyrogram import idle, filters
from pyrogram.types import Message
from pyrogram.enums import ChatType, ParseMode
from pyrogram.errors import FloodWait

from config import Config
from MusicLyrics import bot, userbot, pytgcalls, __version__

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s - %(levelname)s - %(message)s",
)
LOG = logging.getLogger("MusicLyrics")


# -- Logger helper: send messages to LOG_GROUP_ID and/or OWNER_ID --
# Uses fire-and-forget pattern to NEVER block command handlers.

async def _safe_send(chat_id: int, text: str, photo: str = None):
    """Send a message to a chat, silently ignoring all errors."""
    try:
        if photo:
            await bot.send_photo(chat_id, photo=photo, caption=text)
        else:
            await bot.send_message(
                chat_id, text=text,
                disable_web_page_preview=True,
            )
    except FloodWait as e:
        LOG.warning("FloodWait %ds for chat %s, skipping log.", e.value, chat_id)
    except Exception:
        pass  # Never crash on log failure


def log_to_group(text: str, photo: str = None):
    """Schedule a log message (non-blocking, fire-and-forget)."""
    if Config.LOG_GROUP_ID:
        asyncio.create_task(_safe_send(Config.LOG_GROUP_ID, text, photo))
    if Config.OWNER_ID and Config.OWNER_ID != Config.LOG_GROUP_ID:
        asyncio.create_task(_safe_send(Config.OWNER_ID, text, photo))


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
    """Send a branded startup notification."""
    if not Config.LOG_GROUP_ID and not Config.OWNER_ID:
        return
    bot_me = await bot.get_me()
    user_info = "N/A (no userbot)"
    if userbot:
        try:
            user_me = await userbot.get_me()
            user_info = user_me.first_name
        except Exception:
            user_info = "N/A"

    handler_count = sum(len(h) for h in bot.dispatcher.groups.values())
    text = (
        f"**MusicLyrics v{__version__} Started**\n\n"
        f"Bot  : @{bot_me.username}\n"
        f"User : {user_info}\n"
        f"Handlers : {handler_count}\n"
        f"PyTgCalls : {'Active' if pytgcalls else 'Disabled'}\n\n"
        f"[Support]({Config.SUPPORT_GROUP}) | "
        f"[Channel]({Config.SUPPORT_CHANNEL}) | "
        f"[Owner]({Config.OWNER_LINK})"
    )
    # Direct send (not fire-and-forget) only for startup
    for cid in {Config.LOG_GROUP_ID, Config.OWNER_ID} - {0, None}:
        await _safe_send(cid, text, photo=Config.BRAND_PHOTO)


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
                "%s: FloodWait %ds. Attempt %d/%d.",
                name, wait, attempt, max_retries,
            )
            await asyncio.sleep(wait + 2)
        except Exception as e:
            LOG.exception("%s: Failed to start (attempt %d/%d): %s",
                          name, attempt, max_retries, e)
            if attempt < max_retries:
                await asyncio.sleep(10)
            else:
                raise


# -- Runtime event logging (only important events, non-blocking) --

def _setup_event_logging():
    """Register lightweight event-logging handlers."""

    @bot.on_message(filters.new_chat_members)
    async def on_bot_added(client, message: Message):
        """Log when bot is added to a new group."""
        try:
            me = await client.get_me()
            for member in message.new_chat_members:
                if member.id == me.id:
                    added_by = message.from_user.mention if message.from_user else "Unknown"
                    log_to_group(
                        f"**Bot Added to New Group**\n\n"
                        f"Group: {message.chat.title}\n"
                        f"Chat ID: {message.chat.id}\n"
                        f"Added by: {added_by}"
                    )
                    break
        except Exception:
            LOG.exception("Error in on_bot_added")

    @bot.on_message(filters.left_chat_member)
    async def on_bot_removed(client, message: Message):
        """Log when bot is removed from a group."""
        try:
            me = await client.get_me()
            if message.left_chat_member and message.left_chat_member.id == me.id:
                log_to_group(
                    f"**Bot Removed from Group**\n\n"
                    f"Group: {message.chat.title}\n"
                    f"Chat ID: {message.chat.id}"
                )
        except Exception:
            LOG.exception("Error in on_bot_removed")

    LOG.info("Event logging handlers registered.")


async def _delete_webhook():
    """Explicitly delete any Telegram webhook to ensure long polling works."""
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/deleteWebhook"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                result = await resp.json()
                if result.get("result"):
                    LOG.info("Webhook deleted successfully.")
                else:
                    LOG.info("deleteWebhook response: %s", result)
    except Exception as e:
        LOG.warning("Could not delete webhook: %s", e)


async def _check_bot_info():
    """Log bot info for diagnostics."""
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/getWebhookInfo"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                result = await resp.json()
                info = result.get("result", {})
                webhook_url = info.get("url", "")
                pending = info.get("pending_update_count", 0)
                LOG.info(
                    "Webhook info: url=%s, pending_updates=%d, last_error=%s",
                    webhook_url or "(none)",
                    pending,
                    info.get("last_error_message", "none"),
                )
                if webhook_url:
                    LOG.warning(
                        "WEBHOOK IS SET to '%s'! This prevents long polling. "
                        "Deleting it now...", webhook_url
                    )
                    await _delete_webhook()
    except Exception as e:
        LOG.warning("Could not check webhook info: %s", e)


async def main():
    """Start the bot, userbot, and py-tgcalls, then idle."""

    result = _load_plugins()

    handler_count = sum(len(h) for h in bot.dispatcher.groups.values())
    LOG.info("Total handlers registered: %d", handler_count)
    if handler_count == 0:
        LOG.warning("No handlers registered after plugin loading!")

    # CRITICAL: Delete any webhook BEFORE starting the bot.
    # If a webhook is set (from a previous deployment), Telegram sends all
    # updates to the webhook URL and long polling receives NOTHING.
    await _check_bot_info()
    await _delete_webhook()

    await _start_with_retry(bot, "Bot")

    # Verify bot can receive updates
    bot_me = await bot.get_me()
    LOG.info("Bot identity: @%s (ID: %d)", bot_me.username, bot_me.id)

    if Config.STRING_SESSION and userbot and pytgcalls:
        await _start_with_retry(userbot, "Userbot")
        try:
            await pytgcalls.start()
            LOG.info("PyTgCalls started.")
        except Exception:
            LOG.exception("PyTgCalls failed to start.")
            log_to_group(
                "**Warning:** PyTgCalls failed to start.\n"
                "Music streaming may not work."
            )

    # Setup event logging (lightweight, non-blocking)
    _setup_event_logging()

    await _send_startup_message()

    if result:
        loaded, failed = result
        if failed > 0:
            log_to_group(
                f"**Plugin Loading Report**\n\n"
                f"Loaded: {loaded}\nFailed: {failed}\n"
                "Check server logs for details."
            )

    LOG.info("MusicLyrics v%s is running.", __version__)
    await idle()

    # Graceful shutdown
    LOG.info("Shutting down MusicLyrics...")
    log_to_group("**MusicLyrics is shutting down...**")
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
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
