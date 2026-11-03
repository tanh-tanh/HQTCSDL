"""Run every labelled query and store the ranked results for v_eval_summary."""

from __future__ import annotations

from footage.db import execute, query, query_one
from footage.search import run_search


def run_evaluation(connection, name: str, sources, top_k: int, pool: int,
                   tolerance_sec: float, visual=None, limit: int | None = None,
                   progress=print) -> int:
    queries = query(
        connection,
        "SELECT q.request_id, q.request_text FROM dbo.ground_truth g "
        "JOIN dbo.requests q ON q.request_id = g.request_id ORDER BY q.request_id",
    )
    if limit:
        queries = queries[:limit]
    run_id = query_one(
        connection,
        "INSERT INTO dbo.eval_runs (run_name, top_k, tolerance_sec) OUTPUT INSERTED.run_id "
        "VALUES (?, ?, ?)",
        name, top_k, tolerance_sec,
    )["run_id"]
    for source in sources:
        execute(connection, "INSERT INTO dbo.eval_run_sources (run_id, source_code) VALUES (?, ?)",
                run_id, source)
    connection.commit()
    for position, item in enumerate(queries, 1):
        outcome = run_search(connection, item["request_text"], sources, top_k, pool, visual)
        execute(connection,
                "INSERT INTO dbo.eval_requests (run_id, request_id, latency_ms) VALUES (?, ?, ?)",
                run_id, item["request_id"], outcome.latency_ms)
        if outcome.results:
            cursor = connection.cursor()
            cursor.fast_executemany = True
            cursor.executemany(
                "INSERT INTO dbo.eval_results (run_id, request_id, rank_no, keyframe_id) "
                "VALUES (?, ?, ?, ?)",
                [(run_id, item["request_id"], row["rank"], row["keyframe_id"])
                 for row in outcome.results],
            )
        connection.commit()
        progress(f"[{position}/{len(queries)}] {item['request_id']}: "
                 f"{len(outcome.results)} results, {outcome.latency_ms} ms")
    return run_id
