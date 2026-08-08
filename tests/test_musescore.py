import json
import subprocess
from pathlib import Path

import pytest
from test_score import write_score

from msm.musescore import Musescore
from msm.score import Score


def test_metadata_runs_expected_command(tmp_path):
    path = tmp_path / "score.mscz"
    write_score(path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        metadata = {
            "title": "Test Score",
            "subtitle": "",
            "composer": "",
            "keysig": 0,
            "timesig": "4/4",
            "measures": 1,
            "lyrics": "",
            "fileVersion": 40,
            "mscoreVersion": "4.4",
            "pages": 2,
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"metadata": metadata}).encode(), stderr=b"")

    metadata = Musescore("custom-mscore", runner=runner).metadata(Score(path))

    assert metadata.pages == 2
    assert calls == [(["custom-mscore", str(path.absolute()), "--score-meta"], {"capture_output": True})]


def test_export_returns_only_new_or_changed_outputs(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)
    output = tmp_path / "score.png"
    unchanged = tmp_path / "score-old.png"
    unchanged.write_bytes(b"old")

    def runner(command, **kwargs):
        output.write_bytes(b"new")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    results = Musescore(runner=runner).export_to(Score(score_path), output)

    assert results == [output]


def test_conversion_job_uses_temporary_config(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)
    output = tmp_path / "out" / "score.pdf"
    job_path: Path | None = None

    def runner(command, **kwargs):
        nonlocal job_path
        job_path = Path(command[-1])
        assert job_path.is_file()
        output.write_bytes(b"pdf")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    results = Musescore(runner=runner).conversion_job([{"in": score_path, "out": output}])

    assert results == {score_path: [output]}
    assert job_path is not None and not job_path.exists()


def test_conversion_job_does_not_report_outputs_for_skipped_inputs(tmp_path):
    missing = tmp_path / "missing.mscz"
    stale = tmp_path / "stale.pdf"
    stale.write_bytes(b"stale")

    def runner(command, **kwargs):
        raise AssertionError("MuseScore should not run when every job is skipped")

    with pytest.warns(UserWarning, match="skipping job"):
        results = Musescore(runner=runner).conversion_job([{"in": missing, "out": stale}])

    assert results == {}
