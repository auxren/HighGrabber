"""Concurrent, resumable downloads with exponential-backoff retry.

Hightail rate-limits aggressive clients by returning 200 with an empty body,
so `_attempt_download` treats a short response as a failure even without an
HTTP error code.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from . import config
from .api import HightailClient, HightailFile

console = Console()

_INVALID = '<>:"/\\|?*'
_BACKOFFS: tuple[int, ...] = (15, 60, 180, 300, 600)


def sanitize_filename(name: str) -> str:
    out = "".join("-" if ch in _INVALID else ch for ch in name)
    return out.strip().strip(".") or "unnamed"


@dataclass(slots=True)
class DownloadResult:
    file: HightailFile
    path: Path
    status: str  # "done" | "skip" | "fail"
    error: Optional[str] = None


@dataclass(slots=True)
class DownloadPlan:
    files: list[HightailFile]
    dest: Path
    filename_map: dict[str, Path] = field(default_factory=dict)


def build_plan(files: list[HightailFile], dest: Path) -> DownloadPlan:
    dest.mkdir(parents=True, exist_ok=True)
    plan = DownloadPlan(files=list(files), dest=dest)
    for f in files:
        plan.filename_map[f.file_id] = dest / sanitize_filename(f.name)
    return plan


async def _attempt_download(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    expected_size: int,
    progress: Progress,
    task: TaskID,
) -> bool:
    existing = dest.stat().st_size if dest.exists() else 0
    if existing == expected_size:
        progress.update(task, completed=expected_size)
        return True
    headers = {}
    if 0 < existing < expected_size:
        headers["Range"] = f"bytes={existing}-"
    mode = "ab" if existing else "wb"
    progress.update(task, completed=existing)
    written = existing
    async with client.stream("GET", url, headers=headers) as r:
        if r.status_code not in (200, 206):
            return False
        # If server ignored our Range and returned 200 from byte 0, rewrite.
        if existing and r.status_code == 200:
            mode = "wb"
            written = 0
            progress.update(task, completed=0)
        with dest.open(mode) as fh:
            async for chunk in r.aiter_bytes(chunk_size=1 << 20):
                fh.write(chunk)
                written += len(chunk)
                progress.update(task, completed=written)
    return dest.exists() and dest.stat().st_size == expected_size


def _fmt_size(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    x = float(n)
    for u in units:
        if x < 1024 or u == "TB":
            return f"{x:.1f} {u}" if u != "B" else f"{int(x)} B"
        x /= 1024
    return f"{x} B"


async def _download_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    f: HightailFile,
    dest: Path,
    progress: Progress,
    on_session_expired: Callable[[], None],
) -> DownloadResult:
    if dest.exists() and dest.stat().st_size == f.size:
        console.print(f"  [dim]skip[/dim] {f.name}")
        return DownloadResult(file=f, path=dest, status="skip")
    if dest.suffix.lower() == ".zip":
        unzipped = dest.with_suffix("")
        if unzipped.is_dir() and any(unzipped.iterdir()):
            console.print(f"  [dim]skip[/dim] {f.name} [dim](extracted)[/dim]")
            return DownloadResult(file=f, path=dest, status="skip")
    url = HightailClient.download_url(f)
    last_err = ""
    # The semaphore limits how many downloads run at once. We register the
    # rich progress task only once we're inside it — registering thousands of
    # tasks up front before any await deadlocks the live display thread.
    async with sem:
        console.print(f"  [cyan]get[/cyan]  {f.name} [dim]({_fmt_size(f.size)})[/dim]")
        task = progress.add_task(f.name, total=f.size, start=True)
        try:
            for attempt, backoff in enumerate(_BACKOFFS, start=1):
                try:
                    ok = await _attempt_download(client, url, dest, f.size, progress, task)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (401, 403):
                        on_session_expired()
                        console.print(f"  [red]FAIL[/red] {f.name}: auth expired ({exc.response.status_code})")
                        return DownloadResult(
                            file=f, path=dest, status="fail", error=f"auth expired ({exc.response.status_code})"
                        )
                    last_err = str(exc)
                    ok = False
                except httpx.HTTPError as exc:
                    last_err = str(exc)
                    ok = False
                except OSError as exc:
                    console.print(f"  [red]FAIL[/red] {f.name}: OS error: {exc}")
                    return DownloadResult(
                        file=f, path=dest, status="fail", error=f"OS error: {exc}"
                    )
                except Exception as exc:  # noqa: BLE001
                    # h2/httpcore can raise non-httpx exceptions (e.g.
                    # h2.exceptions.ProtocolError when Hightail abruptly
                    # closes a stream). One such error used to abort the
                    # whole asyncio.gather and kill the run; treat them as
                    # transient and retry.
                    last_err = f"{type(exc).__name__}: {exc}"
                    ok = False
                if ok:
                    console.print(f"  [green]done[/green] {f.name}")
                    return DownloadResult(file=f, path=dest, status="done")
                if attempt < len(_BACKOFFS):
                    console.print(f"  [yellow]retry[/yellow] {f.name} in {backoff}s ({last_err or 'short body'})")
                    await asyncio.sleep(backoff)
        finally:
            progress.remove_task(task)
    console.print(f"  [red]FAIL[/red] {f.name}: {last_err or 'unknown'}")
    return DownloadResult(file=f, path=dest, status="fail", error=last_err or "unknown")


async def _run(
    plan: DownloadPlan,
    cookies: httpx.Cookies,
    concurrency: int,
    on_session_expired: Callable[[], None],
) -> list[DownloadResult]:
    limits = httpx.Limits(max_connections=max(4, concurrency * 2))
    headers = {
        "user-agent": config.DEFAULT_USER_AGENT,
        "referer": f"{config.SPACES_HOST}/",
    }
    sem = asyncio.Semaphore(concurrency)
    with Progress(
        TextColumn("[bold blue]{task.description}", justify="left"),
        BarColumn(bar_width=None),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        expand=True,
        console=console,
    ) as progress:
        async with httpx.AsyncClient(
            cookies=cookies,
            headers=headers,
            timeout=httpx.Timeout(30.0, read=None),
            limits=limits,
            http2=True,
            follow_redirects=True,
        ) as client:
            tasks = [
                _download_one(
                    client, sem, f, plan.filename_map[f.file_id], progress, on_session_expired
                )
                for f in plan.files
            ]
            return list(await asyncio.gather(*tasks))


def download_all(
    plan: DownloadPlan,
    cookies: httpx.Cookies,
    *,
    concurrency: int = 2,
    on_session_expired: Callable[[], None] = lambda: None,
) -> list[DownloadResult]:
    return asyncio.run(_run(plan, cookies, concurrency, on_session_expired))
