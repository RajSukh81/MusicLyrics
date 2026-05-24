"""Error logging and owner notification plugin for MusicLyrics bot."""

import logging
import traceback

from pyrogram import filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from MusicLyrics.bot import bot
from config import Config

LOG = logging.getLogger(__name__)


async def notify_owner(text: str):
    """Send a notification to the bot owner and/or LOG_GROUP_ID."""
    # Send to LOG_GROUP_ID
    if Config.LOG_GROUP_ID:
        try:
            await bot.send_message(
                chat_id=Config.LOG_GROUP_ID,
                text=text,
                disable_web_page_preview=True,
            )
        except FloodWait as e:
            import asyncio
            await asyncio.sleep(e.value + 1)
        except Exception:
            LOG.exception("Failed to send notification to LOG_GROUP_ID")

    # Also send to OWNER_ID personally
    if Config.OWNER_ID:
        try:
            await bot.send_message(
                chat_id=Config.OWNER_ID,
                text=text,
                disable_web_page_preview=True,
            )
        except Exception:
            LOG.exception("Failed to send notification to OWNER_ID")


async def log_error(context: str, error: Exception):
    """Log an error to both console and owner/log group."""
    tb = traceback.format_exc()
    LOG.error("Error in %s: %s\n%s", context, error, tb)

    # Truncate traceback for Telegram message limit
    tb_short = tb[-1500:] if len(tb) > 1500 else tb
    text = (
        f"**Error Report**\n\n"
        f"**Context:** {context}\n"
        f"**Error:** `{str(error)[:200]}`\n\n"
        f"```\n{tb_short}\n```"
    )
    await notify_owner(text)


@bot.on_message(filters.command("status") & filters.private)
async def status_cmd(client, message: Message):
    """Show bot status (owner/sudo only)."""
    user_id = message.from_user.id if message.from_user else 0
    if user_id != Config.OWNER_ID and user_id not in Config.SUDO_USERS:
        return

    import psutil
    import time

    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.5)

    # Count handlers
    handler_count = sum(len(h) for h in bot.dispatcher.groups.values())

    text = (
        f"**Bot Status Report**\n\n"
        f"**Handlers:** {handler_count}\n"
        f"**CPU:** {cpu}%\n"
        f"**RAM:** {ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB ({ram.percent}%)\n"
        f"**LOG_GROUP_ID:** `{Config.LOG_GROUP_ID or 'Not set'}`\n"
        f"**OWNER_ID:** `{Config.OWNER_ID}`\n"
        f"**SUDO_USERS:** {len(Config.SUDO_USERS)}\n"
        f"**STRING_SESSION:** {'Set' if Config.STRING_SESSION else 'Not set'}\n"
    )
    await message.reply_text(text)
