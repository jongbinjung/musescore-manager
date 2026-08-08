"""Application configuration loaded from the environment or a profile file."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import TypeVar

from captainplanet import EnvironmentVariable

AWS_ACCESS_KEY_ID = EnvironmentVariable("AWS_ACCESS_KEY_ID", str, obfuscated=True)
AWS_SECRET_ACCESS_KEY = EnvironmentVariable("AWS_SECRET_ACCESS_KEY", str, obfuscated=True)
AWS_ENDPOINT_URL_S3 = EnvironmentVariable("AWS_ENDPOINT_URL_S3", str)

LOCAL_MSCZ_DIRECTORY = EnvironmentVariable("LOCAL_MSCZ_DIRECTORY", lambda value: Path(value).expanduser())
LOCAL_PNG_DIRECTORY = EnvironmentVariable("LOCAL_PNG_DIRECTORY", lambda value: Path(value).expanduser())

MSCORE_CMD = EnvironmentVariable("MSCORE_CMD", str)
MSCZ_BUCKET_NAME = EnvironmentVariable("MSCZ_BUCKET_NAME", str)

T = TypeVar("T")


def get_configs_path() -> Path:
    return Path.home() / ".msm" / "configs"


def _read_from_file(name: str, profile_name: str) -> str | None:
    path = get_configs_path()
    if not path.exists():
        return None

    config = configparser.ConfigParser()
    config.read(path)
    if profile_name not in config:
        raise ValueError(f"'{profile_name}' profile not found in configuration file")

    return config[profile_name].get(name)


def read_value(var: EnvironmentVariable[T], profile_name: str = "default", default_value: T | None = None) -> T | None:
    """Read a setting using environment, profile file, then default precedence."""
    from_env = var.get()
    if from_env is not None:
        return from_env

    from_file = _read_from_file(var.name, profile_name)
    if from_file is not None:
        return var.type_(from_file)

    return default_value


class Configs:
    """Typed access to application settings."""

    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name

    def aws_access_key_id(self) -> str | None:
        return read_value(AWS_ACCESS_KEY_ID, profile_name=self.profile_name)

    def aws_secret_access_key(self) -> str | None:
        return read_value(AWS_SECRET_ACCESS_KEY, profile_name=self.profile_name)

    def aws_endpoint_url_s3(self) -> str | None:
        return read_value(AWS_ENDPOINT_URL_S3, profile_name=self.profile_name)

    def local_mscz_directory(self) -> Path | None:
        return read_value(LOCAL_MSCZ_DIRECTORY, profile_name=self.profile_name)

    def local_png_directory(self) -> Path | None:
        return read_value(LOCAL_PNG_DIRECTORY, profile_name=self.profile_name)

    def mscore_cmd(self) -> str:
        value = read_value(MSCORE_CMD, profile_name=self.profile_name, default_value="mscore")
        assert value is not None
        return value

    def mscz_bucket_name(self) -> str | None:
        return read_value(MSCZ_BUCKET_NAME, profile_name=self.profile_name)
