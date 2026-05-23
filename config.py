import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration for MusicLyrics bot.

    All secrets and tunables are read from environment variables.
    See .env.example for the full list.
    """

    # ── Telegram API ─────────────────────────────────────────────────────
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    STRING_SESSION = os.environ.get("STRING_SESSION", "")

    # ── Database ─────────────────────────────────────────────────────────
    MONGO_URL = os.environ.get(
        "MONGO_URL", "mongodb://localhost:27017/musiclyrics"
    )

    # ── Permissions ──────────────────────────────────────────────────────
    SUDO_USERS: list[int] = [
        int(uid)
        for uid in os.environ.get("SUDO_USERS", "").split()
        if uid.strip()
    ]
    OWNER_ID: int = int(
        os.environ.get("OWNER_ID", SUDO_USERS[0] if SUDO_USERS else 0)
    )

    # ── Logging ──────────────────────────────────────────────────────────
    LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", 0))

    # ── Support & Branding ───────────────────────────────────────────────
    SUPPORT_GROUP = os.environ.get(
        "SUPPORT_GROUP", "https://t.me/+OvozYu7R1EczMGJl"
    )
    SUPPORT_CHANNEL = os.environ.get(
        "SUPPORT_CHANNEL", "https://t.me/RupkothaGolpo"
    )
    OWNER_LINK = "https://t.me/R4J_81"

    BOT_NAME = "MusicLyrics"
    BRAND_PHOTO = (
        "https://pic-link-bot.lovable.app/i/"
        "telegram-1779340031479-5eab5504.jpg"
    )
    BRAND_PHOTO_2 = (
        "https://pic-link-bot.lovable.app/i/"
        "telegram-1779340095109-3b9afb55.jpg"
    )

    START_IMG = BRAND_PHOTO

    # ── Optional Integrations ────────────────────────────────────────────
    SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
    YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
    AI_API_KEY = os.environ.get("AI_API_KEY")

    # ── Playback Defaults ────────────────────────────────────────────────
    DURATION_LIMIT_MIN = int(os.environ.get("DURATION_LIMIT_MIN", 60))
    DOWNLOADS_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "downloads"
    )

    # ── Misc ─────────────────────────────────────────────────────────────
    PING_IMG = BRAND_PHOTO_2
    ALIVE_IMG = BRAND_PHOTO

    # ── Start message text ───────────────────────────────────────────────
    START_TEXT = (
        "**Hey {mention}! I'm {bot_name}** 🎵\n\n"
        "A powerful music streaming bot for Telegram voice chats.\n\n"
        "**Features:** Play music, lyrics lookup, games, security tools & more.\n\n"
        "Hit /help to see all commands.\n\n"
        f"[Support Group]({SUPPORT_GROUP}) | "
        f"[Updates Channel]({SUPPORT_CHANNEL}) | "
        f"[Owner]({OWNER_LINK})"
    )
