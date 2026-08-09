import builtins

import pytest

from msm.exceptions import MissingDependencyError
from msm.gdrive import DriveSession
from msm.remote import Artifact, SyncStatus


class FakeRequest:
    def __init__(self, response, exception=None):
        self.response = response
        self.exception = exception
        self.execute_calls = []

    def execute(self, **kwargs):
        self.execute_calls.append(kwargs)
        return self.response


class FakeBatch:
    def __init__(self, callback):
        self.callback = callback
        self.requests = []

    def add(self, request, request_id):
        self.requests.append((request_id, request))

    def execute(self):
        for request_id, request in self.requests:
            self.callback(request_id, request.response, request.exception)


class FakeFiles:
    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.files_by_id = {file["id"]: file for page in self.pages for file in page.get("files", []) if "id" in file}
        self.list_calls = []
        self.get_calls = []
        self.create_calls = []
        self.create_requests = []
        self.update_calls = []
        self.delete_calls = []
        self.delete_exceptions = {}

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        response = self.pages.pop(0) if self.pages else {"files": list(self.files_by_id.values())}
        return FakeRequest(response)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.files_by_id[kwargs["fileId"]])

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        request = FakeRequest({"id": "created-id", "size": "3", "md5Checksum": "900150983cd24fb0d6963f7d28e17f72"})
        self.create_requests.append(request)
        return request

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return FakeRequest({"id": kwargs["fileId"], "size": "3", "md5Checksum": "900150983cd24fb0d6963f7d28e17f72"})

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return FakeRequest({}, self.delete_exceptions.get(kwargs["fileId"]))


class FakeService:
    def __init__(self, pages=None):
        self.fake_files = FakeFiles(pages)
        self.batches = []

    def files(self):
        return self.fake_files

    def new_batch_http_request(self, callback):
        batch = FakeBatch(callback)
        self.batches.append(batch)
        return batch


def managed_file(**values):
    return {
        "mimeType": "image/png",
        "appProperties": {"musescoreManager": "png-sync"},
        **values,
    }


def test_list_files_paginates_and_indexes_folder_query():
    service = FakeService([{"files": [{"id": "one", "name": "one.png"}], "nextPageToken": "next"}, {"files": []}])
    session = DriveSession(service, "folder-id")

    assert [file["id"] for file in session.list_files()] == ["one"]
    assert len(service.fake_files.list_calls) == 2
    assert "'folder-id' in parents" in service.fake_files.list_calls[0]["q"]
    assert service.fake_files.list_calls[1]["pageToken"] == "next"


def test_sync_file_skips_matching_remote_file(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    service = FakeService(
        [
            {
                "files": [
                    {
                        **managed_file(
                            id="remote-id",
                            name="score.png",
                            size="3",
                            md5Checksum="900150983cd24fb0d6963f7d28e17f72",
                        )
                    }
                ]
            }
        ]
    )
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path)

    assert result.status == SyncStatus.UNCHANGED
    assert result.remote_id == "remote-id"
    assert not service.fake_files.create_calls
    assert not service.fake_files.update_calls


def test_sync_file_creates_missing_remote_file(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    service = FakeService([{"files": []}])
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path)

    assert result.status == SyncStatus.CREATED
    assert result.remote_id == "created-id"
    request = service.fake_files.create_calls[0]
    assert request["body"]["name"] == "score.png"
    assert request["body"]["parents"] == ["folder-id"]
    assert request["body"]["mimeType"] == "image/png"
    assert request["body"]["appProperties"] == {"musescoreManager": "png-sync"}
    assert service.fake_files.create_requests[0].execute_calls == [{"num_retries": 0}]


def test_sync_file_updates_stale_remote_file(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    service = FakeService([{"files": [managed_file(id="remote-id", name="score.png", size="2", md5Checksum="old")]}])
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path)

    assert result.status == SyncStatus.UPDATED
    assert result.remote_id == "remote-id"
    assert service.fake_files.update_calls[0]["fileId"] == "remote-id"
    assert len(service.fake_files.list_calls) == 2
    assert "name = 'score.png'" in service.fake_files.list_calls[1]["q"]
    assert not service.fake_files.get_calls


def test_sync_file_escapes_name_in_mutation_query(tmp_path):
    path = tmp_path / "score\\'s.png"
    path.write_bytes(b"abc")
    service = FakeService([{"files": []}])

    result = DriveSession(service, "folder-id").sync_file(path)

    assert result.status == SyncStatus.CREATED
    assert "name = 'score\\\\\\'s.png'" in service.fake_files.list_calls[1]["q"]


def test_sync_file_dryrun_does_not_mutate(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    service = FakeService([{"files": []}])
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path, dryrun=True)

    assert result.status == SyncStatus.CREATED
    assert not service.fake_files.create_calls
    assert not service.fake_files.update_calls


def test_sync_file_conflicts_with_unmanaged_same_name_file(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    remote = {
        "id": "one",
        "name": "score.png",
        "mimeType": "image/png",
        "size": "2",
        "md5Checksum": "old",
    }
    service = FakeService([{"files": [remote]}])
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path)

    assert result.status == SyncStatus.CONFLICT
    assert result.error is not None
    assert "not managed" in result.error
    assert not service.fake_files.update_calls


def test_sync_force_pushes_a_conflicting_score(tmp_path):
    path = tmp_path / "source.mscz"
    path.write_bytes(b"abc")
    remote = {
        "id": "one",
        "name": "Score-C.mscz",
        "mimeType": "application/x-musescore",
        "size": "2",
        "md5Checksum": "old",
    }
    service = FakeService([{"files": [remote]}])

    result = DriveSession(service, "folder-id").sync(
        Artifact(path=path, name="Score-C.mscz", media_type="application/x-musescore"), force=True
    )

    assert result.status == SyncStatus.UPDATED
    assert result.remote_id == "one"
    assert service.fake_files.update_calls[0]["fileId"] == "one"


def test_sync_file_conflicts_with_unmanaged_same_name_non_png(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    remote = {"id": "one", "name": "score.png", "mimeType": "application/pdf"}
    service = FakeService([{"files": [remote]}])

    result = DriveSession(service, "folder-id").sync_file(path)

    assert result.status == SyncStatus.CONFLICT
    assert not service.fake_files.create_calls


def test_sync_file_conflicts_when_same_name_file_appears_before_update(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    remote = managed_file(id="one", name="score.png", size="2", md5Checksum="old")
    duplicate = {"id": "two", "name": "score.png", "mimeType": "application/pdf"}
    service = FakeService([{"files": [remote]}, {"files": [remote, duplicate]}])

    result = DriveSession(service, "folder-id").sync_file(path)

    assert result.status == SyncStatus.CONFLICT
    assert result.error == "Drive folder changed during sync"
    assert not service.fake_files.update_calls


def test_sync_file_conflicts_with_duplicate_managed_files(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    remote = managed_file(id="one", name="score.png", size="2", md5Checksum="old")
    service = FakeService([{"files": [remote, {**remote, "id": "two"}]}])

    result = DriveSession(service, "folder-id").sync_file(path)

    assert result.status == SyncStatus.CONFLICT
    assert result.error is not None
    assert "multiple app-managed" in result.error
    assert not service.fake_files.update_calls


def test_sync_accepts_score_artifact_name_and_media_type(tmp_path):
    path = tmp_path / "source.mscz"
    path.write_bytes(b"abc")
    service = FakeService([{"files": []}])

    result = DriveSession(service, "folder-id").sync(
        Artifact(path=path, name="Normalized-C.mscz", media_type="application/vnd.musescore.score")
    )

    assert result.name == "Normalized-C.mscz"
    assert service.fake_files.create_calls[0]["body"]["mimeType"] == "application/vnd.musescore.score"


def test_validate_folder_rejects_non_folder():
    service = FakeService([{"files": [{"id": "folder-id", "mimeType": "image/png"}]}])

    with pytest.raises(ValueError, match="not an accessible folder"):
        DriveSession(service, "folder-id").validate_folder()


def test_clear_batches_deletes_and_reports_progress():
    service = FakeService([{"files": [{"id": str(index)} for index in range(201)]}])
    updates = []

    DriveSession(service, "folder-id").clear(progress=lambda amount, total: updates.append((amount, total)))

    assert [len(batch.requests) for batch in service.batches] == [100, 100, 1]
    assert len(service.fake_files.delete_calls) == 201
    assert updates == [(0, 201), (100, 201), (100, 201), (1, 201)]


def test_clear_retries_failed_batch_deletes():
    service = FakeService([{"files": [{"id": "one"}, {"id": "two"}]}])
    service.fake_files.delete_exceptions["two"] = RuntimeError("temporary failure")

    DriveSession(service, "folder-id").clear()

    failed_request = service.batches[0].requests[1][1]
    assert failed_request.execute_calls == [{"num_retries": 3}]


def test_missing_google_dependencies_are_reported_only_when_connecting(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def block_google_import(name, *args, **kwargs):
        if name == "google" or name.startswith(("google.", "google_auth_oauthlib", "googleapiclient")):
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_google_import)

    with pytest.raises(MissingDependencyError, match="'drive' package extra"):
        DriveSession.from_credentials(tmp_path / "credentials.json", "folder-id", tmp_path / "token.json")
