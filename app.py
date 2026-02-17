"""
YouTube Downloader — Flask Backend (Railway-ready)
"""

import os
import re
import json
import subprocess
import tempfile
import sys
from pathlib import Path

from flask import Flask, request, jsonify, render_template, Response, stream_with_context

app = Flask(__name__)

# ── Find yt-dlp ───────────────────────────────────────────────────────────────
def get_ytdlp():
    try:
        r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        if r.returncode == 0:
            return "yt-dlp"
    except FileNotFoundError:
        pass
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], capture_output=True, text=True)
        if r.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass
    return None

YTDLP = get_ytdlp()

# ── Bypass args for cloud/Railway IPs ────────────────────────────────────────
BYPASS_ARGS = [
    "--extractor-args", "youtube:player_client=web",
    "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "--sleep-interval", "1",
    "--max-sleep-interval", "3",
    "--no-cache-dir",
]

# ── Helpers ───────────────────────────────────────────────────────────────────
YOUTUBE_RE = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|embed/)|youtu\.be/)"
    r"[\w\-]{11}"
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
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
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

def build_format_label(height):
    h = height or 0
    if h >= 4320: return "8K"
    if h >= 2160: return "4K"
    if h >= 1440: return "2K"
    return f"{h}p" if h else "?"

def run_ytdlp(args):
    base = YTDLP if isinstance(YTDLP, list) else [YTDLP]
    cmd = base + BYPASS_ARGS + args
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout, result.stderr, result.returncode

def run_ytdlp_fallback(args):
    base = YTDLP if isinstance(YTDLP, list) else [YTDLP]
    fallback = [
        "--extractor-args", "youtube:player_client=tv_embedded",
        "--user-agent", "Mozilla/5.0 (SMART-TV; Linux; Tizen 5.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/5.0 TV Safari/538.1",
        "--no-cache-dir",
    ]
    cmd = base + fallback + args
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout, result.stderr, result.returncode


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    if not YTDLP:
        return jsonify({"error": "yt-dlp not installed. Run: pip install yt-dlp"}), 503

    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL."}), 400

    stdout, stderr, code = run_ytdlp(["--dump-json", "--no-playlist", url])
    if code != 0:
        stdout, stderr, code = run_ytdlp_fallback(["--dump-json", "--no-playlist", url])

    if code != 0:
        err = stderr.lower()
        if "sign in" in err or "age" in err:
            return jsonify({"error": "YouTube is blocking this server's IP. Try a different video or set up cookie auth."}), 403
        if "unavailable" in err or "private" in err:
            return jsonify({"error": "This video is unavailable or private."}), 404
        if "429" in stderr or "too many" in err:
            return jsonify({"error": "Rate limited by YouTube. Please wait a moment and try again."}), 429
        return jsonify({"error": f"Could not fetch video info: {stderr[:300]}"}), 400

    try:
        info = json.loads(stdout)
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to parse video info."}), 500

    all_fmts = info.get("formats") or []

    audio_streams = [
        f for f in all_fmts
        if f.get("vcodec", "none") == "none" and f.get("acodec", "none") != "none"
    ]
    audio_streams.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0, reverse=True)
    best_audio     = audio_streams[0] if audio_streams else None
    best_audio_id  = best_audio.get("format_id") if best_audio else "bestaudio"
    best_audio_abr = (best_audio.get("abr") or best_audio.get("tbr") or 128) if best_audio else 128

    seen_res, video_fmts = set(), []
    for target_h in [4320, 2160, 1440, 1080, 720, 480, 360, 240, 144]:
        candidates = [
            f for f in all_fmts
            if f.get("vcodec", "none") != "none" and f.get("height") == target_h
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda f: (1 if (f.get("ext") or "") == "mp4" else 0, f.get("tbr") or 0))
        h = best.get("height")
        if h in seen_res:
            continue
        seen_res.add(h)
        vid_bytes = best.get("filesize") or best.get("filesize_approx") or 0
        aud_bytes = (best_audio.get("filesize") or best_audio.get("filesize_approx") or 0) if best_audio else 0
        video_fmts.append({
            "format_id":       best["format_id"],
            "audio_format_id": best_audio_id,
            "resolution":      build_format_label(h),
            "height":          h,
            "ext":             "MP4",
            "filesize":        human_size(vid_bytes + aud_bytes) if (vid_bytes + aud_bytes) > 0 else "~",
            "fps":             best.get("fps"),
            "audio_abr":       f"{int(best_audio_abr)}kbps",
        })

    audio_fmts = []
    if audio_streams:
        src_abr = int(best_audio_abr or 128)
        for tier in [
            {"bitrate": "320", "label": "320 kbps", "quality": "Maximum"},
            {"bitrate": "256", "label": "256 kbps", "quality": "High"},
            {"bitrate": "192", "label": "192 kbps", "quality": "Standard"},
            {"bitrate": "128", "label": "128 kbps", "quality": "Compressed"},
        ]:
            audio_fmts.append({
                "format_id": best_audio_id,
                "ext":       "MP3",
                "bitrate":   tier["bitrate"],
                "label":     tier["label"],
                "quality":   tier["quality"],
                "src_abr":   f"{src_abr}kbps source",
            })
        m4a_abr = int(best_audio.get("abr") or best_audio.get("tbr") or 128)
        audio_fmts.append({
            "format_id": best_audio_id,
            "ext":       "M4A",
            "bitrate":   str(m4a_abr),
            "label":     f"{m4a_abr} kbps",
            "quality":   "Original AAC (no re-encode)",
            "src_abr":   "native",
        })

    subs, auto_subs = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    sub_langs = []
    for lang, entries in {**subs, **auto_subs}.items():
        if entries:
            label = f"{lang} (auto)" if lang in auto_subs and lang not in subs else lang
            sub_langs.append({"code": lang, "label": label})

    return jsonify({
        "title":         info.get("title", "Unknown"),
        "channel":       info.get("uploader") or info.get("channel", "Unknown"),
        "thumbnail":     info.get("thumbnail", ""),
        "duration":      format_duration(info.get("duration")),
        "view_count":    f"{info.get('view_count', 0):,}",
        "webpage_url":   info.get("webpage_url", url),
        "video_formats": video_fmts,
        "audio_formats": audio_fmts,
        "subtitles":     sub_langs,
    })


@app.route("/api/download", methods=["POST"])
def api_download():
    if not YTDLP:
        return jsonify({"error": "yt-dlp not found."}), 503

    data            = request.get_json(silent=True) or {}
    url             = (data.get("url") or "").strip()
    format_id       = (data.get("format_id") or "").strip()
    audio_format_id = (data.get("audio_format_id") or "").strip()
    media_type      = data.get("type", "video")
    mp3_bitrate     = str(data.get("bitrate") or "192")
    ext             = str(data.get("ext") or "").upper()

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid URL."}), 400

    tmp_dir  = tempfile.mkdtemp()
    out_tmpl = os.path.join(tmp_dir, "%(title)s.%(ext)s")

    if media_type == "audio":
        if ext == "M4A":
            args = [
                "--format", f"{format_id}/bestaudio[ext=m4a]/bestaudio/best" if format_id else "bestaudio[ext=m4a]/bestaudio/best",
                "--output", out_tmpl, "--no-playlist", url,
            ]
        else:
            args = [
                "--format", format_id if format_id else "bestaudio/best",
                "--output", out_tmpl, "--no-playlist",
                "--extract-audio", "--audio-format", "mp3",
                "--audio-quality", mp3_bitrate + "K", url,
            ]
    else:
        if format_id and audio_format_id:
            fmt_spec = f"{format_id}+{audio_format_id}"
        elif format_id:
            fmt_spec = f"{format_id}+bestaudio/best"
        else:
            fmt_spec = "bestvideo+bestaudio/best"
        args = [
            "--format", fmt_spec,
            "--output", out_tmpl, "--no-playlist",
            "--merge-output-format", "mp4", "--prefer-ffmpeg", url,
        ]

    stdout, stderr, code = run_ytdlp(args)
    if code != 0:
        stdout, stderr, code = run_ytdlp_fallback(args)
    if code != 0:
        return jsonify({"error": f"Processing failed: {stderr[:400]}"}), 500

    actual = None
    for f in sorted(Path(tmp_dir).iterdir()):
        if f.suffix.lower() in (".mp4", ".webm", ".mp3", ".m4a", ".mkv", ".opus", ".aac"):
            actual = f
            break

    if not actual or not actual.exists():
        return jsonify({"error": "Output file not found after processing."}), 500

    safe_title = re.sub(r'[^\w\s\-]', '', actual.stem)[:80].strip()
    dl_name    = f"{safe_title}{actual.suffix}"
    file_size  = actual.stat().st_size
    mime = {
        ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/ogg", ".aac": "audio/aac",
    }.get(actual.suffix.lower(), "application/octet-stream")

    def generate():
        with open(actual, "rb") as fh:
            while chunk := fh.read(65536):
                yield chunk
        try:
            for p in Path(tmp_dir).iterdir():
                p.unlink(missing_ok=True)
            Path(tmp_dir).rmdir()
        except Exception:
            pass

    return Response(
        stream_with_context(generate()),
        mimetype=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{dl_name}"',
            "Content-Length":      str(file_size),
            "X-Accel-Buffering":   "no",
            "Cache-Control":       "no-cache",
        }
    )


@app.route("/api/subtitle", methods=["POST"])
def api_subtitle():
    if not YTDLP:
        return jsonify({"error": "yt-dlp not found."}), 503

    data     = request.get_json(silent=True) or {}
    url      = (data.get("url") or "").strip()
    sub_lang = (data.get("lang") or "en").strip()

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid URL."}), 400

    tmp_dir = tempfile.mkdtemp()
    run_ytdlp([
        "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", sub_lang, "--sub-format", "srt",
        "--output", os.path.join(tmp_dir, "sub.%(ext)s"),
        "--no-playlist", "--quiet", url,
    ])

    srt_file = next((f for f in Path(tmp_dir).iterdir() if f.suffix.lower() == ".srt"), None)
    if not srt_file:
        return jsonify({"error": f"No subtitles found for '{sub_lang}'."}), 404

    content = srt_file.read_text(encoding="utf-8", errors="replace")
    for p in Path(tmp_dir).iterdir():
        p.unlink(missing_ok=True)
    Path(tmp_dir).rmdir()

    return Response(
        content, mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="subtitles_{sub_lang}.srt"'}
    )


if __name__ == "__main__":
    print(f"yt-dlp: {YTDLP}")
    app.run(debug=True, port=5000, threaded=True)
