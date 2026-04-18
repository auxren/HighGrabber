# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e .                          # dev install (src layout, hatchling backend)
python -m playwright install chromium     # one-time: download browser used for login
python -m highgrabber <inputs> -d <dest>  # run without installing the console script
highgrabber login --email X --save-password  # establish session, optionally keychain-save pw
```

There is no test suite or linter configured; `python -m py_compile src/highgrabber/*.py` is the
cheapest full-syntax check.

## Architecture

HighGrabber is a small CLI built around a single authenticated session against three Hightail
endpoints. The modules split strictly along IO concerns.

### Auth is the hard part, not the downloads

Hightail's login endpoint sits behind a WAF + Castle bot detection + reCaptcha, so there is no
scripted HTTP login. `auth.py` launches a real Chromium via Playwright, waits for the user to
complete login, and snapshots the cookie jar to `storage_state.json` under `platformdirs`'
user-config directory. Every subsequent run loads that snapshot and validates it with a
single `isSessionValid` call; only on failure does it re-open a browser.

Credentials live in the system keychain via `keyring` (service `"highgrabber"`, username = email).
The storage-state file is written with `0600` on POSIX.

### API surface is reverse-engineered, not documented

`api.py` encapsulates three endpoints discovered by reading the Spaces SPA bundle:

1. `GET api.spaces.hightail.com/api/v1/spaces/url/<slug>?status=SEND` → space metadata (the
   `id` field is the internal `sp-UUID` used in every other call)
2. `GET api.spaces.hightail.com/api/v1/files/<sp-id>/untagged` → `children[]` file list;
   filter out `isDirectory` and any `fileState` that isn't `AVAILABLE`
3. `GET download.spaces.hightail.com/api/v1/download/<sp>/<fi>/<fv>/<urlenc-name>` → streams
   file bytes and honors `Range` for resume

`401/403` raise `SessionExpired`; `404` on the slug lookup raises `SpaceUnavailable` (Hightail
serves a misleading `{"errorMessage":"invalid status"}` for expired spaces).

### Downloads: two things Hightail does that most hosts don't

`download.py` is an `asyncio` + `httpx` streamer with two non-obvious behaviors it has to
handle:

- **Silent rate-limiting.** Beyond ~2–3 concurrent streams of large archives, Hightail returns
  `HTTP 200` with an empty body instead of `429`. `_attempt_download` treats short responses as
  failures and the caller retries with exponential backoff `(15, 60, 180, 300, 600)s`. Default
  concurrency is `2` for this reason.
- **Range resume is optional.** If we send `Range` but the server returns `200` from byte 0,
  the code detects it and rewrites the file from scratch rather than appending.

On auth failure mid-batch, `_download_one` calls the `on_session_expired` callback and returns
`fail`. `cli._cmd_download` observes that flag, calls `auth.refresh_session()` to re-launch
the browser, and retries only the failed items.

### Input parsing is permissive

`parse.py` accepts URLs, file paths (greps links out of any text — forwarded emails, chat
exports, prose), or `-` for stdin. Slugs are deduplicated in encounter order.

### CLI defaults

`cli.py` routes to `download` when the first argv isn't a known subcommand, so bare
`highgrabber <url>` works. ZIP extraction is on by default via `extract.py` (which has
zip-slip protection and skips already-populated output directories).

## Distribution

- Entry point: `highgrabber = "highgrabber.cli:main"` in `pyproject.toml`.
- `platformdirs` drives all user-facing paths — don't hardcode `~/.config` etc.
- Keep runtime deps minimal (httpx, playwright, keyring, platformdirs, rich). `pyinstaller` is a
  dev-only optional for standalone-binary GitHub releases.
