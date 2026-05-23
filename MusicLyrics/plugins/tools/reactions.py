"""Reaction features plugin for MusicLyrics bot."""

from pyrogram import filters
from pyrogram.types import Message
from pyrogram.errors import ReactionInvalid, MessageNotModified

import random

from MusicLyrics.bot import bot

REACTION_EMOJIS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡",
    "🥱", "🥴", "😍", "🐳", "❤️‍🔥", "🌚", "🌭", "💯", "🤣", "⚡",
    "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈",
    "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇", "😨",
]


@bot.on_message(filters.command("react"))
async def react_cmd(client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        return await message.reply_text(
            "❌ ব্যবহার / Usage:\n"
            "`/react <emoji>` — Reply to a message\n"
            "`/react random` — Random reaction\n"
            "`/react list` — Available emojis"
        )

    sub = args[1].strip().lower()

    if sub == "list":
        text = "🎭 **Available Reactions / উপলব্ধ রিঅ্যাকশন:**\n\n"
        text += "  ".join(REACTION_EMOJIS)
        return await message.reply_text(text)

    if not message.reply_to_message:
        return await message.reply_text(
            "❌ একটি মেসেজে রিপ্লাই দাও। / Reply to a message."
        )

    if sub == "random":
        emoji = random.choice(REACTION_EMOJIS)
    else:
        emoji = args[1].strip()

    try:
        await client.send_reaction(
            chat_id=message.chat.id,
            message_id=message.reply_to_message.id,
            emoji=emoji,
        )
        await message.reply_text(
            f"✅ রিঅ্যাকশন দেওয়া হয়েছে: {emoji}\n"
            f"Reacted with: {emoji}"
        )
    except ReactionInvalid:
        await message.reply_text(
            f"❌ `{emoji}` এই চ্যাটে সাপোর্ট করে না।\n"
            "This emoji is not supported for reactions here."
        )
    except Exception as e:
        await message.reply_text(f"❌ Error: `{e}`")
