"""Playback control commands and inline callback handlers."""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from MusicLyrics.bot import bot
from MusicLyrics.helpers.filters import not_edited
from MusicLyrics.plugins.play.queue import (
    get_queue,
    get_current,
    clear_queue,
    skip_queue,
    toggle_loop,
    shuffle_queue,
    format_duration,
    get_chat_queue,
)
from MusicLyrics.plugins.play.stream import (
    pause_stream,
    resume_stream,
    seek_stream,
    set_volume,
    leave_voice_chat,
    stream_audio,
    stream_video,
    is_active,
)
from MusicLyrics.utils.autodelete import (
    auto_delete_service,
    auto_delete_playing,
    auto_delete_cmd,
)

LOG = logging.getLogger(__name__)

def _control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏸ Pause", callback_data="ctl_pause"),
                InlineKeyboardButton("▶️ Resume", callback_data="ctl_resume"),
                InlineKeyboardButton("⏭ Skip", callback_data="ctl_skip"),
            ],
            [
                InlineKeyboardButton("⏹ Stop", callback_data="ctl_stop"),
                InlineKeyboardButton("📜 Queue", callback_data="ctl_queue"),
                InlineKeyboardButton("🔁 Loop", callback_data="ctl_loop"),
            ],
        ]
    )


# ── /pause ────────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command("pause") & not_edited)
async def pause_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not is_active(chat_id):
        reply = await message.reply_text("❌ কিছু চলছে না এখন।")
        await auto_delete_service(message, reply)
        return
    ok = await pause_stream(chat_id)
    if ok:
        reply = await message.reply_text("⏸ **Paused!**\nResume করতে `/resume` দিন।")
    else:
        reply = await message.reply_text("❌ Pause করা যায়নি।")
    await auto_delete_service(message, reply)


# ── /resume ───────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command("resume") & not_edited)
async def resume_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not is_active(chat_id):
        reply = await message.reply_text("❌ কিছু চলছে না এখন।")
        await auto_delete_service(message, reply)
        return
    ok = await resume_stream(chat_id)
    if ok:
        reply = await message.reply_text("▶️ **Resumed!**")
    else:
        reply = await message.reply_text("❌ Resume করা যায়নি।")
    await auto_delete_service(message, reply)


# ── /skip | /next ─────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command(["skip", "next"]) & not_edited)
async def skip_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not is_active(chat_id):
        reply = await message.reply_text("❌ কিছু চলছে না এখন।")
        await auto_delete_service(message, reply)
        return

    next_item = await skip_queue(chat_id)
    if next_item is None:
        await leave_voice_chat(chat_id)
        reply = await message.reply_text("⏹ **Queue শেষ।** Voice chat থেকে বের হয়ে গেছি।")
        await auto_delete_service(message, reply)
        return

    try:
        if next_item.stream_type == "video":
            await stream_video(chat_id, next_item.media_path,
                               title=next_item.title)
        else:
            await stream_audio(chat_id, next_item.media_path,
                               title=next_item.title)
        dur = format_duration(next_item.duration)
        reply = await message.reply_text(
            f"⏭ **Skipped!**\n\n"
            f"**▶️ এখন চলছে:** {next_item.title}\n"
            f"**⏱ সময়:** {dur}\n"
            f"**👤 অনুরোধকারী:** {next_item.requester}",
            reply_markup=_control_keyboard(),
        )
        await auto_delete_playing(message, reply)
    except Exception:
        LOG.exception("Skip failed in %s", chat_id)
        reply = await message.reply_text("❌ পরের গানে যেতে সমস্যা হয়েছে।")
        await auto_delete_service(message, reply)


# ── /stop | /end ─────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command(["stop", "end"]) & not_edited)
async def stop_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not is_active(chat_id):
        reply = await message.reply_text("❌ কিছু চলছে না এখন।")
        await auto_delete_service(message, reply)
        return
    await leave_voice_chat(chat_id)
    reply = await message.reply_text(
        "⏹ **Stopped!**\n"
        "Queue clear করে voice chat থেকে বের হয়ে গেছি।"
    )
    await auto_delete_service(message, reply)


# ── /seek <seconds> ────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command("seek") & not_edited)
async def seek_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not is_active(chat_id):
        reply = await message.reply_text("❌ কিছু চলছে না এখন।")
        await auto_delete_service(message, reply)
        return
    if len(message.command) < 2:
        reply = await message.reply_text("**Usage:** `/seek <seconds>`")
        await auto_delete_service(message, reply)
        return
    try:
        seconds = int(message.command[1])
    except ValueError:
        reply = await message.reply_text("❌ সঠিক সংখ্যা দিন। Example: `/seek 30`")
        await auto_delete_service(message, reply)
        return
    ok = await seek_stream(chat_id, seconds)
    if ok:
        reply = await message.reply_text(f"⏩ **{seconds}s** এ seek করা হয়েছে।")
    else:
        reply = await message.reply_text(
            "❌ Seek এখনো এই version-এ fully supported নয়।"
        )
    await auto_delete_service(message, reply)


# ── /volume <1-200> ────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command(["volume", "vol"]) & not_edited)
async def volume_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not is_active(chat_id):
        reply = await message.reply_text("❌ কিছু চলছে না এখন।")
        await auto_delete_service(message, reply)
        return
    if len(message.command) < 2:
        reply = await message.reply_text("**Usage:** `/volume <1-200>`")
        await auto_delete_service(message, reply)
        return
    try:
        vol = int(message.command[1])
    except ValueError:
        reply = await message.reply_text("❌ সঠিক সংখ্যা দিন (1-200)।")
        await auto_delete_service(message, reply)
        return
    if not 1 <= vol <= 200:
        reply = await message.reply_text("❌ Volume 1 থেকে 200 এর মধ্যে হতে হবে।")
        await auto_delete_service(message, reply)
        return
    ok = await set_volume(chat_id, vol)
    if ok:
        reply = await message.reply_text(f"🔊 Volume **{vol}%** সেট হয়েছে।")
    else:
        reply = await message.reply_text("❌ Volume পরিবর্তন করা যায়নি।")
    await auto_delete_service(message, reply)


# ── /queue ───────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command("queue") & not_edited)
async def queue_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    items = await get_queue(chat_id)
    if not items:
        reply = await message.reply_text("📜 Queue খালি আছে।")
        await auto_delete_service(message, reply)
        return
    cq = await get_chat_queue(chat_id)
    lines = ["**📜 বর্তমান Queue:**\n"]
    for i, item in enumerate(items):
        marker = "▶️" if i == cq.current_index else f"{i + 1}."
        dur = format_duration(item.duration)
        kind = "🎬" if item.stream_type == "video" else "🎵"
        lines.append(f"{marker} {kind} **{item.title}** [{dur}] — {item.requester}")
    loop_status = "🔁 Loop: চালু" if cq.loop_mode else "🔁 Loop: বন্ধ"
    lines.append(f"\n{loop_status}")
    reply = await message.reply_text("\n".join(lines))
    await auto_delete_playing(message, reply)


# ── /nowplaying | /np ────────────────────────────────────────────────────────

@bot.on_message(filters.command(["nowplaying", "np"]) & not_edited)
async def nowplaying_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    current = await get_current(chat_id)
    if not current:
        reply = await message.reply_text("❌ এখন কিছু চলছে না।")
        await auto_delete_service(message, reply)
        return
    dur = format_duration(current.duration)
    kind = "🎬 Video" if current.stream_type == "video" else "🎵 Audio"
    text = (
        f"**▶️ এখন চলছে**\n\n"
        f"**{kind}:** [{current.title}]({current.url})\n"
        f"**⏱ সময়:** {dur}\n"
        f"**👤 অনুরোধকারী:** {current.requester}"
    )
    if current.thumbnail:
        reply = await bot.send_photo(
            chat_id, photo=current.thumbnail,
            caption=text, reply_markup=_control_keyboard(),
        )
    else:
        reply = await message.reply_text(text, reply_markup=_control_keyboard())
    await auto_delete_playing(message, reply)


# ── /loop ────────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command("loop") & not_edited)
async def loop_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    state = await toggle_loop(chat_id)
    if state:
        reply = await message.reply_text("🔁 **Loop চালু** — বর্তমান গান বারবার চলবে।")
    else:
        reply = await message.reply_text("🔁 **Loop বন্ধ** — Queue স্বাভাবিকভাবে চলবে।")
    await auto_delete_service(message, reply)


# ── /shuffle ───────────────────────────────────────────────────────────[...]

@bot.on_message(filters.command("shuffle") & not_edited)
async def shuffle_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    items = await get_queue(chat_id)
    if len(items) < 2:
        reply = await message.reply_text("❌ Shuffle করার জন্য queue-তে কমপক্ষে ২টা গান থাকা দরকার।")
        await auto_delete_service(message, reply)
        return
    await shuffle_queue(chat_id)
    reply = await message.reply_text("🔀 **Queue shuffle হয়ে গেছে!**")
    await auto_delete_service(message, reply)


# ════════════════════════════════════════════════════════════════[...]
# Callback query handlers (inline keyboard buttons)
# ════════════════════════════════════════════════════════════════[...]

@bot.on_callback_query(filters.regex(r"^ctl_pause$"))
async def cb_pause(client: Client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if not is_active(chat_id):
        await callback.answer("কিছু চলছে না!", show_alert=True)
        return
    ok = await pause_stream(chat_id)
    await callback.answer("⏸ Paused" if ok else "Pause failed")


@bot.on_callback_query(filters.regex(r"^ctl_resume$"))
async def cb_resume(client: Client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if not is_active(chat_id):
        await callback.answer("কিছু চলছে না!", show_alert=True)
        return
    ok = await resume_stream(chat_id)
    await callback.answer("▶️ Resumed" if ok else "Resume failed")


@bot.on_callback_query(filters.regex(r"^ctl_skip$"))
async def cb_skip(client: Client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if not is_active(chat_id):
        await callback.answer("কিছু চলছে না!", show_alert=True)
        return

    next_item = await skip_queue(chat_id)
    if next_item is None:
        await leave_voice_chat(chat_id)
        await callback.answer("Queue শেষ!")
        reply = await callback.message.reply_text(
            "⏹ **Queue শেষ।** Voice chat থেকে বের হয়ে গেছি।"
        )
        await auto_delete_service(reply)
        return

    try:
        if next_item.stream_type == "video":
            await stream_video(chat_id, next_item.media_path,
                               title=next_item.title)
        else:
            await stream_audio(chat_id, next_item.media_path,
                               title=next_item.title)
        await callback.answer(f"⏭ {next_item.title[:30]}")
        dur = format_duration(next_item.duration)
        reply = await callback.message.reply_text(
            f"⏭ **Skipped!**\n\n"
            f"**▶️ এখন চলছে:** {next_item.title}\n"
            f"**⏱ সময়:** {dur}",
            reply_markup=_control_keyboard(),
        )
        await auto_delete_playing(reply)
    except Exception:
        await callback.answer("Skip failed!", show_alert=True)


@bot.on_callback_query(filters.regex(r"^ctl_stop$"))
async def cb_stop(client: Client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if not is_active(chat_id):
        await callback.answer("কিছু চলছে না!", show_alert=True)
        return
    await leave_voice_chat(chat_id)
    await callback.answer("⏹ Stopped")
    reply = await callback.message.reply_text(
        "⏹ **Stopped!** Queue clear হয়ে গেছে।"
    )
    await auto_delete_service(reply)


@bot.on_callback_query(filters.regex(r"^ctl_queue$"))
async def cb_queue(client: Client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    items = await get_queue(chat_id)
    if not items:
        await callback.answer("Queue খালি!", show_alert=True)
        return
    cq = await get_chat_queue(chat_id)
    lines = []
    for i, item in enumerate(items):
        marker = "▶️" if i == cq.current_index else f"{i + 1}."
        dur = format_duration(item.duration)
        lines.append(f"{marker} {item.title} [{dur}]")
    text = "\n".join(lines[:15])  # limit to 15 to avoid message length issues
    if len(items) > 15:
        text += f"\n\n... এবং আরো {len(items) - 15}টি গান"
    await callback.answer(text[:200], show_alert=True)


@bot.on_callback_query(filters.regex(r"^ctl_loop$"))
async def cb_loop(client: Client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    state = await toggle_loop(chat_id)
    await callback.answer(
        "🔁 Loop চালু" if state else "🔁 Loop বন্ধ",
        show_alert=False,
    )
