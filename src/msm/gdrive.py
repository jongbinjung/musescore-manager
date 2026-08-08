"""Google Drive integration for synchronizing exported PNG files."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from msm.config import get_google_token_path
from msm.exceptions import MissingDependencyError

SCOPES = ["https://www.googleapis.com/auth/drive"]
PNG_MIME_TYPE = "image/png"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
MANAGED_PROPERTY = {"musescoreManager": "png-sync"}
FILE_FIELDS = "id,name,parents,mimeType,size,md5Checksum,modifiedTime,trashed,version,appProperties"
DRIVE_FIELDS = f"nextPageToken,incompleteSearch,files({FILE_FIELDS})"


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
    content: bytes


def _google_dependencies():
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
    except ModuleNotFoundError as error:
        raise MissingDependencyError("Google Drive sync requires the 'gcs' package extra") from error

    return Request, Credentials, RefreshError, InstalledAppFlow, build, MediaIoBaseUpload


def _write_token(path: Path, contents: str) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary_path = Path(file.name)
            file.write(contents)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def authenticate(creds_path: Path, token_path: Path | None = None):
    Request, Credentials, RefreshError, InstalledAppFlow, _, _ = _google_dependencies()
    token_path = token_path or get_google_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    creds = None
    if token_path.exists():
        try:
            token_data = json.loads(token_path.read_text())
            stored_scopes = set(token_data.get("scopes", []))
            if set(SCOPES).issubset(stored_scopes):
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except (OSError, TypeError, ValueError):
            pass

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path.expanduser().absolute()), SCOPES)
            creds = flow.run_local_server(port=0)
        _write_token(token_path, creds.to_json())

    return creds


def fingerprint(path: Path) -> LocalFile:
    before = path.stat()
    content = path.read_bytes()
    after = path.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise RuntimeError(f"Source changed while reading: {path}")
    digest = hashlib.md5(content, usedforsecurity=False).hexdigest()
    return LocalFile(path=path, size=len(content), md5=digest, content=content)


class DriveSession:
    """One authenticated Drive client and one sync run's remote index."""

    def __init__(self, service, folder_id: str):
        self.service = service
        self.folder_id = folder_id
        self._files_by_name: dict[str, list[dict]] | None = None

    @classmethod
    def from_credentials(cls, creds_path: Path, folder_id: str, token_path: Path | None = None) -> "DriveSession":
        _, _, _, _, build, _ = _google_dependencies()
        creds = authenticate(creds_path, token_path)
        return cls(build("drive", "v3", credentials=creds), folder_id)

    def validate_folder(self) -> None:
        folder = (
            self.service.files()
            .get(fileId=self.folder_id, fields="id,mimeType,trashed", supportsAllDrives=True)
            .execute(num_retries=3)
        )
        if folder.get("trashed") or folder.get("mimeType") != FOLDER_MIME_TYPE:
            raise ValueError(f"Google Drive destination {self.folder_id} is not an accessible folder")

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

    @staticmethod
    def _is_managed(remote: dict) -> bool:
        return remote.get("appProperties", {}).get("musescoreManager") == MANAGED_PROPERTY["musescoreManager"]

    @staticmethod
    def _same_revision(expected: dict, current: dict) -> bool:
        keys = ("version", "modifiedTime", "md5Checksum", "size")
        return all(str(expected.get(key)) == str(current.get(key)) for key in keys)

    @staticmethod
    def _name_matches(name: str, files: list[dict]) -> list[dict]:
        return [remote for remote in files if remote.get("name") == name]

    def sync_file(self, path: Path, dryrun: bool = False) -> SyncResult:
        local = fingerprint(path)
        matches = self._name_matches(path.name, self._remote_index().get(path.name, []))
        unmanaged = [remote for remote in matches if not self._is_managed(remote)]
        managed = [remote for remote in matches if self._is_managed(remote)]

        if unmanaged:
            return SyncResult(path, SyncStatus.CONFLICT, error="same-name Drive file is not managed by this app")
        if any(remote.get("mimeType") != PNG_MIME_TYPE for remote in managed):
            return SyncResult(path, SyncStatus.CONFLICT, error="same-name app-managed Drive file is not a PNG")
        if len(managed) > 1:
            return SyncResult(path, SyncStatus.CONFLICT, error="multiple app-managed Drive files have this name")

        remote = managed[0] if managed else None

        if remote is not None and remote.get("md5Checksum") == local.md5 and str(remote.get("size")) == str(local.size):
            return SyncResult(path, SyncStatus.SKIPPED, remote_id=remote["id"])

        if dryrun:
            status = SyncStatus.WOULD_UPDATE if remote is not None else SyncStatus.WOULD_CREATE
            return SyncResult(path, status, remote_id=remote.get("id") if remote else None)

        if remote is None:
            if self._name_matches(path.name, self.list_files()):
                return SyncResult(path, SyncStatus.CONFLICT, error="same-name Drive file appeared during sync")
            _, _, _, _, _, MediaIoBaseUpload = _google_dependencies()
            request = self.service.files().create(
                body={
                    "name": path.name,
                    "parents": [self.folder_id],
                    "mimeType": PNG_MIME_TYPE,
                    "appProperties": MANAGED_PROPERTY,
                },
                media_body=MediaIoBaseUpload(io.BytesIO(local.content), mimetype=PNG_MIME_TYPE, resumable=True),
                fields=FILE_FIELDS,
                supportsAllDrives=True,
            )
            response = request.execute(num_retries=0)
            response.update(name=path.name, mimeType=PNG_MIME_TYPE, appProperties=MANAGED_PROPERTY)
            self._remote_index().setdefault(path.name, []).append(response)
            return SyncResult(path, SyncStatus.CREATED, remote_id=response.get("id"))

        refreshed_matches = self._name_matches(path.name, self.list_files())
        if len(refreshed_matches) != 1 or refreshed_matches[0].get("id") != remote["id"]:
            return SyncResult(
                path, SyncStatus.CONFLICT, remote_id=remote["id"], error="Drive folder changed during sync"
            )

        current = (
            self.service.files()
            .get(fileId=remote["id"], fields=FILE_FIELDS, supportsAllDrives=True)
            .execute(num_retries=3)
        )
        if not self._is_managed(current) or not self._same_revision(remote, current):
            return SyncResult(path, SyncStatus.CONFLICT, remote_id=remote["id"], error="Drive file changed during sync")

        _, _, _, _, _, MediaIoBaseUpload = _google_dependencies()
        request = self.service.files().update(
            fileId=remote["id"],
            body={"name": path.name, "mimeType": PNG_MIME_TYPE, "appProperties": MANAGED_PROPERTY},
            media_body=MediaIoBaseUpload(io.BytesIO(local.content), mimetype=PNG_MIME_TYPE, resumable=True),
            fields=FILE_FIELDS,
            supportsAllDrives=True,
        )
        response = request.execute(num_retries=0)
        remote.update(response)
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
