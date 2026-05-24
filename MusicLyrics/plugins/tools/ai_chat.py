"""AI chatbot plugin -- replies to group messages intelligently."""

import asyncio
import logging
import random
import time
import hashlib
from collections import deque
from typing import Optional

import aiohttp

from pyrogram import filters
from pyrogram.types import Message
from pyrogram.enums import ChatType

from MusicLyrics.bot import bot
from config import Config

LOG = logging.getLogger(__name__)

# ── Conversation history per chat (last N exchanges) ──────────────────────────
_MAX_HISTORY = 10
_chat_histories: dict[int, deque] = {}

# ── Track recent replies to avoid repetition ──────────────────────────────────
_MAX_RECENT = 15
_recent_replies: dict[int, deque] = {}


def _get_history(chat_id: int) -> deque:
    if chat_id not in _chat_histories:
        _chat_histories[chat_id] = deque(maxlen=_MAX_HISTORY)
    return _chat_histories[chat_id]


def _get_recent(chat_id: int) -> deque:
    if chat_id not in _recent_replies:
        _recent_replies[chat_id] = deque(maxlen=_MAX_RECENT)
    return _recent_replies[chat_id]


# ── Bangla fallback replies — large set to reduce repetition ──────────────────
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
    "আজকে কেমন আছো? আমি তো সবসময়ই ভালো! 😎",
    "তোমার সাথে কথা বলে মজা লাগছে! 😁",
    "আরে বাহ! তুমি তো ভালোই বলেছো! 👏",
    "একটু অপেক্ষা করো, ভাবছি... 🤔💭",
    "ও আচ্ছা! বুঝলাম বুঝলাম! 💡",
    "তুমি কি গান পছন্দ করো? /play চেষ্টা করো! 🎤",
    "ভিডিও দেখতে চাও? /vplay ট্রাই করো! 🎬",
    "হ্যাঁ ভাই, আমি রেডি! কী করবো বলো! 🚀",
    "দারুণ! আরো কিছু বলো! 🌟",
    "আমি AI বট, কিন্তু তোমার ভালো বন্ধু! 🤝",
    "সুন্দর কথা বলেছো! 💐",
    "ওহো! সেটা তো ইন্টারেস্টিং! 🧐",
    "চলো কিছু মজার কাজ করি! 🎉",
    "তুমি চাইলে /song দিয়ে গান ডাউনলোড করতে পারো! 📥",
    "কোনো প্রশ্ন থাকলে নির্দ্বিধায় জিজ্ঞেস করো! ✋",
    "আমি ২৪/৭ অনলাইন আছি তোমার জন্য! ⏰",
    "তুমি কি জানো আমি গেমও খেলাতে পারি? /ttt চেষ্টা করো! 🎯",
    "হাহা সেটা মজার ছিল! 😆",
    "আচ্ছা ঠিক আছে, পরে আবার কথা হবে! 👋",
    "তোমার জন্য কী করতে পারি আজকে? 🎁",
]

_EMOJI_REACTIONS = [
    "\U0001f44d", "\u2764\ufe0f", "\U0001f525", "\U0001f60d",
    "\U0001f929", "\U0001f44f", "\U0001f601", "\U0001f60e",
]


def _pick_non_repeating(chat_id: int) -> str:
    """Pick a reply from _BANGLA_REPLIES that hasn't been used recently."""
    recent = _get_recent(chat_id)
    available = [r for r in _BANGLA_REPLIES if r not in recent]
    if not available:
        # All used recently, reset
        recent.clear()
        available = _BANGLA_REPLIES
    chosen = random.choice(available)
    recent.append(chosen)
    return chosen


async def _ai_response(text: str, chat_id: int = 0, user_name: str = "") -> str:
    """Get AI response using configured API, or fallback to smart replies."""
    if not Config.AI_API_KEY:
        return _pick_non_repeating(chat_id)

    # Build conversation context from history
    history = _get_history(chat_id)

    # Try Google Gemini API
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.0-flash:generateContent"
            f"?key={Config.AI_API_KEY}"
        )

        # Build conversation messages
        contents = []

        # System instruction via first user message
        system_prompt = (
            "You are MusicLyrics Bot, a fun and helpful Telegram group bot. "
            "Reply in the same language the user writes in (Bengali/Bangla or English). "
            "Keep replies short (1-3 sentences), friendly, and casual. "
            "You can suggest music commands like /play, /vplay, /song when relevant. "
            "Be witty, entertaining, and vary your responses. "
            "Never repeat the same reply twice. "
            "If someone asks about your capabilities, mention music streaming, "
            "games (/quiz, /truth, /dare, /ttt), and other features."
        )

        # Add conversation history for context
        for role, msg in history:
            contents.append({
                "role": role,
                "parts": [{"text": msg}],
            })

        # Add current message
        user_text = f"{text}"
        if user_name:
            user_text = f"[User: {user_name}] {text}"

        contents.append({
            "role": "user",
            "parts": [{"text": user_text}],
        })

        payload = {
            "system_instruction": {
                "parts": [{"text": system_prompt}],
            },
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 200,
                "temperature": 0.9,
                "topP": 0.95,
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            reply = parts[0].get("text", "").strip()
                            if reply:
                                # Save to history
                                history.append(("user", text))
                                history.append(("model", reply))
                                return reply
                else:
                    error_text = await resp.text()
                    LOG.warning(
                        "Gemini API error (status %d): %s",
                        resp.status, error_text[:300],
                    )
    except asyncio.TimeoutError:
        LOG.warning("Gemini API timeout for chat %s", chat_id)
    except Exception as e:
        LOG.warning("AI API call failed: %s", e)

    return _pick_non_repeating(chat_id)


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


def _get_user_name(message: Message) -> str:
    """Extract a display name from the message sender."""
    if message.from_user:
        return message.from_user.first_name or ""
    return ""


@bot.on_message(filters.group & _reply_to_bot_filter, group=50)
async def ai_reply_when_replied(client, message: Message):
    """Respond when someone replies to the bot's message in a group."""
    try:
        await _try_react(client, message)
        user_text = message.text or ""
        if not user_text.strip():
            return
        response = await _ai_response(
            user_text,
            chat_id=message.chat.id,
            user_name=_get_user_name(message),
        )
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
        response = await _ai_response(
            clean_text,
            chat_id=message.chat.id,
            user_name=_get_user_name(message),
        )
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
        response = await _ai_response(
            user_text,
            chat_id=message.chat.id,
            user_name=_get_user_name(message),
        )
        if response:
            await message.reply_text(response)
    except Exception:
        LOG.exception("AI private reply error")
