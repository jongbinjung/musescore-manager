class ConfigurationError(Exception):
    """Raised when there is a configuration error."""

    pass


class MissingDependencyError(ImportError):
    """Raised when a required dependency is missing."""

    pass


class MusescoreError(RuntimeError):
    """Raised when there's a problem with Musescore"""

    pass
