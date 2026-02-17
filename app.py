"""
YouTube Downloader — Flask Backend
Calls yt-dlp via subprocess (same as terminal) to avoid bot detection.

KEY FIX: All video downloads use server-side ffmpeg merge (video+audio).
         Direct URLs are NOT used for video — YouTube serves video/audio
         as separate streams so a direct video URL has NO audio track.
         MP3 and M4A also go through the server for reliability.
"""

import os
import re
import json
import subprocess
import tempfile
import sys
from pathlib import Path

from flask import (
    Flask, request, jsonify, render_template,
    Response, stream_with_context
)

app = Flask(__name__)

# ── Find yt-dlp executable ────────────────────────────────────────────────────
def get_ytdlp():
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            return "yt-dlp"
    except FileNotFoundError:
        pass
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass
    return None

YTDLP = get_ytdlp()

# ── Helpers ───────────────────────────────────────────────────────────────────
YOUTUBE_RE = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|embed/)|youtu\.be/)"
    r"[\w\-]{11}"
)

def is_valid_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_RE.search(url))

def format_duration(seconds) -> str:
    if not seconds:
        return "Unknown"
    try:
        seconds = int(seconds)
    except:
        return "Unknown"
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def human_size(n_bytes) -> str:
    if not n_bytes:
        return "~"
    try:
        n_bytes = float(n_bytes)
    except:
        return "~"
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"

def build_format_label(height) -> str:
    h = height or 0
    if h >= 4320: return "8K"
    if h >= 2160: return "4K"
    if h >= 1440: return "2K"
    return f"{h}p" if h else "?"

def run_ytdlp(args: list) -> tuple:
    if isinstance(YTDLP, list):
        cmd = YTDLP + args
    else:
        cmd = [YTDLP] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.stdout, result.stderr, result.returncode


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    if not YTDLP:
        return jsonify({"error": "yt-dlp not found. Run: pip install yt-dlp"}), 503

    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL."}), 400

    stdout, stderr, code = run_ytdlp(["--dump-json", "--no-playlist", url])

    if code != 0:
        err = stderr.lower()
        if "sign in" in err or "age" in err:
            return jsonify({"error": "This video requires sign-in or is age-restricted."}), 403
        if "unavailable" in err or "private" in err:
            return jsonify({"error": "This video is unavailable or private."}), 404
        return jsonify({"error": f"Could not fetch video info. {stderr[:200]}"}), 400

    try:
        info = json.loads(stdout)
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to parse video info."}), 500

    all_fmts = info.get("formats") or []

    # ── Best audio-only stream (for merging with video) ───────────────────
    audio_streams = [
        f for f in all_fmts
        if f.get("vcodec", "none") == "none"
        and f.get("acodec", "none") != "none"
    ]
    audio_streams.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0, reverse=True)
    best_audio    = audio_streams[0] if audio_streams else None
    best_audio_id = best_audio.get("format_id") if best_audio else "bestaudio"
    best_audio_abr = (
        best_audio.get("abr") or best_audio.get("tbr") or 128
    ) if best_audio else 128

    # ── Video formats — ALL require server-side merge ─────────────────────
    # YouTube ALWAYS delivers video and audio as separate DASH streams above
    # ~360p. A "direct" video URL contains NO audio. We must merge them.
    seen_res   = set()
    video_fmts = []
    HEIGHT_ORDER = [4320, 2160, 1440, 1080, 720, 480, 360, 240, 144]

    for target_h in HEIGHT_ORDER:
        candidates = [
            f for f in all_fmts
            if f.get("vcodec", "none") != "none"
            and f.get("height") == target_h
        ]
        if not candidates:
            continue

        best = max(candidates, key=lambda f: (
            1 if (f.get("ext") or "") == "mp4" else 0,
            f.get("tbr") or 0
        ))

        h = best.get("height")
        if h in seen_res:
            continue
        seen_res.add(h)

        # Combined estimated size
        vid_bytes = best.get("filesize") or best.get("filesize_approx") or 0
        aud_bytes = (best_audio.get("filesize") or best_audio.get("filesize_approx") or 0) if best_audio else 0
        combined  = human_size(vid_bytes + aud_bytes) if (vid_bytes + aud_bytes) > 0 else "~"

        video_fmts.append({
            "format_id":       best["format_id"],
            "audio_format_id": best_audio_id,
            "resolution":      build_format_label(h),
            "height":          h,
            "ext":             "MP4",
            "filesize":        combined,
            "fps":             best.get("fps"),
            "audio_abr":       f"{int(best_audio_abr)}kbps",
        })

    # ── Audio-only formats ────────────────────────────────────────────────
    # All go through the server. MP3 needs ffmpeg re-encode.
    # M4A copies the raw AAC stream (no quality loss).
    # Show all 4 MP3 tiers up to 320 kbps.
    MP3_TIERS = [
        {"bitrate": "320", "label": "320 kbps", "quality": "Maximum"},
        {"bitrate": "256", "label": "256 kbps", "quality": "High"},
        {"bitrate": "192", "label": "192 kbps", "quality": "Standard"},
        {"bitrate": "128", "label": "128 kbps", "quality": "Compressed"},
    ]

    audio_fmts = []
    if audio_streams:
        src_abr = int(best_audio_abr or 128)
        for tier in MP3_TIERS:
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

    # ── Subtitles ─────────────────────────────────────────────────────────
    subs      = info.get("subtitles") or {}
    auto_subs = info.get("automatic_captions") or {}
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
    """
    Unified download handler:
      video  → download video+audio DASH streams, merge to MP4 via ffmpeg
      audio  → MP3: re-encode to mp3 at bitrate | M4A: copy AAC stream
    """
    if not YTDLP:
        return jsonify({"error": "yt-dlp not found."}), 503

    data            = request.get_json(silent=True) or {}
    url             = (data.get("url") or "").strip()
    format_id       = (data.get("format_id") or "").strip()
    audio_format_id = (data.get("audio_format_id") or "").strip()
    media_type      = data.get("type", "video")   # "video" | "audio"
    mp3_bitrate     = str(data.get("bitrate") or "192")
    ext             = str(data.get("ext") or "").upper()

    if not url or not is_valid_youtube_url(url):
        return jsonify({"error": "Invalid URL."}), 400

    tmp_dir  = tempfile.mkdtemp()
    out_tmpl = os.path.join(tmp_dir, "%(title)s.%(ext)s")

    if media_type == "audio":
        if ext == "M4A":
            # Copy the raw AAC/M4A audio stream — no re-encode, best quality
            args = [
                "--format", f"{format_id}/bestaudio[ext=m4a]/bestaudio/best" if format_id else "bestaudio[ext=m4a]/bestaudio/best",
                "--output", out_tmpl,
                "--no-playlist",
                url,
            ]
        else:
            # MP3: download best audio, ffmpeg re-encodes to mp3
            fmt = format_id if format_id else "bestaudio/best"
            args = [
                "--format", fmt,
                "--output", out_tmpl,
                "--no-playlist",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", mp3_bitrate + "K",
                url,
            ]
    else:
        # VIDEO: explicitly combine video+audio format IDs
        # This guarantees audio is present in the output file
        if format_id and audio_format_id:
            fmt_spec = f"{format_id}+{audio_format_id}"
        elif format_id:
            fmt_spec = f"{format_id}+bestaudio/best"
        else:
            fmt_spec = "bestvideo+bestaudio/best"

        args = [
            "--format", fmt_spec,
            "--output", out_tmpl,
            "--no-playlist",
            "--merge-output-format", "mp4",
            "--prefer-ffmpeg",
            url,
        ]

    stdout, stderr, code = run_ytdlp(args)

    if code != 0:
        return jsonify({"error": f"Processing failed: {stderr[:400]}"}), 500

    # Find the output file
    actual = None
    extensions = (".mp4", ".webm", ".mp3", ".m4a", ".mkv", ".opus", ".aac")
    for f in sorted(Path(tmp_dir).iterdir()):
        if f.suffix.lower() in extensions:
            actual = f
            break

    if not actual or not actual.exists():
        return jsonify({"error": "Output file not found after processing."}), 500

    safe_title = re.sub(r'[^\w\s\-]', '', actual.stem)[:80].strip()
    dl_name    = f"{safe_title}{actual.suffix}"
    file_size  = actual.stat().st_size

    suffix = actual.suffix.lower()
    mime_map = {
        ".mp4":  "video/mp4",
        ".webm": "video/webm",
        ".mkv":  "video/x-matroska",
        ".mp3":  "audio/mpeg",
        ".m4a":  "audio/mp4",
        ".opus": "audio/ogg",
        ".aac":  "audio/aac",
    }
    mime = mime_map.get(suffix, "application/octet-stream")

    def generate():
        with open(actual, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
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
    args = [
        "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", sub_lang,
        "--sub-format", "srt",
        "--output", os.path.join(tmp_dir, "sub.%(ext)s"),
        "--no-playlist", "--quiet",
        url,
    ]
    run_ytdlp(args)

    srt_file = None
    for f in Path(tmp_dir).iterdir():
        if f.suffix.lower() == ".srt":
            srt_file = f
            break

    if not srt_file:
        return jsonify({"error": f"No subtitles found for '{sub_lang}'."}), 404

    content = srt_file.read_text(encoding="utf-8", errors="replace")
    for p in Path(tmp_dir).iterdir():
        p.unlink(missing_ok=True)
    Path(tmp_dir).rmdir()

    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="subtitles_{sub_lang}.srt"'}
    )


if __name__ == "__main__":
    print("YouTube Downloader — http://localhost:5000")
    print(f"   yt-dlp found: {YTDLP is not None} ({YTDLP})")
    app.run(debug=True, port=5000, threaded=True)
