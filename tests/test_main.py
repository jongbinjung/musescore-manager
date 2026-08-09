import threading
from unittest.mock import patch

from test_score import write_score
from typer.testing import CliRunner

from msm.main import _target_display_settings, _target_values_prompt, app


def test_cli_exposes_only_new_command_hierarchy():
    runner = CliRunner()

    root = runner.invoke(app, ["--help"])
    sync = runner.invoke(app, ["sync", "--help"])
    targets = runner.invoke(app, ["targets", "--help"])
    removed = runner.invoke(app, ["sync-pngs"])

    assert root.exit_code == 0
    assert all(command in root.output for command in ("normalize", "export", "sync", "targets", "profiles"))
    assert all(kind in sync.output for kind in ("scores", "pngs"))
    assert all(command in targets.output for command in ("list", "add", "clear"))
    assert removed.exit_code == 2


def test_profiles_without_command_shows_help_then_lists_profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".msm" / "configs"
    config_path.parent.mkdir()
    config_path.write_text("[default]\nLOCAL_MSCZ_DIRECTORY=/scores\n")

    result = CliRunner().invoke(app, ["profiles"])

    assert result.exit_code == 0
    assert result.output.index("Usage:") < result.output.index("PROFILE")


def test_profile_add_confirms_before_writing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    values = {"LOCAL_MSCZ_DIRECTORY": "/scores", "LOCAL_PNG_DIRECTORY": "/pngs"}

    with (
        patch("msm.main._profile_values_prompt", return_value=values),
        patch("msm.main.typer.confirm", return_value=False),
    ):
        result = CliRunner().invoke(app, ["profiles", "add", "work"])

    assert result.exit_code == 0, result.output
    assert "LOCAL_MSCZ_DIRECTORY=/scores" in result.output
    assert "No changes made." in result.output
    assert not (tmp_path / ".msm" / "configs").exists()


def test_targets_list_does_not_expose_configuration_values(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".msm" / "configs"
    config_path.parent.mkdir()
    config_path.write_text(
        "[default]\n\n[target.archive]\nTYPE=s3\nBUCKET=private-bucket\nSECRET_ACCESS_KEY=private-secret\n"
    )

    result = CliRunner().invoke(app, ["targets", "list"])

    assert result.exit_code == 0
    assert "archive" in result.output
    assert "s3" in result.output
    assert "private-bucket" in result.output
    assert "private-secret" not in result.output


def test_target_display_settings_can_be_compact_for_selectors():
    class Configs:
        def target_display_values(self, name):
            return ("private-bucket", "-")

    context = type("Context", (), {"configs": Configs()})()

    assert _target_display_settings(context, "archive", "s3") == "bucket=private-bucket\nendpoint=-"
    assert _target_display_settings(context, "archive", "s3", separator=", ") == "bucket=private-bucket, endpoint=-"


def test_target_add_confirms_before_writing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    values = {"TYPE": "s3", "BUCKET": "private-bucket"}

    with (
        patch("msm.main.questionary.select") as select,
        patch("msm.main._target_values_prompt", return_value=values),
        patch("msm.main.typer.confirm", return_value=False),
    ):
        select.return_value.ask.return_value = "s3"
        result = CliRunner().invoke(app, ["targets", "add", "archive"])

    assert result.exit_code == 0, result.output
    assert "BUCKET=private-bucket" in result.output
    assert "No changes made." in result.output
    assert not (tmp_path / ".msm" / "configs").exists()


def test_s3_target_prompt_keeps_entered_optional_values():
    with patch(
        "msm.main._ask_target_text",
        side_effect=["private-bucket", "scores", "https://s3.example", "access", "secret"],
    ):
        values = _target_values_prompt("s3")

    assert values == {
        "TYPE": "s3",
        "BUCKET": "private-bucket",
        "PREFIX": "scores",
        "ENDPOINT_URL": "https://s3.example",
        "ACCESS_KEY_ID": "access",
        "SECRET_ACCESS_KEY": "secret",
    }


def test_targets_without_command_shows_help_then_lists_targets(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".msm" / "configs"
    config_path.parent.mkdir()
    config_path.write_text("[default]\n\n[target.archive]\nTYPE=s3\nBUCKET=private-bucket\n")

    result = CliRunner().invoke(app, ["targets"])

    assert result.exit_code == 0
    assert result.output.index("Usage:") < result.output.index("TARGET")


def test_targets_clear_uses_named_target(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".msm" / "configs"
    config_path.parent.mkdir()
    config_path.write_text("[default]\n\n[target.archive]\nTYPE=s3\nBUCKET=private-bucket\n")

    class FakeTarget:
        dryrun = None

        def clear(self, dryrun=False, progress=None):
            self.dryrun = dryrun
            if progress is not None:
                progress(0, 150)
                progress(100, 150)
                progress(50, 150)

    target = FakeTarget()

    with patch("msm.main._create_target", return_value=target) as create:
        result = CliRunner().invoke(app, ["targets", "clear", "archive"])

    assert result.exit_code == 0, result.output
    assert target.dryrun is False
    assert create.call_args.args[0].name == "archive"
    assert "Found 150 items in archive." in result.output
    assert "Deleted 100/150 items from archive." in result.output
    assert "Deleted 150/150 items from archive." in result.output


def test_targets_clear_selects_target_when_name_is_omitted(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".msm" / "configs"
    config_path.parent.mkdir()
    config_path.write_text("[default]\n\n[target.archive]\nTYPE=s3\nBUCKET=private-bucket\n")
    target = type("Target", (), {"clear": lambda self, dryrun=False, progress=None: None})()

    with patch("msm.main.questionary.select") as select, patch("msm.main._create_target", return_value=target):
        select.return_value.ask.return_value = "archive (bucket=private-bucket, endpoint=-)"
        result = CliRunner().invoke(app, ["targets", "clear"])

    assert result.exit_code == 0, result.output
    select.assert_called_once_with(
        "Which target should be cleared?", choices=["archive (bucket=private-bucket, endpoint=-)"]
    )


def test_export_dryrun_does_not_require_output_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    score_path = tmp_path / "score.mscz"
    write_score(score_path)

    class FakeMusescore:
        def metadata(self, score):
            return type("Metadata", (), {"pages": 2})()

    monkeypatch.setattr("msm.main.Musescore", lambda command: FakeMusescore())

    result = CliRunner().invoke(app, ["--dryrun", "export", "scores", "--path", str(score_path)])

    assert result.exit_code == 0
    assert str(score_path) not in result.output
    assert "Would export 1 scores and 2 PNGs." in result.output


def test_normalize_dryrun_accepts_uppercase_extension(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    score_path = tmp_path / "score.MSCZ"
    write_score(score_path)

    result = CliRunner().invoke(app, ["--dryrun", "normalize", "scores", "--path", str(score_path)])

    assert result.exit_code == 0
    assert str(score_path) not in result.output
    assert "Would normalize 1 scores." in result.output


def test_export_progress_advances_as_workers_finish(monkeypatch, tmp_path):
    first_score = tmp_path / "a-slow.mscz"
    second_score = tmp_path / "b-fast.mscz"
    write_score(first_score, "Slow")
    write_score(second_score, "Fast")
    progress_advanced = threading.Event()
    slow_worker_saw_progress = False

    class FakeProgress:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def add_task(self, *args, **kwargs):
            return 0

        def advance(self, task):
            progress_advanced.set()

        class Console:
            def print(self, *args, **kwargs):
                pass

        console = Console()

    def fake_export(score, musescore, key, base_dir):
        nonlocal slow_worker_saw_progress
        if score.absolute_path == first_score.absolute():
            progress_advanced.wait(timeout=1)
            slow_worker_saw_progress = progress_advanced.is_set()
        return []

    monkeypatch.setattr("msm.main.Progress", FakeProgress)
    monkeypatch.setattr("msm.main.Musescore", lambda command: object())
    monkeypatch.setattr("msm.main.to_pngs", fake_export)

    result = CliRunner().invoke(
        app, ["export", "scores", "--path", str(tmp_path), "--output", str(tmp_path / "pngs"), "--jobs", "2"]
    )

    assert result.exit_code == 0, result.output
    assert slow_worker_saw_progress
