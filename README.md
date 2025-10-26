# EmbyFast - Bypass Server-Side Media Probing

This project aims to reduce startup time for `.strm` files in Emby by bypassing the server-side media probing step and letting the player handle media analysis directly.

## Problem

When Emby encounters a `.strm` file, it probes the remote stream to extract media information (codec, duration, etc.) before starting playback. This adds significant startup delay (often 5-30+ seconds).

**How it works:**
- Sits between Emby clients and server
- Intercepts `/Items/{id}/PlaybackInfo` requests
- For strm files: returns minimal playback info immediately
- For other files: passes through normally

**Usage:**
```bash
pip install -r requirements.txt
python proxy.py http://your-emby-server:8096 your_emby_api_key 8097
```

Then point your Emby client to http://localhost:8097/web/index.html
