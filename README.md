# Video Downloader

A self-hosted web application that downloads videos and audio from popular
platforms.  Paste a link, choose MP4 or MP3, pick a quality, and save the
file to your device.

Built with **Python 3.12**, **Flask**, **yt-dlp**, and **FFmpeg**.
Background-job architecture handles Cloudflare's 100-second proxy timeout.

---

## Quick Start (local, no Docker)

```bash
# 1. Clone and enter the directory
git clone <your-repo-url> video-downloader
cd video-downloader

# 2. Create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Make sure ffmpeg is installed and on PATH
ffmpeg -version

# 5. Run
python app.py
# → http://localhost:10000
```

## Quick Start (Docker)

```bash
docker build -t video-downloader .
docker run -p 10000:10000 video-downloader
# → http://localhost:10000
```

---

## Configuration

All settings are environment variables with sane defaults:

| Variable               | Default | Description                         |
|------------------------|---------|-------------------------------------|
| `PORT`                 | 10000   | HTTP listen port                    |
| `MAX_FILESIZE_MB`      | 500     | Max download file size (MB)         |
| `MAX_DURATION_SECONDS` | 1800    | Max video duration (seconds)        |
| `MAX_CONCURRENT_JOBS`  | 2       | Parallel downloads allowed          |
| `JOB_TTL_SECONDS`      | 900     | Seconds before finished jobs expire |

---

## Format & Quality Options

### MP4 (video)
| Quality | Description           |
|---------|-----------------------|
| 360p    | SD — small file       |
| 480p    | SD+ — moderate file   |
| 720p    | HD — good balance     |
| 1080p   | Full HD — large file  |

If the source doesn't offer the chosen resolution, the best available at or
below it is downloaded.

### MP3 (audio)
| Bitrate | Description           |
|---------|-----------------------|
| 128 kbps| Standard quality      |
| 192 kbps| Good quality          |
| 320 kbps| High quality          |

---

## API

| Endpoint                  | Method | Description                |
|---------------------------|--------|----------------------------|
| `/health`                 | GET    | `{"status":"ok"}`          |
| `/api/jobs`               | POST   | Create a download job      |
| `/api/jobs/<id>`          | GET    | Poll job status / progress |
| `/api/jobs/<id>/file`     | GET    | Download the finished file |

### POST /api/jobs

Body (JSON or form):
```json
{ "url": "https://...", "format": "mp4", "quality": "720" }
```

Returns `202 {"job_id": "..."}`.

### GET /api/jobs/<id>

```json
{ "status": "downloading", "progress": 45, "message": "Downloading…" }
```

Status values: `queued`, `downloading`, `processing`, `done`, `error`.

---

## Limitations

- **Datacenter IPs**: Some sites (YouTube, TikTok) may rate-limit or block
  requests from cloud server IPs.  This is a platform restriction, not a bug.
- **1080p memory**: Merging 1080p video + audio requires more RAM.  On
  Render's free tier (512 MB), this may cause OOM kills.  Use the starter
  plan or higher.
- **iOS Safari**: Auto-download doesn't work; users tap the "Save to device"
  button and the file saves to the Files app.
- **Live streams**: Not supported.
- **Playlists**: Disabled — only single videos.

---

## License

For personal use.  Only download content you own or have permission to
download.
