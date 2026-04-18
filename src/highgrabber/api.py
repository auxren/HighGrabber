"""Thin client over the Hightail Spaces API.

Endpoints used (reverse-engineered from the Spaces web app bundle):

  GET {API_HOST}/api/v1/spaces/url/<slug>?status=SEND
      → space metadata (includes the internal spaceId, sp-...).

  GET {API_HOST}/api/v1/files/<spaceId>/untagged?...
      → the file list for a space (children[]).

  GET {DOWNLOAD_HOST}/api/v1/download/<spaceId>/<fileId>/<versionId>/<urlenc-name>
      → streams the file bytes; supports Range resume.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

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

    def get_space(self, slug: str) -> SpaceInfo:
        r = self._http.get(
            f"{config.API_HOST}/api/v1/spaces/url/{slug}",
            params={"status": "SEND"},
        )
        self._raise_for_auth(r)
        if r.status_code == 404:
            # Hightail returns {"errorMessage":"invalid status"} for expired spaces.
            raise SpaceUnavailable(slug)
        r.raise_for_status()
        data = r.json()
        space_id = data["id"]
        name = data.get("name") or slug

        files = self._list_files(space_id, slug)
        return SpaceInfo(slug=slug, space_id=space_id, name=name, files=files)

    def _list_files(self, space_id: str, slug: str) -> list[HightailFile]:
        r = self._http.get(
            f"{config.API_HOST}/api/v1/files/{space_id}/untagged",
            params={
                "cacheBuster": int(time.time() * 1000),
                "depth": 1,
                "dir": "ASC",
                "limit": 500,
                "offset": 0,
                "sort": "custom",
                "spaceUrl": slug,
                "term": "",
            },
        )
        self._raise_for_auth(r)
        r.raise_for_status()
        data = r.json()
        out: list[HightailFile] = []
        for child in data.get("children", []) or []:
            if child.get("isDirectory"):
                continue
            if child.get("fileState") not in (None, "AVAILABLE"):
                continue
            out.append(
                HightailFile(
                    space_id=child["spaceId"],
                    file_id=child["fileId"],
                    version_id=child["versionId"],
                    name=child["name"],
                    size=int(child.get("size") or 0),
                )
            )
        return out

    @staticmethod
    def download_url(f: HightailFile) -> str:
        from urllib.parse import quote

        return (
            f"{config.DOWNLOAD_HOST}/api/v1/download/"
            f"{f.space_id}/{f.file_id}/{f.version_id}/{quote(f.name)}"
        )
