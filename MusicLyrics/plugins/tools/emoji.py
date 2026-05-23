"""Emoji tools plugin for MusicLyrics bot."""

import random

from pyrogram import filters
from pyrogram.types import Message

from MusicLyrics.bot import bot

EMOJI_LIST = [
    "😀", "😂", "🥰", "😎", "🤩", "😡", "🥺", "😭", "🤯", "🫡",
    "👻", "💀", "🤖", "👽", "🎃", "🔥", "💖", "⭐", "🌈", "🎵",
    "🦋", "🐉", "🍕", "🎮", "🏆", "💎", "🚀", "🌸", "🍀", "🦄",
]


@bot.on_message(filters.command("emoji"))
async def emoji_cmd(_, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        return await message.reply_text(
            "❌ একটি ইমোজি দাও। / Provide an emoji.\n"
            "Example: `/emoji 🔥`"
        )

    emoji = args[1].strip()
    big = f"""
╔══════════════╗
║                              ║
║        {emoji}  {emoji}  {emoji}         ║
║     {emoji}  {emoji}  {emoji}  {emoji}      ║
║        {emoji}  {emoji}  {emoji}         ║
║                              ║
╚══════════════╝
"""
    await message.reply_text(big)


@bot.on_message(filters.command("mixemoji"))
async def mix_emoji_cmd(_, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 3:
        return await message.reply_text(
            "❌ দুইটি ইমোজি দাও। / Provide two emojis.\n"
            "Example: `/mixemoji 🔥 💧`"
        )

    e1, e2 = args[1].strip(), args[2].strip()
    combos = [
        f"{e1}+{e2} = {e1}{e2}✨",
        f"{e2}+{e1} = {e2}{e1}💫",
        f"{e1}×{e2} = {e1}🤝{e2}",
    ]
    text = (
        f"🧪 **Emoji Mix / ইমোজি মিক্স**\n\n"
        + "\n".join(combos)
        + f"\n\n🎨 Mixed result: {e1}{e2}{random.choice(EMOJI_LIST)}"
    )
    await message.reply_text(text)


@bot.on_message(filters.command("randomemoji"))
async def random_emoji_cmd(_, message: Message):
    picked = random.sample(EMOJI_LIST, k=min(5, len(EMOJI_LIST)))
    await message.reply_text(
        f"🎲 **Random Emoji / এলোমেলো ইমোজি:**\n\n"
        f"{'  '.join(picked)}"
    )
