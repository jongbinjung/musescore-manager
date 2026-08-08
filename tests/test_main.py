from test_score import write_score
from typer.testing import CliRunner

from msm.main import app


def test_export_dryrun_does_not_require_output_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    score_path = tmp_path / "score.mscz"
    write_score(score_path)

    result = CliRunner().invoke(app, ["--path", str(score_path), "--dryrun", "export-pngs"])

    assert result.exit_code == 0
    assert "Exporting" in result.output
    assert str(score_path) in result.output


def test_upload_requires_bucket_before_connecting(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    score_path = tmp_path / "score.mscz"
    write_score(score_path)

    result = CliRunner().invoke(app, ["--path", str(score_path), "upload"])

    assert result.exit_code == 1
    assert "S3 bucket not set" in result.output
