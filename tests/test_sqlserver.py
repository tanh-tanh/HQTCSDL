"""Tables, functions, procedures, views, triggers and permissions on SQL Server."""

import json

import pyodbc
import pytest

from footage import auth
from footage.db import call, connect, error_number, execute, query, query_one, transaction
from footage.evaluate import run_evaluation
from footage.search import run_search
from tests.conftest import FakeVisual, admin_connection, connect_as


def scalar(connection, sql, *params):
    return next(iter(query_one(connection, sql, *params).values()))


def make_user(settings, username, roles):
    with transaction(settings, admin=True) as connection:
        return auth.create_user(connection, username, "password123", roles)


def test_fold_and_tokens(db):
    connection = admin_connection(db)
    assert scalar(connection, "SELECT dbo.fn_fold_vi(N'ĐƯỜNG Hồ Chí Minh') AS v") == "duong ho chi minh"
    terms = [row["term"] for row in query(
        connection, "SELECT term FROM dbo.fn_tokens(N'Một người đi xe đạp, áo ĐỎ; quần đen!') ORDER BY term")]
    # "mot" and "nguoi" are stopwords; colour words survive folding.
    assert terms == ["ao", "dap", "den", "di", "do", "quan", "xe"]
    assert scalar(connection, "SELECT text_folded FROM dbo.ocr_texts WHERE keyframe_id = 4") == "ban tin 60 giay"


def test_text_index_postings(db):
    connection = admin_connection(db)
    rows = query(connection,
                 "SELECT t.term FROM dbo.ocr_postings p JOIN dbo.terms t ON t.term_id = p.term_id "
                 "WHERE p.keyframe_id = 4 ORDER BY t.term")
    assert [row["term"] for row in rows] == ["60", "ban", "giay", "tin"]


def test_nearest_keyframe_uses_both_neighbours(db):
    connection = admin_connection(db)
    assert scalar(connection, "SELECT dbo.fn_nearest_keyframe('L01_V001', 3.2) AS v") == 1
    assert scalar(connection, "SELECT dbo.fn_nearest_keyframe('L01_V001', 3.4) AS v") == 2
    assert scalar(connection, "SELECT dbo.fn_nearest_keyframe('L01_V001', 99) AS v") == 2
    assert scalar(connection, "SELECT dbo.fn_nearest_keyframe('L09_V009', 1) AS v") is None


def test_search_fuses_sources_with_reciprocal_rank(db):
    with transaction(db) as connection:
        rows = call(connection, "sp_search_footage", query="bản tin 60 giây xe cơm",
                    visual=json.dumps([2, 4, 0]), use_ocr=True, use_asr=True, pool=200, top_k=10)
    assert [row["keyframe_id"] for row in rows] == [4, 2, 1, 0, 3]
    by_id = {row["keyframe_id"]: row for row in rows}
    # OCR: four rare words; ASR "cơm" lands nearest 3.0s; visual rank 2.
    assert by_id[4]["evidence"] == "asr#2 ocr#1 visual#2"
    assert float(by_id[4]["rrf_score"]) == pytest.approx(1 / 62 + 1 / 61 + 1 / 62, abs=1e-5)
    # ASR "đi xe" (3.0-4.0s) is nearest to the 4.5s keyframe.
    assert by_id[2]["evidence"] == "asr#1 visual#1"


def test_visual_ids_outside_the_database_are_ignored(db):
    with transaction(db) as connection:
        rows = call(connection, "sp_search_footage", query="", visual="[999, 3]", top_k=10)
    assert [row["keyframe_id"] for row in rows] == [3]


def test_run_search_with_fake_visual(db):
    with transaction(db) as connection:
        outcome = run_search(connection, "bản tin 60 giây", ["visual", "ocr"], 5, 200,
                             FakeVisual([0, 1]))
    assert {row["keyframe_id"] for row in outcome.results} == {0, 1, 4}
    assert outcome.results[0]["keyframe_id"] in {0, 4}


@pytest.mark.parametrize("statement, constraint", [
    ("INSERT INTO dbo.users (username, password_hash) VALUES ('Bad Name', 'x')", "CK_users_username"),
    ("INSERT INTO dbo.videos (video_id, collection_id, title) "
     "SELECT 'nope', collection_id, N'x' FROM dbo.collections", "CK_videos_id"),
    ("INSERT INTO dbo.videos (video_id, collection_id, title) VALUES ('L01_V009', 999, N'x')",
     "FK_videos_collection"),
    ("INSERT INTO dbo.usage_licenses (request_id, granted_by, valid_from, valid_to) "
     "SELECT 'q-1', user_id, '2026-01-02', '2026-01-02' FROM dbo.users WHERE username = 'requester'",
     "CK_usage_licenses_period"),
    ("INSERT INTO dbo.requests (request_id, request_text, origin, requested_by, purpose) "
     "SELECT 'q-9', N'x', 'user', user_id, 'resale' FROM dbo.users WHERE username = 'requester'",
     "CK_requests_purpose"),
    ("INSERT INTO dbo.topics (topic_name, parent_topic_id) VALUES (N'mồ côi', 999)", "FK_topics_parent"),
    ("DELETE FROM dbo.users WHERE username = 'requester'", "FK_requests_user"),
    ("INSERT INTO dbo.asr_segments (video_id, start_sec, end_sec, asr_text) "
     "VALUES ('L01_V001', 5, 4, N'x')", "CK_asr_segments_span"),
    ("INSERT INTO dbo.keyframes VALUES (9, 'L09_V009', 1, 0, 0)", "FK_keyframes_video"),
    ("INSERT INTO dbo.keyframes VALUES (9, 'L01_V001', 1, 7, 7)", "UQ_keyframes_video_n"),
])
def test_constraints_reject_bad_rows(db, statement, constraint):
    connection = admin_connection(db)
    with pytest.raises(pyodbc.Error) as error:
        execute(connection, statement)
    assert constraint in str(error.value)


def test_defaults_fill_omitted_columns(db):
    connection = admin_connection(db)
    execute(connection, "INSERT INTO dbo.users (username, password_hash) VALUES ('defaults', 'x')")
    row = query_one(connection, "SELECT full_name, is_active, created_at FROM dbo.users "
                                "WHERE username = 'defaults'")
    assert row["full_name"] == "" and row["is_active"] is True and row["created_at"] is not None


def test_selection_procedures_enforce_permission_and_audit(db):
    reader = make_user(db, "reader1", ["reader"])
    librarian = make_user(db, "librarian1", ["librarian"])
    with transaction(db, user_id=reader) as connection:
        with pytest.raises(pyodbc.Error) as error:
            call(connection, "sp_add_selection", request_id="q-1", keyframe_id=4)
        assert error_number(error.value) == 50001
    with transaction(db, user_id=librarian) as connection:
        call(connection, "sp_add_selection", request_id="q-1", keyframe_id=4, note="first")
        call(connection, "sp_add_selection", request_id="q-1", keyframe_id=4, note="edited")
        selection = query_one(connection, "SELECT selection_id, note FROM dbo.selections WHERE request_id = 'q-1'")
        assert selection["note"] == "edited"
        call(connection, "sp_remove_selection", selection_id=selection["selection_id"])
        rows = query(connection, "SELECT user_id, action FROM dbo.audit_log "
                                 "WHERE action LIKE 'selection.%' ORDER BY log_id")
    assert rows == [{"user_id": librarian, "action": "selection.add"},
                    {"user_id": librarian, "action": "selection.remove"}]


def test_session_context_cannot_be_changed_by_the_app(db):
    reader = make_user(db, "reader2", ["reader"])
    librarian = make_user(db, "librarian2", ["librarian"])
    with transaction(db, user_id=reader) as connection:
        with pytest.raises(pyodbc.Error):
            execute(connection, "EXEC sp_set_session_context N'app_user_id', ?;", librarian)


def test_create_user_is_atomic(db):
    connection = admin_connection(db)
    with pytest.raises(pyodbc.Error) as error:
        call(connection, "sp_create_user", username="ghost", password_hash="x", roles="reader,wizard")
    assert error_number(error.value) == 50002
    assert scalar(connection, "SELECT COUNT(*) AS n FROM dbo.users WHERE username = 'ghost'") == 0


def test_role_changes_and_deactivation_are_audited(db):
    admin = make_user(db, "admin1", ["admin"])
    target = make_user(db, "someone", ["reader"])
    with transaction(db, user_id=admin) as connection:
        auth.set_roles(connection, target, ["librarian"])
        execute(connection, "UPDATE dbo.users SET is_active = 0 WHERE user_id = ?", target)
        actions = [row["action"] for row in query(
            connection, "SELECT action FROM dbo.audit_log WHERE user_id = ? ORDER BY log_id", admin)]
        assert sorted(actions) == ["role.grant", "role.revoke", "user.disable"]
        assert query(connection, "SELECT * FROM dbo.v_user_permissions WHERE user_id = ?", target) == []


def test_view_with_check_option_rejects_rows_leaving_the_view(db):
    make_user(db, "active1", ["reader"])
    connection = admin_connection(db)
    with pytest.raises(pyodbc.Error) as error:
        execute(connection, "UPDATE dbo.v_active_users SET is_active = 0 WHERE username = 'active1'")
    assert error_number(error.value) == 550


def test_progress_report_output_parameters(db):
    connection = admin_connection(db)
    cursor = connection.cursor()
    cursor.execute(
        "DECLARE @t INT, @a INT, @p DECIMAL(5,2);"
        "EXEC dbo.sp_progress_report @total = @t OUTPUT, @selected = @a OUTPUT, @percent_done = @p OUTPUT;"
        "SELECT @t AS total, @a AS selected, @p AS percent_done;"
    )
    breakdown = cursor.fetchall()
    cursor.nextset()
    total, selected, percent = cursor.fetchone()
    assert {row[0] for row in breakdown} == {"sample", "user"}
    assert (total, selected, float(percent)) == (2, 0, 0.0)


def test_sessions_expire_and_store_only_hashes(db):
    user_id = make_user(db, "session1", ["reader"])
    with transaction(db) as connection:
        token = auth.create_session(connection, user_id, hours=1)
        assert auth.session_user(connection, token).username == "session1"
        assert scalar(connection, "SELECT COUNT(*) AS n FROM dbo.sessions WHERE session_hash = ?", token) == 0
    admin = admin_connection(db)
    execute(admin, "UPDATE dbo.sessions SET created_at = DATEADD(HOUR, -2, created_at), "
                   "expires_at = DATEADD(SECOND, -1, SYSDATETIME())")
    with transaction(db) as connection:
        assert auth.session_user(connection, token) is None


def test_authenticate(db):
    make_user(db, "login1", ["reader"])
    with transaction(db) as connection:
        assert auth.authenticate(connection, "LOGIN1 ", "password123").can("search.run")
        assert auth.authenticate(connection, "login1", "wrong-password") is None
        assert auth.authenticate(connection, "nobody", "password123") is None


def test_report_user_is_read_only(db):
    connection = connect_as(db, "archive_report")
    try:
        assert scalar(connection, "SELECT COUNT(*) AS n FROM dbo.keyframes") == 5
        assert call(connection, "sp_search_footage", query="xe", top_k=3)
        for statement in ("INSERT INTO dbo.audit_log (action) VALUES ('hack')",
                          "EXEC dbo.sp_add_selection @request_id = 'q-1', @keyframe_id = 4",
                          "CREATE TABLE dbo.hack (x INT)"):
            with pytest.raises(pyodbc.Error) as error:
                execute(connection, statement)
            assert error_number(error.value) in (229, 262)
    finally:
        connection.rollback()
        connection.close()


def test_app_user_cannot_change_schema_or_corpus(db):
    connection = connect(db)
    try:
        for statement in ("DELETE FROM dbo.keyframes", "INSERT INTO dbo.selections (request_id, keyframe_id) "
                          "VALUES ('q-1', 4)", "DROP TABLE dbo.selections",
                          "ALTER TABLE dbo.users ADD x INT"):
            with pytest.raises(pyodbc.Error):
                execute(connection, statement)
            connection.rollback()
    finally:
        connection.close()


def test_evaluation_views(db):
    connection = connect(db)
    try:
        run_id = run_evaluation(connection, "test", ["visual", "ocr"], 10, 200, 0.5,
                                FakeVisual([0, 4]), progress=lambda _: None)
        first = query_one(connection, "SELECT first_hit FROM dbo.v_eval_first_hit WHERE run_id = ?", run_id)
        summary = query_one(connection, "SELECT * FROM dbo.v_eval_summary WHERE run_id = ?", run_id)
    finally:
        connection.close()
    # q-1 is labelled L01_V002 @ 3.0s = keyframe 4, ranked first by visual and OCR.
    assert first["first_hit"] == 1
    assert summary["requests"] == 1 and summary["sources"] == "ocr,visual"
    assert float(summary["recall_at_1"]) == 1.0 and float(summary["mrr"]) == 1.0


def test_request_workflow_from_submission_to_licence(db):
    reader = make_user(db, "reader3", ["reader"])
    librarian = make_user(db, "librarian3", ["librarian"])
    with transaction(db, admin=True) as connection:
        execute(connection, "UPDATE dbo.users SET organization_id = (SELECT organization_id "
                            "FROM dbo.organizations WHERE org_code = 'PRESS') WHERE user_id = ?", reader)
    with transaction(db, user_id=reader) as connection:
        request_id = call(connection, "sp_submit_request", request_text="  tay đua về đích  ",
                          purpose="broadcast")[0]["request_id"]
    # A reader may ask, but not choose clips or decide.  (sp_grant_license runs
    # with XACT_ABORT ON, so its error rolls back the caller's transaction too:
    # each attempt gets its own.)
    for procedure, params in (("sp_add_selection", {"request_id": request_id, "keyframe_id": 1}),
                              ("sp_grant_license", {"request_id": request_id})):
        with pytest.raises(pyodbc.Error) as error:
            with transaction(db, user_id=reader) as connection:
                call(connection, procedure, **params)
        assert error_number(error.value) == 50001
    assert request_id.startswith("YC-U") and len(request_id) == 8
    with transaction(db, user_id=librarian) as connection:
        with pytest.raises(pyodbc.Error) as error:
            call(connection, "sp_grant_license", request_id=request_id)
        assert error_number(error.value) == 50006
    with transaction(db, user_id=librarian) as connection:
        call(connection, "sp_add_selection", request_id=request_id, keyframe_id=1)
        license_id = call(connection, "sp_grant_license", request_id=request_id,
                          valid_days=90)[0]["license_id"]
    # Once approved, the request and its clips are frozen.
    for procedure, params in (("sp_grant_license", {"request_id": request_id}),
                              ("sp_reject_request", {"request_id": request_id}),
                              ("sp_add_selection", {"request_id": request_id, "keyframe_id": 2})):
        with pytest.raises(pyodbc.Error) as error:
            with transaction(db, user_id=librarian) as connection:
                call(connection, procedure, **params)
        assert error_number(error.value) == 50005
    connection = admin_connection(db)
    row = query_one(connection, "SELECT * FROM dbo.v_license_overview WHERE license_id = ?", license_id)
    assert row["request_id"] == request_id and row["licensee"] == "Báo thử"
    assert row["is_active"] is True and row["days_left"] == 90 and row["clip_count"] == 1
    request = query_one(connection, "SELECT request_text, status, purpose FROM dbo.requests "
                                    "WHERE request_id = ?", request_id)
    assert request == {"request_text": "tay đua về đích", "status": "approved", "purpose": "broadcast"}
    assert scalar(connection, "SELECT detail FROM dbo.audit_log WHERE action = 'request.approve'") == \
        f"{request_id}: pending -> approved"
    # A licence is a record of its own: the request cannot be deleted under it.
    with pytest.raises(pyodbc.Error) as error:
        execute(connection, "DELETE FROM dbo.requests WHERE request_id = ?", request_id)
    assert "FK_usage_licenses_request" in str(error.value)


def test_reject_request(db):
    librarian = make_user(db, "librarian4", ["librarian"])
    with transaction(db, user_id=librarian) as connection:
        call(connection, "sp_reject_request", request_id="q-2")
        for request_id, number in (("q-2", 50005), ("no-such", 50004)):
            with pytest.raises(pyodbc.Error) as error:
                call(connection, "sp_reject_request", request_id=request_id)
            assert error_number(error.value) == number
    connection = admin_connection(db)
    assert scalar(connection, "SELECT status FROM dbo.requests WHERE request_id = 'q-2'") == "rejected"
    assert scalar(connection, "SELECT COUNT(*) AS n FROM dbo.usage_licenses") == 0


def test_topic_subtree_walks_the_hierarchy(db):
    connection = admin_connection(db)
    rows = query(connection, "SELECT topic_name, depth, path FROM dbo.fn_topic_subtree("
                             "(SELECT topic_id FROM dbo.topics WHERE topic_name = N'Đua xe đạp'))")
    assert len(rows) == 13 and max(row["depth"] for row in rows) == 2
    assert "Đua xe đạp > Diễn biến cuộc đua > Nước rút" in {row["path"] for row in rows}


def test_catalogue_views(db):
    connection = admin_connection(db)
    execute(connection, "INSERT INTO dbo.video_topics (video_id, topic_id) "
                        "SELECT 'L01_V001', topic_id FROM dbo.topics WHERE topic_name IN (N'Nước rút', N'Về đích')")
    row = query_one(connection, "SELECT * FROM dbo.v_video_catalog WHERE video_id = 'L01_V001'")
    assert row["owner"] == "Thư viện thử" and row["topics"] == "Nước rút, Về đích"
    stats = query_one(connection, "SELECT * FROM dbo.v_collection_stats WHERE collection_code = 'C1'")
    assert (stats["videos"], stats["keyframes"], stats["topics"]) == (2, 5, 2)
