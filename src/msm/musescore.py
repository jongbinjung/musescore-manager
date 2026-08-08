"""Musescore models constants (enums) for interacting with mscore CLI"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import subprocess
import warnings
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, model_validator

from msm.config import Configs
from msm.utils import normalize_unicode_filename

if TYPE_CHECKING:
    from msm.score import Score


class Key(IntEnum):
    C_FLAT_MAJOR = -7
    G_FLAT_MAJOR = -6
    D_FLAT_MAJOR = -5
    A_FLAT_MAJOR = -4
    E_FLAT_MAJOR = -3
    B_FLAT_MAJOR = -2
    F_MAJOR = -1
    C_MAJOR = 0
    G_MAJOR = 1
    D_MAJOR = 2
    A_MAJOR = 3
    E_MAJOR = 4
    B_MAJOR = 5
    F_SHARP_MAJOR = 6
    C_SHARP_MAJOR = 7

    def __str__(self) -> str:
        """Translate Key enum to human-readable format"""
        match self.name.split("_"):
            case k, "FLAT", _:
                return f"{k}b"
            case k, "SHARP", _:
                return f"{k}#"
            case k, _:
                return f"{k}"


def str2key(key_str: str) -> Key:
    tonic = None
    mod = None

    if len(key_str) == 1:
        tonic = key_str.upper()

    if len(key_str) > 1 and len(key_str) < 3:
        tonic = key_str[0].upper()
        match key_str[1]:
            case "b":
                mod = "FLAT"
            case "#":
                mod = "SHARP"

    if "_" in key_str:
        match key_str.upper().split("_"):
            case tonic, "FLAT", *_:
                mod = "FLAT"
            case tonic, "SHARP", *_:
                mod = "SHARP"
            case tonic, *_:
                mod = None

    if tonic is None:
        raise ValueError(f"Invalid key string: {key_str}")

    if mod is None:
        return Key[f"{tonic}_MAJOR"]

    return Key[f"{tonic}_{mod}_MAJOR"]


class Interval(IntEnum):
    Augmented_Fifth = 15
    Augmented_Fourth = 12
    Augmented_Second = 5
    Augmented_Seventh = 23
    Augmented_Sixth = 19
    Augmented_Third = 9
    Augmented_Unison = 1
    Diminished_Fifth = 13
    Diminished_Fourth = 10
    Diminished_Octave = 24
    Diminished_Seventh = 20
    Diminished_Sixth = 16
    Diminished_Third = 6
    Diminished_Second = 2
    Major_Seventh = 22
    Major_Sixth = 18
    Major_Third = 8
    Major_Second = 4
    Minor_Seventh = 21
    Minor_Sixth = 17
    Minor_Third = 7
    Minor_Second = 3
    Perfect_Fifth = 14
    Perfect_Fourth = 11
    Perfect_Octave = 25
    Perfect_Unison = 0


class ScoreTransposeConfigs(BaseModel):
    # https://musescore.org/en/node/287888
    mode: Literal["by_key", "by_interval", "diatonically"]
    direction: Literal["up", "down", "closest"]
    targetKey: Key | None = None
    transposeInterval: Interval | None = None
    transposeKeySignatures: bool = True
    transposeChordNames: bool = True
    useDoubleSharpsFlats: bool = False

    @model_validator(mode="after")
    def check_mode_and_options(self) -> Self:
        mode = self.mode
        if mode == "by_key" and self.targetKey is None:
            raise ValueError(f"targetKey must be set if {mode=}")
        if mode in ("by_interval", "diatonically") and self.transposeInterval is None:
            raise ValueError(f"transposeInterval must be set if {mode=}")
        return self


class Musescore:
    """API with Musescore CLI binary (mscore)"""

    def __init__(self, configs: Configs = Configs()):
        """Create a Musescore object

        Args:
            configs: Configs object; used to locate mscore binary (mscore_cmd)

        """
        self._mscore_cmd = configs.mscore_cmd()

    def __repr__(self) -> str:
        return self._mscore_cmd

    def conversion_job(self, jobs: list[dict[str, Path]], cleanup: bool = True) -> subprocess.CompletedProcess:
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
            cleanup: remove job config file after running

        Returns:
            subprocess.CompletedProcess: object retrieved from the subprocess the executed the batch job; e.g.,

            ```
            CompletedProcess(
                args=['mscore', '-F', '-j', 'path/to/config.json'],
                returncode=0,
                stdout=b'',
                stderr=b'',
            )
            ```
        """

        # Convert paths to strings and keep a list of expected output paths
        jobs_strs = []
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

        job_configs = Path("_job_configs.json")
        job_configs.write_text(json.dumps(jobs_strs))

        now = datetime.datetime.now()

        try:
            _ = self._run_mscore("-j", str(job_configs.absolute()))
        finally:
            if cleanup:
                job_configs.unlink()

        results = {}
        for job in jobs:
            in_path = job["in"]
            out_path = job["out"]
            results[in_path] = self._collect_results(out_path, since=now)

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

        now = datetime.datetime.now()

        self._run_mscore("--export-to", str(path.absolute()), score=score)

        return self._collect_results(path, since=now)

    def metadata(self, score: Score) -> dict:
        ret = self._run_mscore("--score-meta", score=score)
        return json.loads(ret.stdout)["metadata"]

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
        ret = subprocess.run(cmd, capture_output=True)
        ret.check_returncode()
        return ret

    @staticmethod
    def _collect_results(path: Path | str, since: datetime.datetime | None = None) -> list[Path]:
        """Collect results from a Musescore operation

        For certain outputs (e.g., png conversion), mscore will auto-generate filenames based on the specified output
        filename; for example, myscore.png might result in myscore-1.png, myscore-2.png, etc.

        This helper takes the input specification, and searches for any possible output files. A timestamp indicating
        when the mscore job was triggered may be provided to avoid returning files that were not generated by the
        current job but existed previously.

        Args:
            path: output path description
            since: a timestamp; if specified, only files that have been modified after this time will be returned

        Returns:
            list[Path]: List of paths to all exported files

        """
        path = Path(path)

        glob = f"{path.stem}*{path.suffix}"
        results = [result for result in path.parent.glob(glob)]

        if len(results) == 0:
            # If no results are found, try to normalize the glob to account for Unicode normalization (e.g., MacOS)
            glob = normalize_unicode_filename(glob)
            results = [result for result in path.parent.glob(glob)]

        if since is not None:
            results = [r for r in results if since < datetime.datetime.fromtimestamp(r.stat().st_mtime)]

        results.sort()

        return results

    @staticmethod
    def _validate_score(score: Score) -> str:
        path = score.absolute_path
        if not path.is_file():
            raise FileNotFoundError(f"{path.absolute()} is not a file or does not exist")

        if path.suffix.lower() != ".mscz":
            raise ValueError(f"Only supports '.mscz'; got {path.name}")

        return str(path)
