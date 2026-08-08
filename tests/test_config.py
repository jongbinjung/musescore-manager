from pathlib import Path

import pytest

from msm.config import Configs, get_configs_path


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ENDPOINT_URL_S3",
        "LOCAL_MSCZ_DIRECTORY",
        "LOCAL_PNG_DIRECTORY",
        "MSCORE_CMD",
        "MSCZ_BUCKET_NAME",
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


def test_missing_profile_is_reported(monkeypatch, tmp_path):
    write_config(tmp_path, "[default]\nMSCORE_CMD=mscore\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ValueError, match="'missing' profile not found"):
        Configs(profile_name="missing").mscz_bucket_name()


def test_paths_are_expanded_but_need_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_PNG_DIRECTORY", "~/new-png-directory")

    assert Configs().local_png_directory() == tmp_path / "new-png-directory"


def test_get_configs_path_has_no_filesystem_side_effect(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert get_configs_path() == tmp_path / ".msm" / "configs"
    assert not (tmp_path / ".msm").exists()
