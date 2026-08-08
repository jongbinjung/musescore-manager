from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from msm.gdrive import SyncResult, SyncStatus
from msm.main import app


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def configs():
    config = MagicMock()
    config.google_drive_folder_id.return_value = "folder-id"
    config.google_app_credentials_json_path.return_value = Path("/tmp/credentials.json")
    return config


def invoke_with_config(cli_runner, configs, args):
    with patch("msm.main.Configs", return_value=configs):
        return cli_runner.invoke(app, args)


def test_sync_pngs_missing_folder_id(cli_runner, configs):
    configs.google_drive_folder_id.return_value = None

    result = invoke_with_config(cli_runner, configs, ["sync-pngs"])

    assert result.exit_code == 1
    assert "GOOGLE_DRIVE_FOLDER_ID is not configured" in result.stdout


def test_sync_pngs_missing_credentials(cli_runner, configs):
    configs.google_app_credentials_json_path.return_value = None

    result = invoke_with_config(cli_runner, configs, ["sync-pngs"])

    assert result.exit_code == 1
    assert "GOOGLE_APP_CREDENTIALS_JSON_PATH is not configured" in result.stdout


def test_sync_pngs_missing_directory(cli_runner, configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path / "missing"

    result = invoke_with_config(cli_runner, configs, ["sync-pngs"])

    assert result.exit_code == 1
    assert "does not exist" in result.stdout


def test_sync_pngs_empty_directory(cli_runner, configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path

    result = invoke_with_config(cli_runner, configs, ["sync-pngs"])

    assert result.exit_code == 0
    assert "No PNG files found" in result.stdout


def test_sync_pngs_syncs_all_pngs_without_hyphen_deduplication(cli_runner, configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    files = [tmp_path / name for name in ["AboveAll-G.png", "AboveAll-D.png", "Title-1-of-2.png", "notes.txt"]]
    for path in files:
        path.write_bytes(b"png")

    session = MagicMock()
    session.sync_file.side_effect = lambda path, dryrun: SyncResult(path, SyncStatus.CREATED)
    with patch("msm.main.Configs", return_value=configs), patch("msm.main._create_drive_session", return_value=session):
        result = cli_runner.invoke(app, ["sync-pngs"])

    assert result.exit_code == 0
    assert [call.args[0] for call in session.sync_file.call_args_list] == sorted(files[:3])
    assert "Created AboveAll-G.png" in result.stdout


def test_sync_pngs_passes_dryrun_and_reports_status(cli_runner, configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    path = tmp_path / "score.png"
    path.write_bytes(b"png")
    session = MagicMock()
    session.sync_file.return_value = SyncResult(path, SyncStatus.WOULD_CREATE)

    with patch("msm.main.Configs", return_value=configs), patch("msm.main._create_drive_session", return_value=session):
        result = cli_runner.invoke(app, ["--dryrun", "sync-pngs"])

    assert result.exit_code == 0
    session.sync_file.assert_called_once_with(path, dryrun=True)
    assert "Would create score.png" in result.stdout


def test_sync_pngs_continues_after_failure_and_exits_nonzero(cli_runner, configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    session = MagicMock()
    session.sync_file.side_effect = [RuntimeError("temporary failure"), SyncResult(second, SyncStatus.SKIPPED)]

    with patch("msm.main.Configs", return_value=configs), patch("msm.main._create_drive_session", return_value=session):
        result = cli_runner.invoke(app, ["sync-pngs"])

    assert result.exit_code == 1
    assert session.sync_file.call_count == 2
    assert "Failed to upload a.png" in result.stdout
    assert "Up to date; skipped b.png" in result.stdout


def test_sync_pngs_reports_drive_connection_failure(cli_runner, configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    (tmp_path / "score.png").write_bytes(b"png")

    with (
        patch("msm.main.Configs", return_value=configs),
        patch("msm.main._create_drive_session", side_effect=RuntimeError("not authorized")),
    ):
        result = cli_runner.invoke(app, ["sync-pngs"])

    assert result.exit_code == 1
    assert "Failed to connect to Google Drive: not authorized" in result.stdout


def test_sync_pngs_reports_conflict_details(cli_runner, configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    path = tmp_path / "score.png"
    path.write_bytes(b"png")
    session = MagicMock()
    session.sync_file.return_value = SyncResult(path, SyncStatus.CONFLICT, error="same-name file is unmanaged")

    with patch("msm.main.Configs", return_value=configs), patch("msm.main._create_drive_session", return_value=session):
        result = cli_runner.invoke(app, ["sync-pngs"])

    assert result.exit_code == 1
    assert "Conflict score.png: same-name file is unmanaged" in result.stdout
