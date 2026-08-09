import threading

from test_score import write_score
from typer.testing import CliRunner

from msm.main import app


def test_cli_exposes_only_new_command_hierarchy():
    runner = CliRunner()

    root = runner.invoke(app, ["--help"])
    sync = runner.invoke(app, ["sync", "--help"])
    removed = runner.invoke(app, ["sync-pngs"])

    assert root.exit_code == 0
    assert all(command in root.output for command in ("normalize", "export", "sync"))
    assert all(kind in sync.output for kind in ("scores", "pngs"))
    assert removed.exit_code == 2


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
