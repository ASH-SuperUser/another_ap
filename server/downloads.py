"""Download helpers: direct-URL mp3 fetch + YouTube via yt-dlp (both python-only)."""
from __future__ import annotations

import os
import re
import time

import config


def _safe_name(name: str, default: str = "audio") -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or default).strip())[:80].strip("._")
    return base or default


def fetch_direct_url(url: str) -> dict:
    """Download a remote audio file to data/audio/. Returns {filename, path, size, content_type}."""
    import httpx

    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    # stream so big files don't blow RAM; cap at MAX_UPLOAD_MB
    cap = config.MAX_UPLOAD_MB * 1024 * 1024
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        # guess filename
        cd = r.headers.get("content-disposition", "")
        m = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", cd)
        if m:
            fname = m.group(1).strip().strip('"')
        else:
            fname = url.split("?")[0].rstrip("/").split("/")[-1] or f"fetch_{int(time.time())}.mp3"
        fname = _safe_name(fname)
        if os.path.splitext(fname)[1].lower() not in config.ALLOWED_AUDIO_EXTS:
            # keep server honest: still allow but force .mp3 ext if unknown
            if "." not in fname:
                fname += ".mp3"
        stamp = str(int(time.time() * 1000))[-6:]
        fname = f"{os.path.splitext(fname)[0][:60]}_{stamp}{os.path.splitext(fname)[1]}"
        dest = os.path.join(config.AUDIO_DIR, fname)
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(65536):
                size += len(chunk)
                if size > cap:
                    f.close()
                    os.remove(dest)
                    raise ValueError(f"file exceeds {config.MAX_UPLOAD_MB}MB limit")
                f.write(chunk)
    return {"filename": fname, "path": dest, "size": size, "content_type": ctype}


def yt_info(url: str) -> dict:
    """Fetch metadata (no download) for prefill. Raises RuntimeError on failure."""
    try:
        import yt_dlp  # type: ignore
    except Exception as e:
        raise RuntimeError("yt-dlp is not installed. Run scripts/run.* first.") from e
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise RuntimeError(f"could not read that URL: {e}") from e
    if not info:
        raise RuntimeError("no metadata found for that URL")
    thumb = info.get("thumbnail")
    return {
        "title": info.get("title") or "untitled",
        "artist": info.get("uploader") or info.get("channel") or "unknown",
        "description": (info.get("description") or "")[:2000],
        "duration": info.get("duration"),
        "thumbnail": thumb,
        "webpage_url": info.get("webpage_url") or url,
    }


def has_ffmpeg() -> bool:
    """True when a working ffmpeg binary is on PATH (needed for mp3 extract)."""
    import shutil

    return shutil.which("ffmpeg") is not None


def fetch_image(url: str, prefix: str = "preview_url") -> dict:
    """Download an image URL into thumbs/ for the cover picker. Validated."""
    import httpx

    if not url.startswith(("http://", "https://")):
        raise RuntimeError("url must start with http(s)://")
    os.makedirs(config.THUMB_DIR, exist_ok=True)
    cap = 10 * 1024 * 1024
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "another_ap/1.0"})
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"could not fetch image: {e}") from e
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = {  # content-type first, url suffix as fallback
        "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(ctype)
    if ext is None:
        m = re.search(r"\.(png|jpe?g|webp|gif)(\?|$)", url, re.IGNORECASE)
        if not m:
            raise RuntimeError(f"not an image (content-type: {ctype or 'unknown'})")
        ext = "." + m.group(1).lower().replace("jpeg", "jpg")
    if len(r.content) > cap:
        raise RuntimeError("image exceeds 10MB")
    if len(r.content) < 64:
        raise RuntimeError("image is empty")
    import time as _t

    stamp = str(int(_t.time() * 1000))[-6:]
    dest = os.path.join(config.THUMB_DIR, f"{prefix}_{stamp}{ext}")
    with open(dest, "wb") as f:
        f.write(r.content)
    return {"thumb_path": dest, "thumb_url": "/thumbs/" + os.path.basename(dest),
            "size": len(r.content)}


def yt_download(url: str, progress_hook=None) -> dict:
    """Download best-audio via yt-dlp in a SINGLE attempt.

    - ffmpeg present  -> extract to mp3 (config.YT_PREFERRED_CODEC).
    - ffmpeg missing  -> grab m4a directly (plays in every browser incl.
      Safari, mutagen-readable). No doomed mp3 attempt, no ERROR spam,
      no double download.
    Returns dict with path/filename/size/metadata + `codec` + `mp3` flag
    so the UI can tell the user exactly what landed on disk.
    """
    try:
        import yt_dlp  # type: ignore
    except Exception as e:
        raise RuntimeError("yt-dlp is not installed. Run scripts/run.* first.") from e
    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    stamp = str(int(time.time() * 1000))[-6:]
    tmpl = os.path.join(config.AUDIO_DIR, f"yt_{stamp}_%(title).60s.%(ext)s")

    def hook(d):
        if progress_hook:
            try:
                progress_hook(d)
            except Exception:
                pass

    want_mp3 = has_ffmpeg()

    def build_opts(mp3: bool) -> dict:
        opts: dict = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "outtmpl": tmpl, "progress_hooks": [hook],
        }
        if mp3:
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": config.YT_PREFERRED_CODEC,
                "preferredquality": config.YT_PREFERRED_QUALITY,
            }]
        else:
            # universally playable without any postprocessing
            opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        return opts

    attempts = ("mp3", "raw") if want_mp3 else ("raw",)
    last_err: Exception | None = None
    for attempt in attempts:
        try:
            with yt_dlp.YoutubeDL(build_opts(attempt == "mp3")) as ydl:  # type: ignore
                info = ydl.extract_info(url, download=True)
                fname = ydl.prepare_filename(info)
                if attempt == "mp3":
                    base = os.path.splitext(fname)[0] + ".mp3"
                    if os.path.isfile(base):
                        # mp3 extract worked; drop the pre-extract leftover
                        try:
                            if os.path.isfile(fname) and fname != base:
                                os.remove(fname)
                        except OSError:
                            pass
                        fname = base
                if not os.path.isfile(fname):
                    # find newest matching file as fallback
                    cands = sorted(
                        (os.path.join(config.AUDIO_DIR, f) for f in os.listdir(config.AUDIO_DIR)
                         if f.startswith(f"yt_{stamp}_")),
                        key=os.path.getmtime,
                    )
                    fname = cands[-1] if cands else fname
                size = os.path.getsize(fname) if os.path.isfile(fname) else 0
                if not os.path.isfile(fname) or size == 0:
                    raise RuntimeError("download produced no file")
                codec = os.path.splitext(fname)[1].lower().lstrip(".") or "audio"
                return {
                    "path": fname, "filename": os.path.basename(fname), "size": size,
                    "title": (info or {}).get("title") or "untitled",
                    "artist": (info or {}).get("uploader") or (info or {}).get("channel") or "unknown",
                    "description": ((info or {}).get("description") or "")[:2000],
                    "duration": (info or {}).get("duration"),
                    "thumbnail": (info or {}).get("thumbnail"),
                    "codec": codec, "mp3": codec == "mp3",
                    "note": "" if codec == "mp3" else
                            "saved as .%s (ffmpeg not found — install ffmpeg for mp3)" % codec,
                }
        except Exception as e:
            last_err = e
            # ffmpeg claimed present but postprocessing blew up (broken
            # binary): fall through to the raw attempt instead of dying.
            continue
    raise RuntimeError(f"youtube download failed: {last_err}")
