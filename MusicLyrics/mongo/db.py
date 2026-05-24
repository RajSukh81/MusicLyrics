from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

_client = AsyncIOMotorClient(
    Config.MONGO_URL,
    serverSelectionTimeoutMS=5000,   # 5s timeout instead of 30s default
    connectTimeoutMS=5000,
    socketTimeoutMS=10000,
)
db = _client["MusicLyricsDB"]
