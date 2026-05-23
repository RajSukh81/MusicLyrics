<p align="center">
  <img src="https://pic-link-bot.lovable.app/i/telegram-1779340031479-5eab5504.jpg" alt="MusicLyrics Banner" width="320"/>
  <br>
  <img src="https://pic-link-bot.lovable.app/i/telegram-1779340095109-3b9afb55.jpg" alt="MusicLyrics Logo" width="200"/>
</p>

<h1 align="center">MusicLyrics</h1>

<p align="center">
  <b>A feature-rich Telegram music streaming bot for voice chats</b>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white" alt="Python"></a>
  <a href="https://docs.pyrogram.org/"><img src="https://img.shields.io/badge/Pyrogram-2.x-green?logo=telegram&logoColor=white" alt="Pyrogram"></a>
  <a href="https://github.com/MarshalX/tgcalls"><img src="https://img.shields.io/badge/py--tgcalls-streaming-orange" alt="py-tgcalls"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

<h2 align="center">One-Click Deploy / এক ক্লিকে ডিপ্লয়</h2>

<p align="center">
  <a href="https://heroku.com/deploy?template=https://github.com/RajSukh81/MusicLyrics"><img src="https://www.herokucdn.com/deploy/button.svg" alt="Deploy to Heroku" height="40"></a>
  &nbsp;&nbsp;
  <a href="https://railway.app/new/template?template=https://github.com/RajSukh81/MusicLyrics&envs=API_ID,API_HASH,BOT_TOKEN,STRING_SESSION,MONGO_URL,OWNER_ID"><img src="https://railway.app/button.svg" alt="Deploy on Railway" height="40"></a>
  &nbsp;&nbsp;
  <a href="https://render.com/deploy?repo=https://github.com/RajSukh81/MusicLyrics"><img src="https://render.com/images/deploy-to-render-button.svg" alt="Deploy to Render" height="40"></a>
  &nbsp;&nbsp;
  <a href="https://app.koyeb.com/deploy?type=git&repository=https://github.com/RajSukh81/MusicLyrics&branch=main&name=musiclyrics"><img src="https://www.koyeb.com/static/images/deploy/button.svg" alt="Deploy to Koyeb" height="40"></a>
</p>

<p align="center">
  <a href="https://t.me/+OvozYu7R1EczMGJl">Support Group</a> &bull;
  <a href="https://t.me/RupkothaGolpo">Updates Channel</a> &bull;
  <a href="https://t.me/R4J_81">Owner</a>
</p>

---

## Features

### Music & Streaming
- Stream audio and video in Telegram voice chats
- Play from YouTube, Spotify, SoundCloud, and direct URLs
- Queue management, skip, pause, resume, stop, shuffle
- Lyrics fetching for currently playing tracks
- Volume control and equalizer presets

### Games & Fun
- Built-in mini-games for group entertainment
- Interactive group activities and challenges

### Security & Admin
- Anti-spam and anti-flood protection
- User ban/unban, mute/unmute management
- Blacklist words and link filtering
- Admin-only command restrictions

### Tools & Utilities
- Song/video downloads and format conversion
- Ping, stats, alive checks
- Broadcast messages to all chats
- Detailed bot analytics and logging

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `API_ID` | Yes | Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Yes | Telegram API Hash |
| `BOT_TOKEN` | Yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `STRING_SESSION` | Yes | Pyrogram string session for the assistant/userbot account |
| `MONGO_URL` | Yes | MongoDB connection URI |
| `SUDO_USERS` | Yes | Space-separated Telegram user IDs with sudo access |
| `OWNER_ID` | No | Owner user ID (defaults to first `SUDO_USERS` entry) |
| `LOG_GROUP_ID` | No | Chat ID where the bot logs events |
| `SUPPORT_GROUP` | No | Support group invite link |
| `SUPPORT_CHANNEL` | No | Updates channel link |
| `SPOTIFY_CLIENT_ID` | No | Spotify app client ID for Spotify track support |
| `SPOTIFY_CLIENT_SECRET` | No | Spotify app client secret |
| `YOUTUBE_API_KEY` | No | YouTube Data API v3 key for enhanced search |

> Copy `.env.example` to `.env` and fill in your values before deploying.

---

## Deployment

### Prerequisites (ডিপ্লয় করার আগে যা লাগবে)

1. **MongoDB Atlas (ফ্রি)** — [mongodb.com/atlas](https://www.mongodb.com/atlas) থেকে একটি cluster তৈরি করো, connection string কপি করো, Network Access-এ `0.0.0.0/0` allow করো
2. **Telegram API credentials** — [my.telegram.org](https://my.telegram.org) থেকে `API_ID` ও `API_HASH` নাও
3. **Bot Token** — [@BotFather](https://t.me/BotFather) থেকে Bot তৈরি করে Token কপি করো
4. **Owner ID** — তোমার Telegram User ID জানো ([@userinfobot](https://t.me/userinfobot) এ `/start` দিলে পাবে)
5. **(Optional) String Session** — Voice chat streaming-এর জন্য একটি secondary Telegram account থেকে generate করো:
   ```bash
   pip install pyrogram tgcrypto
   python3 -c "from pyrogram import Client; Client(':memory:', api_id=API_ID, api_hash='HASH').run(Client.export_session_string)"
   ```

---

### Heroku (One-Click Deploy)

**সবচেয়ে সহজ পদ্ধতি:**

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/RajSukh81/MusicLyrics)

1. উপরের **Deploy to Heroku** বাটনে ক্লিক করো
2. Heroku অ্যাকাউন্টে লগইন করো (না থাকলে সাইন আপ করো)
3. App-এর একটি নাম দাও
4. সব **environment variables** ফিল আপ করো (API_ID, API_HASH, BOT_TOKEN, MONGO_URL, OWNER_ID — বাকিগুলো optional)
5. **Deploy app** বাটনে ক্লিক করো
6. ডিপ্লয় শেষ হলে **Manage App** → **Resources** ট্যাবে যাও
7. `web` dyno **বন্ধ** করো (যদি থাকে) এবং `worker` dyno **চালু** করো
8. **More** → **View logs** দিয়ে বট চলছে কিনা দেখো

> **Note:** Heroku Eco/Basic plan ($5/month) লাগবে। ফ্রি plan আর নেই।

---

### Railway (One-Click Deploy)

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/RajSukh81/MusicLyrics&envs=API_ID,API_HASH,BOT_TOKEN,STRING_SESSION,MONGO_URL,OWNER_ID)

1. বাটনে ক্লিক করো → GitHub দিয়ে লগইন করো
2. Environment variables ফিল আপ করো
3. **Deploy** ক্লিক করো — ব্যস!

---

### Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/RajSukh81/MusicLyrics)

1. বাটনে ক্লিক করো → Render অ্যাকাউন্টে লগইন করো
2. **Background Worker** হিসেবে deploy করো
3. Environment variables সেট করো → Deploy

---

### Koyeb

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&repository=https://github.com/RajSukh81/MusicLyrics&branch=main&name=musiclyrics)

1. বাটনে ক্লিক করো → Koyeb অ্যাকাউন্টে লগইন করো
2. Instance type: **Worker** সিলেক্ট করো
3. Environment variables সেট করো → Deploy

---

### Local / VPS

```bash
# Clone the repository
git clone https://github.com/your-username/MusicLyrics.git
cd MusicLyrics

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -U pip
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Run the bot
python3 -m MusicLyrics
```

### Docker (with MongoDB)

```bash
# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Build and run
docker-compose up -d --build   # MongoDB container auto-included

# View logs
docker-compose logs -f
```

---

## Commands

### Music
| Command | Description |
|---|---|
| `/play <query/url>` | Play a song in voice chat |
| `/vplay <query/url>` | Play a video in voice chat |
| `/pause` | Pause the current track |
| `/resume` | Resume playback |
| `/skip` | Skip to the next track |
| `/stop` | Stop playback and clear the queue |
| `/queue` | Show the current queue |
| `/shuffle` | Shuffle the queue |
| `/lyrics <song>` | Fetch lyrics for a song |
| `/volume <0-200>` | Adjust playback volume |

### Admin
| Command | Description |
|---|---|
| `/ban <user>` | Ban a user from the group |
| `/unban <user>` | Unban a user |
| `/mute <user>` | Mute a user |
| `/unmute <user>` | Unmute a user |
| `/purge` | Delete replied-to message and everything after it |

### Tools
| Command | Description |
|---|---|
| `/ping` | Check bot latency |
| `/alive` | Check if the bot is running |
| `/stats` | Show bot statistics |
| `/broadcast <msg>` | Send a message to all chats (sudo only) |
| `/song <query>` | Download and send a song file |
| `/video <query>` | Download and send a video file |

### Games
| Command | Description |
|---|---|
| `/game` | Start a mini-game in the group |

### Security
| Command | Description |
|---|---|
| `/antispam on/off` | Toggle anti-spam protection |
| `/antiflood <count>` | Set flood message limit |
| `/blacklist <word>` | Add a word to the blacklist |

---

## Project Structure

```
MusicLyrics/
├── MusicLyrics/           # Main bot package
│   ├── helpers/           # Helper functions & decorators
│   ├── mongo/             # MongoDB models & queries
│   ├── plugins/           # Bot command handlers
│   │   ├── admin/         # Admin commands
│   │   ├── callbacks/     # Callback query handlers
│   │   ├── games/         # Mini-games
│   │   ├── misc/          # Miscellaneous commands
│   │   ├── play/          # Music playback
│   │   │   └── platforms/ # Platform-specific players
│   │   ├── security/      # Security features
│   │   └── tools/         # Utility commands
│   └── utils/             # Shared utilities
├── config.py              # Centralised configuration
├── requirements.txt       # Python dependencies
├── Dockerfile             # Docker image definition
├── docker-compose.yml     # Docker Compose setup
├── Procfile               # Heroku/Railway process file
├── runtime.txt            # Python version for PaaS
├── .env.example           # Environment variable template
└── .gitignore             # Git ignore rules
```

---

## Credits

- **MusicLyrics** -- Built and maintained by [R4J](https://t.me/R4J_81)
- Powered by [Pyrogram](https://docs.pyrogram.org/) and [py-tgcalls](https://github.com/MarshalX/tgcalls)
- Audio/video downloads via [yt-dlp](https://github.com/yt-dlp/yt-dlp)

---

<p align="center">
  <b>MusicLyrics</b> &mdash; Stream music, play games, keep your groups safe.<br>
  <a href="https://t.me/+OvozYu7R1EczMGJl">Join the community</a>
</p>
