"""AI chatbot plugin -- replies to group messages intelligently."""

import logging
import random
import aiohttp

from pyrogram import filters
from pyrogram.types import Message
from pyrogram.enums import ChatType

from MusicLyrics.bot import bot
from config import Config

LOG = logging.getLogger(__name__)

# Simple AI personality responses when no API key is configured
_BANGLA_REPLIES = [
    "হ্যাঁ ভাই, বলো কী হেল্প লাগবে? 😄",
    "আমি শুনছি! কী দরকার বলো? 🎵",
    "বলো বলো, আমি আছি! 😊",
    "কী খবর? কিছু লাগলে বলো! 🎶",
    "হুম, বুঝেছি! আর কিছু? 🤔",
    "ঠিক আছে ভাই! 👍",
    "মজা করছো নাকি? 😂",
    "আচ্ছা আচ্ছা, তারপর? 😏",
    "ওকে বস! কিছু দরকার হলে বলো! 💪",
    "হা হা, ভালো বলেছো! 😄",
    "তুমি তো দারুণ! 🔥",
    "আমি তোমার বট, যা বলবে করব! 🤖",
    "গান শুনবে? /play দিয়ে গানের নাম লেখো! 🎵",
    "বোর হচ্ছো? /quiz বা /truth দিয়ে গেম খেলো! 🎮",
    "কিছু জানতে চাইলে জিজ্ঞেস করো! 📖",
    "আমি সবসময় তোমার জন্য আছি! 💖",
    "কি বলো? আমি তো বট, কিন্তু তোমার কথা শুনি! 🎧",
    "গান ছাড়ো! /play দাও! 🎶",
    "তুমি ভালো মানুষ! আমি জানি! 😊",
    "হ্যাঁ রে, বলো কী চাই? 🙌",
]

_EMOJI_REACTIONS = [
    "\U0001f44d", "\u2764\ufe0f", "\U0001f525", "\U0001f60d",
    "\U0001f929", "\U0001f44f", "\U0001f601", "\U0001f60e",
]


async def _ai_response(text: str) -> str:
    """Get AI response using configured API, or fallback to simple replies."""
    if not Config.AI_API_KEY:
        return random.choice(_BANGLA_REPLIES)

    # Try Google Gemini API
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.0-flash:generateContent"
            f"?key={Config.AI_API_KEY}"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "You are MusicLyrics Bot, a fun and helpful Telegram group bot. "
                                "Reply in the same language the user writes in (Bengali/Bangla or English). "
                                "Keep replies short (1-3 sentences), friendly, and casual. "
                                "You can suggest music commands like /play, /vplay, /song. "
                                "Be witty and entertaining.\n\n"
                                f"User says: {text}"
                            )
                        }
                    ]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 150,
                "temperature": 0.8,
            },
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
    except Exception as e:
        LOG.warning("AI API call failed: %s", e)

    return random.choice(_BANGLA_REPLIES)


async def _try_react(client, message: Message):
    """Try to add a random emoji reaction to the message."""
    try:
        emoji = random.choice(_EMOJI_REACTIONS)
        # Try multiple methods for compatibility
        try:
            from pyrogram.types import ReactionTypeEmoji
            await client.send_reaction(
                chat_id=message.chat.id,
                message_id=message.id,
                emoji=[ReactionTypeEmoji(emoji=emoji)],
            )
            return
        except (ImportError, TypeError, AttributeError):
            pass
        try:
            await client.send_reaction(
                chat_id=message.chat.id,
                message_id=message.id,
                emoji=emoji,
            )
        except Exception:
            pass
    except Exception:
        pass  # Reactions may not be available


# -- Custom filter: check if message is a reply to the bot --
async def _is_reply_to_bot(_, client, message: Message) -> bool:
    """Return True if message is a text reply to the bot's own message."""
    if not message.text:
        return False
    if message.text.startswith("/"):
        return False
    if not message.reply_to_message:
        return False
    if not message.reply_to_message.from_user:
        return False
    try:
        me = await client.get_me()
        return message.reply_to_message.from_user.id == me.id
    except Exception:
        return False

_reply_to_bot_filter = filters.create(_is_reply_to_bot, name="ReplyToBotFilter")


# -- Custom filter: check if bot is @mentioned --
async def _is_bot_mentioned(_, client, message: Message) -> bool:
    """Return True if bot's @username appears in the message text."""
    if not message.text:
        return False
    if message.text.startswith("/"):
        return False
    try:
        me = await client.get_me()
        return f"@{me.username}" in (message.text or "")
    except Exception:
        return False

_bot_mentioned_filter = filters.create(_is_bot_mentioned, name="BotMentionedFilter")


@bot.on_message(filters.group & _reply_to_bot_filter, group=50)
async def ai_reply_when_replied(client, message: Message):
    """Respond when someone replies to the bot's message in a group."""
    try:
        await _try_react(client, message)
        user_text = message.text or ""
        if not user_text.strip():
            return
        response = await _ai_response(user_text)
        if response:
            await message.reply_text(response)
    except Exception:
        LOG.exception("AI reply error")


@bot.on_message(filters.group & _bot_mentioned_filter, group=51)
async def ai_reply_when_mentioned(client, message: Message):
    """Respond when the bot is @mentioned in a group."""
    try:
        me = await client.get_me()
        clean_text = (message.text or "").replace(f"@{me.username}", "").strip()
        if not clean_text:
            clean_text = "hi"
        await _try_react(client, message)
        response = await _ai_response(clean_text)
        if response:
            await message.reply_text(response)
    except Exception:
        LOG.exception("AI mention reply error")


@bot.on_message(filters.private & filters.text, group=52)
async def ai_reply_private(client, message: Message):
    """Respond to non-command text messages in private chat."""
    user_text = message.text or ""
    if not user_text.strip() or user_text.startswith("/"):
        return
    try:
        await _try_react(client, message)
        response = await _ai_response(user_text)
        if response:
            await message.reply_text(response)
    except Exception:
        LOG.exception("AI private reply error")
