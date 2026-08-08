import xml.etree.ElementTree as ET

import pytest

from msm.metadata import MscxParser
from msm.music import Key


def parse(xml: str) -> MscxParser:
    return MscxParser(ET.ElementTree(ET.fromstring(xml)))


def test_parses_score_metadata():
    parser = parse(
        """<MuseScore version="4.0">
        <programVersion>4.4</programVersion>
        <Score>
          <metaTag name="workTitle">Metadata Title</metaTag>
          <metaTag name="composer">Composer</metaTag>
          <VBox><Text><style>title</style><text>Displayed Title</text></Text></VBox>
          <Staff><Measure>
            <TimeSig><sigN>3</sigN><sigD>4</sigD></TimeSig>
            <KeySig><concertKey>-2</concertKey></KeySig>
            <Chord><Lyrics><text>Hello</text></Lyrics></Chord>
          </Measure></Staff>
        </Score>
        </MuseScore>"""
    )

    metadata = parser.score_metadata()

    assert metadata.title == "Displayed Title"
    assert metadata.composer == "Composer"
    assert metadata.keysig is Key.B_FLAT_MAJOR
    assert metadata.timesig == "3/4"
    assert metadata.measures == 1
    assert metadata.lyrics == "Hello"
    assert metadata.fileVersion == 40
    assert metadata.mscoreVersion == "4.4"


def test_missing_key_defaults_to_c_major():
    parser = parse(
        """<MuseScore version="4.0"><programVersion>4.4</programVersion><Score>
        <metaTag name="workTitle">Title</metaTag><VBox />
        <Measure><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig></Measure>
        </Score></MuseScore>"""
    )

    with pytest.warns(RuntimeWarning, match="defaulting to C_MAJOR"):
        assert parser.keysig is Key.C_MAJOR


def test_text_metadata_does_not_require_vbox():
    parser = parse(
        """<MuseScore version="4.0"><programVersion>4.4</programVersion><Score>
        <metaTag name="workTitle">Title</metaTag><metaTag name="composer">Composer</metaTag>
        </Score></MuseScore>"""
    )

    assert parser.text_metadata.title == "Title"
    assert parser.text_metadata.composer == "Composer"
