"""
Video Downloader — Flask backend with background-job architecture.

Flow: POST /api/jobs -> poll GET /api/jobs/<id> -> GET /api/jobs/<id>/file
Uses yt-dlp + ffmpeg. Single gunicorn worker with threads for concurrency.
"""

import ipaddress
import logging
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, jsonify, render_template, request, send_file
from flask_limiter import Limiter
from werkzeug.middleware.proxy_fix import ProxyFix

# ---------------------------------------------------------------------------
# Configuration (environment variables with sane defaults)
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("PORT", 10000))
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", 500))
MAX_DURATION_SECONDS = int(os.environ.get("MAX_DURATION_SECONDS", 1800))
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", 2))
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", 900))
# /tmp/vd-jobs on Linux/Docker, system temp dir on Windows
JOBS_BASE_DIR = os.environ.get(
    "JOBS_BASE_DIR", os.path.join(tempfile.gettempdir(), "vd-jobs")
)

# Whitelisted format and quality options — anything else returns 400
MP4_QUALITIES = frozenset({360, 480, 720, 1080})
MP3_BITRATES = frozenset({128, 192, 320})
VALID_FORMATS = frozenset({"mp4", "mp3"})
_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("vd")

# ---------------------------------------------------------------------------
# In-memory job store (requires gunicorn --workers 1 with threads)
# ---------------------------------------------------------------------------

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
info_cache: dict[str, tuple[float, dict]] = {}
info_cache_lock = threading.Lock()
_INFO_CACHE_TTL = 300

# ---------------------------------------------------------------------------
# Flask application & rate limiting
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # 64 KB max request body

# Trust one reverse-proxy hop (Cloudflare / Render)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _get_real_ip() -> str:
    """Real client IP: CF-Connecting-IP > first X-Forwarded-For > remote_addr."""
    cf = request.headers.get("CF-Connecting-IP", "").strip()
    if cf and _parse_ip(cf) is not None:
        return cf
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        first = xff.split(",")[0].strip()
        if first and _parse_ip(first) is not None:
            return first
    return request.remote_addr or "127.0.0.1"


limiter = Limiter(
    key_func=_get_real_ip,
    app=app,
    default_limits=["200 per minute"],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# Security headers on all responses
# ---------------------------------------------------------------------------


@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    if request.path.startswith("/api/") or request.path == "/health":
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# ---------------------------------------------------------------------------
# SSRF protection — reject loopback, private, link-local, and reserved IPs
# ---------------------------------------------------------------------------

_PRIVATE_NETS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

_BLOCKED_HOSTNAMES = (
    "localhost",
    "localhost.localdomain",
    "localdomain",
    ".localhost",
    ".local",
    ".internal",
)


def _parse_ip(value: str):
    """Return an ip_address object if value is an IP literal, else None."""
    cleaned = value.strip("[]").split("%", 1)[0]
    try:
        return ipaddress.ip_address(cleaned)
    except ValueError:
        return None


def _is_disallowed_ip(addr) -> bool:
    """Return True if IP address (or its IPv4-mapped form) is non-public."""
    if getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return True
    return any(addr in net for net in _PRIVATE_NETS)


def _validate_url(raw: str | None) -> tuple[str | None, str | None]:
    """Validate URL syntax and block private/reserved destinations."""
    if not raw or not isinstance(raw, str):
        return None, "Please enter a video URL."
    url = raw.strip()
    if not url:
        return None, "Please enter a video URL."
    if len(url) >= 2000:
        return None, "URL is too long (must be under 2000 characters)."

    try:
        parsed = urlparse(url)
    except ValueError:
        return None, "Invalid URL."

    if parsed.scheme not in ("http", "https"):
        return None, "URL must start with http:// or https://."
    if parsed.username or parsed.password:
        return None, "URLs with embedded credentials are not allowed."

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return None, "Invalid URL."

    if any(
        hostname == h or (h.startswith(".") and hostname.endswith(h))
        for h in _BLOCKED_HOSTNAMES
    ):
        return None, "This URL points to a private or reserved address."

    # If hostname is already an IP literal, check it directly
    literal_ip = _parse_ip(hostname)
    if literal_ip is not None:
        if _is_disallowed_ip(literal_ip):
            return None, "This URL points to a private or reserved address."
        return url, None

    # Public domains always contain a dot; single-label names are internal hosts
    if "." not in hostname:
        return None, "This URL points to a private or reserved address."

    # Otherwise resolve hostname via DNS and inspect every returned IP
    try:
        addr_info = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror:
        return None, "Could not resolve the website hostname."

    if not addr_info:
        return None, "Could not resolve the website hostname."

    for _fam, _type, _proto, _canon, sockaddr in addr_info:
        resolved_ip = _parse_ip(sockaddr[0])
        if resolved_ip is None or _is_disallowed_ip(resolved_ip):
            return None, "This URL points to a private or reserved address."

    return url, None


# ---------------------------------------------------------------------------
# Filename sanitisation & friendly error mapping
# ---------------------------------------------------------------------------


def _sanitize_filename(name: str) -> str:
    if not name:
        name = "download"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(". ")
    return (name or "download")[:120]


def _classify_error(msg: str) -> str:
    m = msg.lower()
    if "non-public" in m or "reserved target" in m:
        return "This URL points to a private or reserved address."
    if "playlist" in m:
        return "Playlists are not supported. Please paste a single video link."
    if "live stream" in m or "is_live" in m or "upcoming" in m:
        return "Live streams are not supported."
    if "unsupported url" in m or "no suitable" in m or "no video formats" in m:
        return "This URL is not supported."
    if "private" in m or "sign in" in m or "login" in m or "authentication" in m:
        return "This video is private or requires sign-in."
    if "not available" in m or "removed" in m or "does not exist" in m or "404" in m:
        return "This video is unavailable or has been removed."
    if "duration" in m or "too long" in m or "match_filter" in m:
        return f"This video is too long (max {MAX_DURATION_SECONDS // 60} minutes)."
    if "filesize" in m or "max-filesize" in m or "too large" in m:
        return f"This file is too large (max {MAX_FILESIZE_MB} MB)."
    if "blocked" in m or "403" in m or "forbidden" in m or "bot" in m or "429" in m:
        return "The video site blocked this server request. Try a different link."
    if "copyright" in m:
        return "This video cannot be downloaded due to copyright restrictions."
    if "drm" in m:
        return "This video is DRM-protected and cannot be downloaded."
    if "timed out" in m or "timeout" in m:
        return "The request timed out. Please try again."
    if "geo" in m or "country" in m:
        return "This video is not available in the server's region."
    return "Could not download this video. Please check the link and try again."


# ---------------------------------------------------------------------------
# yt-dlp logger & hooks
# ---------------------------------------------------------------------------


class _YdlCaptureLogger:
    """Captures yt-dlp messages cleanly without leaking stack traces to users."""

    def __init__(self):
        self.messages: list[str] = []

    def debug(self, msg):
        if msg:
            self.messages.append(str(msg))

    def info(self, msg):
        if msg:
            self.messages.append(str(msg))

    def warning(self, msg):
        if msg:
            self.messages.append(str(msg))

    def error(self, msg):
        if msg:
            self.messages.append(str(msg))


def _make_progress_hook(job_id: str):
    def hook(d):
        with jobs_lock:
            job = jobs.get(job_id)
            if not job:
                return
            status = d.get("status")
            if status == "downloading":
                job["status"] = "downloading"
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    job["progress"] = min(int(downloaded / total * 100), 99)
                else:
                    fi = d.get("fragment_index")
                    fc = d.get("fragment_count")
                    if fi and fc and fc > 0:
                        job["progress"] = min(int(fi / fc * 100), 99)
                job["message"] = "Downloading…"
            elif status == "finished":
                job["status"] = "processing"
                job["progress"] = 99
                job["message"] = "Converting…"
            elif status == "error":
                job["status"] = "error"
                job["error"] = "Download failed."
                job["message"] = "Download failed."

    return hook


def _make_pp_hook(job_id: str):
    def hook(d):
        with jobs_lock:
            job = jobs.get(job_id)
            if not job:
                return
            if d.get("status") in ("started", "processing"):
                job["status"] = "processing"
                job["progress"] = 99
                job["message"] = "Converting…"

    return hook


def _build_match_filter():
    def _filter(info_dict, *, incomplete=False):
        if incomplete:
            return None
        if info_dict.get("_type") == "playlist" or "entries" in info_dict:
            raise yt_dlp.utils.DownloadError("Playlists are not supported.")
        if info_dict.get("is_live") or info_dict.get("live_status") in (
            "is_live",
            "is_upcoming",
        ):
            raise yt_dlp.utils.DownloadError("Live streams are not supported.")
        # Re-validate resolved/redirected media URLs against SSRF rules
        for key in ("webpage_url", "url", "manifest_url"):
            target = info_dict.get(key)
            if isinstance(target, str) and target.strip():
                _, url_err = _validate_url(target)
                if url_err:
                    raise yt_dlp.utils.DownloadError(
                        "Rejected non-public reserved target."
                    )
        duration = info_dict.get("duration")
        if (
            MAX_DURATION_SECONDS > 0
            and duration is not None
            and duration > MAX_DURATION_SECONDS
        ):
            raise yt_dlp.utils.DownloadError(
                f"Video duration ({duration}s) exceeds max duration ({MAX_DURATION_SECONDS}s)."
            )
        return None

    return _filter


def _resolve_output_path(
    ydl: yt_dlp.YoutubeDL, info: dict, job_dir: str, fmt: str
) -> str | None:
    """Locate the final post-processed file inside job_dir."""
    requested = info.get("requested_downloads") or []
    for item in requested:
        fp = item.get("filepath")
        if fp and os.path.isfile(fp):
            return fp

    prepared = ydl.prepare_filename(info)
    base, _ = os.path.splitext(prepared)
    preferred_ext = ".mp3" if fmt == "mp3" else ".mp4"
    for candidate in (base + preferred_ext, prepared):
        if candidate and os.path.isfile(candidate):
            return candidate

    for entry in Path(job_dir).iterdir():
        if entry.is_file() and not entry.name.endswith((".part", ".ytdl", ".temp")):
            return str(entry)
    return None


# ---------------------------------------------------------------------------
# Background download worker
# ---------------------------------------------------------------------------


def _run_download(job_id: str, url: str, fmt: str, quality: int):
    job_dir = os.path.join(JOBS_BASE_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    capture_logger = _YdlCaptureLogger()

    try:
        with jobs_lock:
            job = jobs.get(job_id)
            if not job:
                return
            job["status"] = "downloading"
            job["message"] = "Downloading…"

        outtmpl = os.path.join(job_dir, "%(title).80s.%(ext)s")

        ydl_opts: dict = {
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "usenetrc": False,
            "enable_file_urls": False,
            "logger": capture_logger,
            "outtmpl": outtmpl,
            "socket_timeout": 30,
            "retries": 3,
            "match_filter": _build_match_filter(),
            "progress_hooks": [_make_progress_hook(job_id)],
            "postprocessor_hooks": [_make_pp_hook(job_id)],
        }

        if MAX_FILESIZE_MB > 0:
            ydl_opts["max_filesize"] = MAX_FILESIZE_MB * 1024 * 1024

        detected_runtimes = {
            rt: {} for rt in ("deno", "node", "bun", "quickjs") if shutil.which(rt)
        }
        if detected_runtimes:
            ydl_opts["js_runtimes"] = detected_runtimes

        if fmt == "mp4":
            h = quality
            ydl_opts["format"] = (
                f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
                f"b[height<={h}][ext=mp4]/"
                f"b[height<={h}]/b"
            )
            ydl_opts["merge_output_format"] = "mp4"
        else:  # mp3
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(quality),
                }
            ]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = _resolve_output_path(ydl, info, job_dir, fmt) if info else None

        if not filepath or not os.path.isfile(filepath):
            combined = " ".join(capture_logger.messages)
            raise RuntimeError(combined or "Downloaded file not found.")

        max_bytes = MAX_FILESIZE_MB * 1024 * 1024
        if MAX_FILESIZE_MB > 0 and os.path.getsize(filepath) > max_bytes:
            raise RuntimeError("File is larger than max-filesize.")

        actual_ext = os.path.splitext(filepath)[1].lower()
        target_ext = ".mp4" if fmt == "mp4" else ".mp3"
        ext = actual_ext if fmt == "mp4" and actual_ext else target_ext
        title = info.get("title", "download") if isinstance(info, dict) else "download"
        clean_name = _sanitize_filename(title) + ext

        with jobs_lock:
            job = jobs.get(job_id)
            if job:
                job["status"] = "done"
                job["progress"] = 100
                job["message"] = "Your file is ready"
                job["filename"] = clean_name
                job["path"] = filepath
                job["finished_at"] = time.time()

        logger.info("Job %s finished: %s", job_id, clean_name)

    except Exception as exc:
        raw_err = f"{exc} {' '.join(capture_logger.messages)}"
        friendly = _classify_error(raw_err)
        logger.error("Job %s failed: %s", job_id, exc)

        with jobs_lock:
            job = jobs.get(job_id)
            if job:
                job["status"] = "error"
                job["progress"] = 0
                job["error"] = friendly
                job["message"] = friendly
                job["finished_at"] = time.time()

        shutil.rmtree(job_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Cleanup daemon — remove expired jobs & leftover temp dirs every 60 seconds
# ---------------------------------------------------------------------------


def _cleanup_loop():
    interval = max(2, min(60, JOB_TTL_SECONDS))
    while True:
        time.sleep(interval)
        now = time.time()
        expired: list[str] = []
        with jobs_lock:
            for jid, job in list(jobs.items()):
                ref_time = job.get("finished_at")
                if ref_time is not None:
                    if now - ref_time > JOB_TTL_SECONDS:
                        expired.append(jid)
                elif now - job["created_at"] > max(JOB_TTL_SECONDS, MAX_DURATION_SECONDS + 300):
                    expired.append(jid)
            for jid in expired:
                jobs.pop(jid, None)
        for jid in expired:
            d = os.path.join(JOBS_BASE_DIR, jid)
            shutil.rmtree(d, ignore_errors=True)
            logger.info("Cleaned up expired job %s", jid)


def _startup_cleanup():
    """Wipe leftover job directories from previous runs."""
    if os.path.isdir(JOBS_BASE_DIR):
        shutil.rmtree(JOBS_BASE_DIR, ignore_errors=True)
    os.makedirs(JOBS_BASE_DIR, exist_ok=True)


_startup_cleanup()
threading.Thread(target=_cleanup_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


def _extract_qualities(formats: list) -> tuple[list[int], bool]:
    """Return sorted available whitelisted MP4 heights and whether audio exists."""
    heights: set[int] = set()
    has_audio = False
    has_video = False
    for f in formats or []:
        if not isinstance(f, dict) or f.get("has_drm"):
            continue
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        if acodec and acodec != "none":
            has_audio = True
        # Note: direct generic MP4 URLs sometimes omit vcodec while having video
        is_video = (vcodec and vcodec != "none") or (vcodec is None and acodec is None)
        if not is_video:
            continue
        has_video = True
        h = f.get("height")
        w = f.get("width")
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
            heights.add(min(w, h))
        elif isinstance(h, int) and h > 0:
            heights.add(h)

    ordered = sorted(MP4_QUALITIES)
    exact = [q for q in ordered if q in heights]
    if exact:
        return exact, has_audio
    if heights:
        max_h = max(heights)
        capped = [q for q in ordered if q <= max_h]
        return (capped or [360]), has_audio
    if has_video:
        return [360, 480, 720, 1080], has_audio
    return [], has_audio


def _fetch_video_info(url: str) -> dict:
    """Fetch video title, thumbnail, duration, and available qualities via yt-dlp."""
    now = time.time()
    with info_cache_lock:
        cached = info_cache.get(url)
        if cached and cached[0] > now:
            return cached[1]

    capture_logger = _YdlCaptureLogger()
    ydl_opts: dict = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "usenetrc": False,
        "enable_file_urls": False,
        "logger": capture_logger,
        "socket_timeout": 20,
        "retries": 2,
        "match_filter": _build_match_filter(),
    }
    detected_runtimes = {
        rt: {} for rt in ("deno", "node", "bun", "quickjs") if shutil.which(rt)
    }
    if detected_runtimes:
        ydl_opts["js_runtimes"] = detected_runtimes

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise RuntimeError("No media info returned.")

    formats = info.get("formats") or []
    qualities, has_audio = _extract_qualities(formats)
    if not qualities and not has_audio:
        raise RuntimeError("No suitable downloadable formats found.")

    thumb = info.get("thumbnail") or ""
    if not isinstance(thumb, str) or not thumb.startswith("https://"):
        thumb = ""

    raw_dur = info.get("duration")
    duration = int(round(raw_dur)) if isinstance(raw_dur, (int, float)) and raw_dur >= 0 else None
    source = str(info.get("extractor_key") or "").replace("_", " ").strip()
    if source.lower() == "generic":
        source = urlparse(url).hostname or "Direct link"

    payload = {
        "title": str(info.get("title") or "Untitled video").strip()[:240],
        "duration": duration,
        "thumbnail": thumb,
        "qualities": qualities,
        "hasAudio": has_audio,
        "source": source[:60],
    }

    with info_cache_lock:
        if len(info_cache) >= 100:
            oldest_key = next(iter(info_cache))
            info_cache.pop(oldest_key, None)
        info_cache[url] = (now + _INFO_CACHE_TTL, payload)

    return payload


@app.route("/api/info", methods=["POST"])
@limiter.limit("30 per minute")
def inspect_url():
    """Return preview metadata (title, thumbnail, duration, real qualities) for a link."""
    origin = request.headers.get("Origin", "").strip()
    if origin:
        parsed_origin = urlparse(origin)
        if parsed_origin.netloc.lower() != request.host.lower():
            return jsonify({"error": "Cross-origin requests are not allowed."}), 403

    json_data = request.get_json(silent=True)
    if not isinstance(json_data, dict):
        json_data = {}
    url_raw = request.form.get("url")
    if url_raw is None:
        url_raw = json_data.get("url")

    url, err = _validate_url(url_raw)
    if err:
        return jsonify({"error": err}), 400

    try:
        data = _fetch_video_info(url)
        return jsonify(data), 200
    except Exception as exc:
        friendly = _classify_error(str(exc))
        logger.warning("Info check failed for %s: %s", url[:80], exc)
        return jsonify({"error": friendly}), 422


@app.route("/api/jobs", methods=["POST"])
@limiter.limit("5 per minute;50 per day")
def create_job():
    """Validate input, check capacity, spawn background download thread."""
    origin = request.headers.get("Origin", "").strip()
    if origin:
        parsed_origin = urlparse(origin)
        if parsed_origin.netloc.lower() != request.host.lower():
            return jsonify({"error": "Cross-origin requests are not allowed."}), 403

    json_data = request.get_json(silent=True)
    if not isinstance(json_data, dict):
        json_data = {}

    url_raw = request.form.get("url")
    if url_raw is None:
        url_raw = json_data.get("url")

    fmt = request.form.get("format")
    if fmt is None:
        fmt = json_data.get("format")

    quality_raw = request.form.get("quality")
    if quality_raw is None:
        quality_raw = json_data.get("quality")

    # Validate URL (+ SSRF protection)
    url, err = _validate_url(url_raw)
    if err:
        return jsonify({"error": err}), 400

    # Whitelist format
    if not isinstance(fmt, str) or fmt not in VALID_FORMATS:
        return jsonify({"error": "Please choose MP4 or MP3."}), 400

    # Whitelist quality (required, no default)
    if (
        quality_raw is None
        or isinstance(quality_raw, bool)
        or str(quality_raw).strip() == ""
    ):
        return jsonify({"error": "Please choose a quality."}), 400

    try:
        quality = int(str(quality_raw).strip())
    except (ValueError, TypeError):
        return jsonify({"error": "Please choose a quality."}), 400

    if fmt == "mp4" and quality not in MP4_QUALITIES:
        return jsonify(
            {"error": "Please choose a valid MP4 quality (360, 480, 720, or 1080)."}
        ), 400
    if fmt == "mp3" and quality not in MP3_BITRATES:
        return jsonify(
            {"error": "Please choose a valid MP3 bitrate (128, 192, or 320)."}
        ), 400

    # Capacity check
    with jobs_lock:
        active = sum(
            1
            for j in jobs.values()
            if j["status"] in ("queued", "downloading", "processing")
        )
        if active >= MAX_CONCURRENT_JOBS:
            return jsonify(
                {"error": "The server is busy. Please try again in a minute."}
            ), 503

        job_id = uuid.uuid4().hex
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Queued…",
            "filename": None,
            "path": None,
            "error": None,
            "created_at": time.time(),
        }

    logger.info("Job %s created (%s %s) for %s", job_id, fmt, quality, url[:100])

    thread = threading.Thread(
        target=_run_download,
        args=(job_id, url, fmt, quality),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id}), 202


@app.route("/api/jobs/<job_id>")
@limiter.limit("120 per minute")
def job_status(job_id: str):
    """Return current job progress."""
    if not _JOB_ID_RE.match(job_id):
        return jsonify({"error": "Job not found."}), 404

    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        result = {
            "status": job["status"],
            "progress": job["progress"],
            "message": job["message"],
        }
        if job["error"]:
            result["error"] = job["error"]
        if job["filename"]:
            result["filename"] = job["filename"]

    return jsonify(result)


@app.route("/api/jobs/<job_id>/file")
@limiter.limit("120 per minute")
def job_file(job_id: str):
    """Stream the finished file with Range support and no-store caching."""
    if not _JOB_ID_RE.match(job_id):
        return jsonify({"error": "Job not found."}), 404

    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        status = job["status"]
        filepath = job.get("path")
        filename = job.get("filename")

    if status != "done":
        return jsonify({"error": "File is not ready yet."}), 409
    if not filepath or not filename or not os.path.isfile(filepath):
        return jsonify({"error": "File is no longer available."}), 404

    mime = "audio/mpeg" if filename.endswith(".mp3") else "video/mp4"
    resp = send_file(
        filepath,
        as_attachment=True,
        download_name=filename,
        mimetype=mime,
        conditional=True,
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------------------
# Custom JSON error handlers
# ---------------------------------------------------------------------------


@app.errorhandler(403)
def _handle_403(exc):
    return jsonify({"error": "Request not allowed."}), 403


@app.errorhandler(404)
def _handle_404(exc):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found."}), 404
    return render_template("index.html"), 404


@app.errorhandler(405)
def _handle_405(exc):
    return jsonify({"error": "Method not allowed."}), 405


@app.errorhandler(429)
def _handle_429(exc):
    return jsonify(
        {"error": "Too many requests. Please wait a minute and try again."}
    ), 429


@app.errorhandler(500)
def _handle_500(exc):
    return jsonify(
        {"error": "Something went wrong on our end. Please try again later."}
    ), 500


# ---------------------------------------------------------------------------
# Local development entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
