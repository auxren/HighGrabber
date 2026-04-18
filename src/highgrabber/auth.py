"""Authentication: Playwright-driven login + persistent session state.

Hightail's API login endpoint sits behind a WAF / Castle bot-detection and
reCaptcha. A scripted HTTP login is not reliable. Instead we launch a real
Chromium (headless after first run), reuse the session, and refresh on 401.

Credentials are optionally stored in the system keychain via `keyring`:
  - service:  "highgrabber"
  - username: the user's Hightail email
  - password: the user's Hightail password
"""

from __future__ import annotations

import getpass
import json
import sys
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import keyring
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright
from rich.console import Console

from . import config

console = Console(stderr=True)


@dataclass(slots=True)
class Session:
    cookies: dict[str, str]

    def as_httpx_cookies(self) -> httpx.Cookies:
        jar = httpx.Cookies()
        for name, value in self.cookies.items():
            jar.set(name, value, domain=".hightail.com", path="/")
        return jar


def _load_storage_state() -> Optional[dict]:
    try:
        return json.loads(config.STORAGE_STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_storage_state(state: dict) -> None:
    config.ensure_dirs()
    config.STORAGE_STATE_PATH.write_text(json.dumps(state, indent=2))
    try:
        config.STORAGE_STATE_PATH.chmod(0o600)
    except OSError:
        pass


def _session_from_state(state: dict) -> Session:
    jar: dict[str, str] = {}
    for c in state.get("cookies", []):
        dom = c.get("domain", "")
        if "hightail.com" in dom:
            jar[c["name"]] = c["value"]
    return Session(cookies=jar)


def check_session(session: Session) -> bool:
    """Return True iff Hightail still considers the session valid."""
    try:
        r = httpx.get(
            config.SESSION_CHECK_URL,
            params={"cacheBuster": int(time.time() * 1000)},
            cookies=session.as_httpx_cookies(),
            headers={
                "accept": "application/json",
                "referer": f"{config.SPACES_HOST}/",
                "user-agent": config.DEFAULT_USER_AGENT,
            },
            timeout=15.0,
        )
    except httpx.HTTPError:
        return False
    if r.status_code != 200:
        return False
    try:
        return r.json().get("status") == "OK"
    except ValueError:
        return False


def _get_keychain_password(email: str) -> Optional[str]:
    try:
        return keyring.get_password(config.KEYRING_SERVICE, email)
    except keyring.errors.KeyringError:
        return None


def _save_keychain_password(email: str, password: str) -> None:
    try:
        keyring.set_password(config.KEYRING_SERVICE, email, password)
    except keyring.errors.KeyringError as exc:
        console.print(f"[yellow]could not save password to keychain: {exc}[/yellow]")


def delete_keychain_password(email: str) -> None:
    try:
        keyring.delete_password(config.KEYRING_SERVICE, email)
    except keyring.errors.KeyringError:
        pass


def interactive_login(
    email: Optional[str] = None,
    save_password: bool = False,
    headless: bool = False,
) -> Session:
    """Launch a Chromium window for the user to log in; persist + return state.

    If `email` is provided and a keychain password exists, the form is
    prefilled. Any reCaptcha / anti-bot step still requires the user to act.
    """
    if email is None:
        email = input("Hightail email: ").strip()
    password = _get_keychain_password(email)
    if password is None:
        password = getpass.getpass("Hightail password: ")
        if save_password:
            _save_keychain_password(email, password)

    config.ensure_dirs()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=config.DEFAULT_USER_AGENT)
        page = ctx.new_page()

        console.print(f"[cyan]opening {config.LOGIN_URL}[/cyan]")
        page.goto(config.LOGIN_URL, wait_until="domcontentloaded")

        # Best-effort form auto-fill. Selectors are resilient to minor changes:
        # the login form always has these two typed inputs.
        try:
            page.wait_for_selector("input[type='email'], input[name='email']", timeout=15_000)
            page.fill("input[type='email'], input[name='email']", email)
            page.fill("input[type='password'], input[name='password']", password)
        except PWTimeoutError:
            console.print("[yellow]login form did not appear — complete login manually.[/yellow]")

        console.print(
            "[bold]Complete the login in the browser window.[/bold]\n"
            "If a CAPTCHA appears, solve it and click Sign In.\n"
            "This window will close automatically when the session is established."
        )

        # Wait for the session cookie Hightail sets server-side post-login.
        deadline = time.time() + 300
        session: Optional[Session] = None
        while time.time() < deadline:
            state = ctx.storage_state()
            s = _session_from_state(state)
            if "sessionId" in s.cookies and check_session(s):
                session = s
                _save_storage_state(state)
                break
            time.sleep(1.5)

        browser.close()
        if session is None:
            raise RuntimeError(
                "login did not complete within 5 minutes — re-run `highgrabber login`"
            )
        console.print("[green]login successful; session saved.[/green]")
        return session


def load_session(
    *,
    allow_interactive: bool = True,
    email: Optional[str] = None,
    save_password: bool = False,
) -> Session:
    """Return a valid Hightail session, reusing cached state where possible."""
    state = _load_storage_state()
    if state is not None:
        s = _session_from_state(state)
        if s.cookies and check_session(s):
            return s
    if not allow_interactive:
        raise RuntimeError("no valid cached session; run `highgrabber login` first")
    console.print("[yellow]no valid session cached; launching browser login[/yellow]")
    return interactive_login(email=email, save_password=save_password)


def clear_session() -> None:
    try:
        config.STORAGE_STATE_PATH.unlink()
    except FileNotFoundError:
        pass


def refresh_session(email: Optional[str] = None) -> Session:
    """Force a fresh interactive login, discarding any cached state."""
    clear_session()
    return interactive_login(email=email)
