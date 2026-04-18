"""Command-line entry point.

Subcommands:
  highgrabber login     — open a browser to establish a session.
  highgrabber logout    — forget the cached session (keeps keychain password).
  highgrabber download  — (default) fetch every Hightail link from the inputs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from . import __version__
from . import auth
from .api import HightailClient, SessionExpired, SpaceUnavailable
from .download import DownloadResult, build_plan, download_all
from .extract import extract_zip, is_zip
from .parse import collect_slugs

console = Console()
err = Console(stderr=True)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def _cmd_login(args: argparse.Namespace) -> int:
    auth.interactive_login(
        email=args.email,
        save_password=args.save_password,
        headless=False,
    )
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    auth.clear_session()
    if args.forget_password and args.email:
        auth.delete_keychain_password(args.email)
        err.print(f"[green]removed keychain password for {args.email}[/green]")
    err.print("[green]session cleared[/green]")
    return 0


def _resolve_dest(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    return Path.cwd().resolve()


def _cmd_download(args: argparse.Namespace) -> int:
    dest = _resolve_dest(args.dest)
    inputs: list[str] = list(args.inputs) if args.inputs else []
    if not inputs and not sys.stdin.isatty():
        inputs = ["-"]
    if not inputs:
        err.print("[red]no inputs given. Pass a URL, file, or '-' for stdin.[/red]")
        return 2

    slugs = collect_slugs(inputs)
    if not slugs:
        err.print("[red]no Hightail links found in the input.[/red]")
        return 2
    err.print(f"[cyan]found {len(slugs)} Hightail link(s)[/cyan]")

    session = auth.load_session(email=args.email, save_password=args.save_password)

    session_expired = {"flag": False}

    def _on_expired() -> None:
        session_expired["flag"] = True

    all_files = []
    file_to_space: dict[str, str] = {}
    with HightailClient(session) as client:
        for i, slug in enumerate(slugs, 1):
            try:
                info = client.get_space(slug)
            except SessionExpired:
                err.print("[yellow]session expired while enumerating; re-logging in[/yellow]")
                session = auth.refresh_session(email=args.email)
                with HightailClient(session) as c2:
                    info = c2.get_space(slug)
            except SpaceUnavailable:
                err.print(f"[yellow]skip unavailable space #{i}: {slug}[/yellow]")
                continue
            err.print(
                f"[dim]#{i}/{len(slugs)}[/dim] {info.name} "
                f"[dim]({len(info.files)} files, {_fmt_size(info.total_size)})[/dim]"
            )
            for f in info.files:
                all_files.append(f)
                file_to_space[f.file_id] = info.name

    if not all_files:
        err.print("[red]no files to download.[/red]")
        return 1

    total = sum(f.size for f in all_files)
    err.print(
        f"[bold cyan]preparing {len(all_files)} files, total {_fmt_size(total)} → {dest}[/bold cyan]"
    )

    if args.dry_run:
        for f in all_files:
            console.print(f"{_fmt_size(f.size):>10}  {f.name}")
        return 0

    plan = build_plan(all_files, dest)

    results = download_all(
        plan,
        session.as_httpx_cookies(),
        concurrency=args.concurrency,
        on_session_expired=_on_expired,
    )

    if session_expired["flag"]:
        err.print("[yellow]session expired mid-download; refreshing and retrying failed items[/yellow]")
        session = auth.refresh_session(email=args.email)
        failed = [r.file for r in results if r.status == "fail"]
        if failed:
            plan2 = build_plan(failed, dest)
            retry = download_all(
                plan2,
                session.as_httpx_cookies(),
                concurrency=args.concurrency,
            )
            by_id = {r.file.file_id: r for r in results}
            for r in retry:
                by_id[r.file.file_id] = r
            results = list(by_id.values())

    done = [r for r in results if r.status == "done"]
    skipped = [r for r in results if r.status == "skip"]
    failed = [r for r in results if r.status == "fail"]
    err.print(
        f"[bold]downloads[/bold]: [green]{len(done)} done[/green], "
        f"[dim]{len(skipped)} skipped[/dim], [red]{len(failed)} failed[/red]"
    )
    for r in failed:
        err.print(f"  [red]FAIL[/red] {r.file.name}: {r.error}")

    if args.extract:
        successful_paths = [r.path for r in results if r.status in ("done", "skip")]
        zips = [p for p in successful_paths if is_zip(p)]
        if zips:
            err.print(f"[bold cyan]extracting {len(zips)} archive(s)[/bold cyan]")
            for z in zips:
                res = extract_zip(z)
                if res.status == "fail":
                    err.print(f"  [red]EXTRACT FAIL[/red] {z.name}: {res.error}")
                elif res.status == "done":
                    err.print(f"  [green]extracted[/green] {z.name}")
                if args.delete_zips_after and res.status == "done":
                    try:
                        z.unlink()
                    except OSError as exc:
                        err.print(f"  [yellow]could not delete {z.name}: {exc}[/yellow]")

    return 0 if not failed else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="highgrabber",
        description="Bulk-download Hightail Spaces archives.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd")

    dl = sub.add_parser("download", help="Download from Hightail links (default).")
    dl.add_argument("inputs", nargs="*", help="URL, file path, or '-' for stdin.")
    dl.add_argument("-d", "--dest", help="Destination directory (default: cwd).")
    dl.add_argument("-c", "--concurrency", type=int, default=2, help="Parallel downloads (default 2).")
    dl.add_argument("--email", help="Hightail email (used for keychain / prefilled login).")
    dl.add_argument("--save-password", action="store_true", help="Save password to system keychain on login.")
    dl.add_argument("--no-extract", dest="extract", action="store_false", help="Do not unzip archives after download.")
    dl.add_argument("--delete-zips-after", action="store_true", help="Delete .zip after successful extract.")
    dl.add_argument("--dry-run", action="store_true", help="List files that would be downloaded, then exit.")
    dl.set_defaults(extract=True, func=_cmd_download)

    lg = sub.add_parser("login", help="Open a browser and establish a Hightail session.")
    lg.add_argument("--email", help="Hightail email.")
    lg.add_argument("--save-password", action="store_true", help="Save password to system keychain.")
    lg.set_defaults(func=_cmd_login)

    lo = sub.add_parser("logout", help="Forget the cached session.")
    lo.add_argument("--email", help="Email whose keychain entry to remove (with --forget-password).")
    lo.add_argument("--forget-password", action="store_true", help="Also remove the keychain password.")
    lo.set_defaults(func=_cmd_logout)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    argv = list(argv if argv is not None else sys.argv[1:])

    # Default to `download` when the first arg isn't a known subcommand.
    known = {"download", "login", "logout", "-h", "--help", "--version"}
    if not argv or argv[0] not in known:
        argv = ["download", *argv]

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
