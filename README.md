# HighGrabber

Bulk-download from [Hightail Spaces](https://spaces.hightail.com) `receive`
links. Give it a URL, a list of URLs, or a messy document with links buried
in prose — HighGrabber finds them, logs you in once via your browser, and
pulls every file to a destination you choose (auto-unzipping by default).

Works on macOS, Windows, and Linux.

---

## Why

Hightail's web UI lets you download files one space at a time, serially,
through the browser. If a friend shares fifty concert archives, clicking
through fifty tabs and drag-extracting fifty ZIPs is a long afternoon.
HighGrabber does it in one command.

It handles the two annoying parts of automating Hightail:

1. **Login is gated by a WAF + CAPTCHA**, so scripted HTTP login won't work.
   HighGrabber opens a real Chromium window the first time, you sign in once,
   and it reuses the session cookie on every run afterward.
2. **Hightail rate-limits aggressive clients** by returning `HTTP 200` with an
   empty body. HighGrabber detects this, backs off exponentially, and resumes
   partial downloads via `Range` headers.

## Install

Requires **Python 3.10+**.

### With pipx (recommended)

```bash
pipx install git+https://github.com/auxren/HighGrabber.git
highgrabber --version

# One-time: install the Playwright browser used for login
python -m playwright install chromium
```

### With uv

```bash
uv tool install git+https://github.com/auxren/HighGrabber.git
python -m playwright install chromium
```

### From source

```bash
git clone https://github.com/auxren/HighGrabber.git
cd HighGrabber
pip install -e .
python -m playwright install chromium
```

## Quickstart

```bash
# One URL
highgrabber https://spaces.hightail.com/receive/u3l28vnWZd -d ~/Downloads/show

# Many URLs from a file (prose is fine; it finds the links)
highgrabber message.txt -d ~/Downloads/shows

# Piped in
pbpaste | highgrabber -d ~/Downloads/shows

# Multiple inputs at once
highgrabber url1 url2 file.txt -d ~/Downloads/shows
```

The first run opens a Chromium window for you to log in to Hightail. Once
the session is saved (`~/.config/HighGrabber/storage_state.json` on Linux,
`~/Library/Application Support/HighGrabber/` on macOS,
`%APPDATA%\HighGrabber\` on Windows), subsequent runs are fully silent.

## Usage

```
highgrabber [download] [INPUTS...] [options]
highgrabber login    [--email EMAIL] [--save-password]
highgrabber logout   [--forget-password --email EMAIL]
```

### Inputs

Each input is one of:

- a full Hightail URL (`https://spaces.hightail.com/receive/<slug>`)
- a path to any text file — HighGrabber extracts every Hightail link found
  anywhere in it, so forwarded emails, chat exports, and Notes pages all work
- `-` to read from stdin

Slugs are de-duplicated across all inputs in order.

### Options (download)

| flag | default | description |
|---|---|---|
| `-d, --dest PATH` | cwd | where to save files |
| `-c, --concurrency N` | `2` | parallel file downloads. Higher than 3 is usually rate-limited. |
| `--email EMAIL` | prompt | Hightail email (used for keychain lookup) |
| `--save-password` | off | save password to the system keychain on first login |
| `--no-extract` | off | keep ZIPs intact instead of auto-extracting |
| `--delete-zips-after` | off | delete each ZIP after successful extraction |

### Authentication

By default, HighGrabber uses the system keychain (macOS Keychain, Windows
Credential Manager, or Secret Service on Linux) via the `keyring` package.

- `highgrabber login --email you@example.com --save-password` stores your
  password once. Subsequent logins auto-fill the form; you only touch the
  browser if Hightail shows a CAPTCHA.
- `highgrabber logout` removes the cached session; add `--forget-password`
  to also delete the keychain entry.
- A valid session lasts as long as Hightail's cookie does (usually weeks).
  If it expires mid-run, HighGrabber opens the browser again automatically
  and resumes.

## How it works

HighGrabber hits three internal Hightail endpoints that the Spaces web app
uses:

| endpoint | purpose |
|---|---|
| `GET api.spaces.hightail.com/api/v1/spaces/url/<slug>?status=SEND` | resolve a `receive/` slug to an internal space id |
| `GET api.spaces.hightail.com/api/v1/files/<space_id>/untagged` | list files in that space |
| `GET download.spaces.hightail.com/api/v1/download/<sp>/<fi>/<fv>/<name>` | stream one file (supports `Range` resume) |

Authentication is a `sessionId` cookie on `.hightail.com`. HighGrabber gets
it by letting you log in through a real browser (Playwright-managed
Chromium), then snapshots the cookie jar to disk.

Concurrency is `2` by default. Push it higher only if you have fewer, smaller
files; Hightail's rate limiter triggers reliably around 3+ parallel streams
of large archives, and the symptom is `HTTP 200` with an empty body.

## Troubleshooting

**`no valid cached session`** — run `highgrabber login`. This is normal on a
fresh install or after a long break.

**The browser window closes immediately / login times out** — the window
waits 5 minutes for a successful login. If Hightail shows a CAPTCHA, solve
it and click Sign In; the window closes as soon as the session cookie lands.

**Playwright says "Executable doesn't exist"** — you skipped
`python -m playwright install chromium`. Run it once.

**Downloads keep failing with `got=0 of N`** — you're being rate-limited.
Lower `--concurrency` to `1`, wait ~10 minutes, and re-run. HighGrabber
resumes partial downloads, so nothing is lost.

**`macOS: keychain access denied`** — the keyring prompt defaults to
"Allow Always" once you approve. If you clicked Deny, run
`highgrabber logout --forget-password --email you@example.com` and log in
again.

**A space returns 404 / "unavailable"** — Hightail expired or removed that
upload. The error is per-space; other links in the same run continue.

## Ethics & Terms

HighGrabber only fetches files you already have legitimate access to via
`receive/<slug>` links you were sent. It authenticates as *you* with *your*
credentials and respects Hightail's per-session cookie model. You are
responsible for your usage under Hightail's Terms of Service.

## License

MIT. See [LICENSE](LICENSE).
