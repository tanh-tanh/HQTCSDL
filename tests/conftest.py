"""Shared fixtures.  Database tests run against a throwaway `video_archive_test`
database on the configured SQL Server and are skipped when it is unreachable."""

from __future__ import annotations

from dataclasses import replace

import pyodbc
import pytest

from footage.config import load_settings
from footage.db import connect, execute, init_database

TEST_DB = "video_archive_test"


@pytest.fixture(scope="session")
def settings():
    test_settings = replace(load_settings(), db_name=TEST_DB, visual_enabled=False)
    try:
        init_database(test_settings, reset=True, sample=False, progress=lambda _: None)
    except pyodbc.Error as error:
        pytest.skip(f"SQL Server unavailable: {error}")
    yield test_settings
    master = connect(test_settings, admin=True, database=False)
    try:
        execute(master, f"ALTER DATABASE [{TEST_DB}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;")
        execute(master, f"DROP DATABASE [{TEST_DB}];")
    finally:
        master.close()


SEED = [
    "INSERT INTO dbo.organizations (org_code, org_name, org_type) VALUES "
    "('LIB', N'Thư viện thử', 'library'), ('PRESS', N'Báo thử', 'press')",
    "INSERT INTO dbo.collections (collection_code, title, organization_id) "
    "SELECT 'C1', N'Bộ sưu tập thử', organization_id FROM dbo.organizations WHERE org_code = 'LIB'",
    "INSERT INTO dbo.videos (video_id, collection_id, title, fps) "
    "SELECT v.video_id, c.collection_id, v.title, v.fps "
    "FROM (VALUES ('L01_V001', N'Video một', 25), ('L01_V002', N'Video hai', 30)) AS v (video_id, title, fps) "
    "CROSS JOIN dbo.collections c",
    "INSERT INTO dbo.keyframes (keyframe_id, video_id, sample_n, pts_time, frame_idx) VALUES "
    "(0,'L01_V001',1,0.0,0), (1,'L01_V001',2,2.0,50), (2,'L01_V001',3,4.5,112), "
    "(3,'L01_V002',1,0.0,0), (4,'L01_V002',2,3.0,90)",
    "INSERT INTO dbo.ocr_texts (keyframe_id, ocr_text) VALUES "
    "(1, N'Cơm tấm'), (3, N'xe đi to'), (4, N'Bản tin 60 GIÂY')",
    "INSERT INTO dbo.asr_segments (video_id, start_sec, end_sec, asr_text) VALUES "
    "('L01_V001', 3.0, 4.0, N'đi xe'), ('L01_V002', 2.0, 2.5, N'cơm')",
    # The requester of the seeded requests: a journalist of the press member.
    "INSERT INTO dbo.users (username, password_hash, organization_id, user_type) "
    "SELECT 'requester', 'x', organization_id, 'journalist' FROM dbo.organizations WHERE org_code = 'PRESS'",
    "INSERT INTO dbo.requests (request_id, request_text, origin, requested_by) "
    "SELECT v.request_id, v.request_text, v.origin, u.user_id "
    "FROM (VALUES ('q-1', N'bản tin sáu mươi giây', 'sample'), ('q-2', N'xe đạp đỏ', 'user')) "
    "AS v (request_id, request_text, origin) "
    "JOIN dbo.users u ON u.username = 'requester'",
    "INSERT INTO dbo.ground_truth (request_id, video_id, start_sec, end_sec) VALUES ('q-1','L01_V002',3.0,3.0)",
    "EXEC dbo.sp_rebuild_text_index",
]

# Children before parents, so every DELETE satisfies the foreign keys.
TABLES = ["eval_results", "eval_requests", "eval_run_sources", "eval_runs", "search_log_sources",
          "search_log", "audit_log", "usage_licenses", "selections", "ground_truth", "requests",
          "sessions", "user_roles", "users", "ocr_postings", "asr_postings", "terms", "ocr_texts",
          "asr_segments", "keyframes", "video_topics", "videos", "collections", "organizations"]


@pytest.fixture()
def db(settings):
    """A seeded database, emptied again after the test."""
    admin = connect(settings, admin=True)
    for statement in SEED:
        execute(admin, statement)
    yield settings
    for table in TABLES:
        execute(admin, f"DELETE FROM dbo.{table}")
    admin.close()


def admin_connection(settings):
    return connect(settings, admin=True)


def connect_as(settings, database_user: str, user_id: int | None = None):
    """An application-style connection impersonating `database_user`."""
    return connect(replace(settings, app_user=database_user), user_id=user_id)


class FakeVisual:
    """Stands in for SigLIP2: always ranks the same keyframes."""

    ready = True
    status = "fake"

    def __init__(self, ids):
        self.ids = list(ids)

    def search(self, text, k):
        return self.ids[:k]
