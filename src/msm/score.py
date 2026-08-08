"""Models and definitions for interacting with Musescore and Score objects"""

import datetime
import io
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Self
from zipfile import ZipFile

from msm.metadata import MscxParser, ScoreMetadata
from msm.musescore import Musescore
from msm.utils import make_camel_case


class Score:
    """Score object that represents an mscz file"""

    def __init__(self, path: Path | str, read_metadata: bool = False, musescore: Musescore | None = None):
        """Create a Score object that represents an mscz file

        Args:
            path: path to the score
            read_metadata: initialize metadata on object creation; could take a while

        Returns:
            bytes: byte-representation of the requested file type

        Raises:
            FileNotFoundError: if score does not exist, or is not a regular file
            ValueError: if score is not .mscz

        """
        self._path = Path(path)
        self._validate_path()

        self._metadata: ScoreMetadata | None = None
        self._metadata_modified_at: datetime.datetime | None = None
        if read_metadata:
            _ = self.metadata

        if musescore is None:
            musescore = Musescore()
        self.musescore = musescore

    def __repr__(self) -> str:
        return f"Score({self._path.name})"

    @classmethod
    def from_bytes(cls, score_bytes: bytes, path: Path) -> Self:
        """Create a Score object from bytes

        Args:
            score_bytes: byte-representation of the score

        """
        path.write_bytes(score_bytes)
        return cls(path, read_metadata=True)

    def read_bytes(self) -> bytes:
        """Read score bytes from the file

        Returns:
            bytes: byte-representation of the score

        """
        return self._path.read_bytes()

    @property
    def metadata(self) -> ScoreMetadata:
        source_modified_at = self.source_modified_time
        if self._metadata is None or self._metadata_modified_at != source_modified_at:
            self._metadata = self._parse_metadata_from_bytes()
            self._metadata_modified_at = source_modified_at
        return self._metadata

    def update_metadata_from_mscore(self) -> ScoreMetadata:
        source_modified_at = self.source_modified_time
        if self._metadata is None or self._metadata.pages is None or self._metadata_modified_at != source_modified_at:
            metadata_dict = self.musescore.metadata(self)
            self._metadata = ScoreMetadata(**metadata_dict)
            self._metadata_modified_at = source_modified_at
        return self._metadata

    @property
    def source_modified_time(self) -> datetime.datetime:
        self._validate_path()
        return datetime.datetime.fromtimestamp(self._path.stat().st_mtime)

    @property
    def source_modified_time_utc(self) -> datetime.datetime:
        self._validate_path()
        return datetime.datetime.fromtimestamp(self._path.stat().st_mtime, tz=datetime.timezone.utc)

    @property
    def parent_dir(self) -> Path:
        return self._path.parent

    def score_path(self, relative_to: str | Path = ".") -> str:
        return str(self._path.relative_to(relative_to))

    @property
    def absolute_path(self) -> Path:
        return self._path.absolute()

    @property
    def pages(self) -> int:
        if self.metadata.pages is None:
            self.update_metadata_from_mscore()
        pages = self.metadata.pages
        assert pages is not None
        return pages

    def normalize(self, with_key: bool = False) -> Self:
        """Normalize the score name"""
        self.rename(self.normalized_path(with_key=with_key))

        return self

    def rename(self, path: Path):
        """Rename the score file to the new path

        Raises:
            FileExistsError: if the new path already exists

        """
        if path != self._path:
            if path.exists():
                logging.error(f"{str(path.absolute())} already exists for {str(self._path.absolute())}")
                path = path.with_stem(f"{path.stem}-1")
                logging.error(f"Writing to {str(path.absolute())} instead")
            self._path = self._path.rename(path)

    def normalized_path(self, **kwargs) -> Path:
        """Get the normalized path of the score

        KwArgs:
            All keyword arguments are passed to normalized_name

        Returns:
            Path: a new path to a file in the same parent directory but with a normalized filename

        """
        return self.parent_dir / self.normalized_name(**kwargs)

    def normalized_name(self, remove_chars=".,-_/#$&()[]<>", with_key: bool = False, suffix: str = "mscz") -> str:
        """Normalize the name of the score with the specified suffix

        Args:
            remove_chars: characters to remove from the score's title and subtitle when normalizing

        Returns:
            str: normalized name of the score

        """
        metadata = self.metadata
        tokens = []
        tokens.append(make_camel_case(metadata.title.strip(), extra_seps=remove_chars))
        if with_key:
            tokens.append(str(metadata.keysig))
        if metadata.subtitle:
            tokens.append(make_camel_case(metadata.subtitle, extra_seps=remove_chars))
        return f"{'-'.join(tokens)}.{suffix}"

    def _parse_metadata_from_bytes(self) -> ScoreMetadata:
        with ZipFile(io.BytesIO(self.read_bytes()), "r") as z:
            _files = [
                filename for filename in filter(lambda x: x.endswith(".mscx"), z.namelist()) if "/" not in filename
            ]
            if len(_files) > 1:
                raise ValueError(f"Multiple mscx files\n{_files}")
            elif len(_files) == 0:
                raise ValueError(f"No mscx files\n{z.namelist()}")
            else:
                with z.open(_files[0], mode="r") as f:
                    tree = ET.parse(f)

        return MscxParser(tree).score_metadata()

    def _validate_path(self):
        if not self._path.is_file():
            raise FileNotFoundError(f"{self._path.absolute()} is not a file or does not exist")

        if self._path.suffix.lower() != ".mscz":
            raise ValueError(f"Only supports '.mscz'; got {self._path.name}")
