"""Provider-neutral models for one-way remote synchronization."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Artifact:
    path: Path
    name: str
    media_type: str


class SyncStatus(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    REMOTE_NEWER = "remote-newer"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class SyncResult:
    name: str
    status: SyncStatus
    remote_id: str | None = None
    error: str | None = None


class RemoteTarget(Protocol):
    concurrent: bool

    def sync(self, artifact: Artifact, dryrun: bool = False, force: bool = False) -> SyncResult: ...
