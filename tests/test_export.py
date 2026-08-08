import os
import unicodedata
from dataclasses import dataclass

import pytest
from test_score import write_score

from msm.export import to_pngs
from msm.score import Score


@dataclass
class FakeMetadata:
    pages: int | None


class FakeMusescore:
    pages: int | None = 1

    def metadata(self, score):
        return FakeMetadata(pages=self.pages)

    def transpose(self, score, score_transpose_config, return_type):
        raise NotImplementedError

    def export_to(self, score, path):
        generated = []
        assert self.pages is not None
        for page in range(1, self.pages + 1):
            path_for_page = path.with_stem(f"{path.stem}-{page}")
            path_for_page.write_bytes(b"png")
            generated.append(path_for_page)
        return generated


def test_exports_and_normalizes_single_page_name(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)

    results = to_pngs(Score(score_path), musescore=FakeMusescore(), base_dir=tmp_path / "pngs")

    assert results == [tmp_path / "pngs" / "TestScore-C.png"]
    assert results[0].is_file()


def test_skips_export_when_existing_output_is_newer(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)
    export_dir = tmp_path / "pngs"
    export_dir.mkdir()
    existing = export_dir / "TestScore-C.png"
    existing.write_bytes(b"png")
    newer = score_path.stat().st_mtime + 2
    os.utime(existing, (newer, newer))

    assert to_pngs(Score(score_path), musescore=FakeMusescore(), base_dir=export_dir) == []


def test_incomplete_page_set_is_reexported_and_stale_pages_are_removed(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)
    export_dir = tmp_path / "pngs"
    export_dir.mkdir()
    stale = export_dir / "TestScore-C-1-of-3.png"
    stale.write_bytes(b"stale")
    newer = score_path.stat().st_mtime + 2
    os.utime(stale, (newer, newer))
    musescore = FakeMusescore()
    musescore.pages = 2

    results = to_pngs(Score(score_path), musescore=musescore, base_dir=export_dir)

    assert results == [export_dir / "TestScore-C-1-of-2.png", export_dir / "TestScore-C-2-of-2.png"]
    assert not stale.exists()


def test_failed_export_keeps_previous_outputs_and_cleans_workspace(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)
    export_dir = tmp_path / "pngs"
    export_dir.mkdir()
    existing = export_dir / "TestScore-C.png"
    existing.write_bytes(b"old")
    older = score_path.stat().st_mtime - 2
    os.utime(existing, (older, older))

    class FailingMusescore(FakeMusescore):
        def export_to(self, score, path):
            raise RuntimeError("export failed")

    with pytest.raises(RuntimeError, match="export failed"):
        to_pngs(Score(score_path), musescore=FailingMusescore(), base_dir=export_dir)

    assert existing.read_bytes() == b"old"
    assert list(export_dir.iterdir()) == [existing]


def test_extra_cached_page_forces_export(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)
    export_dir = tmp_path / "pngs"
    export_dir.mkdir()
    expected = export_dir / "TestScore-C.png"
    extra = export_dir / "TestScore-C-2-of-2.png"
    expected.write_bytes(b"old")
    extra.write_bytes(b"extra")
    newer = score_path.stat().st_mtime + 2
    os.utime(expected, (newer, newer))
    os.utime(extra, (newer, newer))

    results = to_pngs(Score(score_path), musescore=FakeMusescore(), base_dir=export_dir)

    assert results == [expected]
    assert not extra.exists()


def test_partial_export_does_not_replace_previous_page_set(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)
    export_dir = tmp_path / "pngs"
    export_dir.mkdir()
    previous = [export_dir / "TestScore-C-1-of-2.png", export_dir / "TestScore-C-2-of-2.png"]
    older = score_path.stat().st_mtime - 2
    for output in previous:
        output.write_bytes(b"old")
        os.utime(output, (older, older))

    class PartialMusescore(FakeMusescore):
        pages = 2

        def export_to(self, score, path):
            generated = path.with_stem(f"{path.stem}-1")
            generated.write_bytes(b"partial")
            return [generated]

    with pytest.raises(RuntimeError, match="expected"):
        to_pngs(Score(score_path), musescore=PartialMusescore(), base_dir=export_dir)

    assert [output.read_bytes() for output in previous] == [b"old", b"old"]


def test_missing_page_count_does_not_replace_previous_outputs(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path)
    export_dir = tmp_path / "pngs"
    export_dir.mkdir()
    existing = export_dir / "TestScore-C.png"
    existing.write_bytes(b"old")
    musescore = FakeMusescore()
    musescore.pages = None

    with pytest.raises(RuntimeError, match="positive page count"):
        to_pngs(Score(score_path), musescore=musescore, base_dir=export_dir)

    assert existing.read_bytes() == b"old"


def test_unicode_normalized_output_is_published_with_canonical_name(tmp_path):
    score_path = tmp_path / "score.mscz"
    write_score(score_path, title="Café")

    class NormalizingMusescore(FakeMusescore):
        def export_to(self, score, path):
            normalized = path.with_name(unicodedata.normalize("NFD", path.name)).with_stem(
                f"{unicodedata.normalize('NFD', path.stem)}-1"
            )
            normalized.write_bytes(b"png")
            return [normalized]

    results = to_pngs(Score(score_path), musescore=NormalizingMusescore(), base_dir=tmp_path / "pngs")

    assert results == [tmp_path / "pngs" / "Café-C.png"]
