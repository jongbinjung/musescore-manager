"""Custom type defnitions"""

from pathlib import Path


def valid_path(path_str: str) -> Path:
    """Validate that a string is a valid path

    Args:
        path_str: string representation of the path

    Returns:
        A validated Path object

    Raises:
        ValueError: if the path does not exist
    """
    path = Path(path_str).expanduser()
    if not path.exists():
        raise ValueError(f"Path {path} does not exist.")
    return path
