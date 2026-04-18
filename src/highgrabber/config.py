"""Paths, hosts, and constants."""

from __future__ import annotations

from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "HighGrabber"
KEYRING_SERVICE = "highgrabber"

_DIRS = PlatformDirs(APP_NAME, appauthor=False)

CONFIG_DIR = Path(_DIRS.user_config_dir)
CACHE_DIR = Path(_DIRS.user_cache_dir)

STORAGE_STATE_PATH = CONFIG_DIR / "storage_state.json"
PLAYWRIGHT_USER_DATA_DIR = CACHE_DIR / "playwright-profile"

SPACES_HOST = "https://spaces.hightail.com"
API_HOST = "https://api.spaces.hightail.com"
DOWNLOAD_HOST = "https://download.spaces.hightail.com"

LOGIN_URL = f"{SPACES_HOST}/login"
SESSION_CHECK_URL = f"{API_HOST}/api/v1/auth/isSessionValid"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
