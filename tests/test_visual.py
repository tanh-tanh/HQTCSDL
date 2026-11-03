"""Restricting the FAISS index to the imported frames (no model needed)."""

import pytest

from footage.visual import VisualSearcher

faiss = pytest.importorskip("faiss")
np = pytest.importorskip("numpy")


def team_style_index(count=50, dim=16):
    """Same layout as the team's SigLIP2 file: IDMap2 over an fp16 SQ index,
    vector id == frames.csv row id."""
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(count, dim)).astype("float32")
    faiss.normalize_L2(vectors)
    index = faiss.IndexIDMap2(
        faiss.IndexScalarQuantizer(dim, faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT)
    )
    index.add_with_ids(vectors, np.arange(count, dtype="int64"))
    return index, vectors


def test_restrict_keeps_only_imported_ids_with_original_numbers():
    index, vectors = team_style_index()
    keep = list(range(10, 20)) + [42]
    subset = VisualSearcher._restrict(index, keep)
    assert subset.ntotal == len(keep)
    scores, ids = subset.search(vectors[[15, 42, 3]], 1)
    assert ids[0][0] == 15 and ids[1][0] == 42
    assert ids[2][0] in keep  # frame 3 was not imported, so it can never be returned
    assert scores[0][0] == pytest.approx(1.0, abs=1e-2)


def test_restrict_is_a_no_op_when_everything_is_imported():
    index, _ = team_style_index()
    assert VisualSearcher._restrict(index, list(range(50))) is index
    assert VisualSearcher._restrict(index, []) is index


def test_search_refuses_before_loading():
    searcher = VisualSearcher("model", "missing.faiss")
    assert not searcher.ready
    with pytest.raises(RuntimeError, match="not loaded"):
        searcher.search("xe", 5)


def test_parse_layout_finds_codes_and_id_map(tmp_path):
    from footage.fetch_index import parse_layout

    index, vectors = team_style_index(count=40, dim=16)
    path = tmp_path / "team.faiss"
    faiss.write_index(index, str(path))
    data = path.read_bytes()
    layout = parse_layout(data[:65536])
    codes = np.frombuffer(data[layout.codes_offset:layout.codes_offset + 40 * layout.code_size],
                          dtype="<f2").reshape(40, 16)
    ids = np.frombuffer(data, dtype="<i8", count=40, offset=layout.id_map_offset + 8)
    assert layout.ntotal == 40 and layout.code_size == 32
    assert np.abs(codes.astype("float32") - vectors).max() < 1e-3
    assert (ids == np.arange(40)).all()
    assert layout.id_map_offset + 8 + 40 * 8 == len(data)
