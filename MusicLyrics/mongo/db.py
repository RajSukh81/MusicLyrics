from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

_client = AsyncIOMotorClient(Config.MONGO_URL)
db = _client["MusicLyricsDB"]
