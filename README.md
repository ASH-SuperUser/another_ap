# another_ap — another audio player

minimal terminal-grade audio streaming for your LAN. python only, no node,
no react, no build step. anyone with the URL gets full control (no logins —
it's meant for trusted local nets).

```
git clone <repo> && cd another_ap
scripts/run.sh            # linux / mac / wsl  (foreground, ctrl+c stops)
scripts\run.bat           # windows            (foreground, ctrl+c stops)
python scripts/run.py     # anywhere (auto-creates ./venv, installs deps)

scripts/run_fg.sh         # foreground VERBOSE (debug logs, blocking)
scripts\run_fg.bat        # windows verbose foreground

python scripts/run.py --daemon        # background; logs -> data/another_ap.log
scripts/stop.sh           # stop a running server (pid file, else port scan)
scripts\stop.bat          # windows stop
```
then open **http://127.0.0.1:5050** (host/port in `config.py` or
`ANOTHER_AP_HOST` / `ANOTHER_AP_PORT` env).

## layout

```
config.py          host/port/paths (defaults: 127.0.0.1:5050)
requirements.txt   fastapi · uvicorn · jinja2 · duckdb · pillow · mutagen · yt-dlp · httpx
server/            fastapi backend (main.py, db.py, utils.py, downloads.py)
ui/templates/      jinja2 pages (no js framework, no cdn — works offline)
ui/static/         style.css, app.js, player.js
data/audio/        your tracks live here
data/thumbs/       generated + uploaded covers
data/main_db.db    duckdb (playlists, tracks, themes, settings)
scripts/           run.py (foreground | --daemon | --log-level) · run.sh/run.bat
               run_fg.sh/run_fg.bat (verbose blocking) · stop.py/stop.sh/stop.bat
```

## features

- **home** `/` — random tracks + playlists, global search, stats
- **playlists** `/playlists` — advanced search grammar + sort/filter, create/delete
- **playlist player** `/p/<hash>` — queue, shuffle, loop `off/one/all/Nx`,
  sleep timer, speed/volume/seek, reorder + save, add/remove tracks, edit meta,
  random (PIL-generated) or uploaded covers
- **audios** `/audios` — same search grammar for tracks
- **track player** `/a/<id>` — single player, same controls, add-to-playlist
- **+get** `/download` — upload file · fetch direct mp3 url · youtube
  (metadata prefill via `yt-dlp`, mp3 extract with graceful fallback)
- **themes** — dark/light/amber builtins + custom `#hex` themes stored on the
  server; last-selected remembered per browser
- **keyboard everything** — `?` opens help anywhere, `/` focuses search,
  `space/k/j/l/n/p/m/s/o/z`, `g h/p/a/d`, arrows, `0-9` queue jump

search grammar: `plain words` · `@tags a ; b ; @noti c` · `@title …` ·
`@desc …` · `@songs a ; b` · `@artis a ; b` · `@hash id` — see `/help`.

## api (json)

```
/api/audios /api/audios/{id}            GET (+PATCH/DELETE, POST upload/from-file)
/api/playlists /api/playlists/{id}      GET (+POST/PATCH/DELETE, add/remove/reorder)
/stream/{id}                            http Range streaming (seek-safe)
/api/fetch/url /api/fetch/yt-info /api/fetch/yt
/api/random /api/stats /api/themes /api/settings/theme
```

## notes

- all state is local: copy the repo + `data/` to move servers.
- youtube: mp3 when `ffmpeg` is on PATH, else a direct `.m4a` grab
  (plays in every browser, no postprocessing). install ffmpeg for mp3s.
- nothing is installed globally — `./venv` only.
