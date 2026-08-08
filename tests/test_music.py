import json

import pytest
from pydantic import ValidationError

from msm.music import Interval, Key, ScoreTransposeConfigs, str2key


@pytest.mark.parametrize(
    ("value", "expected"),
    [("C", Key.C_MAJOR), ("Bb", Key.B_FLAT_MAJOR), ("F#", Key.F_SHARP_MAJOR), ("D_FLAT_MAJOR", Key.D_FLAT_MAJOR)],
)
def test_str2key(value, expected):
    assert str2key(value) is expected


def test_key_string_is_suitable_for_filenames():
    assert str(Key.B_FLAT_MAJOR) == "Bb"
    assert str(Key.C_SHARP_MAJOR) == "C#"
    assert str(Key.C_MAJOR) == "C"


def test_transpose_payload_preserves_musescore_field_names():
    config = ScoreTransposeConfigs(mode="by_key", direction="closest", targetKey=Key.D_MAJOR)

    assert config.model_dump() == {
        "mode": "by_key",
        "direction": "closest",
        "targetKey": Key.D_MAJOR,
        "transposeInterval": None,
        "transposeKeySignatures": True,
        "transposeChordNames": True,
        "useDoubleSharpsFlats": False,
    }
    assert json.loads(config.model_dump_json()) == {
        "mode": "by_key",
        "direction": "closest",
        "targetKey": 2,
        "transposeInterval": None,
        "transposeKeySignatures": True,
        "transposeChordNames": True,
        "useDoubleSharpsFlats": False,
    }


def test_transpose_payload_requires_option_for_mode():
    with pytest.raises(ValidationError, match="transposeInterval must be set"):
        ScoreTransposeConfigs(mode="by_interval", direction="up")

    config = ScoreTransposeConfigs(mode="by_interval", direction="up", transposeInterval=Interval.Major_Second)
    assert config.transposeInterval is Interval.Major_Second
