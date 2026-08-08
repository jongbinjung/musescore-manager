from msm.gdrive import DriveSession, SyncStatus


class FakeRequest:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def execute(self, **kwargs):
        return self.response


class FakeFiles:
    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.list_calls = []
        self.create_calls = []
        self.update_calls = []
        self.delete_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        response = self.pages.pop(0) if self.pages else {"files": []}
        return FakeRequest(response)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return FakeRequest({"id": "created-id", "size": "3", "md5Checksum": "900150983cd24fb0d6963f7d28e17f72"})

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return FakeRequest({"id": kwargs["fileId"], "size": "3", "md5Checksum": "900150983cd24fb0d6963f7d28e17f72"})

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return FakeRequest({})


class FakeService:
    def __init__(self, pages=None):
        self.fake_files = FakeFiles(pages)

    def files(self):
        return self.fake_files


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
                        "id": "remote-id",
                        "name": "score.png",
                        "size": "3",
                        "md5Checksum": "900150983cd24fb0d6963f7d28e17f72",
                    }
                ]
            }
        ]
    )
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path)

    assert result.status == SyncStatus.SKIPPED
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


def test_sync_file_updates_stale_remote_file(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    service = FakeService([{"files": [{"id": "remote-id", "name": "score.png", "size": "2", "md5Checksum": "old"}]}])
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path)

    assert result.status == SyncStatus.UPDATED
    assert result.remote_id == "remote-id"
    assert service.fake_files.update_calls[0]["fileId"] == "remote-id"


def test_sync_file_dryrun_does_not_mutate(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    service = FakeService([{"files": []}])
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path, dryrun=True)

    assert result.status == SyncStatus.WOULD_CREATE
    assert not service.fake_files.create_calls
    assert not service.fake_files.update_calls


def test_sync_file_updates_latest_and_deletes_duplicate_remote_matches(tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"abc")
    remote = {
        "id": "one",
        "name": "score.png",
        "size": "2",
        "md5Checksum": "old",
        "modifiedTime": "2026-01-01T00:00:00Z",
    }
    service = FakeService(
        [
            {
                "files": [
                    remote,
                    {**remote, "id": "two", "modifiedTime": "2026-02-01T00:00:00Z"},
                    {**remote, "id": "three", "modifiedTime": "2025-12-01T00:00:00Z"},
                ]
            }
        ]
    )
    session = DriveSession(service, "folder-id")

    result = session.sync_file(path)

    assert result.status == SyncStatus.UPDATED
    assert result.remote_id == "two"
    assert service.fake_files.update_calls[0]["fileId"] == "two"
    assert [call["fileId"] for call in service.fake_files.delete_calls] == ["one", "three"]
