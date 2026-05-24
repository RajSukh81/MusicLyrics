"""Reaction features plugin for MusicLyrics bot."""

from pyrogram import filters
from pyrogram.types import Message, ReactionTypeEmoji
from pyrogram.errors import ReactionInvalid, MessageNotModified

import random
import logging

from MusicLyrics.bot import bot

LOG = logging.getLogger(__name__)

# Actual emoji characters that Telegram supports for reactions
REACTION_EMOJIS = [
    "\U0001f44d",  # thumbs up
    "\U0001f44e",  # thumbs down
    "\u2764\ufe0f",  # red heart
    "\U0001f525",  # fire
    "\U0001f970",  # smiling face with hearts
    "\U0001f44f",  # clapping hands
    "\U0001f601",  # beaming face
    "\U0001f914",  # thinking face
    "\U0001f92f",  # exploding head
    "\U0001f631",  # face screaming
    "\U0001f92c",  # face with symbols on mouth
    "\U0001f622",  # crying face
    "\U0001f389",  # party popper
    "\U0001f929",  # star-struck
    "\U0001f92e",  # face vomiting
    "\U0001f4a9",  # pile of poo
    "\U0001f64f",  # folded hands
    "\U0001f44c",  # OK hand
    "\U0001f54a\ufe0f",  # dove
    "\U0001f921",  # clown face
    "\U0001f971",  # yawning face
    "\U0001f974",  # woozy face
    "\U0001f60d",  # heart eyes
    "\U0001f433",  # spouting whale
    "\u2764\ufe0f\u200d\U0001f525",  # heart on fire
    "\U0001f31a",  # new moon face
    "\U0001f32d",  # hot dog
    "\U0001f4af",  # hundred points
    "\U0001f923",  # rolling on floor laughing
    "\u26a1",  # high voltage
    "\U0001f34c",  # banana
    "\U0001f3c6",  # trophy
    "\U0001f494",  # broken heart
    "\U0001f928",  # face with raised eyebrow
    "\U0001f610",  # neutral face
    "\U0001f353",  # strawberry
    "\U0001f37e",  # bottle with popping cork
    "\U0001f48b",  # kiss mark
    "\U0001f608",  # smiling face with horns
    "\U0001f634",  # sleeping face
    "\U0001f62d",  # loudly crying face
    "\U0001f913",  # nerd face
    "\U0001f47b",  # ghost
    "\U0001f440",  # eyes
    "\U0001f383",  # jack-o-lantern
    "\U0001f648",  # see-no-evil monkey
    "\U0001f607",  # smiling face with halo
    "\U0001f628",  # fearful face
    "\U0001f60e",  # sunglasses
    "\U0001f618",  # face blowing a kiss
]


async def _send_reaction(client, chat_id, message_id, emoji):
    """Send a reaction with compatibility across Pyrogram versions."""
    try:
        # pyrofork / pyrogram v2 with ReactionTypeEmoji
        await client.send_reaction(
            chat_id=chat_id,
            message_id=message_id,
            emoji=[ReactionTypeEmoji(emoji=emoji)],
        )
        return True
    except TypeError:
        pass
    try:
        # Fallback: some versions accept plain emoji string
        await client.send_reaction(
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
        )
        return True
    except Exception:
        pass
    try:
        # Another fallback: reaction parameter
        await client.send_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
        return True
    except Exception as e:
        LOG.warning("All reaction methods failed: %s", e)
        raise


@bot.on_message(filters.command("react"))
async def react_cmd(client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        return await message.reply_text(
            "**Usage:**\n"
            "`/react <emoji>` -- Reply to a message\n"
            "`/react random` -- Random reaction\n"
            "`/react list` -- Available emojis"
        )

    sub = args[1].strip().lower()

    if sub == "list":
        text = "**Available Reactions:**\n\n"
        text += "  ".join(REACTION_EMOJIS)
        return await message.reply_text(text)

    if not message.reply_to_message:
        return await message.reply_text(
            "Reply to a message to react."
        )

    if sub == "random":
        emoji = random.choice(REACTION_EMOJIS)
    else:
        emoji = args[1].strip()

    try:
        await _send_reaction(
            client,
            chat_id=message.chat.id,
            message_id=message.reply_to_message.id,
            emoji=emoji,
        )
        await message.reply_text(f"Reacted with: {emoji}")
    except ReactionInvalid:
        await message.reply_text(
            f"`{emoji}` is not supported for reactions in this chat.\n"
            "Try `/react list` to see available emojis."
        )
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")


@bot.on_message(filters.command("reactall"))
async def reactall_cmd(client, message: Message):
    """React with a random emoji to the replied message."""
    if not message.reply_to_message:
        return await message.reply_text("Reply to a message to react.")

    emoji = random.choice(REACTION_EMOJIS[:20])
    try:
        await _send_reaction(
            client,
            chat_id=message.chat.id,
            message_id=message.reply_to_message.id,
            emoji=emoji,
        )
        await message.reply_text(f"Reacted with: {emoji}")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")
