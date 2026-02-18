"""
YouTube Downloader — Clean Production Version
"""

import os
import re
import json
import subprocess
import tempfile
import sys
from pathlib import Path

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response,
    send_file,
)

app = Flask(__name__)


# ─────────────────────────────────────────────
# Find yt-dlp
# ─────────────────────────────────────────────
def get_ytdlp():
    try:
        r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        if r.returncode == 0:
            return ["yt-dlp"]
    except FileNotFoundError:
        pass

    try:
        r = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass

    return None


YTDLP = get_ytdlp()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/|v/)|youtu\.be/)"
    r"([\w\-]{11})"
)


def is_valid_youtube_url(url):
    return bool(YOUTUBE_RE.search(url))


def format_duration(seconds):
    if not seconds:
        return "Unknown"
    try:
        seconds = int(seconds)
    except Exception:
        return "Unknown"

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def human_size(n_bytes):
    if not n_bytes:
        return "~"
    try:
        n_bytes = float(n_bytes)
    except Exception:
        return "~"

    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024

    return f"{n_bytes:.1f} TB"


def run_ytdlp(args):
    cmd = YTDLP + args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout, result.stderr, result.returncode


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    if not YTDLP:
        return jsonify({"error": "yt-dlp not installed"}), 503

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    stdout, stderr, code = run_ytdlp(["--dump-json", "--no-playlist", url])

    if code != 0:
        return jsonify({"error": stderr[:300]}), 400

    info = json.loads(stdout)
    all_fmts = info.get("formats") or []

    # Get best audio
    audio_streams = [
        f for f in all_fmts
        if f.get("vcodec") == "none" and f.get("acodec") != "none"
    ]
    audio_streams.sort(key=lambda f: f.get("abr") or 0, reverse=True)
    best_audio = audio_streams[0] if audio_streams else None

    # Build video list
    res_map = {}

    for f in all_fmts:
        if f.get("vcodec") == "none":
            continue
        if not f.get("height"):
            continue

        height = f["height"]

        if height not in res_map or (
            f.get("tbr") or 0
        ) > (res_map[height].get("tbr") or 0):
            res_map[height] = f

    video_formats = []

    for height, f in res_map.items():
        vid_bytes = f.get("filesize") or f.get("filesize_approx") or 0
        aud_bytes = (
            best_audio.get("filesize") if best_audio else 0
        )

        video_formats.append({
            "format_id": f["format_id"],
            "resolution": f"{height}p",
            "height": height,
            "ext": "MP4",
            "filesize": human_size(vid_bytes + aud_bytes),
        })

    video_formats.sort(key=lambda x: x["height"], reverse=True)

    return jsonify({
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": format_duration(info.get("duration")),
        "video_formats": video_formats,
    })


@app.route("/api/download", methods=["POST"])
def api_download():
    if not YTDLP:
        return jsonify({"error": "yt-dlp not found"}), 503

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    format_id = (data.get("format_id") or "").strip()

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid URL"}), 400

    tmp_dir = tempfile.mkdtemp()
    out_tmpl = os.path.join(tmp_dir, "%(title)s.%(ext)s")

    fmt_spec = f"{format_id}+bestaudio"

    args = [
        "--format", fmt_spec,
        "--merge-output-format", "mp4",
        "--output", out_tmpl,
        url,
    ]

    stdout, stderr, code = run_ytdlp(args)

    if code != 0:
        return jsonify({"error": stderr[:400]}), 500

    actual = None
    for f in Path(tmp_dir).iterdir():
        if f.suffix.lower() == ".mp4":
            actual = f
            break

    if not actual:
        return jsonify({"error": "Download failed"}), 500

    safe_title = re.sub(r"[^\w\s\-]", "", actual.stem)[:80]
    dl_name = f"{safe_title}.mp4"

    return send_file(
        actual,
        as_attachment=True,
        download_name=dl_name,
    )


@app.route("/debug/ffmpeg")
def debug_ffmpeg():
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
        )
        return f"<pre>{result.stdout}</pre>"
    except Exception as e:
        return str(e)


if __name__ == "__main__":
    app.run(debug=True)
