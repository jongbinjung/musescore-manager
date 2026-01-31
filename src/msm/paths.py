from pathlib import Path

CONFIG_DIR = Path.home() / ".msm"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def get_credentials_path() -> Path:
    return CONFIG_DIR / "credentials"


def get_local_mscz_path() -> Path:
    return CONFIG_DIR / "local_mscz"
