"""Shared utility functions"""

import datetime
import unicodedata
from itertools import chain
from pathlib import Path
from typing import Iterable


def make_camel_case(s: str, extra_seps: Iterable[str] | None = None) -> str:
    """Force CamelCase to a string, after splitting by whitespace and optional delimiters"""

    tokens = s.split()
    if extra_seps is not None:
        for sep in extra_seps:
            tokens = list(chain.from_iterable(t.split(sep) for t in tokens))

    return "".join(_capitalize_first_n(t, n=1) for t in tokens)


def normalize_unicode_filename(s: str) -> str:
    """Normalize filename to NFD to support macOS"""
    return unicodedata.normalize("NFD", s)


def infer_page_number(filename: str) -> int:
    """Infer page number from filename"""
    try:
        return int(Path(filename).stem.split("-")[-1])
    except ValueError:
        return 0


def paths_are_equal(p1: Path, p2: Path) -> bool:
    """Check if two paths are equal, accounting for potential macOS Unicode normalization"""
    if p1 == p2:
        return True

    # Potential macOS Unicode normalization issues with filenames
    p1_absolute_str = str(p1.absolute())
    p2_absolute_str = str(p2.absolute())
    if Path(normalize_unicode_filename(p1_absolute_str)) == Path(normalize_unicode_filename(p2_absolute_str)):
        return True

    return False


def last_modified_time_utc(path: Path) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)


def _capitalize_first_n(s: str, n: int = 1) -> str:
    if n < len(s):
        return s[n - 1].upper() + s[n:]

    return s.upper()
