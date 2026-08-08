import os
from pathlib import Path
from zipfile import ZipFile

from msm.score import Score


def write_score(path: Path, title: str = "Test Score") -> None:
    xml = f"""<MuseScore version="4.0">
    <programVersion>4.4</programVersion><Score>
      <metaTag name="workTitle">{title}</metaTag>
      <VBox /><Staff><Measure>
        <TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig><KeySig><concertKey>0</concertKey></KeySig>
      </Measure></Staff>
    </Score></MuseScore>"""
    with ZipFile(path, "w") as archive:
        archive.writestr("score.mscx", xml)


def test_score_reads_embedded_metadata_and_normalizes_name(tmp_path):
    path = tmp_path / "source.mscz"
    write_score(path, "A Test, Score")

    score = Score(path)

    assert score.metadata.title == "A Test, Score"
    assert score.normalized_name(with_key=True) == "ATestScore-C.mscz"


def test_metadata_cache_is_invalidated_when_source_changes(tmp_path):
    path = tmp_path / "source.mscz"
    write_score(path, "First")
    score = Score(path)
    assert score.metadata.title == "First"

    original_mtime = path.stat().st_mtime
    write_score(path, "Second")
    os.utime(path, (original_mtime + 2, original_mtime + 2))

    assert score.metadata.title == "Second"


def test_score_can_be_created_from_bytes(tmp_path):
    path = tmp_path / "source.mscz"
    source = tmp_path / "bytes.mscz"
    write_score(source)

    score = Score.from_bytes(source.read_bytes(), path)

    assert score.metadata.title == "Test Score"
