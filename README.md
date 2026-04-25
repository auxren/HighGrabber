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
pipx install highgrabber
highgrabber doctor        # downloads the browser used for login
```

Prefer to pin to a specific version? `pipx install highgrabber==0.1.1`.

### With uv

```bash
uv tool install highgrabber
highgrabber doctor
```

### From source (or a branch)

```bash
pipx install git+https://github.com/auxren/HighGrabber.git
highgrabber doctor
```

`highgrabber doctor` installs Playwright's Chromium into the right virtual
environment and verifies your session is good. Run it once after install
and any time you see a browser-related error.

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

- a full Hightail URL (`https://spaces.hightail.com/receive/<slug>` or
  `https://spaces.hightail.com/space/<slug>`)
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
| `--include REGEX` | none | only download files whose name matches this regex (case-insensitive) |
| `--exclude REGEX` | none | skip files whose name matches this regex (case-insensitive) |
| `--no-recursive-skip` | off | disable the default recursive scan of `--dest` that skips files already present (matches by basename, by extracted-archive directory name, or by `YYYY-MM-DD` date in the filename) |

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

**`no valid cached session` / session expired prompts** — run
`highgrabber login`. Normal on a fresh install or after a long break. If
you've been signed out because you logged in to Hightail from somewhere
else (including another HighGrabber machine), Hightail rotates your
session cookie server-side, so running `login` again is the fix.

**The browser window closes immediately / login times out** — the window
waits 5 minutes for a successful login. If Hightail shows a CAPTCHA, solve
it and click Sign In; the window closes as soon as the session cookie
lands. If nothing appears at all, run `highgrabber doctor` to check that
Chromium is installed.

**`Executable doesn't exist` / Playwright browser errors** — run
`highgrabber doctor`. It installs Chromium into the right environment.
The older `python -m playwright install chromium` pattern only works if
you installed HighGrabber into your system Python, not via pipx/uv.

**Downloads fail with `got=0 of N` or `HTTP 307` redirects** — you're
being rate-limited by Hightail. HighGrabber already backs off
(15 s / 60 s / 3 min / 5 min), but if it gives up:

- Lower `--concurrency` to `1`.
- Wait 10–15 minutes, then re-run the same command. Partial downloads
  resume via HTTP `Range` so nothing's lost.
- If it persists for hours, your IP may be temporarily flagged; switching
  network (e.g. VPN on/off) usually clears it.

**Wrong keychain password / authentication loop** — the keyring has a
stale password. Fix:
```bash
highgrabber logout --forget-password --email you@example.com
highgrabber login  --email you@example.com --save-password
```

**`macOS: keychain access denied`** — the macOS Keychain prompt defaults
to "Allow Always" once you approve. If you clicked Deny, follow the
keychain-reset recipe above.

**Disk full mid-batch** — partial files stay on disk; free space, re-run
the same command, and downloads resume from the last byte.

**Corporate proxy** — HighGrabber uses httpx, which respects standard
env vars:
```bash
HTTPS_PROXY=http://proxy.corp:8080 highgrabber <urls>
```

**Windows: Chromium blocked by antivirus** — some AV products quarantine
Playwright's bundled Chrome Headless Shell. Allow
`%LOCALAPPDATA%\ms-playwright\` in your AV, then run
`highgrabber doctor` again.

**A space returns 404 / "unavailable"** — Hightail expired or removed
that upload. It's per-space; other links in the same run continue.

**I want to see what would happen without actually downloading** —
`highgrabber --dry-run <urls>` prints every file and its size, then exits.

## Ethics & Terms

HighGrabber only fetches files you already have legitimate access to via
`receive/<slug>` links you were sent. It authenticates as *you* with *your*
credentials and respects Hightail's per-session cookie model. You are
responsible for your usage under Hightail's Terms of Service.

## License

MIT. See [LICENSE](LICENSE).
