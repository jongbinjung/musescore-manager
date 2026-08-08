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

    @classmethod
    def lazy_from_path(cls, path: Path) -> "_LazySourceSnapshot":
        """Capture local metadata now and defer reading the file."""
        return _LazySourceSnapshot(path)


class _LazySourceSnapshot:
    def __init__(self, path: Path):
        self.path = path
        stat = path.stat()
        self.modified_ns = stat.st_mtime_ns
        self.size = stat.st_size
        self._snapshot: SourceSnapshot | None = None

    def materialize(self) -> SourceSnapshot:
        if self._snapshot is None:
            before = self.path.stat()
            if (before.st_mtime_ns, before.st_size) != (self.modified_ns, self.size):
                raise RuntimeError(f"Source changed while reading: {self.path}")
            content = self.path.read_bytes()
            after = self.path.stat()
            if (after.st_mtime_ns, after.st_size) != (self.modified_ns, self.size):
                raise RuntimeError(f"Source changed while reading: {self.path}")
            self._snapshot = SourceSnapshot(
                version=SourceVersion(
                    modified_ns=after.st_mtime_ns,
                    size=after.st_size,
                    digest=sha256(content).hexdigest(),
                ),
                content=content,
            )
        return self._snapshot

    @property
    def version(self) -> SourceVersion:
        return self.materialize().version

    @property
    def content(self) -> bytes:
        return self.materialize().content


@dataclass(frozen=True)
class RemoteState:
    exists: bool
    version: SourceVersion | None = None
    etag: str | None = None
    modified_ns: int | None = None
    size: int | None = None


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
        max_pool_connections: int | None = None,
    ) -> "S3Store":
        try:
            import boto3
            from botocore.client import Config
        except ModuleNotFoundError as error:
            raise MissingDependencyError("S3 upload requires the 's3' package extra") from error

        config_kwargs: dict[str, object] = {"s3": {"addressing_style": "virtual"}}
        if max_pool_connections is not None:
            config_kwargs["max_pool_connections"] = max_pool_connections
        client = boto3.client(
            "s3",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            endpoint_url=endpoint_url,
            config=Config(**config_kwargs),
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

        metadata = response.get("Metadata", {})
        version = SourceVersion.from_metadata(metadata)
        try:
            modified_ns = int(metadata["source-mtime-ns"])
            size = int(metadata["source-size"])
        except (KeyError, TypeError, ValueError):
            modified_ns = size = None
        if version is not None:
            modified_ns = size = None
        return RemoteState(
            exists=True,
            version=version,
            etag=response.get("ETag"),
            modified_ns=modified_ns,
            size=size,
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
    snapshot = SourceSnapshot.lazy_from_path(path)
    local_version = None
    remote = store.state(key)

    if remote.version is not None:
        # A remote digest takes precedence over timestamps, so it must be compared.
        local_version = snapshot.version
        if remote.version.digest == local_version.digest:
            return SyncResult(key=key, status=SyncStatus.UNCHANGED)

    if remote.version is not None and remote.version.modified_ns > snapshot.modified_ns:
        return SyncResult(key=key, status=SyncStatus.REMOTE_NEWER)

    if remote.version is not None and remote.version.modified_ns == snapshot.modified_ns:
        return SyncResult(key=key, status=SyncStatus.CONFLICT)

    if remote.modified_ns is not None and remote.size is not None:
        if remote.modified_ns > snapshot.modified_ns:
            return SyncResult(key=key, status=SyncStatus.REMOTE_NEWER)
        if remote.modified_ns == snapshot.modified_ns:
            return SyncResult(key=key, status=SyncStatus.CONFLICT)

    status = SyncStatus.UPDATED if remote.exists else SyncStatus.CREATED
    if not dryrun:
        if local_version is None:
            local_version = snapshot.version
        store.upload(snapshot.content, key, local_version, remote)
    return SyncResult(key=key, status=status)
