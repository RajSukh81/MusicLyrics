"""Main callback query handler for MusicLyrics bot."""

from pyrogram import filters
from pyrogram.types import CallbackQuery

from MusicLyrics.bot import bot


@bot.on_callback_query(filters.regex(r"^close$"))
async def close_callback(_, callback: CallbackQuery):
    """Delete the message when the close button is pressed."""
    try:
        await callback.message.delete()
    except Exception:
        await callback.answer("❌ Could not delete message.", show_alert=True)
        return
    await callback.answer("🔒 Closed!")


@bot.on_callback_query(filters.regex(r"^noop$"))
async def noop_callback(_, callback: CallbackQuery):
    """No-operation callback for decorative buttons."""
    await callback.answer()
