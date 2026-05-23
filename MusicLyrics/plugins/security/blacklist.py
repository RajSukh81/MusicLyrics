"""Blacklisted words plugin for MusicLyrics bot."""

from __future__ import annotations

import time
import logging
from collections import defaultdict

from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions
from pyrogram.enums import ChatMemberStatus

from MusicLyrics.bot import bot
from MusicLyrics.mongo.blacklist_db import (
    add_blacklist,
    get_blacklist,
    delete_blacklist,
)
from MusicLyrics.helpers.decorators import admin_required
from config import Config

LOG = logging.getLogger(__name__)

# Track repeat offenders: {chat_id: {user_id: count}}
_offender_count: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
OFFENDER_MUTE_THRESHOLD = 3
MUTE_DURATION = 600  # 10 minutes


# ── Blacklist watcher ──────────────────────────────────────────────────────

@bot.on_message(filters.group & (filters.text | filters.caption), group=-2)
async def blacklist_watcher(client: Client, message: Message):
    if not message.from_user:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    # Skip admins / sudo
    if user_id in Config.SUDO_USERS or user_id == Config.OWNER_ID:
        return
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return
    except Exception:
        pass

    bl_words = await get_blacklist(chat_id)
    if not bl_words:
        return

    text = (message.text or message.caption or "").lower()
    for entry in bl_words:
        word = entry["word"]
        if word in text:
            # Delete the message
            try:
                await message.delete()
            except Exception:
                pass

            # Track offender
            _offender_count[chat_id][user_id] += 1
            count = _offender_count[chat_id][user_id]

            if count >= OFFENDER_MUTE_THRESHOLD:
                # Mute repeat offender
                try:
                    until = int(time.time()) + MUTE_DURATION
                    await client.restrict_chat_member(
                        chat_id,
                        user_id,
                        ChatPermissions(),
                        until_date=until,
                    )
                    _offender_count[chat_id][user_id] = 0
                    await client.send_message(
                        chat_id,
                        f"**Blacklist**\n"
                        f"{message.from_user.mention} বারবার ব্ল্যাকলিস্ট শব্দ ব্যবহার "
                        f"করায় {MUTE_DURATION // 60} মিনিটের জন্য মিউট হয়েছে।\n"
                        f"Muted for {MUTE_DURATION // 60} minutes for repeated "
                        f"blacklisted word usage.",
                    )
                except Exception as e:
                    LOG.warning("Blacklist mute failed: %s", e)
            else:
                try:
                    await client.send_message(
                        chat_id,
                        f"{message.from_user.mention}, এই শব্দ ব্ল্যাকলিস্টে আছে! "
                        f"({count}/{OFFENDER_MUTE_THRESHOLD} warning)\n"
                        f"This word is blacklisted!",
                    )
                except Exception:
                    pass

            break  # Only warn once per message


# ── /blacklist <word> ──────────────────────────────────────────────────────

@bot.on_message(filters.command("blacklist") & filters.group)
@admin_required
async def add_blacklist_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text(
            "**Usage:** `/blacklist <word>`\n"
            "উদাহরণ / Example: `/blacklist spam`"
        )
        return

    word = message.text.split(None, 1)[1].strip().lower()
    await add_blacklist(message.chat.id, word)
    await message.reply_text(
        f"**Blacklist-এ যোগ হয়েছে!** / Added to blacklist!\n"
        f"Word: `{word}`\n"
        f"এই শব্দ সম্বলিত মেসেজ অটোমেটিক ডিলিট হবে।"
    )


# ── /blacklists ─────────────────────────────────────────────────────────────

@bot.on_message(filters.command("blacklists") & filters.group)
async def list_blacklist_cmd(client: Client, message: Message):
    bl_words = await get_blacklist(message.chat.id)
    if not bl_words:
        await message.reply_text(
            "এই চ্যাটে কোনো ব্ল্যাকলিস্ট শব্দ নেই। / No blacklisted words.\n"
            "যোগ করতে: `/blacklist <word>`"
        )
        return

    lines = ["**ব্ল্যাকলিস্ট শব্দ / Blacklisted Words:**\n"]
    for i, entry in enumerate(bl_words, 1):
        lines.append(f"{i}. `{entry['word']}`")

    await message.reply_text("\n".join(lines))


# ── /unblacklist <word> ─────────────────────────────────────────────────────

@bot.on_message(filters.command("unblacklist") & filters.group)
@admin_required
async def remove_blacklist_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("**Usage:** `/unblacklist <word>`")
        return

    word = message.text.split(None, 1)[1].strip().lower()
    await delete_blacklist(message.chat.id, word)
    await message.reply_text(
        f"**Blacklist থেকে সরানো হয়েছে!** / Removed from blacklist!\n"
        f"Word: `{word}`"
    )
