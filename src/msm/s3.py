"""S3 storage adapter and score synchronization workflow."""

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from msm.exceptions import MissingDependencyError


@dataclass(frozen=True)
class SourceVersion:
    modified_ns: int
    size: int
    digest: str

    @classmethod
    def from_path(cls, path: Path) -> "SourceVersion":
        return SourceSnapshot.from_path(path).version

    @classmethod
    def from_metadata(cls, metadata: dict[str, str]) -> "SourceVersion | None":
        try:
            return cls(
                modified_ns=int(metadata["source-mtime-ns"]),
                size=int(metadata["source-size"]),
                digest=metadata["source-sha256"],
            )
        except (KeyError, TypeError, ValueError):
            return None

    def metadata(self) -> dict[str, str]:
        return {
            "source-mtime-ns": str(self.modified_ns),
            "source-size": str(self.size),
            "source-sha256": self.digest,
        }


@dataclass(frozen=True)
class SourceSnapshot:
    version: SourceVersion
    content: bytes

    @classmethod
    def from_path(cls, path: Path) -> "SourceSnapshot":
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise RuntimeError(f"Source changed while reading: {path}")
        return cls(
            version=SourceVersion(
                modified_ns=after.st_mtime_ns,
                size=after.st_size,
                digest=sha256(content).hexdigest(),
            ),
            content=content,
        )


@dataclass(frozen=True)
class RemoteState:
    exists: bool
    version: SourceVersion | None = None
    etag: str | None = None


class ObjectStore(Protocol):
    def state(self, key: str) -> RemoteState: ...

    def upload(self, content: bytes, key: str, version: SourceVersion, remote: RemoteState) -> None: ...


class SyncStatus(Enum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    REMOTE_NEWER = "remote-newer"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class SyncResult:
    key: str
    status: SyncStatus


class S3Store:
    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket = bucket

    @classmethod
    def connect(
        cls,
        bucket: str,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        endpoint_url: str | None = None,
    ) -> "S3Store":
        try:
            import boto3
            from botocore.client import Config
        except ModuleNotFoundError as error:
            raise MissingDependencyError("S3 upload requires the 's3' package extra") from error

        client = boto3.client(
            "s3",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            endpoint_url=endpoint_url,
            config=Config(s3={"addressing_style": "virtual"}),
        )
        return cls(client, bucket)

    def state(self, key: str) -> RemoteState:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            code = str(getattr(error, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return RemoteState(exists=False)
            raise

        return RemoteState(
            exists=True,
            version=SourceVersion.from_metadata(response.get("Metadata", {})),
            etag=response.get("ETag"),
        )

    def upload(self, content: bytes, key: str, version: SourceVersion, remote: RemoteState) -> None:
        if remote.exists and remote.etag is None:
            raise RuntimeError(f"S3 did not return an ETag for existing object: {key}")
        conditions = {"IfMatch": remote.etag} if remote.exists else {"IfNoneMatch": "*"}
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            Metadata=version.metadata(),
            **conditions,
        )


def sync_file(store: ObjectStore, path: Path, key: str, dryrun: bool = False) -> SyncResult:
    snapshot = SourceSnapshot.from_path(path)
    local_version = snapshot.version
    remote = store.state(key)

    if remote.version is not None and remote.version.digest == local_version.digest:
        return SyncResult(key=key, status=SyncStatus.UNCHANGED)

    if remote.version is not None and remote.version.modified_ns > local_version.modified_ns:
        return SyncResult(key=key, status=SyncStatus.REMOTE_NEWER)

    if remote.version is not None and remote.version.modified_ns == local_version.modified_ns:
        return SyncResult(key=key, status=SyncStatus.CONFLICT)

    status = SyncStatus.UPDATED if remote.exists else SyncStatus.CREATED
    if not dryrun:
        store.upload(snapshot.content, key, local_version, remote)
    return SyncResult(key=key, status=status)
