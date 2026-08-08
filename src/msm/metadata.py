"""Metadata models and parsing for MuseScore's embedded MSCX document."""

import warnings
import xml.etree.ElementTree as ET

from pydantic import BaseModel, field_validator

from msm.music import Key


class ScoreMetadata(BaseModel):
    title: str
    subtitle: str
    composer: str
    keysig: Key
    timesig: str
    measures: int
    lyrics: str
    fileVersion: int
    mscoreVersion: str
    tempo: int | None = None
    pages: int | None = None


class TextMetadata(BaseModel):
    title: str
    subtitle: str = ""
    composer: str = ""

    @field_validator("subtitle", "composer", mode="before")
    @classmethod
    def none_to_empty_string(cls, value):
        return "" if value is None else value


class MscxParser:
    """Parse metadata from a MuseScore MSCX XML tree."""

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
        text_metadata = {}
        for metatag in tree.findall(".//metaTag"):
            if metatag.text is None:
                continue
            match metatag.attrib["name"]:
                case "composer":
                    text_metadata["composer"] = metatag.text
                case "workTitle":
                    text_metadata["title"] = metatag.text

        vbox = tree.find(".//VBox")
        for text in vbox.findall(".//Text") if vbox is not None else []:
            value_element = text.find("./text")
            style_element = text.find("./style")
            if value_element is None or style_element is None:
                continue
            value = value_element.text
            if value is None:
                continue
            match style_element.text:
                case "title" | "subtitle" | "composer" as key:
                    text_metadata[key] = value

        return TextMetadata(**text_metadata)

    def score_metadata(self) -> ScoreMetadata:
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
            for measure in self.measures:
                for chord in measure.findall(".//Chord"):
                    lyrics.append(chord.findall(".//Lyrics"))

            syllables = []
            while lyrics:
                lyric = lyrics.pop(0)
                if lyric:
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
                if lyric:
                    lyrics.append(lyric)
            self._lyrics = " ".join(lyric_tokens)
        return self._lyrics

    @property
    def timesig(self) -> str:
        if self._timesig is None:
            timesig = self.tree.find(".//TimeSig")
            self._timesig = f"{timesig.find('./sigN').text}/{timesig.find('./sigD').text}"
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
                self._keysig = Key(int(concert_key.text))
        return self._keysig
