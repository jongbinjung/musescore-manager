import builtins
from pathlib import Path

import pytest

from msm.exceptions import MissingDependencyError
from msm.s3 import RemoteState, S3Store, SourceVersion, SyncStatus, sync_file


class NotFoundError(Exception):
    response = {"Error": {"Code": "404"}}


class ServiceError(Exception):
    response = {"Error": {"Code": "500"}}


class FakeClient:
    def __init__(self, response=None):
        self.response = response
        self.uploads = []

    def head_object(self, **kwargs):
        if self.response is None:
            raise NotFoundError
        return self.response

    def put_object(self, **kwargs):
        self.uploads.append(kwargs)


def test_source_version_round_trips_through_object_metadata(tmp_path):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"score")
    version = SourceVersion.from_path(path)

    assert SourceVersion.from_metadata(version.metadata()) == version
    assert SourceVersion.from_metadata({}) is None


def test_s3_store_reads_missing_and_existing_state():
    assert S3Store(FakeClient(), "scores").state("score.mscz") == RemoteState(exists=False)

    version = SourceVersion(modified_ns=123, size=45, digest="abc")
    client = FakeClient({"Metadata": version.metadata(), "ETag": '"etag"'})
    assert S3Store(client, "scores").state("score.mscz") == RemoteState(
        exists=True,
        version=version,
        etag='"etag"',
    )


def test_s3_store_propagates_service_errors():
    class FailingClient(FakeClient):
        def head_object(self, **kwargs):
            raise ServiceError

    with pytest.raises(ServiceError):
        S3Store(FailingClient(), "scores").state("score.mscz")


def test_s3_store_reports_missing_optional_dependency(monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "boto3":
            raise ModuleNotFoundError("No module named 'boto3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(MissingDependencyError, match="'s3' package extra"):
        S3Store.connect("scores")


def test_sync_uploads_new_file_with_source_metadata(tmp_path):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"score")
    client = FakeClient()
    store = S3Store(client, "scores")

    result = sync_file(store, path, "Score-C.mscz")

    assert result.status is SyncStatus.CREATED
    assert client.uploads == [
        {
            "Bucket": "scores",
            "Key": "Score-C.mscz",
            "Body": b"score",
            "Metadata": SourceVersion.from_path(path).metadata(),
            "IfNoneMatch": "*",
        }
    ]


def test_sync_skips_matching_file(tmp_path):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"score")
    version = SourceVersion.from_path(path)
    client = FakeClient({"Metadata": version.metadata()})

    result = sync_file(S3Store(client, "scores"), path, "Score-C.mscz")

    assert result.status is SyncStatus.UNCHANGED
    assert client.uploads == []


def test_sync_conditionally_updates_older_remote_file(tmp_path):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"local")
    local = SourceVersion.from_path(path)
    remote = SourceVersion(modified_ns=local.modified_ns - 1, size=3, digest="old")
    client = FakeClient({"Metadata": remote.metadata(), "ETag": '"old-etag"'})

    result = sync_file(S3Store(client, "scores"), path, "Score-C.mscz")

    assert result.status is SyncStatus.UPDATED
    assert client.uploads[0]["Body"] == b"local"
    assert client.uploads[0]["IfMatch"] == '"old-etag"'


def test_sync_dryrun_reads_state_without_uploading(tmp_path):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"score")
    client = FakeClient({"Metadata": {}})

    result = sync_file(S3Store(client, "scores"), path, "Score-C.mscz", dryrun=True)

    assert result.status is SyncStatus.UPDATED
    assert client.uploads == []


def test_sync_dryrun_does_not_read_file_when_remote_metadata_is_missing(tmp_path, monkeypatch):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"score")
    client = FakeClient({"Metadata": {}})

    def fail_read(self):
        raise AssertionError("file should not be read")

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    result = sync_file(S3Store(client, "scores"), path, "Score-C.mscz", dryrun=True)

    assert result.status is SyncStatus.UPDATED


def test_sync_dryrun_does_not_read_file_when_remote_is_newer(tmp_path, monkeypatch):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"score")
    local_stat = path.stat()
    client = FakeClient(
        {
            "Metadata": {
                "source-mtime-ns": str(local_stat.st_mtime_ns + 1),
                "source-size": "1",
            }
        }
    )

    def fail_read(self):
        raise AssertionError("file should not be read")

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    result = sync_file(S3Store(client, "scores"), path, "Score-C.mscz", dryrun=True)

    assert result.status is SyncStatus.REMOTE_NEWER


def test_sync_does_not_overwrite_newer_remote_file(tmp_path):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"local")
    local = SourceVersion.from_path(path)
    remote = SourceVersion(modified_ns=local.modified_ns + 1, size=6, digest="different")
    client = FakeClient({"Metadata": remote.metadata()})

    result = sync_file(S3Store(client, "scores"), path, "Score-C.mscz")

    assert result.status is SyncStatus.REMOTE_NEWER
    assert client.uploads == []


def test_sync_detects_same_timestamp_content_conflict(tmp_path):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"local")
    local = SourceVersion.from_path(path)
    remote = SourceVersion(modified_ns=local.modified_ns, size=local.size, digest="different")
    client = FakeClient({"Metadata": remote.metadata()})

    result = sync_file(S3Store(client, "scores"), path, "Score-C.mscz")

    assert result.status is SyncStatus.CONFLICT
    assert client.uploads == []


def test_sync_uses_content_digest_for_unchanged_file(tmp_path):
    path = tmp_path / "score.mscz"
    path.write_bytes(b"same")
    local = SourceVersion.from_path(path)
    remote = SourceVersion(modified_ns=local.modified_ns + 1, size=local.size, digest=local.digest)
    client = FakeClient({"Metadata": remote.metadata()})

    result = sync_file(S3Store(client, "scores"), path, "Score-C.mscz")

    assert result.status is SyncStatus.UNCHANGED
