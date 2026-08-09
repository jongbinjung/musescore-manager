from pathlib import Path

import pytest

from msm.config import Configs, get_configs_path, get_google_token_path


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ENDPOINT_URL_S3",
        "GOOGLE_APP_CREDENTIALS_JSON_PATH",
        "GOOGLE_DRIVE_FOLDER_ID",
        "LOCAL_MSCZ_DIRECTORY",
        "LOCAL_PNG_DIRECTORY",
        "MSCORE_CMD",
        "MSCZ_BUCKET_NAME",
        "JOBS",
    ):
        monkeypatch.delenv(name, raising=False)


def write_config(home: Path, contents: str) -> Path:
    path = home / ".msm" / "configs"
    path.parent.mkdir()
    path.write_text(contents)
    return path


def test_environment_takes_precedence_without_reading_invalid_profile(monkeypatch, tmp_path):
    write_config(tmp_path, "[default]\nMSCORE_CMD=from-file\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MSCORE_CMD", "from-env")

    assert Configs(profile_name="missing").mscore_cmd() == "from-env"


def test_profile_value_takes_precedence_over_default(monkeypatch, tmp_path):
    write_config(tmp_path, "[work]\nMSCORE_CMD=from-file\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Configs(profile_name="work").mscore_cmd() == "from-file"


def test_missing_value_uses_default(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Configs().mscore_cmd() == "mscore"
    assert Configs().mscz_bucket_name() is None
    assert Configs().jobs() == 4


def test_jobs_can_be_configured_by_profile_or_environment(monkeypatch, tmp_path):
    write_config(tmp_path, "[work]\nJOBS=6\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Configs(profile_name="work").jobs() == 6

    monkeypatch.setenv("JOBS", "8")
    assert Configs(profile_name="work").jobs() == 8


def test_jobs_must_be_positive(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JOBS", "0")

    with pytest.raises(ValueError, match="JOBS must be at least 1"):
        Configs().jobs()


def test_missing_profile_is_reported(monkeypatch, tmp_path):
    write_config(tmp_path, "[default]\nMSCORE_CMD=mscore\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ValueError, match="'missing' profile not found"):
        Configs(profile_name="missing").mscz_bucket_name()


def test_paths_are_expanded_but_need_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_PNG_DIRECTORY", "~/new-png-directory")

    assert Configs().local_png_directory() == tmp_path / "new-png-directory"


def test_google_settings_are_typed(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_APP_CREDENTIALS_JSON_PATH", "~/credentials.json")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-id")

    assert Configs().google_app_credentials_json_path() == tmp_path / "credentials.json"
    assert Configs().google_drive_folder_id() == "folder-id"


def test_get_configs_path_has_no_filesystem_side_effect(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert get_configs_path() == tmp_path / ".msm" / "configs"
    assert get_google_token_path() == tmp_path / ".msm" / "google-drive-token.json"
    assert not (tmp_path / ".msm").exists()
