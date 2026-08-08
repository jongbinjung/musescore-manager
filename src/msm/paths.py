from pathlib import Path

CONFIG_DIR = Path.home() / ".msm"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def get_configs_path() -> Path:
    return CONFIG_DIR / "configs"


def get_google_token_path() -> Path:
    return CONFIG_DIR / "google-drive-token.json"
