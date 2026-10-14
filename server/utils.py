"""Shared helpers: ids, tags, durations, thumbnails, search-query parser."""
from __future__ import annotations

import hashlib
import os
import random
import re
import secrets
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------- ids / tags

def new_id(nbytes: int = 6) -> str:
    """Stable-looking unique hash, e.g. 12 hex chars. Never changes once stored."""
    return secrets.token_hex(nbytes)


def split_tags(raw: str | None) -> list[str]:
    """Tags are ';' separated. Normalise: strip, drop empties, de-dup (case-insensitive)."""
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.replace(",", ";").split(";"):
        t = part.strip()
        if not t:
            continue
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
    return out


def join_tags(tags: list[str]) -> str:
    return " ; ".join(tags)


def fmt_duration(sec: float | None) -> str:
    if sec is None:
        return "--:--"
    try:
        s = int(float(sec))
    except (TypeError, ValueError):
        return "--:--"
    h, rem = divmod(max(0, s), 3600)
    m, sec_ = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec_:02d}"
    return f"{m}:{sec_:02d}"


def fmt_bytes(n: int | None) -> str:
    if not n and n != 0:
        return "?"
    n = int(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}GB"


def audio_duration(path: str) -> float | None:
    """Best-effort duration via mutagen (optional dep). Returns seconds or None."""
    try:
        from mutagen import File as MutagenFile  # type: ignore
    except Exception:
        return None
    try:
        m = MutagenFile(path)
        if m is not None and getattr(m, "info", None) is not None:
            length = getattr(m.info, "length", None)
            if length:
                return float(length)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------- thumbnails

_GRADIENTS = [
    ((16, 18, 26), (46, 200, 166)),
    ((20, 16, 38), (168, 110, 255)),
    ((28, 20, 12), (255, 170, 60)),
    ((10, 26, 30), (90, 180, 255)),
    ((30, 12, 22), (255, 90, 140)),
    ((12, 28, 18), (150, 255, 170)),
]


def generate_thumbnail(path: str, seed_text: str = "", size: tuple[int, int] = (480, 480)) -> str:
    """Generate a terminal-ish gradient + scanline + glyph cover with PIL. Returns path."""
    from PIL import Image, ImageDraw  # local import so utils stays light

    rnd = random.Random(hashlib.sha256(seed_text.encode()).hexdigest())
    c0, c1 = rnd.choice(_GRADIENTS)
    w, h = size
    img = Image.new("RGB", (w, h), c0)
    px = img.load()
    assert px is not None
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(c0[0] + (c1[0] - c0[0]) * t)
        g = int(c0[1] + (c1[1] - c0[1]) * t)
        b = int(c0[2] + (c1[2] - c0[2]) * t)
        for x in range(w):
            # subtle diagonal banding for texture
            wob = int(10 * ((x * 0.7 + y * 1.3) % 32) / 32)
            px[x, y] = (min(255, r + wob), min(255, g + wob), min(255, b + wob))
    d = ImageDraw.Draw(img, "RGBA")
    # scanlines
    for y in range(0, h, 4):
        d.line([(0, y), (w, y)], fill=(0, 0, 0, 42), width=1)
    # terminal frame
    m = 26
    d.rectangle([m, m, w - m, h - m], outline=(255, 255, 255, 200), width=2)
    d.rectangle([m, m, w - m, m + 34], fill=(0, 0, 0, 110))
    for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([m + 12 + i * 20, m + 10, m + 24 + i * 20, m + 22], fill=col)
    # big glyph from seed
    glyph = (seed_text.strip()[:2] or "ap").upper()
    # use default bitmap font scaled via text box (no external fonts -> offline safe)
    try:
        # draw blocky letters with rectangles if PIL has no fonts; fallback to text
        d.text((w // 2, h // 2 + 20), glyph, fill=(255, 255, 255, 235), anchor="mm")
        d.text((w // 2, h - m - 26), "$ another_ap", fill=(255, 255, 255, 200), anchor="mm")
    except Exception:
        pass
    # vignette-ish corners
    d.rectangle([0, 0, w - 1, h - 1], outline=(0, 0, 0, 90), width=6)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    return path


# ---------------------------------------------------------------- search parser
# Query language (shared by playlists + audios):
#   plain text          -> match-all substring across fields
#   @tags a ; b ; @noti c ; d
#   @title <text> [@noti <text>]
#   @desc  <text> [@noti <text>]
#   @songs a ; b ...  (playlist: member track titles)
#   @artis a ; b ...  (playlist: member artists  / audio: artist field)
#   @noti splits include/exclude within the *current* directive.
#   Multiple directives AND together. Plain tokens AND together.

_DIRECTIVES = ("tags", "title", "desc", "songs", "artis", "artist", "author", "hash", "id")


@dataclass
class ParsedQuery:
    plain: list[str] = field(default_factory=list)
    tags_inc: list[str] = field(default_factory=list)
    tags_exc: list[str] = field(default_factory=list)
    title_inc: list[str] = field(default_factory=list)
    title_exc: list[str] = field(default_factory=list)
    desc_inc: list[str] = field(default_factory=list)
    desc_exc: list[str] = field(default_factory=list)
    songs_inc: list[str] = field(default_factory=list)
    songs_exc: list[str] = field(default_factory=list)
    artis_inc: list[str] = field(default_factory=list)
    artis_exc: list[str] = field(default_factory=list)
    hash_inc: list[str] = field(default_factory=list)


def parse_search(q: str | None) -> ParsedQuery:
    pq = ParsedQuery()
    if not q or not q.strip():
        return pq
    text = q.strip()
    # find directive spans: @word ... up to next @word or end
    pattern = re.compile(r"@(\w+)\s*([^@]*)", re.IGNORECASE)
    pos = 0
    directives_found = False
    last = ""  # last real directive, so a following @noti routes to its exclude list
    for m in pattern.finditer(text):
        directives_found = True
        # any plain text before first directive
        if m.start() > pos:
            head = text[pos : m.start()].strip()
            pq.plain.extend([t for t in head.split() if t])
        name = m.group(1).lower()
        body = m.group(2).strip()
        pos = m.end()
        if name == "noti":
            # '@noti' after a directive excludes from THAT directive, e.g.
            # '@tags a ; b ; @noti c' excludes tag c (not title/desc).
            if body:
                if last == "tags":
                    pq.tags_exc.extend([t for t in (s.strip() for s in body.split(";")) if t])
                elif last in ("title",):
                    pq.title_exc.append(body)
                elif last in ("desc",):
                    pq.desc_exc.append(body)
                elif last in ("songs",):
                    pq.songs_exc.extend([t for t in (s.strip() for s in body.split(";")) if t])
                elif last in ("artis", "artist", "author"):
                    pq.artis_exc.extend([t for t in (s.strip() for s in body.split(";")) if t])
                else:
                    pq.title_exc.append(body)
                    pq.desc_exc.append(body)
            continue
        if name not in _DIRECTIVES:
            # unknown @foo -> treat literally as plain
            pq.plain.append("@" + name)
            if body:
                pq.plain.extend(body.split())
            continue
        # split body on @noti for include/exclude
        parts = re.split(r"@noti", body, flags=re.IGNORECASE)
        inc_raw = parts[0].strip()
        exc_raw = " ".join(p.strip() for p in parts[1:]).strip()
        if name == "tags":
            pq.tags_inc.extend([t for t in (s.strip() for s in inc_raw.split(";")) if t])
            pq.tags_exc.extend([t for t in (s.strip() for s in exc_raw.split(";")) if t])
        elif name == "title":
            if inc_raw:
                pq.title_inc.append(inc_raw)
            if exc_raw:
                pq.title_exc.append(exc_raw)
        elif name == "desc":
            if inc_raw:
                pq.desc_inc.append(inc_raw)
            if exc_raw:
                pq.desc_exc.append(exc_raw)
        elif name == "songs":
            pq.songs_inc.extend([t for t in (s.strip() for s in inc_raw.split(";")) if t])
            pq.songs_exc.extend([t for t in (s.strip() for s in exc_raw.split(";")) if t])
        elif name in ("artis", "artist", "author"):
            pq.artis_inc.extend([t for t in (s.strip() for s in inc_raw.split(";")) if t])
            pq.artis_exc.extend([t for t in (s.strip() for s in exc_raw.split(";")) if t])
        elif name in ("hash", "id"):
            if inc_raw:
                pq.hash_inc.append(inc_raw.split()[0])
        last = name if name in _DIRECTIVES else last
    if not directives_found:
        pq.plain = [t for t in text.split() if t]
    else:
        # trailing plain text after last directive (no @) was consumed as body;
        # that is intended: body belongs to directive.
        pass
    return pq


def match_score(hay: str, needle: str) -> int:
    """0=no match, 1=partial, 2=exact/contains-full-string. hay/needle lowered by caller."""
    if not needle:
        return 0
    if hay == needle:
        return 2
    if needle in hay:
        return 2 if len(needle) >= 4 else 1
    # word-level partial
    words = needle.split()
    hit = sum(1 for wd in words if wd and wd in hay)
    if hit == len(words) and words:
        return 1
    return 0
