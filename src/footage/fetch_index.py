"""Download only some batches' vectors from the archive's 928 MB SigLIP2 index.

The index file is an IndexIDMap2 around an fp16 IndexScalarQuantizer:

    "IxM2" header | "IxSQ" header | quantizer | codes[ntotal * code_size] | id_map[ntotal]

HTTP range requests read the headers, the id map (8 bytes per vector) and
then only the code rows of the wanted frames, about 2.3 KB each, so a batch
such as L23 costs ~17 MB instead of the whole file.  The rows are decoded to
float32 and written as a small exact index with the same ids.
"""

from __future__ import annotations

import csv
import struct
import urllib.request
from dataclasses import dataclass
from pathlib import Path

QT_FP16 = 4  # faiss::ScalarQuantizer::QT_fp16


@dataclass
class IndexLayout:
    dimension: int
    ntotal: int
    code_size: int
    codes_offset: int
    id_map_offset: int


def _read_header(buffer: bytes, offset: int, fourcc: bytes) -> tuple[int, int, int]:
    if buffer[offset:offset + 4] != fourcc:
        raise ValueError(f"expected {fourcc!r} at byte {offset}, found {buffer[offset:offset + 4]!r}")
    offset += 4
    dimension, ntotal = struct.unpack_from("<iq", buffer, offset)
    offset += 4 + 8 + 8 + 8  # d, ntotal, two reserved int64
    offset += 1  # is_trained
    (metric,) = struct.unpack_from("<i", buffer, offset)
    offset += 4
    if metric > 1:
        offset += 4  # metric_arg
    return dimension, ntotal, offset


def parse_layout(head: bytes) -> IndexLayout:
    """Locate the code and id-map arrays from the first few KB of the file."""
    dimension, ntotal, offset = _read_header(head, 0, b"IxM2")
    _, _, offset = _read_header(head, offset, b"IxSQ")
    qtype, _rangestat, _rangestat_arg, _d, code_size = struct.unpack_from("<iifQQ", head, offset)
    if qtype != QT_FP16:
        raise ValueError(f"only fp16 scalar quantizers are supported, got qtype {qtype}")
    offset += 4 + 4 + 4 + 8 + 8
    (trained,) = struct.unpack_from("<Q", head, offset)
    offset += 8 + 4 * trained
    (code_bytes,) = struct.unpack_from("<Q", head, offset)
    offset += 8
    if code_bytes != ntotal * code_size:
        raise ValueError("code array size does not match ntotal * code_size")
    return IndexLayout(dimension, ntotal, code_size, offset, offset + code_bytes)


def _get(url: str, start: int, length: int) -> bytes:
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{start + length - 1}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    if len(data) != length:
        raise IOError(f"range request returned {len(data)} bytes, expected {length}")
    return data


def frame_ids(frames_csv: Path, batches: set[str]) -> list[int]:
    with Path(frames_csv).open(newline="", encoding="utf-8") as file:
        return sorted(int(row["frame_id"]) for row in csv.DictReader(file)
                      if row["video_id"].split("_", 1)[0] in batches)


def contiguous(positions: list[int]) -> list[tuple[int, int]]:
    runs = []
    for position in sorted(positions):
        if runs and position == runs[-1][0] + runs[-1][1]:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((position, 1))
    return runs


def fetch_subset(url: str, frames_csv: Path, batches: set[str], out: Path, progress=print) -> Path:
    import faiss
    import numpy as np

    wanted = frame_ids(frames_csv, batches)
    if not wanted:
        raise ValueError(f"no frames for batches {sorted(batches)}")
    layout = parse_layout(_get(url, 0, 64 * 1024))
    progress(f"index: {layout.ntotal} vectors x {layout.dimension} dims, "
             f"{layout.code_size} bytes per vector")

    raw_ids = _get(url, layout.id_map_offset, 8 + 8 * layout.ntotal)
    (count,) = struct.unpack_from("<Q", raw_ids, 0)
    id_map = np.frombuffer(raw_ids, dtype="<i8", count=count, offset=8)
    position_of = {int(frame_id): position for position, frame_id in enumerate(id_map)}
    missing = [frame_id for frame_id in wanted if frame_id not in position_of]
    if missing:
        raise ValueError(f"{len(missing)} frames are not in the index, e.g. {missing[:3]}")

    positions = [position_of[frame_id] for frame_id in wanted]
    blocks = []
    for start, length in contiguous(positions):
        data = _get(url, layout.codes_offset + start * layout.code_size, length * layout.code_size)
        blocks.append(np.frombuffer(data, dtype="<f2").reshape(length, layout.dimension))
    vectors = np.vstack(blocks).astype("float32")
    ids = np.array([int(id_map[position]) for start, length in contiguous(positions)
                    for position in range(start, start + length)], dtype="int64")

    norms = np.linalg.norm(vectors, axis=1)
    progress(f"fetched {len(ids)} vectors; norm min {norms.min():.3f} max {norms.max():.3f}")
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(layout.dimension))
    index.add_with_ids(vectors, ids)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(out))
    progress(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return out
