"""another_ap server — FastAPI app: pages + JSON API + HTTP range streaming."""
from __future__ import annotations

import mimetypes
import os
import time

import config
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from server import db as store
from server.downloads import fetch_direct_url, fetch_image, yt_download, yt_info
from server.utils import (
    audio_duration,
    fmt_bytes,
    fmt_duration,
    generate_thumbnail,
    new_id,
    parse_search,
    split_tags,
)

app = FastAPI(title="another_ap")
templates = Jinja2Templates(directory=config.TEMPLATES_DIR)
templates.env.filters["fmt_duration"] = fmt_duration
templates.env.filters["fmt_bytes"] = fmt_bytes

os.makedirs(config.AUDIO_DIR, exist_ok=True)
os.makedirs(config.THUMB_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.mount("/thumbs", StaticFiles(directory=config.THUMB_DIR), name="thumbs")


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    sweep_preview_thumbs()
    if config.AUTOGEN_ENABLED:
        try:
            summary = store.sync_autogen_playlists()
            if summary.get("created") or summary.get("updated") or summary.get("removed"):
                print(f"$ autogen playlists: +{len(summary['created'])} ~{len(summary['updated'])} -{len(summary['removed'])}")
        except Exception as e:
            print(f"$ autogen sync failed (non-fatal): {e}")


def _maybe_sync_autogen() -> None:
    """Re-sync tag playlists after library writes (cheap no-op when disabled)."""
    if config.AUTOGEN_SYNC_ON_WRITE:
        try:
            store.sync_autogen_playlists()
        except Exception:
            pass


# ---------------------------------------------------------------- helpers

def _thumb_url(path: str | None) -> str | None:
    if not path:
        return None
    # normalize separators: db may hold Windows paths while serving from posix
    return "/thumbs/" + os.path.basename(path.replace("\\", "/"))


def _audio_public(a: dict) -> dict:
    return {
        "id": a["id"], "title": a["title"], "artist": a["artist"],
        "description": a["description"], "tags": a["tags"],
        "thumb_url": _thumb_url(a.get("thumb")),
        "duration_sec": a.get("duration_sec"),
        "duration": fmt_duration(a.get("duration_sec")),
        "file_size": a.get("file_size"), "plays": a.get("play_count", 0),
        "stream_url": f"/stream/{a['id']}",
        "page_url": f"/a/{a['id']}",
    }


def _playlist_public(p: dict, with_tracks: bool = False) -> dict:
    d = {
        "id": p["id"], "title": p["title"], "description": p["description"],
        "tags": p["tags"], "thumb_url": _thumb_url(p.get("thumb")),
        "count": p["count"], "total_sec": p["total_sec"],
        "total": fmt_duration(p["total_sec"]),
        "page_url": f"/p/{p['id']}",
        "is_autogen": bool(p.get("is_autogen")), "autogen_tag": p.get("autogen_tag"),
        "plays": p.get("play_count", 0),
    }
    if with_tracks:
        d["tracks"] = [_audio_public(t) for t in p["tracks"]]
    return d


def _active_theme() -> dict:
    name = store.get_setting("active_theme", "dark")
    t = store.get_theme(name) or store.get_theme("dark")
    assert t is not None
    t["active_name"] = t["name"]
    return t


def _page_ctx(request: Request, extra: dict | None = None) -> dict:
    ctx: dict = {
        "request": request, "app_name": config.APP_NAME,
        "tagline": config.APP_TAGLINE, "theme": _active_theme(),
        "themes": store.list_themes(), "config": config,
    }
    if extra:
        ctx.update(extra)
    return ctx


def _save_thumb_upload(up: UploadFile, prefix: str) -> str:
    ext = os.path.splitext(up.filename or "")[1].lower() or ".png"
    if ext not in set(config.ALLOWED_IMAGE_EXTS):
        raise HTTPException(400, "image must be png/jpg/webp/gif")
    dest = os.path.join(config.THUMB_DIR, f"{prefix}_{new_id(4)}{ext}")
    with open(dest, "wb") as f:
        while True:
            chunk = up.file.read(65536)
            if not chunk:
                break
            f.write(chunk)
    return dest


def _ensure_thumb(prefix: str, seed: str) -> str:
    return generate_thumbnail(os.path.join(config.THUMB_DIR, f"{prefix}_{new_id(4)}.png"), seed)


def _accept_thumb_path(p: str | None) -> str | None:
    """Validate a client-supplied thumb path (preview-then-keep flow)."""
    if not p:
        return None
    ap = os.path.abspath(str(p))
    if os.path.dirname(ap) != os.path.abspath(config.THUMB_DIR):
        raise HTTPException(400, "thumb must live in thumbs dir")
    if not os.path.isfile(ap):
        raise HTTPException(400, "thumb file missing (preview expired?)")
    if os.path.splitext(ap)[1].lower() not in set(config.ALLOWED_IMAGE_EXTS):
        raise HTTPException(400, "not an image file")
    return ap


def _adopt_preview_thumb(path: str, kind: str, ident: str) -> str:
    """Kept covers shed the preview_ prefix so the abandon-sweep never eats them."""
    base = os.path.basename(path)
    if not base.startswith("preview_"):
        return path
    ext = os.path.splitext(base)[1].lower() or ".png"
    dest = os.path.join(config.THUMB_DIR, f"{kind}_{ident[:8]}_{new_id(3)}{ext}")
    try:
        os.rename(path, dest)
    except OSError:
        return path
    return dest


def sweep_preview_thumbs(max_age_s: float = 3600) -> int:
    """Delete abandoned preview_* covers (older than max_age, unreferenced).

    Never touches files still referenced by any audio/playlist row — even if
    they still carry the preview_ name (kept covers from before adopting).
    """
    now = time.time()
    removed = 0
    try:
        with store.db() as con:
            rows_a = con.execute("SELECT thumb_path FROM audios").fetchall()
            rows_p = con.execute("SELECT thumb_path FROM playlists").fetchall()
        referenced = set()
        for (tp,) in list(rows_a) + list(rows_p):
            if tp:
                referenced.add(os.path.basename(str(tp).replace("\\", "/")))
    except Exception:
        referenced = set()
    try:
        for f in os.listdir(config.THUMB_DIR):
            if not f.startswith("preview_") or f in referenced:
                continue
            full = os.path.join(config.THUMB_DIR, f)
            try:
                if now - os.path.getmtime(full) > max_age_s:
                    os.remove(full)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    import random

    audios = [_audio_public(a) for a in store.list_audios()]
    playlists = [_playlist_public(p) for p in store.list_playlists()]
    random.shuffle(audios)
    random.shuffle(playlists)
    return templates.TemplateResponse(request, "index.html", _page_ctx(request, {
        "audios": audios[: config.HOME_SUGGEST_N],
        "playlists": playlists[: config.HOME_SUGGEST_N],
        "stats": store.stats(), "q": "",
    }))


@app.get("/playlists", response_class=HTMLResponse)
def playlists_page(request: Request, q: str = "", sort: str = "recent", order: str = "desc"):
    pq = parse_search(q)
    results = [_playlist_public(p, True) for p in store.search_playlists(pq, sort, order)]
    return templates.TemplateResponse(request, "playlists.html", _page_ctx(request, {
        "playlists": results, "q": q, "sort": sort, "order": order,
        "stats": store.stats(),
    }))


@app.get("/autogen-playlists", response_class=HTMLResponse)
def autogen_playlists_page(request: Request, q: str = "", sort: str = "recent", order: str = "desc"):
    pq = parse_search(q)
    results = [_playlist_public(p, True) for p in store.search_autogen_playlists(pq, sort, order)]
    return templates.TemplateResponse(request, "autogen-playlists.html", _page_ctx(request, {
        "playlists": results, "q": q, "sort": sort, "order": order,
        "stats": store.stats(),
    }))


@app.get("/p/{pid}", response_class=HTMLResponse)
def playlist_page(request: Request, pid: str):
    p = store.get_playlist(pid)
    if not p:
        raise HTTPException(404, "no such playlist")
    import random
    others = [_playlist_public(x) for x in store.list_playlists() if x["id"] != pid]
    random.shuffle(others)
    return templates.TemplateResponse(request, "playlist.html", _page_ctx(request, {
        "pl": _playlist_public(p, True), "raw": p,
        "others": others[:6], "q": "",
    }))


@app.get("/audios", response_class=HTMLResponse)
def audios_page(request: Request, q: str = "", sort: str = "recent", order: str = "desc"):
    pq = parse_search(q)
    results = [_audio_public(a) for a in store.search_audios(pq, sort, order)]
    return templates.TemplateResponse(request, "audios.html", _page_ctx(request, {
        "audios": results, "q": q, "sort": sort, "order": order,
        "stats": store.stats(),
    }))


@app.get("/a/{aid}", response_class=HTMLResponse)
def audio_page(request: Request, aid: str):
    a = store.get_audio(aid)
    if not a:
        raise HTTPException(404, "no such audio")
    import random
    others = [_audio_public(x) for x in store.list_audios() if x["id"] != aid]
    random.shuffle(others)
    return templates.TemplateResponse(request, "audio.html", _page_ctx(request, {
        "track": _audio_public(a), "raw": a,
        "others": others[:8], "q": "",
    }))


@app.get("/download", response_class=HTMLResponse)
def download_page(request: Request):
    return templates.TemplateResponse(request, "download.html", _page_ctx(request, {"q": ""}))


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return templates.TemplateResponse(request, "help.html", _page_ctx(request, {"q": ""}))


# ---------------------------------------------------------------- streaming (Range support for seeking)

@app.get("/stream/{aid}")
def stream(aid: str, request: Request):
    a = store.get_audio(aid)
    if not a or not a.get("file_path") or not os.path.isfile(a["file_path"]):
        raise HTTPException(404, "audio file missing")
    path = a["file_path"]
    size = os.path.getsize(path)
    ctype, _ = mimetypes.guess_type(path)
    ctype = ctype or "audio/mpeg"
    if config.HIGH_DISK_USAGE_MODE:
        store.bump_play(aid)  # low disk mode: zero writes on plain streaming

    range_h = request.headers.get("range")
    if not range_h:
        return FileResponse(path, media_type=ctype, filename=os.path.basename(path))

    # parse "bytes=start-end"
    try:
        unit, _, spec = range_h.partition("=")
        start_s, _, end_s = spec.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)
        if start >= size or end < start:
            raise ValueError
    except ValueError:
        return StreamingResponse(iter(()), status_code=416,
                                 headers={"Content-Range": f"bytes */{size}"})

    length = end - start + 1

    def gen():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(gen(), status_code=206, media_type=ctype, headers={
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes", "Content-Length": str(length),
    })


# ---------------------------------------------------------------- cover previews
# preview-then-keep flow: generate random covers without saving, then PATCH
# the entity with the chosen thumb_path. Abandoned previews are swept on
# startup; the UI also discards its own session previews on keep.

@app.post("/api/preview-thumbnail")
def api_preview_thumbnail(payload: dict | None = None):
    seed = ((payload or {}).get("seed") or "") + new_id(4) + str(time.time())
    tp = generate_thumbnail(os.path.join(config.THUMB_DIR, f"preview_{new_id(4)}.png"), seed)
    return {"thumb_path": tp, "thumb_url": _thumb_url(tp)}


@app.post("/api/preview-cleanup")
def api_preview_cleanup(payload: dict):
    paths = payload.get("paths") or []
    removed = 0
    for p in paths:
        try:
            base = os.path.basename(str(p))
            if not base.startswith("preview_"):
                continue
            full = os.path.join(config.THUMB_DIR, base)
            if os.path.isfile(full):
                os.remove(full)
                removed += 1
        except (OSError, ValueError):
            pass
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------- audio APIs

@app.get("/api/audios")
def api_audios(q: str = "", sort: str = "recent", order: str = "desc"):
    return {"results": [_audio_public(a) for a in store.search_audios(parse_search(q), sort, order)]}


@app.get("/api/audios/{aid}")
def api_audio_one(aid: str):
    a = store.get_audio(aid)
    if not a:
        raise HTTPException(404, "not found")
    return _audio_public(a)


@app.post("/api/audios/upload")
async def api_audio_upload(
    file: UploadFile = File(...),
    title: str = Form(""), artist: str = Form(""), description: str = Form(""),
    tags: str = Form(""), thumb: UploadFile | None = File(None),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in config.ALLOWED_AUDIO_EXTS:
        raise HTTPException(400, f"unsupported audio type {ext!r}")
    dest = os.path.join(config.AUDIO_DIR, f"{new_id(6)}_{(file.filename or 'audio').replace('/', '_')}"[:120])
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)
    thumb_path: str | None = None
    if thumb is not None and getattr(thumb, "filename", None):
        thumb_path = _save_thumb_upload(thumb, "au")
    if not thumb_path:
        thumb_path = _ensure_thumb("au", title or file.filename or "ap")
    dur = audio_duration(dest)
    a = store.create_audio(title or os.path.splitext(file.filename or "untitled")[0],
                           artist or "unknown", description, tags, dest, thumb_path, dur, size)
    _maybe_sync_autogen()
    return _audio_public(a)


@app.post("/api/audios/from-file")
def api_audio_from_file(payload: dict):
    """Commit a previously staged file (via /api/fetch/url or /api/fetch/yt) as a library audio."""
    path = (payload.get("path") or "").strip()
    if not path or not os.path.isfile(path):
        raise HTTPException(400, "staged file not found; fetch/download first")
    # ensure inside AUDIO_DIR (no path traversal commits)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(config.AUDIO_DIR):
        raise HTTPException(400, "staged file outside audio dir")
    title = payload.get("title") or os.path.splitext(os.path.basename(path))[0]
    thumb_path = payload.get("thumb_path")
    remote_thumb = (payload.get("remote_thumb") or "").strip()
    if not thumb_path and remote_thumb and remote_thumb.startswith("http"):
        try:
            import httpx
            r = httpx.get(remote_thumb, timeout=30, follow_redirects=True)
            r.raise_for_status()
            tp = os.path.join(config.THUMB_DIR, f"au_{new_id(4)}.jpg")
            with open(tp, "wb") as f:
                f.write(r.content)
            thumb_path = tp
        except Exception:
            thumb_path = None
    if not thumb_path:
        thumb_path = _ensure_thumb("au", title)
    dur = audio_duration(path)
    if payload.get("duration") and not dur:
        try:
            dur = float(payload["duration"])
        except (TypeError, ValueError):
            pass
    size = os.path.getsize(path)
    a = store.create_audio(title, payload.get("artist") or "unknown",
                           payload.get("description") or "", payload.get("tags") or "",
                           path, thumb_path, dur, size)
    _maybe_sync_autogen()
    return _audio_public(a)


@app.patch("/api/audios/{aid}")
def api_audio_patch(aid: str, payload: dict):
    fields = {k: payload[k] for k in ("title", "artist", "description", "tags") if k in payload}
    if "thumb_path" in payload:
        fields["thumb_path"] = _adopt_preview_thumb(
            _accept_thumb_path(payload.get("thumb_path")) or "", "au", aid)
    a = store.update_audio(aid, fields)
    if not a:
        raise HTTPException(404, "not found")
    if "tags" in fields:
        _maybe_sync_autogen()
    return _audio_public(a)


@app.delete("/api/audios/{aid}")
def api_audio_delete(aid: str):
    a = store.delete_audio(aid)
    if not a:
        raise HTTPException(404, "not found")
    _maybe_sync_autogen()
    return {"ok": True, "deleted": aid}


@app.post("/api/audios/{aid}/thumbnail/random")
def api_audio_thumb_random(aid: str, payload: dict | None = None):
    a = store.get_audio(aid)
    if not a:
        raise HTTPException(404, "not found")
    seed = (payload or {}).get("seed") or (a["title"] + aid + str(time.time()))
    tp = _ensure_thumb("au", seed)
    a = store.update_audio(aid, {"thumb_path": tp})
    return _audio_public(a)  # type: ignore


@app.post("/api/audios/{aid}/thumbnail/upload")
async def api_audio_thumb_upload(aid: str, thumb: UploadFile = File(...)):
    if not store.get_audio(aid):
        raise HTTPException(404, "not found")
    tp = _save_thumb_upload(thumb, "au")
    a = store.update_audio(aid, {"thumb_path": tp})
    return _audio_public(a)  # type: ignore


# ---------------------------------------------------------------- playlist APIs

@app.get("/api/playlists")
def api_playlists(q: str = "", sort: str = "recent", order: str = "desc",
                  min_songs: int | None = None, max_songs: int | None = None,
                  min_dur: float | None = None, max_dur: float | None = None,
                  include_autogen: bool = False):
    ps = store.search_playlists(parse_search(q), sort, order, min_songs, max_songs, min_dur, max_dur,
                                include_autogen=include_autogen)
    return {"results": [_playlist_public(p, True) for p in ps]}


@app.get("/api/autogen-playlists")
def api_autogen_playlists(q: str = "", sort: str = "recent", order: str = "desc",
                          min_songs: int | None = None, max_songs: int | None = None,
                          min_dur: float | None = None, max_dur: float | None = None):
    ps = store.search_autogen_playlists(parse_search(q), sort, order, min_songs, max_songs, min_dur, max_dur)
    return {"results": [_playlist_public(p, True) for p in ps]}


@app.post("/api/playlists")
def api_playlist_create(payload: dict):
    title = (payload.get("title") or "untitled").strip()
    p = store.create_playlist(title, payload.get("description") or "",
                              payload.get("tags") or "", None,
                              payload.get("track_ids") or [])
    # thumbnail: always ensure one
    tp = _ensure_thumb("pl", title + p["id"])
    p = store.update_playlist(p["id"], {"thumb_path": tp})
    return _playlist_public(p or store.get_playlist(p["id"]) or {}, True)  # type: ignore


@app.get("/api/playlists/{pid}")
def api_playlist_one(pid: str):
    p = store.get_playlist(pid)
    if not p:
        raise HTTPException(404, "not found")
    return _playlist_public(p, True)


@app.patch("/api/playlists/{pid}")
def api_playlist_patch(pid: str, payload: dict):
    fields = {k: payload[k] for k in ("title", "description", "tags") if k in payload}
    if "thumb_path" in payload:
        fields["thumb_path"] = _adopt_preview_thumb(
            _accept_thumb_path(payload.get("thumb_path")) or "", "pl", pid)
    p = store.update_playlist(pid, fields)
    if not p:
        raise HTTPException(404, "not found")
    return _playlist_public(p, True)


@app.delete("/api/playlists/{pid}")
def api_playlist_delete(pid: str):
    p = store.delete_playlist(pid)
    if not p:
        raise HTTPException(404, "not found")
    return {"ok": True, "deleted": pid}


@app.post("/api/playlists/{pid}/add")
def api_playlist_add(pid: str, payload: dict):
    aid = (payload.get("audio_id") or "").strip()
    if not aid:
        raise HTTPException(400, "audio_id required")
    p = store.playlist_add(pid, aid)
    if not p:
        raise HTTPException(404, "playlist or audio not found")
    return _playlist_public(p, True)


@app.post("/api/playlists/{pid}/remove")
def api_playlist_remove(pid: str, payload: dict):
    p = store.playlist_remove(pid, payload.get("audio_id", ""), payload.get("position"))
    if not p:
        raise HTTPException(404, "not found")
    return _playlist_public(p, True)


@app.post("/api/playlists/{pid}/remove-many")
def api_playlist_remove_many(pid: str, payload: dict):
    aids = payload.get("audio_ids") or []
    if not isinstance(aids, list) or not aids:
        raise HTTPException(400, "audio_ids must be a non-empty list")
    p = store.playlist_remove_many(pid, [str(x) for x in aids])
    if not p:
        raise HTTPException(404, "not found")
    return _playlist_public(p, True)


@app.post("/api/playlists/{pid}/play")
def api_playlist_play(pid: str):
    """Count one playlist play (client pings on track start). No-op in low disk mode."""
    if not config.HIGH_DISK_USAGE_MODE:
        return {"ok": True, "counted": False}
    if not store.bump_playlist_play(pid):
        raise HTTPException(404, "not found")
    return {"ok": True, "counted": True}


@app.post("/api/playlists/{pid}/reorder")
def api_playlist_reorder(pid: str, payload: dict):
    ids = payload.get("ordered_ids") or []
    if not isinstance(ids, list):
        raise HTTPException(400, "ordered_ids must be a list")
    p = store.playlist_reorder(pid, [str(x) for x in ids])
    if not p:
        raise HTTPException(404, "not found")
    return _playlist_public(p, True)


@app.post("/api/playlists/{pid}/thumbnail/random")
def api_playlist_thumb_random(pid: str, payload: dict | None = None):
    p = store.get_playlist(pid)
    if not p:
        raise HTTPException(404, "not found")
    seed = (payload or {}).get("seed") or (p["title"] + pid + str(time.time()))
    tp = _ensure_thumb("pl", seed)
    p = store.update_playlist(pid, {"thumb_path": tp})
    return _playlist_public(p or {}, True)  # type: ignore


@app.post("/api/playlists/{pid}/thumbnail/upload")
async def api_playlist_thumb_upload(pid: str, thumb: UploadFile = File(...)):
    if not store.get_playlist(pid):
        raise HTTPException(404, "not found")
    tp = _save_thumb_upload(thumb, "pl")
    p = store.update_playlist(pid, {"thumb_path": tp})
    return _playlist_public(p or {}, True)  # type: ignore


# ---------------------------------------------------------------- fetch / download APIs

@app.post("/api/fetch/image")
def api_fetch_image(payload: dict):
    """Fetch a cover image from a plain http(s) URL into thumbs/ (staged)."""
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    try:
        return fetch_image(url)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/fetch/url")
def api_fetch_url(payload: dict):
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http(s)://")
    try:
        return fetch_direct_url(url)
    except Exception as e:
        raise HTTPException(400, f"fetch failed: {e}") from e


@app.post("/api/fetch/yt-info")
def api_yt_info(payload: dict):
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http(s)://")
    try:
        return yt_info(url)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/fetch/yt")
def api_yt_dl(payload: dict):
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http(s)://")
    try:
        return yt_download(url)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


# ---------------------------------------------------------------- misc APIs

@app.get("/api/random")
def api_random(kind: str = "audio", n: int = 6):
    import random

    n = max(1, min(24, n))
    if kind == "playlist":
        items = store.list_playlists()
        random.shuffle(items)
        return {"results": [_playlist_public(p) for p in items[:n]]}
    items = store.list_audios()
    random.shuffle(items)
    return {"results": [_audio_public(a) for a in items[:n]]}


@app.get("/api/stats")
def api_stats():
    s = store.stats()
    s["total"] = fmt_duration(s["total_sec"])
    return s


@app.get("/api/themes")
def api_themes():
    return {"active": store.get_setting("active_theme", "dark"), "themes": store.list_themes()}


@app.post("/api/themes")
def api_theme_save(payload: dict):
    name = split_tags(payload.get("name") or "")
    if not name:
        raise HTTPException(400, "theme name required")
    tname = (payload.get("name") or "").strip()[:40]
    colors = {k: payload.get(k) or payload.get("colors", {}).get(k) for k in
              ("bg", "panel", "fg", "muted", "accent", "warn", "danger", "border", "sel")}
    for k, v in colors.items():
        if not v or not str(v).startswith("#"):
            raise HTTPException(400, f"color {k} must be a #hex value")
    return store.save_theme(tname, colors)  # type: ignore


@app.delete("/api/themes/{name}")
def api_theme_delete(name: str):
    if not store.delete_theme(name):
        raise HTTPException(400, "cannot delete builtin or missing theme")
    return {"ok": True}


@app.get("/api/settings/theme")
def api_theme_get():
    return {"active": store.get_setting("active_theme", "dark")}


@app.put("/api/settings/theme")
def api_theme_set(payload: dict):
    name = (payload.get("active") or payload.get("name") or "").strip()
    if not store.get_theme(name):
        raise HTTPException(404, "no such theme")
    store.set_setting("active_theme", name)
    return {"active": name}


@app.get("/health")
def health():
    return {"ok": True, "app": config.APP_NAME}


def run() -> None:
    import uvicorn

    store.init_db()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    run()
