from unittest.mock import MagicMock, patch

import pytest
from test_score import write_score
from typer.testing import CliRunner

from msm.config import S3TargetConfig
from msm.main import PNG_MEDIA_TYPE, SCORE_MEDIA_TYPE, app
from msm.remote import SyncResult, SyncStatus


@pytest.fixture
def configs():
    config = MagicMock()
    config.jobs.return_value = 4
    config.default_target.side_effect = lambda kind: f"default-{kind}"
    config.target.side_effect = lambda name: S3TargetConfig(name=name, bucket="bucket")
    return config


class FakeTarget:
    concurrent = False

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []
        self.force_calls = []

    def sync(self, artifact, dryrun=False, force=False):
        self.calls.append((artifact, dryrun))
        self.force_calls.append(force)
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return SyncResult(artifact.name, SyncStatus.CREATED)


def invoke(configs, args, target=None, input_text=None, conflict_action="Ignore"):
    target = target or FakeTarget()
    with (
        patch("msm.main.Configs", return_value=configs),
        patch("msm.main._create_target", return_value=target),
        patch("msm.main.questionary.select") as select,
    ):
        select.return_value.ask.return_value = conflict_action
        result = CliRunner().invoke(app, args, input=input_text)
    return result, target


def test_sync_pngs_uses_default_named_target_and_all_png_files(configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    files = [tmp_path / name for name in ("AboveAll-G.png", "AboveAll-D.png", "notes.txt")]
    for path in files:
        path.write_bytes(b"png")

    result, target = invoke(configs, ["sync", "pngs"])

    assert result.exit_code == 0
    assert configs.target.call_args.args == ("default-pngs",)
    assert [call[0].path for call in target.calls] == sorted(files[:2])
    assert all(call[0].media_type == PNG_MEDIA_TYPE for call in target.calls)
    assert "Synced: 2 created" in result.output


def test_sync_scores_uses_normalized_names_and_explicit_target(configs, tmp_path):
    score = tmp_path / "source.mscz"
    write_score(score, "Title")

    result, target = invoke(configs, ["sync", "scores", "--path", str(score), "--target", "drive"])

    assert result.exit_code == 0
    artifact = target.calls[0][0]
    assert artifact.path == score
    assert artifact.name == "Title.mscz"
    assert artifact.media_type == SCORE_MEDIA_TYPE
    assert configs.target.call_args.args == ("drive",)


def test_sync_empty_directory_does_not_connect(configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path

    with patch("msm.main.Configs", return_value=configs), patch("msm.main._create_target") as create:
        result = CliRunner().invoke(app, ["sync", "pngs"])

    assert result.exit_code == 0
    assert "No PNG files found" in result.output
    create.assert_not_called()


def test_sync_requires_a_target(configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    configs.default_target.side_effect = None
    configs.default_target.return_value = None
    (tmp_path / "score.png").write_bytes(b"png")

    result, _ = invoke(configs, ["sync", "pngs"])

    assert result.exit_code == 1
    assert "use --target" in result.output


def test_sync_dryrun_uses_normal_statuses_and_does_not_hide_preview(configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    path = tmp_path / "score.png"
    path.write_bytes(b"png")
    target = FakeTarget([SyncResult("score.png", SyncStatus.CREATED)])

    result, target = invoke(configs, ["--dryrun", "-v", "sync", "pngs"], target)

    assert result.exit_code == 0
    assert target.calls[0][1] is True
    assert "Would be Created score.png" in result.output
    assert "Would sync: 1 created" in result.output


def test_sync_continues_after_failure_and_conflicts_exit_nonzero(configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(b"png")
    target = FakeTarget(
        [
            RuntimeError("temporary failure"),
            SyncResult("b.png", SyncStatus.UNCHANGED),
            SyncResult("c.png", SyncStatus.CONFLICT, error="same-name file is unmanaged"),
        ]
    )

    result, target = invoke(configs, ["sync", "pngs"], target)

    assert result.exit_code == 1
    assert len(target.calls) == 3
    assert "Failed to sync a.png: temporary failure" in result.output
    assert "Conflict c.png: same-name file is unmanaged" in result.output


def test_sync_force_pushes_all_conflicts(configs, tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"png")
    configs.local_png_directory.return_value = tmp_path
    target = FakeTarget([SyncResult("score.png", SyncStatus.CONFLICT)])

    result, target = invoke(configs, ["sync", "pngs"], target, conflict_action="Force-push all")

    assert result.exit_code == 0
    assert target.force_calls == [False, True]
    assert "Synced: 1 created" in result.output


def test_sync_can_ignore_conflicts(configs, tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"png")
    configs.local_png_directory.return_value = tmp_path
    target = FakeTarget([SyncResult("score.png", SyncStatus.CONFLICT)])

    result, target = invoke(configs, ["sync", "pngs"], target, conflict_action="Ignore")

    assert result.exit_code == 0
    assert target.force_calls == [False]
    assert "1 conflicts" in result.output


def test_sync_reviews_conflicts_one_by_one(configs, tmp_path):
    path = tmp_path / "score.png"
    path.write_bytes(b"png")
    configs.local_png_directory.return_value = tmp_path
    target = FakeTarget([SyncResult("score.png", SyncStatus.CONFLICT)])

    result, target = invoke(
        configs,
        ["sync", "pngs"],
        target,
        input_text="y\n",
        conflict_action="Review one by one",
    )

    assert result.exit_code == 0
    assert target.force_calls == [False, True]


def test_remote_newer_is_an_unsuccessful_sync(configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    (tmp_path / "score.png").write_bytes(b"png")
    target = FakeTarget([SyncResult("score.png", SyncStatus.REMOTE_NEWER)])

    result, _ = invoke(configs, ["sync", "pngs"], target)

    assert result.exit_code == 1
    assert "Remote newer score.png" in result.output


def test_target_connection_errors_are_reported(configs, tmp_path):
    configs.local_png_directory.return_value = tmp_path
    (tmp_path / "score.png").write_bytes(b"png")

    with (
        patch("msm.main.Configs", return_value=configs),
        patch("msm.main._create_target", side_effect=RuntimeError("not authorized")),
    ):
        result = CliRunner().invoke(app, ["sync", "pngs", "--target", "gallery"])

    assert result.exit_code == 1
    assert "Failed to connect to target 'gallery': not authorized" in result.output


def test_sync_rejects_duplicate_normalized_score_names_before_connecting(configs, tmp_path):
    write_score(tmp_path / "one.mscz", "Duplicate")
    write_score(tmp_path / "two.mscz", "Duplicate")
    configs.local_mscz_directory.return_value = tmp_path

    with patch("msm.main.Configs", return_value=configs), patch("msm.main._create_target") as create:
        result = CliRunner().invoke(app, ["sync", "scores"])

    assert result.exit_code == 1
    assert "Duplicate remote name" in result.output
    create.assert_not_called()
