import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration for MusicLyrics bot.

    All secrets and tunables are read from environment variables.
    See .env.example for the full list.
    """

    # ── Telegram API ─────────────────────────────────────────────────────
    try:
        API_ID = int(os.environ["API_ID"])
    except KeyError:
        raise SystemExit("ERROR: API_ID environment variable is required. Get it from https://my.telegram.org")
    API_HASH = os.environ.get("API_HASH")
    if not API_HASH:
        raise SystemExit("ERROR: API_HASH environment variable is required. Get it from https://my.telegram.org")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    if not BOT_TOKEN:
        raise SystemExit("ERROR: BOT_TOKEN environment variable is required. Get it from @BotFather")
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
    _raw_owner = os.environ.get("OWNER_ID", "")
    if _raw_owner.strip():
        OWNER_ID: int = int(_raw_owner)
    elif SUDO_USERS:
        OWNER_ID: int = SUDO_USERS[0]
    else:
        OWNER_ID: int = 0

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

    BOT_NAME = os.environ.get("BOT_USERNAME", "MusicLyrics")
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

    # ── Proxy for YouTube (essential for cloud deployments like Heroku) ──
    # Set a residential/datacenter proxy to bypass YouTube IP blocks.
    # Format: "http://user:pass@host:port" or "socks5://host:port"
    YOUTUBE_PROXY = os.environ.get("YOUTUBE_PROXY", "")

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
