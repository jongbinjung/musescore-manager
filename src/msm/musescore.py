"""Musescore models constants (enums) for interacting with mscore CLI"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from msm.metadata import ScoreMetadata
from msm.music import ScoreTransposeConfigs
from msm.utils import normalize_unicode_filename

if TYPE_CHECKING:
    from msm.score import Score


class Musescore:
    """API with Musescore CLI binary (mscore)"""

    def __init__(self, command: str = "mscore", runner: Callable = subprocess.run):
        self._mscore_cmd = command
        self._runner = runner

    def __repr__(self) -> str:
        return self._mscore_cmd

    def conversion_job(self, jobs: list[dict[str, Path]]) -> dict[Path, list[Path]]:
        """Run a conversion batch job

        Args:
            jobs: list of dictionaries with in and out keys; e.g.,
                [
                    {
                        "in": Path("path/to/infile"),
                        "out": Path("path/to/outfile"),
                    },
                    ...
                ]
        Returns:
            Generated output paths keyed by input path.
        """

        # Convert paths to strings and keep a list of expected output paths
        jobs_strs = []
        valid_jobs = []
        previous_results = {}
        for job in jobs:
            try:
                in_path = job["in"]
                out_path = job["out"]
            except KeyError as e:
                raise KeyError(f"Expected 'in' and 'out' keys in job; got {job}") from e
            if not in_path.is_file():
                warnings.warn(f"{in_path} is not a file or does not exist; skipping job {job}")
                continue
            if not out_path.parent.exists():
                logging.info("Creating parent directory for %s", out_path.parent)
                out_path.parent.mkdir(parents=True, exist_ok=True)
            jobs_strs.append({"in": str(in_path.absolute()), "out": str(out_path.absolute())})
            valid_jobs.append(job)
            previous_results[in_path] = self._snapshot_results(out_path)

        if not valid_jobs:
            return {}

        with tempfile.TemporaryDirectory() as temporary_directory:
            job_configs = Path(temporary_directory) / "jobs.json"
            job_configs.write_text(json.dumps(jobs_strs))
            self._run_mscore("-j", str(job_configs))

        results = {}
        for job in valid_jobs:
            in_path = job["in"]
            out_path = job["out"]
            results[in_path] = self._collect_results(out_path, previous=previous_results.get(in_path, {}))

        return results

    def export_to(self, score: Score, path: Path | str) -> list[Path]:
        """Export score to the specified format

        Args:
            score: Score object to export
            path: target path to export to; the suffix determines the format

        Returns:
            list[Path]: List of paths to all exported files

        """
        path = Path(path)

        previous = self._snapshot_results(path)
        self._run_mscore("--export-to", str(path.absolute()), score=score)

        return self._collect_results(path, previous=previous)

    def metadata(self, score: Score) -> ScoreMetadata:
        ret = self._run_mscore("--score-meta", score=score)
        return ScoreMetadata(**json.loads(ret.stdout)["metadata"])

    def transpose(
        self,
        score: Score,
        score_transpose_config: ScoreTransposeConfigs,
        return_type: str,
    ) -> bytes:
        """Transpose mscz file located in Path

        Args:
            score: Score object to transpose
            score_transpose_config: Configuration values
            return_type: type of file to return; pdf and mscz are supported

        Returns:
            bytes: byte-representation of the requested file type

        Raises:
            FileNotFoundError: if score does not exist, or is not a regular file
            KeyError: if the requested return_type doesn't exist
            ValueError: if score is not .mscz

        """
        ret = self._run_mscore(
            "--score-transpose",
            score_transpose_config.model_dump_json(),
            score=score,
        )

        parsed_output = json.loads(ret.stdout)

        try:
            return base64.b64decode(parsed_output[return_type.lower()])
        except KeyError as e:
            raise KeyError(
                f"Requested type {return_type} not generated; supported values are {parsed_output.keys()}"
            ) from e

    def _run_mscore(self, *args, score: Score | None = None) -> subprocess.CompletedProcess:
        cmd = [self._mscore_cmd]
        if score is not None:
            path_str = self._validate_score(score)
            cmd.append(path_str)

        for arg in args:
            cmd.append(arg)
        ret = self._runner(cmd, capture_output=True)
        ret.check_returncode()
        return ret

    @classmethod
    def _snapshot_results(cls, path: Path | str) -> dict[Path, int]:
        return {result: result.stat().st_mtime_ns for result in cls._matching_results(path)}

    @classmethod
    def _collect_results(cls, path: Path | str, previous: dict[Path, int] | None = None) -> list[Path]:
        """Collect results from a Musescore operation

        For certain outputs (e.g., png conversion), mscore will auto-generate filenames based on the specified output
        filename; for example, myscore.png might result in myscore-1.png, myscore-2.png, etc.

        This helper takes the input specification, and searches for any possible output files. A timestamp indicating
        when the mscore job was triggered may be provided to avoid returning files that were not generated by the
        current job but existed previously.

        Args:
            path: output path description
            previous: output modification times captured before running MuseScore

        Returns:
            list[Path]: List of paths to all exported files

        """
        previous = previous or {}
        results = [
            result
            for result in cls._matching_results(path)
            if result not in previous or result.stat().st_mtime_ns != previous[result]
        ]
        results.sort()
        return results

    @staticmethod
    def _matching_results(path: Path | str) -> list[Path]:
        path = Path(path)
        glob = f"{path.stem}*{path.suffix}"
        results = list(path.parent.glob(glob))
        if not results:
            results = list(path.parent.glob(normalize_unicode_filename(glob)))
        return results

    @staticmethod
    def _validate_score(score: Score) -> str:
        path = score.absolute_path
        if not path.is_file():
            raise FileNotFoundError(f"{path.absolute()} is not a file or does not exist")

        return str(path)
