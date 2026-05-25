"""AI chatbot plugin -- replies to group messages intelligently.

Uses Google Gemini API with automatic model fallback and smart
context-aware local replies when API quota is exhausted.
"""

import asyncio
import logging
import random
import re
import time
from collections import deque
from typing import Optional

import aiohttp

from pyrogram import filters
from pyrogram.types import Message
from pyrogram.enums import ChatType

from MusicLyrics.bot import bot, get_bot_info
from config import Config

LOG = logging.getLogger(__name__)

# ── Conversation history per chat ─────────────────────────────────────────────
_MAX_HISTORY = 20  # Increased for better context
_chat_histories: dict[int, deque] = {}

# ── Track recent replies to avoid repetition ──────────────────────────────────
_MAX_RECENT = 30
_recent_replies: dict[int, deque] = {}

# ── API rate limit tracking ───────────────────────────────────────────────────
# Per-model cooldown: different models have different quotas
_model_cooldown_until: dict[str, float] = {}  # model -> timestamp
_API_COOLDOWN_SECONDS = 60   # wait 60s after a 429 before retrying
_API_COOLDOWN_BACKOFF = 1.5  # multiply cooldown on repeated 429s
_model_cooldown_multiplier: dict[str, float] = {}  # model -> current multiplier
_MAX_COOLDOWN_MULTIPLIER = 4.0  # max 60*4 = 240s cooldown


def _get_history(chat_id: int) -> deque:
    if chat_id not in _chat_histories:
        _chat_histories[chat_id] = deque(maxlen=_MAX_HISTORY)
    return _chat_histories[chat_id]


def _get_recent(chat_id: int) -> deque:
    if chat_id not in _recent_replies:
        _recent_replies[chat_id] = deque(maxlen=_MAX_RECENT)
    return _recent_replies[chat_id]


# ── Context-aware reply categories ────────────────────────────────────────────
_GREETINGS = [
    "হ্যালো! কেমন আছো? আমি MusicLyrics Bot! 😊",
    "হাই! কী খবর? কিছু লাগলে বলো! 🎵",
    "স্বাগতম! আমি তোমার মিউজিক বট! কী দরকার বলো! 🎶",
    "হ্যাঁ বলো! কী সাহায্য করতে পারি? 😄",
    "নমস্কার! আজকে কী শুনবে? /play দাও! 🎧",
]

_MUSIC_RESPONSES = [
    "গান শুনতে চাও? /play দিয়ে গানের নাম লেখো! 🎵",
    "অবশ্যই! /play <গানের নাম> দিয়ে চেষ্টা করো! 🎶",
    "ভিডিও সহ শুনতে চাইলে /vplay ব্যবহার করো! 🎬",
    "গান ডাউনলোড করতে /song <নাম> দাও! 📥",
    "/play দাও, আমি তোমার জন্য বাজাবো! 🎤",
]

_FUN_RESPONSES = [
    "গেম খেলবে? /quiz বা /truth চেষ্টা করো! 🎮",
    "বোর হচ্ছো? /ttt দিয়ে টিক-ট্যাক-টো খেলো! 🎯",
    "/dare দাও, মজা করো! 😂",
    "/flip দিয়ে কয়েন টস করো! 🪙",
    "/dice দিয়ে ডাইস গড়াও! 🎲",
]

_THANKS_RESPONSES = [
    "ধন্যবাদ তোমাকেও! 😊",
    "স্বাগতম! আরো কিছু লাগলে বলো! 💖",
    "কোনো ব্যাপার না! আমি তো তোমার জন্যই! 🤖",
    "আনন্দিত হলাম সাহায্য করতে পেরে! 🙌",
    "ইউ আর ওয়েলকাম! 🎵",
]

_GENERAL_REPLIES = [
    "হ্যাঁ ভাই, বলো কী হেল্প লাগবে? 😄",
    "আমি শুনছি! কী দরকার বলো? 🎵",
    "বলো বলো, আমি আছি! 😊",
    "কী খবর? কিছু লাগলে বলো! 🎶",
    "হুম, বুঝেছি! আর কিছু? 🤔",
    "ঠিক আছে ভাই! 👍",
    "আচ্ছা আচ্ছা, তারপর? 😏",
    "ওকে বস! কিছু দরকার হলে বলো! 💪",
    "তুমি তো দারুণ! 🔥",
    "আমি তোমার বট, যা বলবে করব! 🤖",
    "কিছু জানতে চাইলে জিজ্ঞেস করো! 📖",
    "আমি সবসময় তোমার জন্য আছি! 💖",
    "হ্যাঁ ভাই, আমি রেডি! কী করবো বলো! 🚀",
    "দারুণ! আরো কিছু বলো! 🌟",
    "আমি AI বট, কিন্তু তোমার ভালো বন্ধু! 🤝",
    "ওহো! সেটা তো ইন্টারেস্টিং! 🧐",
    "চলো কিছু মজার কাজ করি! 🎉",
    "কোনো প্রশ্ন থাকলে নির্দ্বিধায় জিজ্ঞেস করো! ✋",
    "আমি ২৪/৭ অনলাইন আছি তোমার জন্য! ⏰",
    "আজকে কেমন আছো? আমি তো সবসময়ই ভালো! 😎",
    "তোমার সাথে কথা বলে মজা লাগছে! 😁",
    "আরে বাহ! তুমি তো ভালোই বলেছো! 👏",
    "একটু অপেক্ষা করো, ভাবছি... 🤔💭",
    "ও আচ্ছা! বুঝলাম বুঝলাম! 💡",
    "হাহা সেটা মজার ছিল! 😆",
    "তোমার জন্য কী করতে পারি আজকে? 🎁",
    "সুন্দর কথা বলেছো! 💐",
    "মজা করছো নাকি? 😂",
    "হা হা, ভালো বলেছো! 😄",
    "তুমি ভালো মানুষ! আমি জানি! 😊",
]

_EMOJI_REACTIONS = [
    "\U0001f44d", "\u2764\ufe0f", "\U0001f525", "\U0001f60d",
    "\U0001f929", "\U0001f44f", "\U0001f601", "\U0001f60e",
]

# ── Keyword patterns for smart replies ────────────────────────────────────────
_GREETING_KEYWORDS = re.compile(
    r"\b(hi|hello|hey|হাই|হ্যালো|হেলো|স্বাগতম|নমস্কার|সুপ্রভাত|শুভ|কেমন আছ|কি খবর|কি অবস্থা)\b",
    re.IGNORECASE,
)
_MUSIC_KEYWORDS = re.compile(
    r"\b(গান|music|song|play|বাজা|শুনব|শুনতে|গানের|মিউজিক|ভিডিও|video|audio|অডিও)\b",
    re.IGNORECASE,
)
_FUN_KEYWORDS = re.compile(
    r"\b(game|গেম|খেল|মজা|fun|বোর|bore|quiz|truth|dare|খেলা|খেলব)\b",
    re.IGNORECASE,
)
_THANKS_KEYWORDS = re.compile(
    r"\b(ধন্যবাদ|thanks|thank|থ্যাংক|tnx|thx|ty|শুকরিয়া)\b",
    re.IGNORECASE,
)


def _smart_reply(text: str, chat_id: int) -> str:
    """Generate context-aware reply based on keyword matching."""
    recent = _get_recent(chat_id)

    # Detect category
    if _GREETING_KEYWORDS.search(text):
        pool = _GREETINGS
    elif _MUSIC_KEYWORDS.search(text):
        pool = _MUSIC_RESPONSES
    elif _FUN_KEYWORDS.search(text):
        pool = _FUN_RESPONSES
    elif _THANKS_KEYWORDS.search(text):
        pool = _THANKS_RESPONSES
    else:
        pool = _GENERAL_REPLIES

    # Pick non-repeating from the category
    available = [r for r in pool if r not in recent]
    if not available:
        # Try general pool
        available = [r for r in _GENERAL_REPLIES if r not in recent]
    if not available:
        recent.clear()
        available = pool

    chosen = random.choice(available)
    recent.append(chosen)
    return chosen


# ── Gemini API models (ordered by preference) ────────────────────────────────
_GEMINI_MODELS = [
    "gemini-2.5-flash",            # Newest, best quality
    "gemini-2.5-flash-lite",       # Lighter, higher quota
    "gemini-2.0-flash-lite",       # Highest free quota
    "gemini-2.0-flash",            # Standard
    "gemini-1.5-flash",            # Fallback
]

# ── Enhanced system prompt for better AI responses ────────────────────────────
_SYSTEM_PROMPT = (
    "You are MusicLyrics Bot — a witty, fun, and helpful Telegram music bot. "
    "You chat naturally like a real friend in Bengali (বাংলা) or English, "
    "matching whatever language the user writes in.\n\n"
    "Guidelines:\n"
    "- Keep replies concise (1-4 sentences) but meaningful and engaging\n"
    "- Be warm, witty, and use casual tone — like chatting with a friend\n"
    "- Use relevant emojis naturally but don't overdo it\n"
    "- NEVER repeat your previous replies — always say something new\n"
    "- If asked about music/songs, suggest /play, /vplay, /song commands\n"
    "- If asked about games, mention /quiz, /truth, /dare, /ttt, /flip\n"
    "- If asked what you can do, give a brief overview of your features\n"
    "- Answer factual questions accurately when you know the answer\n"
    "- For questions you don't know, be honest but friendly about it\n"
    "- If someone is rude, stay polite but firm\n"
    "- If someone shares feelings, be empathetic and supportive\n"
    "- You can joke, be sarcastic (lightheartedly), and have personality\n"
    "- You are created by RajSukh (Owner), support group link available via /start\n"
    "- Your features: music streaming in VC (audio + video), song download, "
    "lyrics, games, group security (ban/mute/warn), AI chat, translation, "
    "sticker tools, and more\n"
)


async def _ai_response(text: str, chat_id: int = 0, user_name: str = "") -> str:
    """Get AI response — tries Gemini API with model fallback,
    then uses smart context-aware local replies."""

    if not Config.AI_API_KEY:
        return _smart_reply(text, chat_id)

    history = _get_history(chat_id)

    # Try each Gemini model (skip those in cooldown)
    for model in _GEMINI_MODELS:
        if time.time() < _model_cooldown_until.get(model, 0):
            LOG.debug("Model %s in cooldown, skipping", model)
            continue
        result = await _try_gemini(model, text, chat_id, user_name, history)
        if result:
            return result

    # All models failed or in cooldown, use smart reply
    return _smart_reply(text, chat_id)


async def _try_gemini(
    model: str, text: str, chat_id: int,
    user_name: str, history: deque
) -> Optional[str]:
    """Try a single Gemini model. Returns reply or None."""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{model}:generateContent"
    )

    contents = []
    for role, msg in history:
        contents.append({"role": role, "parts": [{"text": msg}]})

    user_text = f"[User: {user_name}] {text}" if user_name else text
    contents.append({"role": "user", "parts": [{"text": user_text}]})

    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 300,
            "temperature": 0.9,
            "topP": 0.95,
        },
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                headers={"x-goog-api-key": Config.AI_API_KEY},
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
                                history.append(("user", text))
                                history.append(("model", reply))
                                # Reset cooldown multiplier on success
                                _model_cooldown_multiplier[model] = 1.0
                                return reply
                elif resp.status == 429:
                    # Quota exceeded — set per-model cooldown with backoff
                    multiplier = _model_cooldown_multiplier.get(model, 1.0)
                    cooldown = _API_COOLDOWN_SECONDS * multiplier
                    _model_cooldown_until[model] = time.time() + cooldown
                    _model_cooldown_multiplier[model] = min(
                        multiplier * _API_COOLDOWN_BACKOFF,
                        _MAX_COOLDOWN_MULTIPLIER,
                    )
                    LOG.warning(
                        "Gemini %s quota exceeded (429). Cooldown %.0fs.",
                        model, cooldown,
                    )
                    return None
                else:
                    LOG.warning("Gemini %s HTTP %d", model, resp.status)
                    return None
    except asyncio.TimeoutError:
        LOG.warning("Gemini %s timeout", model)
    except Exception as e:
        LOG.warning("Gemini %s error: %s", model, e)

    return None


# ── Reactions ─────────────────────────────────────────────────────────────────

async def _try_react(client, message: Message):
    try:
        emoji = random.choice(_EMOJI_REACTIONS)
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
        pass


# ── Filters (using cached get_bot_info to avoid FloodWait) ───────────────────

async def _is_reply_to_bot(_, client, message: Message) -> bool:
    if not message.text or message.text.startswith("/"):
        return False
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return False
    try:
        me = await get_bot_info()
        return message.reply_to_message.from_user.id == me.id
    except Exception:
        return False

_reply_to_bot_filter = filters.create(_is_reply_to_bot, name="ReplyToBotFilter")


async def _is_bot_mentioned(_, client, message: Message) -> bool:
    if not message.text or message.text.startswith("/"):
        return False
    try:
        me = await get_bot_info()
        return f"@{me.username}" in (message.text or "")
    except Exception:
        return False

_bot_mentioned_filter = filters.create(_is_bot_mentioned, name="BotMentionedFilter")


def _get_user_name(message: Message) -> str:
    if message.from_user:
        return message.from_user.first_name or ""
    return ""


# ── Handlers ──────────────────────────────────────────────────────────────────

@bot.on_message(filters.group & _reply_to_bot_filter, group=50)
async def ai_reply_when_replied(client, message: Message):
    try:
        await _try_react(client, message)
        user_text = message.text or ""
        if not user_text.strip():
            return
        response = await _ai_response(
            user_text, chat_id=message.chat.id,
            user_name=_get_user_name(message),
        )
        if response:
            await message.reply_text(response)
    except Exception:
        LOG.exception("AI reply error")


@bot.on_message(filters.group & _bot_mentioned_filter, group=51)
async def ai_reply_when_mentioned(client, message: Message):
    try:
        me = await get_bot_info()
        clean_text = (message.text or "").replace(f"@{me.username}", "").strip()
        if not clean_text:
            clean_text = "hi"
        await _try_react(client, message)
        response = await _ai_response(
            clean_text, chat_id=message.chat.id,
            user_name=_get_user_name(message),
        )
        if response:
            await message.reply_text(response)
    except Exception:
        LOG.exception("AI mention reply error")


@bot.on_message(filters.private & filters.text, group=52)
async def ai_reply_private(client, message: Message):
    user_text = message.text or ""
    if not user_text.strip() or user_text.startswith("/"):
        return
    try:
        await _try_react(client, message)
        response = await _ai_response(
            user_text, chat_id=message.chat.id,
            user_name=_get_user_name(message),
        )
        if response:
            await message.reply_text(response)
    except Exception:
        LOG.exception("AI private reply error")
