"""Pure-Python parts: password hashing, script splitting, sample generation."""

from pathlib import Path

from footage.auth import hash_password, verify_password
from footage.db import SCHEMA_PARTS, split_batches
from footage.fetch_index import contiguous
from footage.sample import MAX_ROWS, insert_statements, read_fps, read_labels, sql_text
from footage.web import image_path, resolve_image

ROOT = Path(__file__).resolve().parents[1]


def test_password_hash_round_trip_and_salting():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong horse", stored)
    assert hash_password("correct horse") != stored


def test_verify_rejects_malformed_hashes():
    assert not verify_password("x", "")
    assert not verify_password("x", "bcrypt$1$2$3$4$5")
    assert not verify_password("x", "scrypt$notanumber$8$1$AAAA$AAAA")


def test_split_batches_on_go_lines():
    script = "-- comment only\nCREATE TABLE a (id INT);\ngo\nCREATE PROCEDURE p AS\nSELECT 'GO';\nGO  \n\nGO\n"
    assert split_batches(script) == ["-- comment only\nCREATE TABLE a (id INT);",
                                     "CREATE PROCEDURE p AS\nSELECT 'GO';"]


def test_every_shipped_script_is_utf8_with_bom_and_splits():
    for name in ("00_database.sql", *SCHEMA_PARTS):
        raw = (ROOT / "db" / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), f"{name} needs a UTF-8 BOM for SSMS"
        assert split_batches(raw.decode("utf-8-sig")), name


def test_sql_text_escapes_quotes_and_flattens_lines():
    assert sql_text("it's\nĐà Lạt") == "N'it''s Đà Lạt'"


def test_insert_statements_respect_the_row_limit():
    rows = [(str(i),) for i in range(MAX_ROWS + 1)]
    statements = insert_statements("t", ("x",), rows)
    assert len(statements) == 2
    assert statements[1].count("\n    (") == 1


def test_contiguous_runs():
    assert contiguous([9, 3, 4, 5]) == [(3, 3), (9, 1)]
    assert contiguous([]) == []


def test_fps_comes_from_the_tracked_keyframe_maps():
    rates = read_fps(ROOT / "data" / "map-keyframes" / "map-keyframes")
    assert len(rates) == 873
    assert rates["L21_V001"] == 30.0


def test_sample_requests_are_unique_and_verified():
    labels = read_labels(ROOT / "labels" / "l23_requests.csv", {f"L23_V{n:03d}" for n in range(1, 26)})
    ids = [label["request_id"] for label in labels]
    assert len(ids) == 120 and len(set(ids)) == 120
    assert ids[0] == "YC-001" and ids[-1] == "YC-120"
    assert all(label["end_sec"] >= label["start_sec"] for label in labels)


def test_requests_on_videos_not_exported_are_skipped():
    labels = read_labels(ROOT / "labels" / "l23_requests.csv", {"L23_V001"})
    assert labels and all(label["video_id"] == "L23_V001" for label in labels)


def test_image_path_pads_to_three_digits():
    assert image_path("L23_V006", 7) == "keyframes/keyframes/L23_V006/007.jpg"
    assert image_path("L25_V081", 1086) == "keyframes/keyframes/L25_V081/1086.jpg"


def test_resolve_image_finds_unpacked_batch_zip(tmp_path):
    # L23.zip holds L23/keyframes/keyframes/<video>/<n>.jpg
    unpacked = tmp_path / "L23" / "keyframes" / "keyframes" / "L23_V006"
    unpacked.mkdir(parents=True)
    (unpacked / "120.jpg").write_bytes(b"jpg")
    assert resolve_image(tmp_path, "L23_V006", 120) == unpacked / "120.jpg"
    assert resolve_image(tmp_path, "L23_V006", 404) is None
