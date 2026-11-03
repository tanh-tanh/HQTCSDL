"""One search: ask the visual channel, call sp_search_footage, log it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter

from footage.db import call, execute, query_one

SOURCES = ("visual", "ocr", "asr")
SOURCE_LABELS = {
    "visual": "Hình ảnh (SigLIP2)",
    "ocr": "Chữ trên hình (OCR)",
    "asr": "Lời nói (ASR)",
}


@dataclass
class SearchOutcome:
    results: list[dict]
    latency_ms: int
    sources: tuple[str, ...]
    notes: list[str] = field(default_factory=list)


def run_search(connection, text: str, sources, top_k: int, pool: int,
               visual=None) -> SearchOutcome:
    sources = tuple(name for name in SOURCES if name in set(sources))
    started = perf_counter()
    notes, visual_ids = [], None
    if "visual" in sources:
        if visual is not None and visual.ready:
            visual_ids = visual.search(text, pool)
        else:
            status = visual.status if visual is not None else "disabled"
            notes.append(f"Bỏ qua kênh hình ảnh: {status}.")
    results = call(
        connection, "sp_search_footage",
        query=text,
        visual=json.dumps(visual_ids) if visual_ids is not None else None,
        use_ocr="ocr" in sources,
        use_asr="asr" in sources,
        pool=pool,
        top_k=top_k,
    )
    for rank, row in enumerate(results, 1):
        row["rank"] = rank
    latency_ms = round((perf_counter() - started) * 1000)
    return SearchOutcome(results, latency_ms, sources, notes)


def log_search(connection, user_id: int | None, text: str, outcome: SearchOutcome,
               top_k: int) -> None:
    row = query_one(
        connection,
        "INSERT INTO dbo.search_log (user_id, request_text, top_k, result_count, latency_ms) "
        "OUTPUT INSERTED.search_id VALUES (?, ?, ?, ?, ?)",
        user_id, text, top_k, len(outcome.results), outcome.latency_ms,
    )
    for source in outcome.sources:
        execute(connection,
                "INSERT INTO dbo.search_log_sources (search_id, source_code) VALUES (?, ?)",
                row["search_id"], source)
