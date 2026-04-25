"""Thin client over the Hightail Spaces API.

Endpoints used (reverse-engineered from the Spaces web app bundle):

  GET {API_HOST}/api/v1/spaces/url/<slug>?status=ACTIVE
      → space metadata (includes the internal spaceId, sp-...).

  GET {API_HOST}/api/v2/spacetags/folders/<spaceId>?limit=N&offset=M
      → list of tag-folders (paginated). Spaces with many files are typically
      organized into tag-folders rather than at the untagged root.

  GET {API_HOST}/api/v1/files/<spaceId>/tag/<tagId>?limit=N&offset=M&...
      → files inside one tag-folder (paginated children[]).

  GET {API_HOST}/api/v1/files/<spaceId>/untagged?...
      → files at the space root (no tag). Often empty for tagged spaces.

  GET {DOWNLOAD_HOST}/api/v1/download/<spaceId>/<fileId>/<versionId>/<urlenc-name>
      → streams the file bytes; supports Range resume.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from . import config
from .auth import Session


class SessionExpired(Exception):
    """Raised when a request is rejected for missing / expired credentials."""


class SpaceUnavailable(Exception):
    """Raised when Hightail reports the space in an invalid state (expired / deleted)."""


@dataclass(slots=True)
class HightailFile:
    space_id: str
    file_id: str
    version_id: str
    name: str
    size: int


@dataclass(slots=True)
class SpaceInfo:
    slug: str
    space_id: str
    name: str
    files: list[HightailFile]

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


class HightailClient:
    def __init__(self, session: Session) -> None:
        self._http = httpx.Client(
            cookies=session.as_httpx_cookies(),
            headers={
                "accept": "application/json",
                "origin": config.SPACES_HOST,
                "referer": f"{config.SPACES_HOST}/",
                "user-agent": config.DEFAULT_USER_AGENT,
            },
            timeout=30.0,
            http2=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "HightailClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _raise_for_auth(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise SessionExpired(f"{r.status_code} on {r.url}")

    def get_space(
        self,
        slug: str,
        progress: Optional[Callable[[str], None]] = None,
    ) -> SpaceInfo:
        r = self._http.get(
            f"{config.API_HOST}/api/v1/spaces/url/{slug}",
            params={"status": "ACTIVE"},
        )
        self._raise_for_auth(r)
        if r.status_code == 404:
            # Hightail returns {"errorMessage":"invalid status"} for expired spaces.
            raise SpaceUnavailable(slug)
        r.raise_for_status()
        data = r.json()
        space_id = data["id"]
        name = data.get("name") or slug

        files = self._list_files(space_id, slug, progress=progress)
        return SpaceInfo(slug=slug, space_id=space_id, name=name, files=files)

    _RETRY_STATUSES = (408, 429, 500, 502, 503, 504)
    _RETRY_BACKOFFS = (5, 15, 45, 120)

    def _get_with_retry(self, url: str, *, params: dict) -> httpx.Response:
        """Hightail's listing API is slow and frequently 504s; retry with backoff."""
        last_exc: Optional[Exception] = None
        attempts = len(self._RETRY_BACKOFFS) + 1
        for i in range(attempts):
            try:
                r = self._http.get(url, params=params, timeout=180.0)
                self._raise_for_auth(r)
                if r.status_code in self._RETRY_STATUSES:
                    last_exc = httpx.HTTPStatusError(
                        f"{r.status_code} on listing", request=r.request, response=r
                    )
                else:
                    r.raise_for_status()
                    return r
            except (httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ConnectError) as exc:
                last_exc = exc
            if i < len(self._RETRY_BACKOFFS):
                time.sleep(self._RETRY_BACKOFFS[i])
        assert last_exc is not None
        raise last_exc

    def _list_tags(self, space_id: str) -> list[dict]:
        """Return every tag-folder (`st-...`) defined on the space."""
        out: list[dict] = []
        offset = 0
        page_size = 200
        while True:
            r = self._get_with_retry(
                f"{config.API_HOST}/api/v2/spacetags/folders/{space_id}",
                params={"limit": page_size, "offset": offset},
            )
            page = r.json() or []
            if not isinstance(page, list):
                break
            out.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return out

    def _list_files(
        self,
        space_id: str,
        slug: str,
        progress: Optional[Callable[[str], None]] = None,
    ) -> list[HightailFile]:
        if progress is None:
            progress = lambda _msg: None
        out: list[HightailFile] = []
        progress("listing untagged root…")
        out.extend(self._list_at("untagged", space_id, slug))
        tags = self._list_tags(space_id)
        progress(f"found {len(tags)} tag-folder(s)")
        for i, tag in enumerate(tags, 1):
            tag_id = tag.get("id")
            if not tag_id:
                continue
            tag_name = tag.get("name") or tag_id
            progress(f"  [{i}/{len(tags)}] {tag_name}")
            out.extend(self._list_at(f"tag/{tag_id}", space_id, slug))
            if i < len(tags):
                time.sleep(0.5)
        return _dedupe_by_file_id(out)

    def _list_at(self, suffix: str, space_id: str, slug: str) -> list[HightailFile]:
        """Paginate through `/files/<sp>/<suffix>` and collect AVAILABLE files."""
        out: list[HightailFile] = []
        offset = 0
        page_size = 500
        while True:
            r = self._get_with_retry(
                f"{config.API_HOST}/api/v1/files/{space_id}/{suffix}",
                params={
                    "cacheBuster": int(time.time() * 1000),
                    "depth": 1,
                    "dir": "ASC",
                    "limit": page_size,
                    "offset": offset,
                    "sort": "custom",
                    "spaceUrl": slug,
                    "term": "",
                },
            )
            data = r.json() or {}
            children = data.get("children") or []
            if not isinstance(children, list):
                break
            for child in children:
                if child.get("isDirectory"):
                    continue
                if child.get("fileState") not in (None, "AVAILABLE"):
                    continue
                try:
                    out.append(
                        HightailFile(
                            space_id=child["spaceId"],
                            file_id=child["fileId"],
                            version_id=child["versionId"],
                            name=child["name"],
                            size=int(child.get("size") or 0),
                        )
                    )
                except KeyError:
                    continue
            if len(children) < page_size:
                break
            offset += page_size
        return out

    @staticmethod
    def download_url(f: HightailFile) -> str:
        from urllib.parse import quote

        return (
            f"{config.DOWNLOAD_HOST}/api/v1/download/"
            f"{f.space_id}/{f.file_id}/{f.version_id}/{quote(f.name)}"
        )


def _dedupe_by_file_id(files: list[HightailFile]) -> list[HightailFile]:
    """Drop duplicates produced when one file appears under multiple tags."""
    seen: set[str] = set()
    out: list[HightailFile] = []
    for f in files:
        if f.file_id in seen:
            continue
        seen.add(f.file_id)
        out.append(f)
    return out
