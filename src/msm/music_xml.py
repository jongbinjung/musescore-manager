"""Dealing with MusicXML files"""

import warnings
import xml.etree.ElementTree as ET

from pydantic import ValidationError

from msm.models import ScoreMetadata, TextMetadata
from msm.musescore import Key


class MusicXMLParser:
    """Parse MusicXML tree"""

    def __init__(self, tree: ET.ElementTree):
        self.tree = tree
        self.file_version = int(tree.getroot().attrib["version"].replace(".", ""))
        self.measures = tree.findall(".//Measure")
        self.mscore_version = tree.find("./programVersion").text
        self.text_metadata = self.parse_text_metadata(tree)

        self._keysig: Key | None = None
        self._lyrics: str | None = None
        self._timesig: str | None = None

    @staticmethod
    def parse_text_metadata(tree: ET.ElementTree) -> TextMetadata:
        metatags = tree.findall(".//metaTag")
        text_metadata = {}
        for m in metatags:
            if m.text is None:
                continue
            match m.attrib["name"]:
                case "composer":
                    text_metadata["composer"] = m.text
                case "workTitle":
                    text_metadata["title"] = m.text
        for t in tree.find(".//VBox").findall(".//Text"):
            if t.find("./text").text is None:
                continue
            match t.find("./style").text:
                case "title" | "subtitle" | "composer" as key:
                    text_metadata[key] = t.find("./text").text
        try:
            return TextMetadata(**text_metadata)
        except ValidationError as e:
            print(f"Failed to parse text metadata\n{text_metadata}")
            raise e

    def score_metadata(self) -> ScoreMetadata:
        """Parse score metadata from MusicXML tree"""
        return ScoreMetadata(
            title=self.text_metadata.title,
            subtitle=self.text_metadata.subtitle,
            composer=self.text_metadata.composer,
            keysig=self.keysig,
            timesig=self.timesig,
            measures=len(self.measures),
            lyrics=self.lyrics,
            fileVersion=self.file_version,
            mscoreVersion=self.mscore_version,
        )

    @property
    def lyrics(self) -> str:
        if self._lyrics is None:
            lyric_tokens = []
            lyrics = []
            for m in self.measures:
                for c in m.findall(".//Chord"):
                    lyrics.append(c.findall(".//Lyrics"))

            # A Chord can have multiple Lyrics, each corresponding to different verses for the same Chord
            # So for each Chord, we extract the first Lyrics element, and push any remaining to the back of the list
            syllables = []
            while len(lyrics) > 0:
                lyric = lyrics.pop(0)
                if len(lyric) > 0:
                    note = lyric.pop(0)
                    syllabic = note.find("./syllabic")
                    lyric_token = note.find("./text").text
                    if syllabic is not None:
                        if lyric_token is not None:
                            syllables.append(lyric_token.strip())
                        if syllabic.text == "end":
                            lyric_token = "".join(syllables)
                            syllables = []
                        else:
                            lyric_token = None
                    if lyric_token is not None:
                        lyric_tokens.append(lyric_token.strip())
                # Any remaining Lyrics elements are pushed to the back of the list
                if len(lyric) > 0:
                    lyrics.append(lyric)
            self._lyrics = " ".join(lyric_tokens)
        return self._lyrics

    @property
    def timesig(self) -> str:
        if self._timesig is None:
            ts = self.tree.find(".//TimeSig")
            self._timesig = f"{ts.find('./sigN').text}/{ts.find('./sigD').text}"
        return self._timesig

    @property
    def keysig(self) -> Key:
        if self._keysig is None:
            concert_key = self.tree.find(".//concertKey")
            if concert_key is None:
                warnings.warn(
                    f"No concertKey found; defaulting to C_MAJOR ({self.text_metadata.title})",
                    RuntimeWarning,
                )
                self._keysig = Key.C_MAJOR
            else:
                self._keysig = Key(int(self.tree.find(".//concertKey").text))
        return self._keysig
