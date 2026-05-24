"""Emoji tools plugin for MusicLyrics bot."""

import random

from pyrogram import filters
from pyrogram.types import Message

from MusicLyrics.bot import bot

EMOJI_LIST = [
    "\U0001f600", "\U0001f602", "\U0001f970", "\U0001f60e", "\U0001f929",
    "\U0001f621", "\U0001f97a", "\U0001f62d", "\U0001f92f", "\U0001fae1",
    "\U0001f47b", "\U0001f480", "\U0001f916", "\U0001f47d", "\U0001f383",
    "\U0001f525", "\U0001f496", "\u2b50", "\U0001f308", "\U0001f3b5",
    "\U0001f98b", "\U0001f409", "\U0001f355", "\U0001f3ae", "\U0001f3c6",
    "\U0001f48e", "\U0001f680", "\U0001f338", "\U0001f340", "\U0001f984",
]


@bot.on_message(filters.command("emoji"))
async def emoji_cmd(_, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        return await message.reply_text(
            "Provide an emoji.\n"
            "Example: `/emoji \U0001f525`"
        )

    emoji = args[1].strip()
    big = (
        f"{emoji} {emoji} {emoji} {emoji} {emoji}\n"
        f"{emoji} {emoji} {emoji} {emoji} {emoji}\n"
        f"{emoji} {emoji} {emoji} {emoji} {emoji}\n"
        f"{emoji} {emoji} {emoji} {emoji} {emoji}\n"
        f"{emoji} {emoji} {emoji} {emoji} {emoji}"
    )
    await message.reply_text(big)


@bot.on_message(filters.command("mixemoji"))
async def mix_emoji_cmd(_, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 3:
        return await message.reply_text(
            "Provide two emojis.\n"
            "Example: `/mixemoji \U0001f525 \U0001f4a7`"
        )

    e1, e2 = args[1].strip(), args[2].strip()
    combos = [
        f"{e1} + {e2} = {e1}{e2} \u2728",
        f"{e2} + {e1} = {e2}{e1} \U0001f4ab",
        f"{e1} x {e2} = {e1} \U0001f91d {e2}",
    ]
    text = (
        f"**Emoji Mix**\n\n"
        + "\n".join(combos)
        + f"\n\nMixed result: {e1}{e2}{random.choice(EMOJI_LIST)}"
    )
    await message.reply_text(text)


@bot.on_message(filters.command("randomemoji"))
async def random_emoji_cmd(_, message: Message):
    picked = random.sample(EMOJI_LIST, k=min(5, len(EMOJI_LIST)))
    await message.reply_text(
        f"**Random Emoji:**\n\n"
        f"{'  '.join(picked)}"
    )
