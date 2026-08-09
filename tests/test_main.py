import threading

from test_score import write_score
from typer.testing import CliRunner

from msm.main import app


def test_export_dryrun_does_not_require_output_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    score_path = tmp_path / "score.mscz"
    write_score(score_path)

    class FakeMusescore:
        def metadata(self, score):
            return type("Metadata", (), {"pages": 2})()

    monkeypatch.setattr("msm.main.Musescore", lambda command: FakeMusescore())

    result = CliRunner().invoke(app, ["--path", str(score_path), "--dryrun", "export-pngs"])

    assert result.exit_code == 0
    assert f"Exporting {score_path}" not in result.output
    assert str(score_path) not in result.output
    assert "Would export 1 scores and 2 PNGs." in result.output


def test_upload_requires_bucket_before_connecting(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    score_path = tmp_path / "score.mscz"
    write_score(score_path)

    result = CliRunner().invoke(app, ["--path", str(score_path), "upload"])

    assert result.exit_code == 1
    assert "S3 bucket not set" in result.output


def test_upload_does_not_connect_for_empty_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    scores = tmp_path / "scores"
    scores.mkdir()

    def fail_connect(*args, **kwargs):
        raise AssertionError("S3 should not connect when there are no scores")

    monkeypatch.setattr("msm.main.S3Store.connect", fail_connect)

    result = CliRunner().invoke(app, ["--path", str(scores), "upload"])

    assert result.exit_code == 0
    assert "No MSCZ files found" in result.output


def test_normalize_dryrun_accepts_uppercase_extension(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    score_path = tmp_path / "score.MSCZ"
    write_score(score_path)

    result = CliRunner().invoke(app, ["--path", str(score_path), "--dryrun", "normalize"])

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

    result = CliRunner().invoke(app, ["--path", str(tmp_path), "export-pngs", "--jobs", "2"])

    assert result.exit_code == 0, result.output
    assert slow_worker_saw_progress
