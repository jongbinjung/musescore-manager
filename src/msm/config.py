"""Application, profile, and named remote-target configuration."""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from captainplanet import EnvironmentVariable

LOCAL_MSCZ_DIRECTORY = EnvironmentVariable("LOCAL_MSCZ_DIRECTORY", lambda value: Path(value).expanduser())
LOCAL_PNG_DIRECTORY = EnvironmentVariable("LOCAL_PNG_DIRECTORY", lambda value: Path(value).expanduser())
MSCORE_CMD = EnvironmentVariable("MSCORE_CMD", str)
JOBS = EnvironmentVariable("JOBS", int)
DEFAULT_SCORES_TARGET = EnvironmentVariable("DEFAULT_SCORES_TARGET", str)
DEFAULT_PNGS_TARGET = EnvironmentVariable("DEFAULT_PNGS_TARGET", str)

TARGET_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
PROFILE_FIELDS = (
    "LOCAL_MSCZ_DIRECTORY",
    "LOCAL_PNG_DIRECTORY",
    "MSCORE_CMD",
    "JOBS",
    "DEFAULT_SCORES_TARGET",
    "DEFAULT_PNGS_TARGET",
)


@dataclass(frozen=True)
class S3TargetConfig:
    name: str
    bucket: str
    prefix: str = ""
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


@dataclass(frozen=True)
class DriveTargetConfig:
    name: str
    folder_id: str
    credentials_path: Path
    token_path: Path


TargetConfig = S3TargetConfig | DriveTargetConfig


def get_configs_path() -> Path:
    return Path.home() / ".msm" / "configs"


def get_google_token_path(target_name: str = "default") -> Path:
    return Path.home() / ".msm" / "tokens" / f"google-drive-{target_name}.json"


def _parser() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(get_configs_path())
    return config


def _write_parser(config: configparser.ConfigParser) -> None:
    path = get_configs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as config_file:
        config.write(config_file)


def _read_from_file(name: str, profile_name: str) -> str | None:
    config = _parser()
    if not config.sections():
        return None
    if profile_name not in config:
        raise ValueError(f"'{profile_name}' profile not found in configuration file")
    return config[profile_name].get(name)


def read_value(var: EnvironmentVariable, profile_name: str = "default", default_value: Any = None) -> Any:
    """Read a setting using environment, profile file, then default precedence."""
    from_env = var.get()
    if from_env is not None:
        return from_env
    from_file = _read_from_file(var.name, profile_name)
    return var.type_(from_file) if from_file is not None else default_value


def _target_env_name(target_name: str, setting: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "_", target_name).upper()
    return f"MSM_TARGET_{normalized}_{setting}"


def _target_value(section: configparser.SectionProxy, target_name: str, setting: str) -> str | None:
    return os.environ.get(_target_env_name(target_name, setting)) or section.get(setting)


def _required_target_value(section: configparser.SectionProxy, target_name: str, setting: str) -> str:
    value = _target_value(section, target_name, setting)
    if not value:
        raise ValueError(f"Target '{target_name}' requires {setting}")
    return value


class Configs:
    """Typed access to local settings and named remote targets."""

    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name

    def local_mscz_directory(self) -> Path | None:
        return read_value(LOCAL_MSCZ_DIRECTORY, profile_name=self.profile_name)

    def local_png_directory(self) -> Path | None:
        return read_value(LOCAL_PNG_DIRECTORY, profile_name=self.profile_name)

    def mscore_cmd(self) -> str:
        value = read_value(MSCORE_CMD, profile_name=self.profile_name, default_value="mscore")
        assert value is not None
        return value

    def jobs(self) -> int:
        value = read_value(JOBS, profile_name=self.profile_name, default_value=4)
        assert value is not None
        if value < 1:
            raise ValueError("JOBS must be at least 1")
        return value

    def default_target(self, artifact_kind: str) -> str | None:
        variable = DEFAULT_SCORES_TARGET if artifact_kind == "scores" else DEFAULT_PNGS_TARGET
        return read_value(variable, profile_name=self.profile_name)

    def profiles(self) -> list[str]:
        """Return profile names, excluding named target sections."""
        return [section for section in _parser().sections() if not section.startswith("target.")]

    def profile_values(self, name: str) -> dict[str, str]:
        """Return configured profile values without applying environment overrides."""
        if not TARGET_NAME.fullmatch(name):
            raise ValueError(f"Invalid profile name: {name}")
        config = _parser()
        if name not in config:
            raise ValueError(f"Profile '{name}' not found in configuration file")
        return {key.upper(): value for key, value in config[name].items() if key.upper() in PROFILE_FIELDS}

    def save_profile(self, name: str, values: dict[str, str]) -> None:
        """Create or replace one profile while preserving target sections."""
        if not TARGET_NAME.fullmatch(name):
            raise ValueError(f"Invalid profile name: {name}")
        unknown = set(values) - set(PROFILE_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported profile settings: {', '.join(sorted(unknown))}")
        config = _parser()
        if name not in config:
            config.add_section(name)
        else:
            for key in list(config[name]):
                config[name].pop(key)
        for key, value in values.items():
            config[name][key] = value
        _write_parser(config)

    def clear_profile(self, name: str) -> None:
        if not TARGET_NAME.fullmatch(name):
            raise ValueError(f"Invalid profile name: {name}")
        config = _parser()
        if name not in config or name.startswith("target."):
            raise ValueError(f"Profile '{name}' not found in configuration file")
        config.remove_section(name)
        _write_parser(config)

    def save_target(self, name: str, provider: str, values: dict[str, str]) -> None:
        """Create or replace a named target while preserving other sections."""
        if not TARGET_NAME.fullmatch(name):
            raise ValueError(f"Invalid target name: {name}")
        allowed = {
            "s3": {"TYPE", "BUCKET", "PREFIX", "ENDPOINT_URL", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"},
            "google-drive": {"TYPE", "FOLDER_ID", "CREDENTIALS_PATH", "TOKEN_PATH"},
        }
        if provider not in allowed:
            raise ValueError(f"Unsupported target provider: {provider}")
        if set(values) - allowed[provider]:
            raise ValueError(f"Unsupported {provider} target settings")
        config = _parser()
        section_name = f"target.{name}"
        if section_name not in config:
            config.add_section(section_name)
        else:
            for key in list(config[section_name]):
                config[section_name].pop(key)
        for key, value in values.items():
            config[section_name][key] = value
        _write_parser(config)

    def targets(self) -> dict[str, str]:
        """Return configured target names and provider types without exposing settings."""
        config = _parser()
        return {
            section.removeprefix("target."): config[section].get("TYPE", "").lower()
            for section in config.sections()
            if section.startswith("target.")
        }

    def target_display_values(self, name: str) -> tuple[str, ...]:
        """Return non-sensitive target values suitable for display."""
        target = self.target(name)
        if isinstance(target, S3TargetConfig):
            return (target.bucket, target.endpoint_url or "-")
        return (f"https://drive.google.com/drive/folders/{target.folder_id}",)

    def target(self, name: str) -> TargetConfig:
        if not TARGET_NAME.fullmatch(name):
            raise ValueError(f"Invalid target name: {name}")
        section_name = f"target.{name}"
        config = _parser()
        if section_name not in config:
            raise ValueError(f"Remote target '{name}' not found in configuration file")
        section = config[section_name]
        provider = _required_target_value(section, name, "TYPE").lower()

        if provider == "s3":
            return S3TargetConfig(
                name=name,
                bucket=_required_target_value(section, name, "BUCKET"),
                prefix=(_target_value(section, name, "PREFIX") or "").strip("/"),
                endpoint_url=_target_value(section, name, "ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL_S3"),
                access_key_id=_target_value(section, name, "ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID"),
                secret_access_key=_target_value(section, name, "SECRET_ACCESS_KEY")
                or os.environ.get("AWS_SECRET_ACCESS_KEY"),
            )
        if provider in {"drive", "google-drive"}:
            token = _target_value(section, name, "TOKEN_PATH")
            return DriveTargetConfig(
                name=name,
                folder_id=_required_target_value(section, name, "FOLDER_ID"),
                credentials_path=Path(_required_target_value(section, name, "CREDENTIALS_PATH")).expanduser(),
                token_path=Path(token).expanduser() if token else get_google_token_path(name),
            )
        raise ValueError(f"Target '{name}' has unsupported TYPE '{provider}'")
