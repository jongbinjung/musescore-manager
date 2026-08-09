"""Google Drive integration for synchronizing files."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from msm.exceptions import MissingDependencyError
from msm.remote import Artifact, SyncResult, SyncStatus

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
MANAGED_PROPERTY = {"musescoreManager": "png-sync"}
FILE_FIELDS = "id,name,parents,mimeType,size,md5Checksum,modifiedTime,trashed,version,appProperties"
DRIVE_FIELDS = f"nextPageToken,incompleteSearch,files({FILE_FIELDS})"


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
        raise MissingDependencyError("Google Drive sync requires the 'drive' package extra") from error

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


def authenticate(creds_path: Path, token_path: Path):
    Request, Credentials, RefreshError, InstalledAppFlow, _, _ = _google_dependencies()
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


def _escape_drive_query_string(value: str) -> str:
    """Escape a value used as a single-quoted Google Drive query string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveSession:
    """One authenticated Drive client and one sync run's remote index."""

    concurrent = False

    def __init__(self, service, folder_id: str):
        self.service = service
        self.folder_id = folder_id
        self._files_by_name: dict[str, list[dict]] | None = None

    @classmethod
    def from_credentials(cls, creds_path: Path, folder_id: str, token_path: Path) -> "DriveSession":
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

    def list_files(self, name: str | None = None) -> list[dict]:
        files: list[dict] = []
        page_token = None
        query = f"'{_escape_drive_query_string(self.folder_id)}' in parents and trashed = false"
        if name is not None:
            query = f"{query} and name = '{_escape_drive_query_string(name)}'"
        while True:
            request = self.service.files().list(
                q=query,
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

    def sync(self, artifact: Artifact, dryrun: bool = False, force: bool = False) -> SyncResult:
        return self._sync_file(artifact.path, artifact.name, artifact.media_type, dryrun, force)

    def sync_file(
        self,
        path: Path,
        dryrun: bool = False,
        remote_name: str | None = None,
        media_type: str = "image/png",
    ) -> SyncResult:
        return self._sync_file(path, remote_name or path.name, media_type, dryrun)

    def _sync_file(
        self, path: Path, remote_name: str, media_type: str, dryrun: bool, force: bool = False
    ) -> SyncResult:
        local = fingerprint(path)
        matches = self._name_matches(remote_name, self._remote_index().get(remote_name, []))
        unmanaged = [remote for remote in matches if not self._is_managed(remote)]
        managed = [remote for remote in matches if self._is_managed(remote)]

        if unmanaged and not force:
            return SyncResult(remote_name, SyncStatus.CONFLICT, error="same-name Drive file is not managed by this app")
        if any(remote.get("mimeType") != media_type for remote in managed) and not force:
            return SyncResult(
                remote_name, SyncStatus.CONFLICT, error="same-name app-managed Drive file has another type"
            )
        if len(matches) > 1 and len(managed) <= 1:
            return SyncResult(remote_name, SyncStatus.CONFLICT, error="multiple same-name Drive files exist")
        if len(managed) > 1:
            return SyncResult(remote_name, SyncStatus.CONFLICT, error="multiple app-managed Drive files have this name")

        remote = matches[0] if force and len(matches) == 1 else (managed[0] if managed else None)

        if remote is not None and remote.get("md5Checksum") == local.md5 and str(remote.get("size")) == str(local.size):
            return SyncResult(remote_name, SyncStatus.UNCHANGED, remote_id=remote["id"])

        if dryrun:
            status = SyncStatus.UPDATED if remote is not None else SyncStatus.CREATED
            return SyncResult(remote_name, status, remote_id=remote.get("id") if remote else None)

        if remote is None:
            if self.list_files(remote_name):
                return SyncResult(remote_name, SyncStatus.CONFLICT, error="same-name Drive file appeared during sync")
            _, _, _, _, _, MediaIoBaseUpload = _google_dependencies()
            request = self.service.files().create(
                body={
                    "name": remote_name,
                    "parents": [self.folder_id],
                    "mimeType": media_type,
                    "appProperties": MANAGED_PROPERTY,
                },
                media_body=MediaIoBaseUpload(io.BytesIO(local.content), mimetype=media_type, resumable=True),
                fields=FILE_FIELDS,
                supportsAllDrives=True,
            )
            response = request.execute(num_retries=0)
            response.update(name=remote_name, mimeType=media_type, appProperties=MANAGED_PROPERTY)
            self._remote_index().setdefault(remote_name, []).append(response)
            return SyncResult(remote_name, SyncStatus.CREATED, remote_id=response.get("id"))

        if force:
            current = remote
        else:
            refreshed_matches = self.list_files(remote_name)
            if len(refreshed_matches) != 1 or refreshed_matches[0].get("id") != remote["id"]:
                return SyncResult(
                    remote_name, SyncStatus.CONFLICT, remote_id=remote["id"], error="Drive folder changed during sync"
                )

            current = refreshed_matches[0]
            if not self._is_managed(current) or not self._same_revision(remote, current):
                return SyncResult(
                    remote_name, SyncStatus.CONFLICT, remote_id=remote["id"], error="Drive file changed during sync"
                )

        _, _, _, _, _, MediaIoBaseUpload = _google_dependencies()
        request = self.service.files().update(
            fileId=remote["id"],
            body={"name": remote_name, "mimeType": media_type, "appProperties": MANAGED_PROPERTY},
            media_body=MediaIoBaseUpload(io.BytesIO(local.content), mimetype=media_type, resumable=True),
            fields=FILE_FIELDS,
            supportsAllDrives=True,
        )
        response = request.execute(num_retries=0)
        remote.update(response)
        return SyncResult(remote_name, SyncStatus.UPDATED, remote_id=remote["id"])
