"""Data for the 3D archive interface (RING / CORRIDOR / CONSTELLATION).

Everything here reads the existing schema through the existing search path:
`run_search` -> `sp_search_footage`, enriched from `v_video_catalog`.  Nothing
in this module invents metadata: a field the database does not have is sent
as null and the interface leaves it out.
"""

from __future__ import annotations

import hashlib
import re
import hmac
import secrets
import threading
import time
from collections import deque
from decimal import Decimal

from footage.db import query
from footage.search import SOURCES, SearchOutcome, log_search, run_search

VIEWS = ("ring", "corridor", "constellation")
USERNAME = re.compile(r"[a-z0-9_.\-]{3,32}")
TOP_K_CHOICES = (20, 50, 100, 200)
MAX_TOP_K = 200
BROWSE_PER_VIDEO = 2
BROWSE_LIMIT = 60


def _num(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def clamp_k(k: int) -> int:
    return max(1, min(int(k), MAX_TOP_K))


def chosen_sources(src: list[str] | None) -> list[str]:
    return [name for name in (src or SOURCES) if name in SOURCES]


def parse_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() else None


# ----------------------------------------------------------------------
# Catalogue lookups
# ----------------------------------------------------------------------

def video_metadata(connection, video_ids) -> dict[str, dict]:
    ids = sorted(set(video_ids))
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    rows = query(
        connection,
        "SELECT video_id, title, collection_code, collection_title, owner, access_level, topics "
        f"FROM dbo.v_video_catalog WHERE video_id IN ({marks})",
        *ids,
    )
    return {row["video_id"]: row for row in rows}


def filtered_video_ids(connection, collection_id: int | None, topic_id: int | None) -> set[str] | None:
    """Videos allowed by the collection/topic filters; None when no filter is set."""
    if collection_id is None and topic_id is None:
        return None
    where, params = [], []
    if collection_id is not None:
        where.append("v.collection_id = ?")
        params.append(collection_id)
    if topic_id is not None:
        # A parent topic also matches every topic below it (recursive CTE in the function).
        where.append("EXISTS (SELECT 1 FROM dbo.video_topics vt "
                     "JOIN dbo.fn_topic_subtree(?) s ON s.topic_id = vt.topic_id "
                     "WHERE vt.video_id = v.video_id)")
        params.append(topic_id)
    rows = query(connection, "SELECT v.video_id FROM dbo.videos v WHERE " + " AND ".join(where), *params)
    return {row["video_id"] for row in rows}


def filter_options(connection) -> dict:
    topics = query(connection, "SELECT topic_id, topic_name, parent_topic_id FROM dbo.topics")
    children: dict[int | None, list[dict]] = {}
    for row in topics:
        children.setdefault(row["parent_topic_id"], []).append(row)
    ordered: list[dict] = []

    def walk(parent, depth):
        for row in sorted(children.get(parent, []), key=lambda r: r["topic_name"]):
            ordered.append({"id": row["topic_id"], "name": row["topic_name"],
                            "parent": row["parent_topic_id"], "depth": depth})
            walk(row["topic_id"], depth + 1)

    walk(None, 0)
    collections = query(
        connection,
        "SELECT collection_id, title, owner, videos FROM dbo.v_collection_stats ORDER BY collection_id",
    )
    return {
        "topics": ordered,
        "collections": [{"id": row["collection_id"], "title": row["title"], "owner": row["owner"],
                         "videos": row["videos"]} for row in collections],
    }


def user_roles(connection, user_id: int) -> list[str]:
    rows = query(connection,
                 "SELECT r.role_name FROM dbo.user_roles ur JOIN dbo.roles r ON r.role_id = ur.role_id "
                 "WHERE ur.user_id = ? ORDER BY r.role_id", user_id)
    return [row["role_name"] for row in rows]


# ----------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------

def serialize(rows: list[dict], meta: dict[str, dict]) -> list[dict]:
    items = []
    for rank, row in enumerate(rows, 1):
        info = meta.get(row["video_id"], {})
        topics = [t.strip() for t in (info.get("topics") or "").split(",") if t.strip()]
        keyframe_id = int(row["keyframe_id"])
        items.append({
            "keyframe_id": keyframe_id,
            "video_id": row["video_id"],
            "pts_time": _num(row["pts_time"]),
            "frame_idx": row.get("frame_idx"),
            "rank": rank,
            "score": _num(row.get("rrf_score")),
            "evidence": row.get("evidence"),
            "image_url": f"/img/{keyframe_id}",
            "detail_url": f"/frames/{keyframe_id}",
            "title": info.get("title"),
            "collection": info.get("collection_title"),
            "owner": info.get("owner"),
            "access_level": info.get("access_level"),
            "topics": topics,
        })
    return items


def search_payload(connection, *, text: str, sources: list[str], top_k: int, pool: int,
                   visual, user_id: int, collection_id: int | None = None,
                   topic_id: int | None = None, runner=run_search) -> dict:
    """Run the existing fused search; apply catalogue filters to the ranked list."""
    allowed = filtered_video_ids(connection, collection_id, topic_id)
    # With a filter the procedure is asked for its widest list so that the
    # filtered top-K is still the best K of what the archive holds.
    fetch_k = MAX_TOP_K if allowed is not None else top_k
    outcome = runner(connection, text, sources, fetch_k, pool, visual)
    rows = outcome.results
    if allowed is not None:
        rows = [row for row in rows if row["video_id"] in allowed][:top_k]
    outcome = SearchOutcome(rows, outcome.latency_ms, outcome.sources, outcome.notes)
    log_search(connection, user_id, text, outcome, top_k)
    return {
        "mode": "search",
        "query": text,
        "top_k": top_k,
        "sources": list(outcome.sources),
        "latency_ms": outcome.latency_ms,
        "notes": outcome.notes,
        "filters": {"collection": collection_id, "topic": topic_id},
        "results": serialize(rows, video_metadata(connection, [r["video_id"] for r in rows])),
    }


def browse_payload(connection, *, collection_id: int | None = None, topic_id: int | None = None,
                   per_video: int = BROWSE_PER_VIDEO, limit: int = BROWSE_LIMIT) -> dict:
    """The idle state before a query: evenly spaced real keyframes of each video."""
    allowed = filtered_video_ids(connection, collection_id, topic_id)
    where, params = "", []
    if allowed is not None:
        if not allowed:
            return {"mode": "browse", "query": "", "results": [],
                    "filters": {"collection": collection_id, "topic": topic_id}}
        marks = ", ".join("?" for _ in allowed)
        where = f"WHERE k.video_id IN ({marks})"
        params = sorted(allowed)
    rows = query(
        connection,
        "WITH numbered AS ("
        "  SELECT k.keyframe_id, k.video_id, k.sample_n, k.pts_time, k.frame_idx,"
        "         ROW_NUMBER() OVER (PARTITION BY k.video_id ORDER BY k.pts_time) AS rn,"
        "         COUNT(*) OVER (PARTITION BY k.video_id) AS n"
        f"  FROM dbo.keyframes k {where}"
        "), picks AS ("
        f"  SELECT j FROM (VALUES {', '.join(f'({j})' for j in range(1, per_video + 1))}) AS p (j)"
        ") "
        "SELECT TOP (?) x.keyframe_id, x.video_id, x.sample_n, x.pts_time, x.frame_idx "
        "FROM numbered x JOIN picks p "
        f"  ON x.rn = CAST(CEILING(x.n * p.j / {per_video + 1.0}) AS INT) "
        "ORDER BY p.j, x.video_id",
        *params, limit,
    )
    seen: set[int] = set()
    rows = [row for row in rows if not (row["keyframe_id"] in seen or seen.add(row["keyframe_id"]))]
    return {
        "mode": "browse",
        "query": "",
        "filters": {"collection": collection_id, "topic": topic_id},
        "results": serialize(rows, video_metadata(connection, [r["video_id"] for r in rows])),
    }


# ----------------------------------------------------------------------
# Abuse protection for the public endpoints (login, registration)
# ----------------------------------------------------------------------

class RateLimiter:
    """Sliding-window counter per key, in process memory.

    Enough for one uvicorn worker; behind several workers put a shared limiter
    (or the reverse proxy's) in front as well.
    """

    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str) -> deque:
        hits = self._hits.setdefault(key, deque())
        cutoff = self.clock() - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._trim(key)) >= self.limit

    def hit(self, key: str) -> None:
        with self._lock:
            self._trim(key).append(self.clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


CSRF_COOKIE = "archive_csrf"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie_token: str | None, form_token: str | None) -> bool:
    if not cookie_token or not form_token:
        return False
    return hmac.compare_digest(cookie_token.encode(), form_token.encode())


def asset_version(path) -> str:
    """Short content hash for cache-busting the built bundle."""
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"
