# YT·DL — YouTube Downloader

A premium, minimal YouTube downloader with a Flask backend and modern black/white UI.

---

## ✅ Features

- Paste any YouTube URL → Fetch video info instantly
- Dynamic quality selection (144p → 8K where available)
- Audio-only download (MP3 via ffmpeg conversion)
- Subtitle download (.srt) with language selection
- Auto-merges video + audio for high-res formats (1080p+)
- Clean error handling — no crashes, no random videos
- Horizontal-scroll URL input (no layout breaking)
- Progress bar during download
- Fully responsive (mobile-ready)

---

## 📦 Requirements

- Python 3.9+
- ffmpeg (for merging 1080p+ video + audio streams)

---

## 🚀 Quick Start

### 1. Clone / copy project files

```
yt-downloader/
├── app.py
├── requirements.txt
├── templates/
│   └── index.html
└── README.md
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

> If pip install fails due to network issues in some sandboxed environments, install manually:
> ```bash
> pip install flask yt-dlp flask-cors
> ```

### 3. Install ffmpeg (required for 1080p/4K/8K merging)

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt install ffmpeg
```

**Windows:**
Download from https://ffmpeg.org/download.html and add to PATH.

### 4. Run the server

```bash
python app.py
```

Open: **http://localhost:5000**

---

## 🔌 API Reference

### `POST /api/info`
Fetch video metadata and available formats.

**Request:**
```json
{ "url": "https://www.youtube.com/watch?v=VIDEO_ID" }
```

**Response:**
```json
{
  "title": "Video Title",
  "channel": "Channel Name",
  "thumbnail": "https://...",
  "duration": "3:45",
  "view_count": "1,234,567",
  "video_formats": [...],
  "audio_formats": [...],
  "subtitles": [{"code": "en", "label": "English (auto)"}]
}
```

---

### `POST /api/download`
Stream a video or audio download.

**Request:**
```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "format_id": "137",
  "type": "video",
  "needs_merge": true
}
```

Returns binary stream with `Content-Disposition` header.

---

### `POST /api/subtitle`
Download subtitle file.

**Request:**
```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "lang": "en"
}
```

Returns `.srt` file as plain text.

---

## 🎨 UI Design

- **Fonts:** Syne (headings) + DM Sans (body)
- **Background:** `#0F0F0F`
- **Cards:** `#181818`
- **Border:** `#2A2A2A`
- Pure black/white/off-white palette — no gradients

---

## ⚠️ Legal Notice

This tool is for **personal use only**.
Respect YouTube's [Terms of Service](https://www.youtube.com/t/terms) and copyright law.
Do not download content you don't have permission to download.
