"""Google Drive integration for synchronizing exported PNG files."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from msm.paths import get_google_token_path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
PNG_MIME_TYPE = "image/png"
DRIVE_FIELDS = "nextPageToken,incompleteSearch,files(id,name,parents,mimeType,size,md5Checksum,modifiedTime,trashed)"


class SyncStatus(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    SKIPPED = "skipped"
    WOULD_CREATE = "would_create"
    WOULD_UPDATE = "would_update"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class SyncResult:
    path: Path
    status: SyncStatus
    remote_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class LocalFile:
    path: Path
    size: int
    md5: str


def authenticate(creds_path: Path, token_path: Path | None = None) -> Credentials:
    token_path = token_path or get_google_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path.expanduser().absolute()), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
        os.chmod(token_path, 0o600)

    return creds


def fingerprint(path: Path) -> LocalFile:
    digest = hashlib.md5(usedforsecurity=False)
    size = 0
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return LocalFile(path=path, size=size, md5=digest.hexdigest())


class DriveSession:
    """One authenticated Drive client and one sync run's remote index."""

    def __init__(self, service, folder_id: str):
        self.service = service
        self.folder_id = folder_id
        self._files_by_name: dict[str, list[dict]] | None = None

    @classmethod
    def from_credentials(cls, creds_path: Path, folder_id: str, token_path: Path | None = None) -> "DriveSession":
        creds = authenticate(creds_path, token_path)
        return cls(build("drive", "v3", credentials=creds), folder_id)

    def list_files(self) -> list[dict]:
        files: list[dict] = []
        page_token = None
        while True:
            request = self.service.files().list(
                q=f"'{self.folder_id}' in parents and trashed = false",
                spaces="drive",
                pageSize=1000,
                pageToken=page_token,
                fields=DRIVE_FIELDS,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            response = request.execute(num_retries=3)
            if response.get("incompleteSearch"):
                raise RuntimeError("Google Drive returned an incomplete search")
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return files

    def _remote_index(self) -> dict[str, list[dict]]:
        if self._files_by_name is None:
            self._files_by_name = {}
            for remote in self.list_files():
                self._files_by_name.setdefault(remote["name"], []).append(remote)
        return self._files_by_name

    def sync_file(self, path: Path, dryrun: bool = False) -> SyncResult:
        local = fingerprint(path)
        matches = [
            remote
            for remote in self._remote_index().get(path.name, [])
            if remote.get("mimeType", PNG_MIME_TYPE) == PNG_MIME_TYPE
        ]

        duplicates = []
        if matches:
            remote = max(matches, key=lambda candidate: candidate.get("modifiedTime", ""))
            duplicates = [candidate for candidate in matches if candidate is not remote]
        else:
            remote = None

        if remote is not None and remote.get("md5Checksum") == local.md5 and str(remote.get("size")) == str(local.size):
            return SyncResult(path, SyncStatus.SKIPPED, remote_id=remote["id"])

        if dryrun:
            status = SyncStatus.WOULD_UPDATE if remote is not None else SyncStatus.WOULD_CREATE
            return SyncResult(path, status, remote_id=remote.get("id") if remote else None)

        if remote is None:
            request = self.service.files().create(
                body={"name": path.name, "parents": [self.folder_id], "mimeType": PNG_MIME_TYPE},
                media_body=MediaFileUpload(str(path), mimetype=PNG_MIME_TYPE, resumable=True),
                fields="id,md5Checksum,size",
                supportsAllDrives=True,
            )
            response = request.execute(num_retries=3)
            self._remote_index().setdefault(path.name, []).append(response)
            return SyncResult(path, SyncStatus.CREATED, remote_id=response.get("id"))

        request = self.service.files().update(
            fileId=remote["id"],
            body={"name": path.name, "mimeType": PNG_MIME_TYPE},
            media_body=MediaFileUpload(str(path), mimetype=PNG_MIME_TYPE, resumable=True),
            fields="id,md5Checksum,size",
            supportsAllDrives=True,
        )
        response = request.execute(num_retries=3)
        remote.update(response)
        for duplicate in duplicates:
            self.service.files().delete(fileId=duplicate["id"], supportsAllDrives=True).execute(num_retries=3)
        return SyncResult(path, SyncStatus.UPDATED, remote_id=remote["id"])


def upload_file(
    file_path: Path,
    folder_id: str,
    creds_path: Path,
    dryrun: bool = False,
    token_path: Path | None = None,
) -> SyncResult:
    """Synchronize one file; retained as a compatibility entry point."""
    session = DriveSession.from_credentials(creds_path, folder_id, token_path)
    return session.sync_file(file_path, dryrun=dryrun)
