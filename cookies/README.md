# YouTube Cookies

This directory holds Netscape-format cookie files (`.txt`) exported from
a YouTube-logged-in browser session.

## Why?

YouTube blocks yt-dlp on cloud servers (Heroku, Railway, Render, etc.)
with a "Sign in to confirm you're not a bot" error. Providing cookies
from a real browser session bypasses this.

## How to get cookies

1. Install the "Get cookies.txt LOCALLY" browser extension
   (Chrome/Firefox)
2. Go to youtube.com and sign in
3. Click the extension icon and export cookies for youtube.com
4. Save the file as `cookies1.txt` in this directory
5. You can add multiple cookie files (`cookies2.txt`, etc.) —
   one is randomly selected per download request

## Important

- Do NOT commit cookie files to git (they are in `.gitignore`)
- Cookies expire periodically — re-export when downloads start failing
- Use a secondary/throwaway Google account, not your main one
