"""Data object to deal with configurations/secrets"""

import configparser
import logging
from pathlib import Path

from captainplanet import EnvironmentVariable

from msm.exceptions import ConfigurationError, MusescoreError
from msm.paths import get_configs_path
from msm.types import valid_path

LOGGER = logging.getLogger(__name__)

AWS_ACCESS_KEY_ID = EnvironmentVariable("AWS_ACCESS_KEY_ID", str, obfuscated=True)
AWS_SECRET_ACCESS_KEY = EnvironmentVariable("AWS_SECRET_ACCESS_KEY", str, obfuscated=True)
AWS_ENDPOINT_URL_S3 = EnvironmentVariable("AWS_ENDPOINT_URL_S3", str)
AWS_ENDPOINT_URL_IAM = EnvironmentVariable("AWS_ENDPOINT_URL_IAM", str)
AWS_REGION = EnvironmentVariable("AWS_REGION", str)

GOOGLE_DRIVE_FOLDER_ID = EnvironmentVariable("GOOGLE_DRIVE_FOLDER_ID", str)
GOOGLE_APP_CREDENTIALS_JSON_PATH = EnvironmentVariable("GOOGLE_APP_CREDENTIALS_JSON_PATH", valid_path)

LOCAL_MSCZ_DIRECTORY = EnvironmentVariable("LOCAL_MSCZ_DIRECTORY", valid_path)
LOCAL_PNG_DIRECTORY = EnvironmentVariable("LOCAL_PNG_DIRECTORY", valid_path)

MSCORE_CMD = EnvironmentVariable("MSCORE_CMD", str)
MSCZ_BUCKET_NAME = EnvironmentVariable("MSCZ_BUCKET_NAME", str)

NOTION_TOKEN = EnvironmentVariable("NOTION_TOKEN", str, None, obfuscated=True)


def _read_from_file(name: str, profile_name: str = "default") -> str | None:
    path = get_configs_path()
    if not path.exists():
        return None

    config = configparser.ConfigParser()
    config.read(path)
    if profile_name not in config:
        raise ValueError(f"'{profile_name}' profile not found in credentials file")

    return config[profile_name].get(name)


def _read_from_env(var: EnvironmentVariable) -> str | None:
    return var.get()


def read_value(var: EnvironmentVariable, profile_name: str = "default", default_value=None):
    """Read the value of the given environment variable from either the environment or the config file

    Return type is determined by the EnvironmentVariable.type
    """
    from_file = var.type_(_read_from_file(var.name, profile_name=profile_name))
    from_env = _read_from_env(var)
    return from_env or from_file or default_value


class Configs:
    """Configuration and secret values for the application

    All values are read from
        1. An environment variable of the same name, but in ALL CAPS
        2. A configuration file located at ~/.msm/credentials under the section [{profile_name}]
    in that order.

    """

    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name

    def aws_access_key_id(self) -> str | None:
        """AWS Access Key ID"""
        return read_value(AWS_ACCESS_KEY_ID, profile_name=self.profile_name)

    def aws_secret_access_key(self) -> str | None:
        """AWS Secret Access Key"""
        return read_value(AWS_SECRET_ACCESS_KEY, profile_name=self.profile_name)

    def aws_endpoint_url_s3(self) -> str | None:
        """AWS Endpoint URL for S3"""
        return read_value(AWS_ENDPOINT_URL_S3, profile_name=self.profile_name)

    def aws_endpoint_url_iam(self) -> str | None:
        """AWS Endpoint URL for IAM"""
        return read_value(AWS_ENDPOINT_URL_IAM, profile_name=self.profile_name)

    def aws_region(self) -> str | None:
        """AWS Region"""
        return read_value(AWS_REGION, profile_name=self.profile_name)

    def google_app_credentials_json_path(self) -> str | None:
        """Path to Google application credentials JSON file for Google Drive integration"""
        return read_value(GOOGLE_APP_CREDENTIALS_JSON_PATH, profile_name=self.profile_name)

    def google_drive_folder_id(self) -> str | None:
        """Google Drive folder ID to upload PNGs to"""
        return read_value(GOOGLE_DRIVE_FOLDER_ID, profile_name=self.profile_name)

    def local_mscz_directory(self) -> Path | None:
        """Local directory where .mscz files are stored"""
        return read_value(LOCAL_MSCZ_DIRECTORY, profile_name=self.profile_name)

    def local_png_directory(self) -> Path | None:
        """Local directory where .png exports are stored"""
        return read_value(LOCAL_PNG_DIRECTORY, profile_name=self.profile_name)

    def mscore_cmd(self) -> str:
        """Path to the MuseScore command line executable

        Raises:
            MusescoreError: if the Musescore command is not configured properly

        """
        import subprocess

        cmd = read_value(MSCORE_CMD, profile_name=self.profile_name, default_value="mscore")

        if cmd is None:
            raise ConfigurationError(
                "MuseScore command is not configured. "
                "Please set the MSCORE_CMD environment variable or "
                "configure it in the credentials file (~/.msm/credentials)."
            )

        try:
            v = subprocess.run([cmd, "-v"], capture_output=True)
        except FileNotFoundError as e:
            raise MusescoreError(f"Problem running Musescore command {cmd}") from e
        LOGGER.info("Using MuseScore command: %s, version: %s", cmd, v.stdout.decode().strip())
        return read_value(MSCORE_CMD, profile_name=self.profile_name)

    def mscz_bucket_name(self) -> Path | None:
        """Local directory where .mscz files are stored"""
        return read_value(MSCZ_BUCKET_NAME, profile_name=self.profile_name)

    def notion_token(self) -> str | None:
        """Notion integration token"""
        return read_value(NOTION_TOKEN, profile_name=self.profile_name)
