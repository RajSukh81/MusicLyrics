"""Start and help commands for MusicLyrics bot."""

from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from pyrogram.enums import ChatType

from MusicLyrics.bot import bot
from config import Config
from MusicLyrics.mongo.users_db import add_user


def _start_keyboard():
    """Build the start menu inline keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Add Me to Group",
                url=f"https://t.me/{Config.BOT_NAME}?startgroup=true",
            ),
        ],
        [
            InlineKeyboardButton("💬 Support", url=Config.SUPPORT_GROUP),
            InlineKeyboardButton("📢 Channel", url=Config.SUPPORT_CHANNEL),
        ],
        [
            InlineKeyboardButton("👑 Owner", url=Config.OWNER_LINK),
            InlineKeyboardButton("📖 Help", callback_data="help_main"),
        ],
        [
            InlineKeyboardButton("🔒 Close", callback_data="close"),
        ],
    ])


HELP_CATEGORIES = {
    "music": {
        "title": "🎵 Music / মিউজিক",
        "text": (
            "🎵 **Music Commands / মিউজিক কমান্ড:**\n\n"
            "▸ `/play <song>` — Play a song in VC\n"
            "▸ `/vplay <song>` — Play video in VC\n"
            "▸ `/pause` — Pause playback\n"
            "▸ `/resume` — Resume playback\n"
            "▸ `/skip` — Skip current song\n"
            "▸ `/stop` — Stop & leave VC\n"
            "▸ `/queue` — Show queue\n"
            "▸ `/song <query>` — Download song\n"
            "▸ `/vsong <query>` — Download video\n"
        ),
    },
    "games": {
        "title": "🎮 Games / গেমস",
        "text": (
            "🎮 **Game Commands / গেম কমান্ড:**\n\n"
            "▸ `/ttt` — Tic Tac Toe\n"
            "▸ `/quiz` — Start a quiz\n"
            "▸ `/truth` — Truth question\n"
            "▸ `/dare` — Dare challenge\n"
            "▸ `/flip` — Coin flip\n"
            "▸ `/dice` — Roll a dice\n"
            "▸ `/wordseek` — Word seek game\n"
            "▸ `/kill` — Kill game\n"
        ),
    },
    "security": {
        "title": "🔒 Security / সিকিউরিটি",
        "text": (
            "🔒 **Security Commands / সিকিউরিটি কমান্ড:**\n\n"
            "▸ `/ban` — Ban a user\n"
            "▸ `/unban` — Unban a user\n"
            "▸ `/mute` — Mute a user\n"
            "▸ `/unmute` — Unmute a user\n"
            "▸ `/warn` — Warn a user\n"
            "▸ `/antispam` — Toggle anti-spam\n"
            "▸ `/antiflood` — Toggle anti-flood\n"
            "▸ `/captcha` — Toggle captcha\n"
            "▸ `/blacklist` — Manage blacklist\n"
            "▸ `/setwelcome` — Set welcome message\n"
        ),
    },
    "tools": {
        "title": "🛠 Tools / টুলস",
        "text": (
            "🛠 **Tool Commands / টুল কমান্ড:**\n\n"
            "▸ `/tr <lang> <text>` — Translate\n"
            "▸ `/tts <text>` — Text to speech\n"
            "▸ `/sticker` — Photo to sticker\n"
            "▸ `/toimg` — Sticker to image\n"
            "▸ `/kang` — Steal sticker\n"
            "▸ `/info` — User info\n"
            "▸ `/chatinfo` — Chat info\n"
            "▸ `/paste` — Paste text online\n"
            "▸ `/telegraph` — Upload to Telegraph\n"
            "▸ `/tagall` — Tag all members\n"
            "▸ `/afk` — Set AFK status\n"
            "▸ `/react` — React to message\n"
            "▸ `/emoji` — Big emoji\n"
        ),
    },
    "admin": {
        "title": "👑 Admin / অ্যাডমিন",
        "text": (
            "👑 **Admin Commands / অ্যাডমিন কমান্ড:**\n\n"
            "▸ `/broadcast` — Broadcast message (sudo)\n"
            "▸ `/stats` — Bot statistics (sudo)\n"
            "▸ `/addsudo` — Add sudo user (owner)\n"
            "▸ `/rmsudo` — Remove sudo user (owner)\n"
            "▸ `/sudolist` — List sudo users\n"
            "▸ `/ping` — Bot latency\n"
            "▸ `/alive` — Bot status\n"
        ),
    },
}


def _help_main_keyboard():
    """Build the help menu inline keyboard."""
    buttons = []
    row = []
    for key, cat in HELP_CATEGORIES.items():
        row.append(InlineKeyboardButton(cat["title"], callback_data=f"help_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔒 Close", callback_data="close")])
    return InlineKeyboardMarkup(buttons)


HELP_MAIN_TEXT = (
    "📖 **Help Menu / হেল্প মেনু**\n\n"
    "নিচের ক্যাটাগরি থেকে বেছে নাও:\n"
    "Choose a category below:\n"
)


@bot.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    """Handle /start command."""
    if message.from_user:
        await add_user(
            message.from_user.id,
            message.from_user.first_name or "",
            message.from_user.username or "",
        )

    mention = message.from_user.mention if message.from_user else "User"

    if message.chat.type == ChatType.PRIVATE:
        text = Config.START_TEXT.format(
            mention=mention,
            bot_name=Config.BOT_NAME,
        )
        try:
            await message.reply_photo(
                photo=Config.START_IMG,
                caption=text,
                reply_markup=_start_keyboard(),
            )
        except Exception:
            await message.reply_text(text, reply_markup=_start_keyboard())
    else:
        await message.reply_text(
            f"🎵 **{Config.BOT_NAME} চালু আছে!**\n\n"
            f"Hey {mention}! সব ফিচার দেখতে আমাকে DM-এ /start দাও।\n"
            f"DM me /start for the full menu!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Help", callback_data="help_main")],
            ]),
        )


@bot.on_message(filters.command("help"))
async def help_cmd(_, message: Message):
    """Handle /help command."""
    await message.reply_text(
        HELP_MAIN_TEXT,
        reply_markup=_help_main_keyboard(),
    )
