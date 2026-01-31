import os

import pytest

from msm.environment_variables import EnvironmentVariable

TEST_VAR_NAME = "_TEST_VAR"


def test_get_default_if_not_set(monkeypatch):
    default_value = "some-default-value"
    monkeypatch.delenv(TEST_VAR_NAME, raising=False)
    ev = EnvironmentVariable(TEST_VAR_NAME, str, default_value=default_value)
    assert ev.get() == default_value


def test_get_from_environment_if_set(monkeypatch):
    default_value = "some-default-value"
    test_value = "some-other-value-that-is-not-default"
    monkeypatch.setenv(TEST_VAR_NAME, test_value)
    ev = EnvironmentVariable(TEST_VAR_NAME, str, default_value)
    assert ev.get() == test_value


def test_set(monkeypatch):
    value_to_set = "some-initial-value"
    monkeypatch.delenv(TEST_VAR_NAME, raising=False)
    ev = EnvironmentVariable(TEST_VAR_NAME, str)
    ev.set(value_to_set)
    assert ev.get() == value_to_set
    assert os.environ[TEST_VAR_NAME] == value_to_set


def test_unset(monkeypatch):
    default_value = "some-default-value"
    value_to_set = "some-initial-value"
    monkeypatch.setenv(TEST_VAR_NAME, value_to_set)
    ev = EnvironmentVariable(TEST_VAR_NAME, str, default_value)
    assert ev.get() == value_to_set
    ev.unset()
    assert ev.get() == default_value
    assert TEST_VAR_NAME not in os.environ


@pytest.mark.parametrize("expected", [True, False])
def test_defined(monkeypatch, expected):
    if expected:
        monkeypatch.setenv(TEST_VAR_NAME, "value")
    else:
        monkeypatch.delenv(TEST_VAR_NAME, raising=False)
    ev = EnvironmentVariable(TEST_VAR_NAME, str, "default")
    assert ev.defined == expected


@pytest.mark.parametrize(
    "as_type,value,expected",
    [
        (int, "42", 42),
        (float, "3.14", 3.14),
        (str, "hello", "hello"),
        (bool, "0", False),
        (bool, "false", False),
        (bool, "FALSE", False),
        (bool, "False", False),
        (bool, "NO", False),
        (bool, "no", False),
        (bool, "No", False),
        (bool, "Off", False),
        (bool, "OFF", False),
        (bool, "off", False),
        (bool, "1", True),
        (bool, "true", True),
        (bool, "TRUE", True),
        (bool, "True", True),
        (bool, "yes", True),
        (bool, "YES", True),
        (bool, "Yes", True),
        (bool, "on", True),
        (bool, "ON", True),
        (bool, "On", True),
        # "Type" can be arbitrary Callable[[str], T]
        (lambda s: s.split(":")[1], "This:value", "value"),
        (lambda s: s.split(","), "a,b,c", ["a", "b", "c"]),
    ],
)
def test_type_conversion(monkeypatch, as_type, value: str, expected):
    monkeypatch.setenv(TEST_VAR_NAME, value)
    ev = EnvironmentVariable(TEST_VAR_NAME, as_type, value)
    assert ev.get_raw() == value
    assert ev.get() == expected


def test_get_raw_not_set(monkeypatch):
    monkeypatch.delenv(TEST_VAR_NAME, raising=False)
    ev = EnvironmentVariable(TEST_VAR_NAME, str, "default")
    assert ev.get_raw() is None


def test_secret_values(monkeypatch):
    value = "super-secret-value"
    monkeypatch.setenv(TEST_VAR_NAME, value)

    # Default values are obscured
    ev = EnvironmentVariable(TEST_VAR_NAME, str, value, is_secret=True)
    assert ev.get() == value
    assert value not in str(ev)
    assert value not in repr(ev)

    # Set values are obscured
    ev = EnvironmentVariable(TEST_VAR_NAME, str, is_secret=True)
    assert ev.get() == value
    assert value not in str(ev)
    assert value not in repr(ev)

    # Parsing failures still obscure any value
    with pytest.raises(ValueError) as exc_info:
        ev = EnvironmentVariable(TEST_VAR_NAME, int, is_secret=True)
        ev.get()
    assert value not in str(exc_info.value)
