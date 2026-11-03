"""The web interface end to end, with a fake visual channel."""

import pytest
from fastapi.testclient import TestClient

from footage import auth
from footage.db import query_one, transaction
from footage.web import create_app
from tests.conftest import FakeVisual


@pytest.fixture()
def users(db):
    ids = {}
    with transaction(db, admin=True) as connection:
        for name, roles in (("reader1", ["reader"]), ("librarian1", ["librarian"]),
                            ("admin1", ["admin"])):
            ids[name] = auth.create_user(connection, name, "password123", roles)
    return ids


@pytest.fixture()
def client(db, tmp_path):
    from dataclasses import replace

    settings = replace(db, keyframes_dir=tmp_path)
    with TestClient(create_app(settings, visual=FakeVisual([2, 4, 0]))) as test_client:
        yield test_client


def login(client, username):
    response = client.post("/login", data={"username": username, "password": "password123"},
                           follow_redirects=False)
    assert response.status_code == 303
    return response


def test_pages_require_login(client):
    response = client.get("/?q=xe", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")
    assert client.get("/img/2", follow_redirects=False).status_code == 303


def test_wrong_password_is_rejected(client, users):
    response = client.post("/login", data={"username": "reader1", "password": "nope-nope"})
    assert response.status_code == 401
    assert "Sai tên đăng nhập" in response.text


def test_session_cookie_is_http_only(client, users):
    cookie = login(client, "reader1").headers["set-cookie"]
    assert "archive_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()


def test_login_does_not_redirect_off_site(client, users):
    response = client.post("/login", data={"username": "reader1", "password": "password123",
                                           "next": "//evil.example/"}, follow_redirects=False)
    assert response.headers["location"] == "/"


def test_search_page_shows_fused_results(client, users):
    login(client, "reader1")
    response = client.get("/", params={"q": "bản tin 60 giây", "src": ["visual", "ocr"], "k": 5})
    assert response.status_code == 200
    assert "/frames/2" in response.text and "/frames/4" in response.text
    assert "visual#1" in response.text


def test_missing_image_falls_back_to_placeholder(client, users):
    login(client, "reader1")
    response = client.get("/img/2")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")


def test_reader_cannot_select_shots(client, users):
    login(client, "reader1")
    response = client.post("/selections", data={"request_id": "q-1", "keyframe_id": 4})
    assert response.status_code == 403
    assert client.get("/admin/users").status_code == 403


def test_librarian_selects_exports_and_removes_shots(client, users, db):
    login(client, "librarian1")
    assert client.post("/selections", data={"request_id": "q-1", "keyframe_id": 4, "note": "ok"},
                       follow_redirects=False).status_code == 303
    page = client.get("/frames/4")
    assert "q-1" in page.text
    csv_text = client.get("/selections/export.csv").content.decode("utf-8-sig")
    assert csv_text.splitlines()[1].startswith("q-1,1,L01_V002,90,")
    with transaction(db, admin=True) as connection:
        selection_id = query_one(connection, "SELECT selection_id FROM dbo.selections")["selection_id"]
    assert client.post(f"/selections/{selection_id}/delete", follow_redirects=False).status_code == 303
    assert client.get("/selections/export.csv").content.decode("utf-8-sig").count("\n") == 1


def test_unknown_query_is_reported_not_crashed(client, users):
    login(client, "librarian1")
    response = client.post("/selections", data={"request_id": "no-such", "keyframe_id": 4})
    assert response.status_code == 200
    assert "không tồn tại" in response.text


def test_admin_manages_users(client, users):
    login(client, "admin1")
    response = client.post("/admin/users", data={
        "username": "newbie", "password": "password123", "roles": ["librarian"]},
        follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/admin/users")
    assert "newbie" in page.text and "role.grant" in page.text
    # An admin cannot lock themselves out.
    response = client.post(f"/admin/users/{users['admin1']}", data={"roles": ["reader"]})
    assert "Không thể tự khoá" in response.text
    # Disabling a user ends their sessions.
    other = TestClient(client.app)
    login(other, "reader1")
    client.post(f"/admin/users/{users['reader1']}", data={"roles": ["reader"]})
    assert other.get("/", follow_redirects=False).status_code == 303


def test_short_password_is_refused(client, users):
    login(client, "admin1")
    response = client.post("/admin/users", data={"username": "shorty", "password": "abc",
                                                 "roles": ["reader"]})
    assert "ít nhất 8 ký tự" in response.text


def test_logout_ends_the_session(client, users):
    login(client, "reader1")
    client.post("/logout")
    assert client.get("/", follow_redirects=False).status_code == 303



def test_search_over_budget_is_cancelled_with_a_message(db, users, tmp_path, monkeypatch):
    from dataclasses import replace

    import footage.web

    def slow_search(connection, *args, **kwargs):
        connection.cursor().execute("WAITFOR DELAY '00:00:03'")

    monkeypatch.setattr(footage.web, "run_search", slow_search)
    settings = replace(db, keyframes_dir=tmp_path, search_timeout=1)
    with TestClient(create_app(settings, visual=FakeVisual([]))) as slow_client:
        login(slow_client, "reader1")
        response = slow_client.get("/", params={"q": "xe"})
    assert response.status_code == 200
    assert "quá thời gian" in response.text


def test_reader_submits_and_sees_only_own_requests(client, users):
    login(client, "reader1")
    response = client.post("/requests", data={"request_text": "tay đua áo vàng", "purpose": "research"},
                           follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/requests")
    assert "YC-U" in page.text and "Chờ xử lý" in page.text
    assert "q-1" not in page.text  # someone else's request
    assert client.post("/requests/q-1/reject").status_code == 403


def test_reader_export_excludes_other_requests(client, users):
    login(client, "librarian1")
    client.post("/selections", data={"request_id": "q-1", "keyframe_id": 4})
    assert "q-1" in client.get("/selections/export.csv").content.decode("utf-8-sig")
    client.post("/logout")
    login(client, "reader1")
    assert client.get("/selections/export.csv").content.decode("utf-8-sig").count("\n") == 1


def test_librarian_grants_a_licence(client, users):
    login(client, "librarian1")
    # No clip chosen yet: the database refuses and the page explains why.
    response = client.post("/requests/q-1/license", data={"valid_days": 90})
    assert response.status_code == 409 and "Chưa chọn đoạn" in response.text
    client.post("/selections", data={"request_id": "q-1", "keyframe_id": 4})
    assert client.post("/requests/q-1/license", data={"valid_days": 90},
                       follow_redirects=False).status_code == 303
    page = client.get("/requests?status=approved")
    assert "q-1" in page.text and "Đã cấp phép" in page.text and "q-2" not in page.text
    # The frame page no longer offers the approved request.
    assert 'value="q-1"' not in client.get("/frames/4").text
