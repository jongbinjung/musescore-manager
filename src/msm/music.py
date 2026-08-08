"""Musical values used by score metadata and the MuseScore protocol."""

from enum import IntEnum
from typing import Literal, Self

from pydantic import BaseModel, model_validator


class Key(IntEnum):
    C_FLAT_MAJOR = -7
    G_FLAT_MAJOR = -6
    D_FLAT_MAJOR = -5
    A_FLAT_MAJOR = -4
    E_FLAT_MAJOR = -3
    B_FLAT_MAJOR = -2
    F_MAJOR = -1
    C_MAJOR = 0
    G_MAJOR = 1
    D_MAJOR = 2
    A_MAJOR = 3
    E_MAJOR = 4
    B_MAJOR = 5
    F_SHARP_MAJOR = 6
    C_SHARP_MAJOR = 7

    def __str__(self) -> str:
        match self.name.split("_"):
            case key, "FLAT", _:
                return f"{key}b"
            case key, "SHARP", _:
                return f"{key}#"
            case key, _:
                return key
        raise ValueError(f"Invalid key name: {self.name}")


def str2key(key_str: str) -> Key:
    tonic = None
    modifier = None

    if len(key_str) == 1:
        tonic = key_str.upper()

    if len(key_str) == 2:
        tonic = key_str[0].upper()
        match key_str[1]:
            case "b":
                modifier = "FLAT"
            case "#":
                modifier = "SHARP"

    if "_" in key_str:
        match key_str.upper().split("_"):
            case tonic, "FLAT", *_:
                modifier = "FLAT"
            case tonic, "SHARP", *_:
                modifier = "SHARP"
            case tonic, *_:
                modifier = None

    if tonic is None:
        raise ValueError(f"Invalid key string: {key_str}")

    if modifier is None:
        return Key[f"{tonic}_MAJOR"]

    return Key[f"{tonic}_{modifier}_MAJOR"]


class Interval(IntEnum):
    Augmented_Fifth = 15
    Augmented_Fourth = 12
    Augmented_Second = 5
    Augmented_Seventh = 23
    Augmented_Sixth = 19
    Augmented_Third = 9
    Augmented_Unison = 1
    Diminished_Fifth = 13
    Diminished_Fourth = 10
    Diminished_Octave = 24
    Diminished_Seventh = 20
    Diminished_Sixth = 16
    Diminished_Third = 6
    Diminished_Second = 2
    Major_Seventh = 22
    Major_Sixth = 18
    Major_Third = 8
    Major_Second = 4
    Minor_Seventh = 21
    Minor_Sixth = 17
    Minor_Third = 7
    Minor_Second = 3
    Perfect_Fifth = 14
    Perfect_Fourth = 11
    Perfect_Octave = 25
    Perfect_Unison = 0


class ScoreTransposeConfigs(BaseModel):
    """MuseScore's score-transpose JSON payload."""

    mode: Literal["by_key", "by_interval", "diatonically"]
    direction: Literal["up", "down", "closest"]
    targetKey: Key | None = None
    transposeInterval: Interval | None = None
    transposeKeySignatures: bool = True
    transposeChordNames: bool = True
    useDoubleSharpsFlats: bool = False

    @model_validator(mode="after")
    def check_mode_and_options(self) -> Self:
        if self.mode == "by_key" and self.targetKey is None:
            raise ValueError("targetKey must be set if mode='by_key'")
        if self.mode in ("by_interval", "diatonically") and self.transposeInterval is None:
            raise ValueError(f"transposeInterval must be set if mode={self.mode!r}")
        return self
