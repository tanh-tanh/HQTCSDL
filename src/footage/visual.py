"""SigLIP2 text-to-image search on CPU over the archive's precomputed FAISS index.

SigLIP2 was trained on multilingual text, so Vietnamese descriptions are
embedded directly; no translation step is needed.  The index and the text
tower must come from the same checkpoint (google/siglip2-so400m-patch16-384).
A vector's FAISS id is its row in frames.csv, i.e. keyframes.keyframe_id.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# SigLIP's text tower has 64 positions; longer prompts are split into
# overlapping windows and each frame keeps its best window score.
CONTEXT_LENGTH = 64
CHUNK_OVERLAP = 8


def contiguous_ranges(ids: list[int]) -> list[tuple[int, int]]:
    """[(start, count)] covering sorted ids, e.g. [3,4,5,9] -> [(3,3),(9,1)]."""
    ranges = []
    for value in sorted(ids):
        if ranges and value == ranges[-1][0] + ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], ranges[-1][1] + 1)
        else:
            ranges.append((value, 1))
    return ranges


class VisualSearcher:
    """Loads lazily in a background thread; `search` is serialized."""

    def __init__(self, model_name: str, index_path: Path, cache_dir: str | None = None):
        self.model_name = model_name
        self.index_path = Path(index_path)
        self.cache_dir = cache_dir
        self.status = "not loaded"
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._model = self._tokenizer = self._index = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def start(self, keyframe_ids: list[int]) -> None:
        threading.Thread(target=self.load, args=(keyframe_ids,), daemon=True).start()

    def load(self, keyframe_ids: list[int]) -> None:
        try:
            self.status = "loading"
            import faiss
            import torch
            from transformers import AutoModel, AutoTokenizer

            torch.set_grad_enabled(False)
            index = faiss.read_index(str(self.index_path))
            self._index = self._restrict(index, keyframe_ids)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, cache_dir=self.cache_dir)
            self._model = AutoModel.from_pretrained(self.model_name, cache_dir=self.cache_dir).eval()
            self.status = f"ready ({self._index.ntotal} vectors)"
            self._ready.set()
        except Exception as error:  # surfaced on the search page, not fatal
            log.exception("visual search unavailable")
            self.status = f"unavailable: {error}"

    @staticmethod
    def _restrict(index, keyframe_ids: list[int]):
        """Search only the frames that were imported into MySQL.

        Importing one batch (e.g. L23) keeps 1-2% of the corpus.  Searching
        the full index would fill the candidate pool with frames the
        database does not know, so copy the imported vectors into a small
        exact index instead.  frame ids are contiguous per batch, which makes
        this a few block reads.
        """
        import faiss
        import numpy as np

        if not keyframe_ids or len(keyframe_ids) >= index.ntotal:
            return index
        blocks, ids = [], []
        for start, count in contiguous_ranges(keyframe_ids):
            blocks.append(index.reconstruct_n(start, count))
            ids.append(np.arange(start, start + count, dtype="int64"))
        subset = faiss.IndexIDMap(faiss.IndexFlatIP(index.d))
        subset.add_with_ids(np.vstack(blocks).astype("float32"), np.concatenate(ids))
        return subset

    def _chunks(self, text: str) -> list[str]:
        token_ids = self._tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"]
        if len(token_ids) <= CONTEXT_LENGTH:
            return [text]
        chunks, stride = [], CONTEXT_LENGTH - CHUNK_OVERLAP
        for start in range(0, len(token_ids), stride):
            chunks.append(self._tokenizer.decode(
                token_ids[start:start + CONTEXT_LENGTH], skip_special_tokens=True
            ))
            if start + CONTEXT_LENGTH >= len(token_ids):
                break
        return chunks

    def search(self, text: str, k: int) -> list[int]:
        """Keyframe ids, best first."""
        if not self.ready:
            raise RuntimeError(f"visual search is {self.status}")
        import faiss
        import torch

        with self._lock:
            inputs = self._tokenizer(
                # SigLIP was trained on fixed-length (max_length-padded) text.
                self._chunks(text), padding="max_length", truncation=True,
                max_length=CONTEXT_LENGTH, return_tensors="pt",
            )
            with torch.inference_mode():
                vectors = self._model.get_text_features(**inputs).float().numpy()
            faiss.normalize_L2(vectors)
            scores, ids = self._index.search(vectors, min(k, self._index.ntotal))
        best: dict[int, float] = {}
        for score_row, id_row in zip(scores, ids):
            for score, frame_id in zip(score_row, id_row):
                if frame_id >= 0 and score > best.get(int(frame_id), float("-inf")):
                    best[int(frame_id)] = float(score)
        return sorted(best, key=best.get, reverse=True)[:k]
