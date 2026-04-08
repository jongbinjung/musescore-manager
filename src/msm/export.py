"""Functions to export scores"""

import logging
from pathlib import Path

from msm.utils import infer_page_number

from .musescore import Key, ScoreTransposeConfigs
from .score import Score
from .utils import last_modified_time_utc


def to_pngs(score: Score, key: Key | None = None, base_dir: Path = Path()) -> list[str]:
    """Export a score to PNG files, optionally transposing to a specified key before export

    If target PNGs already exist with a more recent timestamp than the score, this function will skip export and return
    an empty list.

    Args:
        score: The Score object to export

    Returns:
        list of exported files paths

    """
    tmp_score_path = base_dir / "_tmp_transposed.mscz"

    if key is not None and score.metadata.keysig != key:
        # Create temporary transposed score
        transpose_configs = ScoreTransposeConfigs(
            mode="by_key",
            direction="closest",
            targetKey=key,
        )
        transposed_bytes = score.musescore.transpose(
            score=score,
            score_transpose_config=transpose_configs,
            return_type="mscz",
        )
        score = Score.from_bytes(transposed_bytes, path=tmp_score_path)

    score_last_modified_time_utc = score.source_modified_time_utc

    target_path = base_dir / score.normalized_name(with_key=True, suffix="png")

    try:
        png_last_modified_time_utc = max(map(last_modified_time_utc, base_dir.glob(f"{target_path.stem}*.png")))
    except ValueError as e:
        if "max()" in str(e):
            png_last_modified_time_utc = None

    if png_last_modified_time_utc is not None and png_last_modified_time_utc > score_last_modified_time_utc:
        logging.info("Target PNG(s) are up to date; skipping export")
        tmp_score_path.unlink(missing_ok=True)
        return []

    files = score.musescore.export_to(score, path=base_dir / score.normalized_name(with_key=True, suffix="png"))

    # Clean up
    tmp_score_path.unlink(missing_ok=True)
    return _rename_for_page_numbers(files)


def _rename_for_page_numbers(files: list[Path]) -> list[Path]:
    if len(files) <= 1:
        return _remove_numbers(files)

    total_pages = max(infer_page_number(file.name) for file in files)

    results = []
    for file in files:
        new_name = file.stem + f"-of-{total_pages:d}" + file.suffix
        results.append(_rename(file, new_name))
    return results


def _remove_numbers(files: list[Path]) -> list[Path]:
    results = []
    for file in files:
        if infer_page_number(file.name) == 0:
            results.append(file)
            continue
        new_name = file.stem.rsplit("-", 1)[0] + file.suffix
        results.append(_rename(file, new_name))
    return results


def _rename(path: Path, new_name: str) -> Path:
    new_path = path.parent / new_name
    return path.rename(new_path)
