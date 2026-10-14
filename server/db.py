"""DuckDB storage layer for another_ap.

Tables: audios, playlists, playlist_items, themes, settings.
Connections are short-lived (open -> query -> close) to keep DuckDB happy
under uvicorn's threads.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any

import config
from server.utils import ParsedQuery, match_score, new_id, split_tags

try:
    import duckdb  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("duckdb is required. Run scripts/run.* to install deps.") from e


def _connect():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    os.makedirs(config.THUMB_DIR, exist_ok=True)
    con = duckdb.connect(config.DB_PATH)
    return con


@contextmanager
def db():
    con = _connect()
    try:
        yield con
    finally:
        try:
            con.close()
        except Exception:
            pass


BUILTIN_THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0b0e11", "panel": "#11151b", "fg": "#c9d4d8",
        "muted": "#6b7885", "accent": "#46c8a6", "warn": "#ffbd2e",
        "danger": "#ff5f56", "border": "#232b33", "sel": "#1a2b26",
    },
    "light": {
        "bg": "#f4f2ec", "panel": "#ffffff", "fg": "#1c2226",
        "muted": "#6d7680", "accent": "#0e7c5b", "warn": "#9a6200",
        "danger": "#c22e2e", "border": "#ddd8cc", "sel": "#e3f2e9",
    },
    "amber": {
        "bg": "#0e0c08", "panel": "#171310", "fg": "#ffd98a",
        "muted": "#8a7452", "accent": "#ffb000", "warn": "#ffcf5c",
        "danger": "#ff5f56", "border": "#2b2317", "sel": "#241c0e",
    },
}


def init_db() -> None:
    with db() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS audios(
                id VARCHAR PRIMARY KEY,
                title VARCHAR, artist VARCHAR, description VARCHAR,
                tags VARCHAR, file_path VARCHAR, thumb_path VARCHAR,
                duration_sec DOUBLE, file_size BIGINT,
                created_at DOUBLE, play_count BIGINT DEFAULT 0
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS playlists(
                id VARCHAR PRIMARY KEY,
                title VARCHAR, description VARCHAR, tags VARCHAR,
                thumb_path VARCHAR, created_at DOUBLE, updated_at DOUBLE
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS playlist_items(
                playlist_id VARCHAR, audio_id VARCHAR,
                position INTEGER, added_at DOUBLE,
                PRIMARY KEY (playlist_id, position)
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS themes(
                name VARCHAR PRIMARY KEY, bg VARCHAR, panel VARCHAR,
                fg VARCHAR, muted VARCHAR, accent VARCHAR, warn VARCHAR,
                danger VARCHAR, border VARCHAR, sel VARCHAR,
                builtin BOOLEAN DEFAULT FALSE, updated_at DOUBLE
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS settings(
                key VARCHAR PRIMARY KEY, value VARCHAR
            )"""
        )
        # seed themes
        for name, t in BUILTIN_THEMES.items():
            con.execute(
                """INSERT INTO themes(name,bg,panel,fg,muted,accent,warn,danger,border,sel,builtin,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,TRUE,?) ON CONFLICT(name) DO NOTHING""",
                [name, t["bg"], t["panel"], t["fg"], t["muted"], t["accent"],
                 t["warn"], t["danger"], t["border"], t["sel"], time.time()],
            )
        con.execute(
            "INSERT INTO settings(key,value) VALUES('active_theme','dark') ON CONFLICT(key) DO NOTHING"
        )


# ---------------------------------------------------------------- row helpers

def _row_to_audio(r: dict[str, Any]) -> dict[str, Any]:
    tags = split_tags(r.get("tags") or "")
    return {
        "id": r["id"], "title": r.get("title") or "untitled",
        "artist": r.get("artist") or "unknown",
        "description": r.get("description") or "",
        "tags": tags, "tags_raw": r.get("tags") or "",
        "file_path": r.get("file_path"), "thumb": r.get("thumb_path"),
        "duration_sec": r.get("duration_sec"), "file_size": r.get("file_size"),
        "created_at": r.get("created_at"), "play_count": r.get("play_count") or 0,
    }


def _row_to_playlist(r: dict[str, Any], items: list[dict] | None = None) -> dict[str, Any]:
    tags = split_tags(r.get("tags") or "")
    total = sum((i.get("duration_sec") or 0) for i in (items or []))
    return {
        "id": r["id"], "title": r.get("title") or "untitled",
        "description": r.get("description") or "", "tags": tags,
        "tags_raw": r.get("tags") or "", "thumb": r.get("thumb_path"),
        "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        "count": len(items or []), "total_sec": total,
        "tracks": items or [],
    }


def _fetchall(con, sql: str, params: list | None = None) -> list[dict]:
    params = params or []
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------- audios

def create_audio(title: str, artist: str, description: str, tags: str,
                 file_path: str, thumb_path: str | None,
                 duration: float | None, size: int | None) -> dict:
    aid = new_id(6)
    now = time.time()
    with db() as con:
        # collision guard (astronomically unlikely, but cheap)
        while _fetchall(con, "SELECT id FROM audios WHERE id=?", [aid]):
            aid = new_id(6)
        con.execute(
            "INSERT INTO audios VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [aid, title.strip() or "untitled", artist.strip() or "unknown",
             description or "", " ; ".join(split_tags(tags)),
             file_path, thumb_path, duration, size, now, 0],
        )
    return get_audio(aid)  # type: ignore


def get_audio(aid: str) -> dict | None:
    with db() as con:
        rows = _fetchall(con, "SELECT * FROM audios WHERE id=?", [aid])
    if not rows:
        return None
    return _row_to_audio(rows[0])


def update_audio(aid: str, fields: dict[str, Any]) -> dict | None:
    allowed = {"title", "artist", "description", "tags", "thumb_path"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "tags":
            v = " ; ".join(split_tags(v or ""))
        sets.append(f"{k}=?")
        params.append(v)
    if sets:
        params.append(aid)
        with db() as con:
            con.execute(f"UPDATE audios SET {', '.join(sets)} WHERE id=?", params)
    return get_audio(aid)


def delete_audio(aid: str) -> dict | None:
    a = get_audio(aid)
    if not a:
        return None
    with db() as con:
        con.execute("DELETE FROM playlist_items WHERE audio_id=?", [aid])
        con.execute("DELETE FROM audios WHERE id=?", [aid])
    # remove files best-effort
    for p in (a.get("file_path"), a.get("thumb")):
        try:
            if p and os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    return a


def list_audios() -> list[dict]:
    with db() as con:
        rows = _fetchall(con, "SELECT * FROM audios ORDER BY created_at DESC")
    return [_row_to_audio(r) for r in rows]


def bump_play(aid: str) -> None:
    with db() as con:
        con.execute("UPDATE audios SET play_count = play_count + 1 WHERE id=?", [aid])


def _audio_matches(a: dict, pq: ParsedQuery) -> tuple[bool, int]:
    score = 0
    title = (a["title"] or "").lower()
    artist = (a["artist"] or "").lower()
    desc = (a["description"] or "").lower()
    tags = [t.lower() for t in a["tags"]]
    aid = (a["id"] or "").lower()
    blob = f"{title} {artist} {desc} {' '.join(tags)} {aid}"
    for t in pq.plain:
        tl = t.lower()
        if tl in blob or tl == aid:
            score += 2 if len(tl) >= 3 else 1
        else:
            return False, 0
    for h in pq.hash_inc:
        if h.lower() not in aid:
            return False, 0
        score += 5
    for t in pq.tags_inc:
        if t.lower() not in tags:
            return False, 0
        score += 3
    for t in pq.tags_exc:
        if t.lower() in tags:
            return False, 0
    for t in pq.title_inc:
        s = match_score(title, t.lower())
        if not s:
            return False, 0
        score += s + 2
    for t in pq.title_exc:
        if match_score(title, t.lower()):
            return False, 0
    for t in pq.desc_inc:
        s = match_score(desc, t.lower())
        if not s:
            return False, 0
        score += s
    for t in pq.desc_exc:
        if match_score(desc, t.lower()):
            return False, 0
    for t in pq.artis_inc:
        if t.lower() not in artist:
            return False, 0
        score += 3
    for t in pq.artis_exc:
        if t.lower() in artist:
            return False, 0
    for t in pq.songs_inc:  # for audios, @songs == title contains
        if t.lower() not in title:
            return False, 0
        score += 2
    for t in pq.songs_exc:
        if t.lower() in title:
            return False, 0
    return True, score


def search_audios(pq: ParsedQuery, sort: str = "recent", order: str = "desc") -> list[dict]:
    audios = list_audios()
    ranked: list[tuple[int, dict]] = []
    for a in audios:
        ok, score = _audio_matches(a, pq)
        if ok:
            ranked.append((score, a))
    rev = order != "asc"
    if sort == "name":
        ranked.sort(key=lambda x: x[1]["title"].lower(), reverse=rev)
    elif sort == "duration":
        ranked.sort(key=lambda x: (x[1]["duration_sec"] or 0), reverse=rev)
    elif sort == "plays":
        ranked.sort(key=lambda x: x[1]["play_count"], reverse=rev)
    else:  # recent / relevance hybrid
        ranked.sort(key=lambda x: (x[0], x[1]["created_at"] or 0), reverse=True if order == "desc" else False)
        if sort == "recent":
            ranked.sort(key=lambda x: x[1]["created_at"] or 0, reverse=rev)
    return [a for _, a in ranked]


# ---------------------------------------------------------------- playlists

def create_playlist(title: str, description: str, tags: str, thumb_path: str | None,
                    track_ids: list[str] | None = None) -> dict:
    pid = new_id(6)
    now = time.time()
    with db() as con:
        while _fetchall(con, "SELECT id FROM playlists WHERE id=?", [pid]):
            pid = new_id(6)
        con.execute(
            "INSERT INTO playlists VALUES(?,?,?,?,?,?,?)",
            [pid, title.strip() or "untitled", description or "",
             " ; ".join(split_tags(tags)), thumb_path, now, now],
        )
        if track_ids:
            for i, aid in enumerate(track_ids):
                con.execute(
                    "INSERT INTO playlist_items VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                    [pid, aid, i, now],
                )
    return get_playlist(pid)  # type: ignore


def get_playlist(pid: str) -> dict | None:
    with db() as con:
        rows = _fetchall(con, "SELECT * FROM playlists WHERE id=?", [pid])
        if not rows:
            return None
        items = _fetchall(
            con,
            """SELECT a.* FROM playlist_items pi
               JOIN audios a ON a.id = pi.audio_id
               WHERE pi.playlist_id=? ORDER BY pi.position ASC""",
            [pid],
        )
    tracks = [_row_to_audio(r) for r in items]
    return _row_to_playlist(rows[0], tracks)


def update_playlist(pid: str, fields: dict[str, Any]) -> dict | None:
    allowed = {"title", "description", "tags", "thumb_path"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "tags":
            v = " ; ".join(split_tags(v or ""))
        sets.append(f"{k}=?")
        params.append(v)
    sets.append("updated_at=?")
    params.append(time.time())
    params.append(pid)
    with db() as con:
        if _fetchall(con, "SELECT id FROM playlists WHERE id=?", [pid]):
            con.execute(f"UPDATE playlists SET {', '.join(sets)} WHERE id=?", params)
    return get_playlist(pid)


def delete_playlist(pid: str) -> dict | None:
    p = get_playlist(pid)
    if not p:
        return None
    with db() as con:
        con.execute("DELETE FROM playlist_items WHERE playlist_id=?", [pid])
        con.execute("DELETE FROM playlists WHERE id=?", [pid])
    try:
        if p.get("thumb") and os.path.isfile(p["thumb"]):
            os.remove(p["thumb"])
    except OSError:
        pass
    return p


def playlist_add(pid: str, aid: str) -> dict | None:
    with db() as con:
        prow = _fetchall(con, "SELECT id FROM playlists WHERE id=?", [pid])
        arow = _fetchall(con, "SELECT id FROM audios WHERE id=?", [aid])
        if not prow or not arow:
            return None
        cur = con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM playlist_items WHERE playlist_id=?", [pid]).fetchone()
        nxt = int(cur[0]) if cur else 0
        con.execute("INSERT INTO playlist_items VALUES(?,?,?,?) ON CONFLICT DO NOTHING", [pid, aid, nxt, time.time()])
        con.execute("UPDATE playlists SET updated_at=? WHERE id=?", [time.time(), pid])
    return get_playlist(pid)


def playlist_remove(pid: str, aid: str, position: int | None = None) -> dict | None:
    with db() as con:
        if position is None:
            con.execute("DELETE FROM playlist_items WHERE playlist_id=? AND audio_id=?", [pid, aid])
        else:
            con.execute("DELETE FROM playlist_items WHERE playlist_id=? AND position=?", [pid, position])
        # compact positions
        rows = _fetchall(con, "SELECT audio_id FROM playlist_items WHERE playlist_id=? ORDER BY position ASC", [pid])
        con.execute("DELETE FROM playlist_items WHERE playlist_id=?", [pid])
        for i, r in enumerate(rows):
            con.execute("INSERT INTO playlist_items VALUES(?,?,?,?)", [pid, r["audio_id"], i, time.time()])
        con.execute("UPDATE playlists SET updated_at=? WHERE id=?", [time.time(), pid])
    return get_playlist(pid)


def playlist_reorder(pid: str, ordered_ids: list[str]) -> dict | None:
    with db() as con:
        if not _fetchall(con, "SELECT id FROM playlists WHERE id=?", [pid]):
            return None
        con.execute("DELETE FROM playlist_items WHERE playlist_id=?", [pid])
        for i, aid in enumerate(ordered_ids):
            if _fetchall(con, "SELECT id FROM audios WHERE id=?", [aid]):
                con.execute("INSERT INTO playlist_items VALUES(?,?,?,?)", [pid, aid, i, time.time()])
        con.execute("UPDATE playlists SET updated_at=? WHERE id=?", [time.time(), pid])
    return get_playlist(pid)


def list_playlists() -> list[dict]:
    with db() as con:
        prows = _fetchall(con, "SELECT * FROM playlists ORDER BY updated_at DESC")
        out = []
        for pr in prows:
            items = _fetchall(
                con,
                """SELECT a.* FROM playlist_items pi JOIN audios a ON a.id=pi.audio_id
                   WHERE pi.playlist_id=? ORDER BY pi.position ASC""",
                [pr["id"]],
            )
            out.append(_row_to_playlist(pr, [_row_to_audio(r) for r in items]))
    return out


def _playlist_matches(p: dict, pq: ParsedQuery) -> tuple[bool, int]:
    score = 0
    title = (p["title"] or "").lower()
    desc = (p["description"] or "").lower()
    tags = [t.lower() for t in p["tags"]]
    pid = (p["id"] or "").lower()
    song_blob = " ".join((t["title"] or "").lower() for t in p["tracks"])
    art_blob = " ".join((t["artist"] or "").lower() for t in p["tracks"])
    blob = f"{title} {desc} {' '.join(tags)} {song_blob} {art_blob} {pid}"
    for t in pq.plain:
        if t.lower() in blob:
            score += 2
        else:
            return False, 0
    for h in pq.hash_inc:
        if h.lower() not in pid:
            return False, 0
        score += 5
    for t in pq.tags_inc:
        if t.lower() not in tags:
            return False, 0
        score += 3
    for t in pq.tags_exc:
        if t.lower() in tags:
            return False, 0
    for t in pq.title_inc:
        s = match_score(title, t.lower())
        if not s:
            return False, 0
        score += s + 2
    for t in pq.title_exc:
        if match_score(title, t.lower()):
            return False, 0
    for t in pq.desc_inc:
        s = match_score(desc, t.lower())
        if not s:
            return False, 0
        score += s
    for t in pq.desc_exc:
        if match_score(desc, t.lower()):
            return False, 0
    for t in pq.songs_inc:
        if t.lower() not in song_blob:
            return False, 0
        score += 3
    for t in pq.songs_exc:
        if t.lower() in song_blob:
            return False, 0
    for t in pq.artis_inc:
        if t.lower() not in art_blob:
            return False, 0
        score += 3
    for t in pq.artis_exc:
        if t.lower() in art_blob:
            return False, 0
    return True, score


def search_playlists(pq: ParsedQuery, sort: str = "recent", order: str = "desc",
                     min_songs: int | None = None, max_songs: int | None = None,
                     min_dur: float | None = None, max_dur: float | None = None) -> list[dict]:
    out: list[tuple[int, dict]] = []
    for p in list_playlists():
        if min_songs is not None and p["count"] < min_songs:
            continue
        if max_songs is not None and p["count"] > max_songs:
            continue
        if min_dur is not None and p["total_sec"] < min_dur:
            continue
        if max_dur is not None and p["total_sec"] > max_dur:
            continue
        ok, score = _playlist_matches(p, pq)
        if ok:
            out.append((score, p))
    rev = order != "asc"
    if sort == "name":
        out.sort(key=lambda x: x[1]["title"].lower(), reverse=rev)
    elif sort == "songs":
        out.sort(key=lambda x: x[1]["count"], reverse=rev)
    elif sort == "duration":
        out.sort(key=lambda x: x[1]["total_sec"], reverse=rev)
    else:
        if any(pq.plain or pq.tags_inc or pq.title_inc or pq.desc_inc for _ in [0]) and sort == "recent":
            out.sort(key=lambda x: (x[0], x[1]["updated_at"] or 0), reverse=True if order == "desc" else False)
        else:
            out.sort(key=lambda x: x[1]["updated_at"] or 0, reverse=rev)
    return [p for _, p in out]


# ---------------------------------------------------------------- themes/settings

def list_themes() -> list[dict]:
    with db() as con:
        return _fetchall(con, "SELECT * FROM themes ORDER BY name ASC")


def get_theme(name: str) -> dict | None:
    with db() as con:
        rows = _fetchall(con, "SELECT * FROM themes WHERE name=?", [name])
    return rows[0] if rows else None


def save_theme(name: str, colors: dict[str, str]) -> dict | None:
    keys = ("bg", "panel", "fg", "muted", "accent", "warn", "danger", "border", "sel")
    vals = [colors.get(k, "#000000") for k in keys]
    with db() as con:
        con.execute(
            """INSERT INTO themes(name,bg,panel,fg,muted,accent,warn,danger,border,sel,builtin,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,FALSE,?)
               ON CONFLICT(name) DO UPDATE SET bg=excluded.bg, panel=excluded.panel,
               fg=excluded.fg, muted=excluded.muted, accent=excluded.accent, warn=excluded.warn,
               danger=excluded.danger, border=excluded.border, sel=excluded.sel, updated_at=excluded.updated_at""",
            [name, *vals, time.time()],
        )
    return get_theme(name)


def delete_theme(name: str) -> bool:
    with db() as con:
        rows = _fetchall(con, "SELECT builtin FROM themes WHERE name=?", [name])
        if not rows or rows[0].get("builtin"):
            return False
        con.execute("DELETE FROM themes WHERE name=?", [name])
        cur = _fetchall(con, "SELECT value FROM settings WHERE key='active_theme'")
        if cur and cur[0]["value"] == name:
            con.execute("UPDATE settings SET value='dark' WHERE key='active_theme'")
        return True


def get_setting(key: str, default: str = "") -> str:
    with db() as con:
        rows = _fetchall(con, "SELECT value FROM settings WHERE key=?", [key])
    return rows[0]["value"] if rows else default


def set_setting(key: str, value: str) -> None:
    with db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, value])


def stats() -> dict:
    with db() as con:
        na = con.execute("SELECT COUNT(*) FROM audios").fetchone()[0]
        np = con.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
        dur = con.execute("SELECT COALESCE(SUM(duration_sec),0) FROM audios").fetchone()[0]
    return {"audios": int(na), "playlists": int(np), "total_sec": float(dur or 0)}
