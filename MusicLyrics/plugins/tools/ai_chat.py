"""AI chatbot plugin -- replies to messages intelligently.

Uses Google Gemini API with automatic model fallback.
Properly answers all kinds of questions — factual, conversational, etc.
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
_MAX_HISTORY = 20
_chat_histories: dict[int, deque] = {}

# ── API rate limit tracking ───────────────────────────────────────────────────
_model_cooldown_until: dict[str, float] = {}
_API_COOLDOWN_SECONDS = 60
_API_COOLDOWN_BACKOFF = 1.5
_model_cooldown_multiplier: dict[str, float] = {}
_MAX_COOLDOWN_MULTIPLIER = 4.0

_EMOJI_REACTIONS = [
    "\U0001f44d", "\u2764\ufe0f", "\U0001f525", "\U0001f60d",
    "\U0001f929", "\U0001f44f", "\U0001f601", "\U0001f60e",
]


def _get_history(chat_id: int) -> deque:
    if chat_id not in _chat_histories:
        _chat_histories[chat_id] = deque(maxlen=_MAX_HISTORY)
    return _chat_histories[chat_id]


# ── Gemini API models (ordered by preference) ────────────────────────────────
# Use stable model IDs that are guaranteed to exist on Google's API.
# The API accepts both versioned and latest aliases.
_GEMINI_MODELS = [
    "gemini-2.0-flash-lite",       # Highest free-tier quota, fast
    "gemini-2.0-flash",            # Good quality + speed balance
    "gemini-1.5-flash",            # Reliable stable fallback
    "gemini-1.5-flash-8b",         # Smallest, highest quota fallback
]

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are MusicLyrics Bot — a smart, friendly, and helpful Telegram bot.\n\n"
    "IMPORTANT RULES:\n"
    "1. You MUST answer ALL questions properly and accurately. If someone asks "
    "'What is Python?', answer it correctly. If someone asks a math question, "
    "solve it. If someone asks about history, science, or anything, give the "
    "correct answer. You are a KNOWLEDGEABLE assistant.\n"
    "2. Match the user's language — if they write in Bengali (বাংলা), reply in "
    "Bengali. If English, reply in English. If mixed, reply in mixed.\n"
    "3. Keep replies concise (1-5 sentences) but COMPLETE and CORRECT.\n"
    "4. Be warm and friendly — like a smart friend chatting.\n"
    "5. Use emojis naturally but sparingly.\n"
    "6. If asked about your features or commands, mention:\n"
    "   - /play <song> — Play music in voice chat\n"
    "   - /vplay <song> — Play video in voice chat\n"
    "   - /song <query> — Download song\n"
    "   - /pause, /resume, /skip, /stop — Playback controls\n"
    "   - /quiz, /truth, /dare, /ttt, /flip, /dice — Games\n"
    "   - /tr, /tts, /sticker, /info — Tools\n"
    "7. You are created by RajSukh (Owner).\n"
    "8. NEVER refuse to answer a question. Always try your best.\n"
    "9. For questions you genuinely don't know, say so honestly but suggest "
    "where to find the answer.\n"
    "10. Do NOT give generic filler responses. Every reply should be meaningful.\n"
)


async def _ai_response(text: str, chat_id: int = 0, user_name: str = "") -> str:
    """Get AI response from Gemini API with model fallback."""

    if not Config.AI_API_KEY:
        LOG.warning("AI_API_KEY not set — cannot generate AI response")
        return "AI API key সেট করা নেই। Owner-কে বলো AI_API_KEY সেট করতে।"

    history = _get_history(chat_id)

    # Try each Gemini model (skip those in cooldown)
    last_error = ""
    for model in _GEMINI_MODELS:
        if time.time() < _model_cooldown_until.get(model, 0):
            LOG.debug("Model %s in cooldown, skipping", model)
            continue
        result, error = await _try_gemini(model, text, chat_id, user_name, history)
        if result:
            return result
        if error:
            last_error = error

    # All models failed
    LOG.error("All Gemini models failed for chat %s. Last error: %s", chat_id, last_error)
    return (
        "দুঃখিত, এই মুহূর্তে AI সার্ভারে সমস্যা হচ্ছে। "
        "একটু পরে আবার চেষ্টা করো! 🙏"
    )


async def _try_gemini(
    model: str, text: str, chat_id: int,
    user_name: str, history: deque
) -> tuple[Optional[str], str]:
    """Try a single Gemini model.

    Returns (reply, error_msg). reply is None on failure.
    """

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{model}:generateContent"
    )

    # Build conversation history
    contents = []
    for role, msg in history:
        contents.append({"role": role, "parts": [{"text": msg}]})

    # Add current user message
    user_text = text
    if user_name:
        user_text = f"[{user_name}]: {text}"
    contents.append({"role": "user", "parts": [{"text": user_text}]})

    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 500,
            "temperature": 0.8,
            "topP": 0.95,
            "topK": 40,
        },
        # Safety settings — allow most content for natural conversation
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": Config.AI_API_KEY,
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        # Check if response was blocked by safety
                        finish_reason = candidates[0].get("finishReason", "")
                        if finish_reason == "SAFETY":
                            LOG.warning("Gemini %s: response blocked by safety filter", model)
                            return None, "safety_blocked"

                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            reply = parts[0].get("text", "").strip()
                            if reply:
                                # Save to history
                                history.append(("user", text))
                                history.append(("model", reply))
                                _model_cooldown_multiplier[model] = 1.0
                                LOG.info("Gemini %s replied for chat %s (%d chars)",
                                         model, chat_id, len(reply))
                                return reply, ""
                    # No valid response in candidates
                    LOG.warning("Gemini %s: empty candidates for chat %s. Response: %s",
                                model, chat_id, str(data)[:300])
                    return None, "empty_response"

                elif resp.status == 429:
                    multiplier = _model_cooldown_multiplier.get(model, 1.0)
                    cooldown = _API_COOLDOWN_SECONDS * multiplier
                    _model_cooldown_until[model] = time.time() + cooldown
                    _model_cooldown_multiplier[model] = min(
                        multiplier * _API_COOLDOWN_BACKOFF,
                        _MAX_COOLDOWN_MULTIPLIER,
                    )
                    LOG.warning("Gemini %s: 429 quota exceeded. Cooldown %.0fs.", model, cooldown)
                    return None, "rate_limited"

                elif resp.status == 404:
                    body = await resp.text()
                    LOG.error("Gemini %s: 404 NOT FOUND — model name may be invalid. Body: %s",
                              model, body[:200])
                    # Permanently cooldown invalid models for 1 hour
                    _model_cooldown_until[model] = time.time() + 3600
                    return None, f"model_not_found:{model}"

                elif resp.status in (400, 403):
                    body = await resp.text()
                    LOG.error("Gemini %s: HTTP %d — API key or request issue. Body: %s",
                              model, resp.status, body[:300])
                    return None, f"http_{resp.status}"

                else:
                    body = await resp.text()
                    LOG.warning("Gemini %s: HTTP %d. Body: %s", model, resp.status, body[:200])
                    return None, f"http_{resp.status}"

    except asyncio.TimeoutError:
        LOG.warning("Gemini %s: timeout (20s)", model)
        return None, "timeout"
    except Exception as e:
        LOG.warning("Gemini %s: exception: %s", model, e)
        return None, str(e)


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
