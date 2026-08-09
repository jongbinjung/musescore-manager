from pathlib import Path

import pytest

from msm.config import Configs, DriveTargetConfig, S3TargetConfig, get_configs_path, get_google_token_path


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ENDPOINT_URL_S3",
        "DEFAULT_PNGS_TARGET",
        "DEFAULT_SCORES_TARGET",
        "LOCAL_MSCZ_DIRECTORY",
        "LOCAL_PNG_DIRECTORY",
        "MSCORE_CMD",
        "JOBS",
        "MSM_TARGET_ARCHIVE_BUCKET",
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


def test_profile_values_and_defaults(monkeypatch, tmp_path):
    write_config(tmp_path, "[work]\nMSCORE_CMD=from-file\nJOBS=6\nDEFAULT_SCORES_TARGET=archive\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    configs = Configs(profile_name="work")

    assert configs.mscore_cmd() == "from-file"
    assert configs.jobs() == 6
    assert configs.default_target("scores") == "archive"
    assert configs.default_target("pngs") is None


def test_missing_values_use_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Configs().mscore_cmd() == "mscore"
    assert Configs().jobs() == 4


def test_jobs_must_be_positive(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JOBS", "0")

    with pytest.raises(ValueError, match="JOBS must be at least 1"):
        Configs().jobs()


def test_missing_profile_is_reported(monkeypatch, tmp_path):
    write_config(tmp_path, "[default]\nMSCORE_CMD=mscore\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ValueError, match="'missing' profile not found"):
        Configs(profile_name="missing").local_mscz_directory()


def test_paths_are_expanded_but_need_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_PNG_DIRECTORY", "~/new-png-directory")

    assert Configs().local_png_directory() == tmp_path / "new-png-directory"


def test_reads_s3_named_target_and_target_environment_override(monkeypatch, tmp_path):
    write_config(
        tmp_path,
        "[default]\n\n[target.archive]\nTYPE=s3\nBUCKET=file-bucket\nPREFIX=scores/current\nENDPOINT_URL=https://s3.example\n",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MSM_TARGET_ARCHIVE_BUCKET", "env-bucket")

    assert Configs().target("archive") == S3TargetConfig(
        name="archive", bucket="env-bucket", prefix="scores/current", endpoint_url="https://s3.example"
    )


def test_reads_drive_named_target_with_scoped_default_token(monkeypatch, tmp_path):
    write_config(
        tmp_path,
        "[default]\n\n[target.gallery]\nTYPE=google-drive\nFOLDER_ID=folder-id\nCREDENTIALS_PATH=~/credentials.json\n",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Configs().target("gallery") == DriveTargetConfig(
        name="gallery",
        folder_id="folder-id",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / ".msm" / "tokens" / "google-drive-gallery.json",
    )


def test_lists_target_names_and_types_without_target_values(monkeypatch, tmp_path):
    write_config(
        tmp_path,
        "[default]\n\n[target.archive]\nTYPE=s3\nBUCKET=secret-bucket\nSECRET_ACCESS_KEY=secret\n"
        "\n[target.gallery]\nTYPE=google-drive\nFOLDER_ID=secret-folder\n",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Configs().targets() == {"archive": "s3", "gallery": "google-drive"}


def test_target_display_values_include_only_safe_destination_details(monkeypatch, tmp_path):
    write_config(
        tmp_path,
        "[default]\n\n[target.archive]\nTYPE=s3\nBUCKET=private-bucket\nENDPOINT_URL=https://s3.example\n"
        "\n[target.gallery]\nTYPE=google-drive\nFOLDER_ID=folder-id\nCREDENTIALS_PATH=secret.json\n",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Configs().target_display_values("archive") == ("private-bucket", "https://s3.example")
    assert Configs().target_display_values("gallery") == ("https://drive.google.com/drive/folders/folder-id",)


def test_target_validation(monkeypatch, tmp_path):
    write_config(tmp_path, "[default]\n\n[target.bad]\nTYPE=ftp\n")
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ValueError, match="unsupported TYPE"):
        Configs().target("bad")
    with pytest.raises(ValueError, match="Invalid target name"):
        Configs().target("../bad")
    with pytest.raises(ValueError, match="not found"):
        Configs().target("missing")


def test_config_paths_have_no_filesystem_side_effect(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert get_configs_path() == tmp_path / ".msm" / "configs"
    assert get_google_token_path("gallery") == tmp_path / ".msm" / "tokens" / "google-drive-gallery.json"
    assert not (tmp_path / ".msm").exists()
