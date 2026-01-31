"""Pydantic models for types"""

import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from msm.musescore import Key


class UpdatedAt:
    """Mixin that automatically updates updated_at field."""

    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.now)

    model_config = ConfigDict(validate_assignment=True)

    @model_validator(mode="after")
    @classmethod
    def update_updated_at(cls, obj: Self) -> Self:
        """Update updated_at field."""
        # must disable validation to avoid infinite loop
        obj.model_config["validate_assignment"] = False

        # update updated_at field
        obj.updated_at = datetime.datetime.now()

        # enable validation again
        obj.model_config["validate_assignment"] = True
        return obj


class ScoreMetadata(BaseModel, UpdatedAt):
    title: str
    subtitle: str
    composer: str
    keysig: Key
    timesig: str
    measures: int
    lyrics: str
    fileVersion: int
    mscoreVersion: str

    # Optional fields that must be inferred via mscore
    tempo: int | None = None
    pages: int | None = None


class TextMetadata(BaseModel):
    title: str
    subtitle: str = ""
    composer: str = ""

    @field_validator("subtitle", "composer", mode="before")
    @classmethod
    def none_to_empty_string(cls, v):
        if v is None:
            return ""
        return v
