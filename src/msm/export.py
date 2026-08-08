"""Functions to export scores"""

import logging
import re
import unicodedata
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from msm.music import Key, ScoreTransposeConfigs
from msm.score import Score


class MetadataWithPages(Protocol):
    pages: int | None


class ScoreRenderer(Protocol):
    def metadata(self, score: Score) -> MetadataWithPages: ...

    def transpose(
        self,
        score: Score,
        score_transpose_config: ScoreTransposeConfigs,
        return_type: str,
    ) -> bytes: ...

    def export_to(self, score: Score, path: Path | str) -> list[Path]: ...


def to_pngs(score: Score, musescore: ScoreRenderer, key: Key | None = None, base_dir: Path = Path()) -> list[Path]:
    """Export a score to PNG files, optionally transposing to a specified key before export

    If target PNGs already exist with a more recent timestamp than the score, this function will skip export and return
    an empty list.

    Args:
        score: The Score object to export

    Returns:
        list of exported files paths

    """
    base_dir.mkdir(parents=True, exist_ok=True)
    source_modified_time_ns = score.source_modified_time_ns

    with TemporaryDirectory(dir=base_dir) as temporary_directory:
        workspace = Path(temporary_directory)
        if key is not None and score.metadata.keysig != key:
            transpose_configs = ScoreTransposeConfigs(mode="by_key", direction="closest", targetKey=key)
            transposed_bytes = musescore.transpose(
                score=score,
                score_transpose_config=transpose_configs,
                return_type="mscz",
            )
            score = Score.from_bytes(transposed_bytes, path=workspace / "transposed.mscz")

        target_path = base_dir / score.normalized_name(with_key=True, suffix="png")
        pages = musescore.metadata(score).pages
        if pages is None or pages < 1:
            raise RuntimeError("MuseScore metadata did not include a positive page count")
        expected_outputs = _expected_outputs(target_path, pages)
        managed_outputs = _managed_outputs(target_path)
        expected_names = {_canonical_name(path) for path in expected_outputs}
        managed_names = {_canonical_name(path) for path in managed_outputs}
        managed_by_name = {_canonical_name(path): path for path in managed_outputs}

        if (
            len(managed_outputs) == len(expected_outputs)
            and managed_names == expected_names
            and all(managed_by_name[name].stat().st_mtime_ns > source_modified_time_ns for name in expected_names)
        ):
            logging.info("Target PNG(s) are up to date; skipping export")
            return []

        files = musescore.export_to(score, path=workspace / target_path.name)
        generated = _rename_for_page_numbers(files)
        generated_names = {_canonical_name(path) for path in generated}
        if expected_names and generated_names != expected_names:
            raise RuntimeError(f"MuseScore generated {sorted(generated_names)}; expected {sorted(expected_names)}")

        expected_by_name = {_canonical_name(path): path for path in expected_outputs}
        results = []
        for generated_file in generated:
            destination = expected_by_name[_canonical_name(generated_file)]
            generated_file.replace(destination)
            results.append(destination)

        for obsolete in set(managed_outputs) - set(results):
            obsolete.unlink()
        return results


def _expected_outputs(target: Path, pages: int) -> list[Path]:
    if pages == 1:
        return [target]
    return [target.with_stem(f"{target.stem}-{page}-of-{pages}") for page in range(1, pages + 1)]


def _managed_outputs(target: Path) -> list[Path]:
    target_stem = unicodedata.normalize("NFC", target.stem)
    target_suffix = unicodedata.normalize("NFC", target.suffix)
    pattern = re.compile(rf"^{re.escape(target_stem)}(?:-\d+(?:-of-\d+)?)?{re.escape(target_suffix)}$")
    return [
        path
        for path in target.parent.iterdir()
        if path.is_file() and pattern.fullmatch(unicodedata.normalize("NFC", path.name))
    ]


def _canonical_name(path: Path) -> str:
    return unicodedata.normalize("NFC", path.name)


def _rename_for_page_numbers(files: list[Path]) -> list[Path]:
    if len(files) <= 1:
        return _remove_numbers(files)

    total_pages = max(_infer_page_number(file.name) for file in files)

    results = []
    for file in files:
        new_name = file.stem + f"-of-{total_pages:d}" + file.suffix
        results.append(_rename(file, new_name))
    return results


def _remove_numbers(files: list[Path]) -> list[Path]:
    results = []
    for file in files:
        if _infer_page_number(file.name) == 0:
            results.append(file)
            continue
        new_name = file.stem.rsplit("-", 1)[0] + file.suffix
        results.append(_rename(file, new_name))
    return results


def _rename(path: Path, new_name: str) -> Path:
    new_path = path.parent / new_name
    return path.rename(new_path)


def _infer_page_number(filename: str) -> int:
    try:
        return int(Path(filename).stem.split("-")[-1])
    except ValueError:
        return 0
