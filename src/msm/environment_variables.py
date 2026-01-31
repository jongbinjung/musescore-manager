"""Environment variables for secrets and configurations"""

import os
from typing import Callable, TypeVar

from msm.types import valid_path

T = TypeVar("T")

_FALSY = {"0", "false", "no", "off"}
_TRUTHY = {"1", "on", "true", "yes"}


def _bool(value: str) -> bool:
    value_lower = value.lower()
    if value_lower in _TRUTHY:
        return True
    elif value_lower in _FALSY:
        return False
    else:
        raise ValueError(f"Cannot convert {value} to boolean.")


class SecretValueError(ValueError):
    """Custom error for secret value conversion failures."""

    __suppress_context__ = True


class EnvironmentVariable:
    """Representation of an environment variable with some expected type"""

    def __init__(
        self,
        name: str,
        as_type: Callable[[str], T],
        default_value: T | None = None,
        is_secret: bool = False,
    ):
        """

        Args:
            name: name of the environment variable
            as_type: type conversion function to convert the environment variable's string value to the expected type
            default_value: default value to use if the environment variable is not set
            is_secret: if set to True, the variable's value is obfuscated when printed

        Raises:
            ValueError: If the conversion of the environment variable's value fails.

        """
        if as_type is bool:
            # Always use custom converter for bool types
            as_type = _bool
        self.name = name
        self.type = as_type
        self.default = default_value
        self.is_secret = is_secret

    @property
    def defined(self) -> bool:
        return self.name in os.environ

    def set(self, value):
        os.environ[self.name] = str(value)

    def unset(self):
        os.environ.pop(self.name, None)

    def get_raw(self) -> str | None:
        """Get the raw string value of the environment variable, or None if not set"""
        return os.getenv(self.name)

    def get(self) -> T | None:
        """Reads the value of the environment variable if it exists and converts it to the expected type

        Otherwise returns default value.

        """
        val = self.get_raw()
        if val is not None:
            try:
                return self.type(val)
            except Exception as e:
                if self.is_secret:
                    # Do NOT inheret the original exception's context for secret values since they might leak sensitive
                    # information
                    raise SecretValueError(f"Failed to convert {self._printable(val)!r} for {self.name}")
                raise ValueError(f"Failed to convert {self._printable(val)!r} for {self.name}") from e
        return self.default

    def _printable(self, value) -> str:
        """Returns a printable representation of the value"""
        if self.is_secret:
            return "*****"
        return str(value)

    def __str__(self):
        return f"{self.name} (value={self._printable(self.get())})"

    def __repr__(self):
        return repr(self.name)

    def __format__(self, format_spec: str) -> str:
        return self.name.__format__(format_spec)


AWS_ACCESS_KEY_ID = EnvironmentVariable("AWS_ACCESS_KEY_ID", str)
AWS_SECRET_ACCESS_KEY = EnvironmentVariable("AWS_SECRET_ACCESS_KEY", str)
AWS_ENDPOINT_URL_S3 = EnvironmentVariable("AWS_ENDPOINT_URL_S3", str)
AWS_ENDPOINT_URL_IAM = EnvironmentVariable("AWS_ENDPOINT_URL_IAM", str)
AWS_REGION = EnvironmentVariable("AWS_REGION", str)

LOCAL_MSCZ_DIRECTORY = EnvironmentVariable("LOCAL_MSCZ_DIRECTORY", valid_path)

MSCORE_CMD = EnvironmentVariable("MSCORE_CMD", str, "mscore")

NOTION_TOKEN = EnvironmentVariable("NOTION_TOKEN", str, None, is_secret=True)
