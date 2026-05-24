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
        await asyncio.sleep(e.value + 1)
        try:
            await bot.send_message(chat_id, text=text, disable_web_page_preview=True)
        except Exception:
            pass
    except Exception as exc:
        LOG.warning("Failed to send log to %s: %s", chat_id, exc)


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
            user_info = f"{user_me.first_name} (ID: {user_me.id})"
        except Exception:
            user_info = "N/A"

    handler_count = sum(len(h) for h in bot.dispatcher.groups.values())

    # Gather system info
    try:
        import psutil
        ram = psutil.virtual_memory()
        ram_info = f"{ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB ({ram.percent}%)"
        cpu_info = f"{psutil.cpu_percent(interval=0.3)}%"
    except Exception:
        ram_info = "N/A"
        cpu_info = "N/A"

    text = (
        f"**MusicLyrics v{__version__} Started Successfully!**\n\n"
        f"**Bot:** @{bot_me.username} (ID: `{bot_me.id}`)\n"
        f"**Userbot:** {user_info}\n"
        f"**Handlers:** {handler_count}\n"
        f"**PyTgCalls:** {'Active' if pytgcalls else 'Disabled'}\n"
        f"**CPU:** {cpu_info}\n"
        f"**RAM:** {ram_info}\n"
        f"**LOG_GROUP_ID:** `{Config.LOG_GROUP_ID or 'Not set'}`\n"
        f"**OWNER_ID:** `{Config.OWNER_ID}`\n\n"
        f"All systems operational. Bot is ready to receive commands.\n\n"
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
                        f"Chat ID: `{message.chat.id}`\n"
                        f"Members: {message.chat.members_count or 'N/A'}\n"
                        f"Added by: {added_by}"
                    )
                    # Send welcome message to the group
                    await message.reply_text(
                        f"**ধন্যবাদ আমাকে যোগ করার জন্য!** 🎵\n\n"
                        f"আমি {Config.BOT_NAME}! Music streaming, games, "
                        f"security tools সব আছে।\n\n"
                        f"/help দিয়ে সব কমান্ড দেখো!\n"
                        f"Use /help to see all commands."
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
                removed_by = message.from_user.mention if message.from_user else "Unknown"
                log_to_group(
                    f"**Bot Removed from Group**\n\n"
                    f"Group: {message.chat.title}\n"
                    f"Chat ID: `{message.chat.id}`\n"
                    f"Removed by: {removed_by}"
                )
        except Exception:
            LOG.exception("Error in on_bot_removed")

    @bot.on_message(filters.command([
        "play", "p", "vplay", "vp", "song", "vsong",
    ]) & filters.group, group=98)
    async def _log_music_commands(client, message: Message):
        """Log music commands in groups to owner."""
        if not message.from_user:
            return
        user = message.from_user
        cmd = message.text or ""
        log_to_group(
            f"**Music Command**\n\n"
            f"**Group:** {message.chat.title}\n"
            f"**Chat ID:** `{message.chat.id}`\n"
            f"**User:** {user.mention} (`{user.id}`)\n"
            f"**Command:** `{cmd[:200]}`"
        )

    LOG.info("Event logging handlers registered.")


def _setup_catchall_handler():
    """Register a catch-all handler so the bot always responds."""

    @bot.on_message(filters.command([
        "start", "help", "play", "p", "vplay", "vp", "pause", "resume",
        "skip", "next", "stop", "end", "seek", "volume", "vol", "queue",
        "nowplaying", "np", "loop", "shuffle", "song", "vsong", "ping",
        "alive", "ban", "unban", "mute", "unmute", "warn", "antispam",
        "antiflood", "captcha", "blacklist", "setwelcome", "tr", "tts",
        "sticker", "s", "toimg", "kang", "getsticker", "stickerid",
        "info", "chatinfo", "paste", "telegraph", "tagall", "afk",
        "react", "reactall", "emoji", "mixemoji", "randomemoji",
        "broadcast", "stats", "addsudo", "rmsudo", "sudolist",
        "status", "ttt", "quiz", "truth", "dare", "flip", "dice",
        "wordseek", "kill", "pin", "unpin", "purge",
        "filter", "filters", "clearfilter", "notes", "save", "get",
    ]) & filters.private, group=99)
    async def _log_private_commands(client, message: Message):
        """Log all private commands to owner/log group."""
        if not message.from_user:
            return
        user = message.from_user
        cmd = message.text or message.caption or ""
        log_to_group(
            f"**Private Command**\n\n"
            f"**User:** {user.mention} (`{user.id}`)\n"
            f"**Username:** @{user.username or 'N/A'}\n"
            f"**Command:** `{cmd[:200]}`"
        )

    @bot.on_message(~filters.me & ~filters.service & filters.private, group=100)
    async def _catchall_private(client, message: Message):
        """Respond to unrecognized private messages with unknown command notice."""
        if message.text and message.text.startswith("/"):
            cmd = message.text.split()[0]
            await message.reply_text(
                f"❌ **Unknown command:** `{cmd}`\n\n"
                f"কমান্ড তালিকা দেখতে /help দিন।\n"
                f"Use /help to see all available commands."
            )
        # Non-command private messages are handled by the AI chat plugin

    LOG.info("Catch-all and mention handlers registered.")


async def _delete_webhook():
    """Explicitly delete any Telegram webhook and drop pending updates."""
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/deleteWebhook"
    params = {"drop_pending_updates": True}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                result = await resp.json()
                if result.get("result"):
                    LOG.info("Webhook deleted + pending updates dropped.")
                else:
                    LOG.warning("deleteWebhook response: %s", result)
    except Exception as e:
        LOG.warning("Could not delete webhook: %s", e)
        # Fallback: try GET method
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{url}?drop_pending_updates=true",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    result = await resp.json()
                    LOG.info("Webhook delete fallback response: %s", result)
        except Exception as e2:
            LOG.error("Webhook delete fallback also failed: %s", e2)


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

    # CRITICAL: Delete webhook FIRST — if a webhook is set, Telegram sends
    # all updates there and long-polling receives NOTHING.
    LOG.info("Step 1: Checking and deleting webhook...")
    await _check_bot_info()
    await _delete_webhook()
    # Wait a moment for Telegram to process the webhook deletion
    await asyncio.sleep(2)

    LOG.info("Step 2: Loading plugins...")
    result = _load_plugins()

    handler_count = sum(len(h) for h in bot.dispatcher.groups.values())
    LOG.info("Total handlers registered: %d", handler_count)
    if handler_count == 0:
        LOG.warning("No handlers registered after plugin loading!")

    LOG.info("Step 3: Starting bot client...")
    await _start_with_retry(bot, "Bot")

    # Double-check: delete webhook again AFTER bot.start() in case
    # Pyrogram re-set it during handshake -- but do NOT drop pending
    # updates this time, as that would discard updates Pyrogram is
    # already polling for and can desync the getUpdates offset.
    _wh_url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/deleteWebhook"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _wh_url, json={"drop_pending_updates": False},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                _wh_resp = await resp.json()
                LOG.info("Post-start webhook delete: %s", _wh_resp)
    except Exception as e:
        LOG.warning("Post-start webhook delete failed: %s", e)

    # Verify bot can receive updates
    bot_me = await bot.get_me()
    LOG.info("Bot identity: @%s (ID: %d)", bot_me.username, bot_me.id)

    if Config.STRING_SESSION and userbot and pytgcalls:
        LOG.info("Step 4: Starting userbot + PyTgCalls...")
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
    else:
        LOG.info("Step 4: Skipped userbot (STRING_SESSION not set).")

    # Setup event logging (lightweight, non-blocking)
    LOG.info("Step 5: Setting up event logging and catch-all handler...")
    _setup_event_logging()
    _setup_catchall_handler()

    await _send_startup_message()

    if result:
        loaded, failed = result
        if failed > 0:
            log_to_group(
                f"**Plugin Loading Report**\n\n"
                f"Loaded: {loaded}\nFailed: {failed}\n"
                "Check server logs for details."
            )

    LOG.info("MusicLyrics v%s is running. Waiting for updates...", __version__)
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
                        await pytgcalls.leave_group_call(chat_id)
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
