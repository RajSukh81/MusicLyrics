from pyrogram import Client
from pyrogram.enums import ParseMode
from config import Config

bot = Client(
    name="MusicLyricsBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    parse_mode=ParseMode.MARKDOWN,
)
