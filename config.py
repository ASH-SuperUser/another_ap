"""another_ap : another audio player — central config.

Edit values here to change how the server runs. Everything is local,
no accounts, no cloud. Anyone on your LAN with the URL gets full control.
"""
from __future__ import annotations

import os

# ---- network ----
HOST: str = os.environ.get("ANOTHER_AP_HOST", "127.0.0.1")
PORT: int = int(os.environ.get("ANOTHER_AP_PORT", "5050"))

# ---- paths (all relative to repo root) ----
ROOT_DIR: str = os.path.dirname(os.path.abspath(__file__))
DATA_DIR: str = os.path.join(ROOT_DIR, "data")
AUDIO_DIR: str = os.path.join(DATA_DIR, "audio")
THUMB_DIR: str = os.path.join(DATA_DIR, "thumbs")
DB_PATH: str = os.path.join(DATA_DIR, "main_db.db")

UI_DIR: str = os.path.join(ROOT_DIR, "ui")
TEMPLATES_DIR: str = os.path.join(UI_DIR, "templates")
STATIC_DIR: str = os.path.join(UI_DIR, "static")

# ---- app ----
APP_NAME: str = "another_ap"
APP_TAGLINE: str = "another audio player // minimal terminal-grade streaming"

# suggestions / pagination
HOME_SUGGEST_N: int = 8
PAGE_SIZE: int = 50
MAX_UPLOAD_MB: int = 500

# audio formats we accept / serve
ALLOWED_AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".flac", ".webm", ".mp4"}
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# youtube download: preferred codec (falls back gracefully if ffmpeg missing)
YT_PREFERRED_CODEC: str = "mp3"
YT_PREFERRED_QUALITY: str = "192"
