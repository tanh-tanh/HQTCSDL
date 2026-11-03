"""Server-rendered web interface: login, search, frame detail, access requests, admin."""

from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import pyodbc
from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from footage import auth
from footage.config import Settings, load_settings
from footage.db import call, connect, error_number, execute, query, query_one, set_actor
from footage.search import SOURCE_LABELS, SOURCES, log_search, run_search

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
NEIGHBOURS = 6
PERMISSION_DENIED = 50001  # THROW number used by the stored procedures
# Other THROW numbers of the request workflow (05_procedures.sql).
WORKFLOW_ERRORS = {
    50004: "Yêu cầu khai thác không tồn tại.",
    50005: "Yêu cầu đã được duyệt hoặc từ chối nên không thay đổi được nữa.",
    50006: "Chưa chọn đoạn tư liệu nào cho yêu cầu này nên chưa cấp phép được.",
}
FOREIGN_KEY_VIOLATION = 547
DUPLICATE_KEY = (2601, 2627)
PURPOSES = {"broadcast": "Phát sóng", "research": "Nghiên cứu", "education": "Giảng dạy",
            "exhibition": "Trưng bày"}
STATUSES = {"pending": "Chờ xử lý", "approved": "Đã cấp phép", "rejected": "Từ chối"}
LICENSE_DAYS = (30, 90, 180, 365)
USER_TYPES = {"staff": "Cán bộ", "journalist": "Nhà báo", "researcher": "Nhà nghiên cứu",
              "student": "Sinh viên", "other": "Khác"}


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def image_path(video_id: str, sample_n: int) -> str:
    """Where frames.csv puts a keyframe: three-digit, zero-padded sample number."""
    return f"keyframes/keyframes/{video_id}/{sample_n:03d}.jpg"


def resolve_image(keyframes_dir: Path, video_id: str, sample_n: int) -> Path | None:
    """Find a keyframe image across the folder layouts the archive has used."""
    relative = Path(image_path(video_id, sample_n))
    name = relative.name
    batch = video_id.split("_", 1)[0]
    candidates = [
        keyframes_dir / batch / relative,  # L23.zip unpacked: L23/keyframes/keyframes/<video>/
        keyframes_dir / relative,
        keyframes_dir / video_id / name,
        keyframes_dir / batch / video_id / name,
    ]
    return next((path for path in candidates if path.is_file()), None)


def create_app(settings: Settings | None = None, visual=None) -> FastAPI:
    settings = settings or load_settings()

    def start_visual() -> None:
        if app.state.visual is not None or not settings.visual_enabled:
            return
        if not settings.siglip_index.is_file():
            log.warning("visual search disabled: %s not found", settings.siglip_index)
            return
        from footage.visual import VisualSearcher

        searcher = VisualSearcher(settings.siglip_model, settings.siglip_index)
        connection = connect(settings)
        try:
            ids = [row["keyframe_id"] for row in query(connection, "SELECT keyframe_id FROM dbo.keyframes")]
        finally:
            connection.close()
        searcher.start(ids)
        app.state.visual = searcher

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        start_visual()
        yield

    app = FastAPI(title="Audiovisual library", docs_url=None, redoc_url=None, lifespan=lifespan)
    templates = Jinja2Templates(directory=HERE / "templates")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.state.visual = visual

    # ------------------------------------------------------------------
    # Per-request database connection and the signed-in user
    # ------------------------------------------------------------------

    def database():
        connection = connect(settings)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def current_user(request: Request, connection=Depends(database)) -> auth.User:
        user = auth.session_user(connection, request.cookies.get(auth.SESSION_COOKIE))
        if user is None:
            raise LoginRequired()
        set_actor(connection, user.user_id)
        return user

    def require(permission: str):
        def check(user: auth.User = Depends(current_user)) -> auth.User:
            if not user.can(permission):
                raise Forbidden()
            return user
        return check

    @app.exception_handler(LoginRequired)
    def to_login(request: Request, _: LoginRequired):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse("/login?" + urlencode({"next": target}), status_code=303)

    @app.exception_handler(Forbidden)
    def forbidden(request: Request, _: Forbidden):
        return templates.TemplateResponse(
            request, "message.html",
            {"user": None, "title": "Không có quyền",
             "message": "Tài khoản của bạn không có quyền thực hiện thao tác này."},
            status_code=403,
        )

    @app.exception_handler(pyodbc.Error)
    def database_error(request: Request, exc: pyodbc.Error):
        # THROW 50001 in a procedure: the database refused the permission.
        number = error_number(exc)
        if number == PERMISSION_DENIED:
            return forbidden(request, Forbidden())
        if number in WORKFLOW_ERRORS:
            return templates.TemplateResponse(
                request, "message.html",
                {"user": None, "title": "Không thực hiện được", "message": WORKFLOW_ERRORS[number]},
                status_code=409,
            )
        raise exc

    def render(request: Request, name: str, user: auth.User | None, **context):
        context.update(user=user, visual=app.state.visual, settings=settings,
                       purposes=PURPOSES, statuses=STATUSES, user_types=USER_TYPES)
        return templates.TemplateResponse(request, name, context)

    def back(request: Request, fallback: str) -> str:
        referer = request.headers.get("referer", "")
        host = request.headers.get("host", "")
        # Only bounce back to this site.
        return referer if host and f"//{host}/" in referer else fallback

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return render(request, "login.html", None, next=next, error=None)

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...),
              next: str = Form("/"), connection=Depends(database)):
        user = auth.authenticate(connection, username, password)
        if user is None:
            return templates.TemplateResponse(
                request, "login.html",
                {"user": None, "next": next, "error": "Sai tên đăng nhập hoặc mật khẩu."},
                status_code=401,
            )
        token = auth.create_session(connection, user.user_id, settings.session_hours)
        execute(connection,
                "INSERT INTO dbo.audit_log (user_id, action, detail) VALUES (?, 'login', ?)",
                user.user_id, request.client.host if request.client else "")
        # Only local paths, so a crafted link cannot bounce to another site.
        target = next if next.startswith("/") and not next.startswith("//") else "/"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            auth.SESSION_COOKIE, token, max_age=settings.session_hours * 3600,
            httponly=True, samesite="lax", secure=settings.secure_cookies,
        )
        return response

    @app.post("/logout")
    def logout(request: Request, connection=Depends(database)):
        auth.end_session(connection, request.cookies.get(auth.SESSION_COOKIE))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(auth.SESSION_COOKIE)
        return response

    # ------------------------------------------------------------------
    # Search and inspection
    # ------------------------------------------------------------------

    def request_list(connection) -> list[dict]:
        return query(
            connection,
            "SELECT request_id, origin, request_text, status, selection_count, has_ground_truth "
            "FROM dbo.v_request_progress "
            "ORDER BY CASE origin WHEN 'user' THEN 0 ELSE 1 END, request_id",
        )

    @app.get("/", response_class=HTMLResponse)
    def search_page(request: Request, q: str = "", request_id: str = "", k: int = 50,
                    src: list[str] | None = Query(None),
                    user: auth.User = Depends(require("search.run")),
                    connection=Depends(database)):
        queries = request_list(connection)
        if request_id and not q:
            q = next((row["request_text"] for row in queries if row["request_id"] == request_id), "")
        chosen = [name for name in (src or SOURCES) if name in SOURCES]
        top_k = max(1, min(k, 200))
        outcome, error = None, None
        if q.strip():
            try:
                outcome = run_search(connection, q, chosen, top_k, settings.search_pool,
                                     app.state.visual)
                log_search(connection, user.user_id, q, outcome, top_k)
            except pyodbc.OperationalError as exc:
                # ODBC cancelled the statement on the server (HYT00 = timeout).
                connection.rollback()
                reason = "quá thời gian" if "HYT00" in str(exc) else "lỗi"
                error = f"Truy vấn bị huỷ ({reason}, giới hạn {settings.search_timeout}s)."
        return render(request, "search.html", user, q=q, request_id=request_id, k=top_k,
                      chosen=chosen, sources=SOURCES, labels=SOURCE_LABELS,
                      queries=queries, outcome=outcome, error=error)

    @app.get("/frames/{keyframe_id}", response_class=HTMLResponse)
    def frame_page(request: Request, keyframe_id: int, request_id: str = "",
                   user: auth.User = Depends(require("search.run")),
                   connection=Depends(database)):
        frame = query_one(
            connection,
            "SELECT k.keyframe_id, k.video_id, k.sample_n, k.pts_time, k.frame_idx, "
            "       v.fps, v.batch, o.ocr_text, c.title, c.collection_title, c.owner, "
            "       c.access_level, c.topics "
            "FROM dbo.keyframes k JOIN dbo.videos v ON v.video_id = k.video_id "
            "JOIN dbo.v_video_catalog c ON c.video_id = k.video_id "
            "LEFT JOIN dbo.ocr_texts o ON o.keyframe_id = k.keyframe_id "
            "WHERE k.keyframe_id = ?",
            keyframe_id,
        )
        if frame is None:
            return render(request, "message.html", user, title="Không tìm thấy",
                          message=f"Không có keyframe {keyframe_id}.")
        # NEIGHBOURS frames on each side, read through UQ_keyframes_video_time.
        neighbours = query(
            connection,
            "SELECT keyframe_id, pts_time FROM ("
            "  SELECT TOP (?) keyframe_id, pts_time FROM dbo.keyframes "
            "  WHERE video_id = ? AND pts_time < ? ORDER BY pts_time DESC) AS before_frames "
            "UNION ALL "
            "SELECT keyframe_id, pts_time FROM ("
            "  SELECT TOP (?) keyframe_id, pts_time FROM dbo.keyframes "
            "  WHERE video_id = ? AND pts_time >= ? ORDER BY pts_time) AS after_frames "
            "ORDER BY pts_time",
            NEIGHBOURS, frame["video_id"], frame["pts_time"],
            NEIGHBOURS + 1, frame["video_id"], frame["pts_time"],
        )
        speech = query(
            connection,
            "SELECT start_sec, end_sec, asr_text FROM dbo.asr_segments "
            "WHERE video_id = ? AND end_sec >= ? - 15 AND start_sec <= ? + 15 ORDER BY start_sec",
            frame["video_id"], frame["pts_time"], frame["pts_time"],
        )
        chosen_for = query(
            connection,
            "SELECT selection_id, request_id, selected_by, created_at FROM dbo.v_selection_export "
            "WHERE keyframe_id = ? ORDER BY request_id",
            keyframe_id,
        )
        # Clips can only be added to requests that are still pending.
        pending = [row for row in request_list(connection) if row["status"] == "pending"]
        return render(request, "frame.html", user, frame=frame, neighbours=neighbours,
                      speech=speech, chosen_for=chosen_for, queries=pending,
                      request_id=request_id)

    @app.get("/img/{keyframe_id}")
    def image(keyframe_id: int, user: auth.User = Depends(require("search.run")),
              connection=Depends(database)):
        row = query_one(connection,
                        "SELECT video_id, sample_n FROM dbo.keyframes WHERE keyframe_id = ?",
                        keyframe_id)
        path = resolve_image(settings.keyframes_dir, row["video_id"], row["sample_n"]) if row else None
        if path is None:
            return FileResponse(HERE / "static" / "missing.svg", media_type="image/svg+xml")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    # ------------------------------------------------------------------
    # Access requests: submit, choose clips, grant a licence or reject
    # ------------------------------------------------------------------

    def requests_context(connection, user: auth.User, status: str = "") -> dict:
        # Staff who decide on requests see all of them; a reader sees their own.
        everyone = user.can("license.grant")
        mine = "" if everyone else " WHERE requested_by = ?"
        params = [] if everyone else [user.username]
        counts = {row["status"]: row["n"] for row in query(
            connection, f"SELECT status, COUNT(*) AS n FROM dbo.v_request_progress{mine} GROUP BY status",
            *params)}
        status = status if status in STATUSES else ""
        if status:
            mine += (" AND" if mine else " WHERE") + " status = ?"
            params.append(status)
        progress = query(
            connection,
            f"SELECT * FROM dbo.v_request_progress{mine} "
            "ORDER BY CASE origin WHEN 'user' THEN 0 ELSE 1 END, request_id",
            *params,
        )
        shown = {row["request_id"]: row["status"] == "pending" for row in progress}
        grouped: dict[str, list[dict]] = {}
        for row in query(connection, "SELECT * FROM dbo.v_selection_export ORDER BY request_id, rank_no"):
            if row["request_id"] in shown:
                grouped.setdefault(row["request_id"], []).append(row)
        return {"progress": progress, "grouped": grouped, "editable": shown, "counts": counts,
                "status": status, "all_requests": everyone}

    @app.get("/requests", response_class=HTMLResponse)
    def requests_page(request: Request, status: str = "",
                      user: auth.User = Depends(require("search.run")),
                      connection=Depends(database)):
        return render(request, "requests.html", user, error=None,
                      **requests_context(connection, user, status))

    @app.post("/requests")
    def submit_request(request: Request, request_text: str = Form(...),
                       purpose: str = Form("research"),
                       user: auth.User = Depends(require("request.submit")),
                       connection=Depends(database)):
        if not request_text.strip() or purpose not in PURPOSES:
            return render(request, "requests.html", user,
                          error="Cần mô tả đoạn tư liệu và chọn một mục đích sử dụng.",
                          **requests_context(connection, user))
        call(connection, "sp_submit_request", request_text=request_text.strip()[:2000], purpose=purpose)
        return RedirectResponse("/requests", status_code=303)

    @app.post("/requests/{request_id}/license")
    def grant_license(request: Request, request_id: str, valid_days: int = Form(365),
                      user: auth.User = Depends(require("license.grant")),
                      connection=Depends(database)):
        if valid_days not in LICENSE_DAYS:
            valid_days = 365
        call(connection, "sp_grant_license", request_id=request_id, valid_days=valid_days)
        return RedirectResponse(back(request, "/requests"), status_code=303)

    @app.post("/requests/{request_id}/reject")
    def reject_request(request: Request, request_id: str,
                       user: auth.User = Depends(require("license.grant")),
                       connection=Depends(database)):
        call(connection, "sp_reject_request", request_id=request_id)
        return RedirectResponse(back(request, "/requests"), status_code=303)

    @app.post("/selections")
    def add_selection(request: Request, request_id: str = Form(...), keyframe_id: int = Form(...),
                   note: str = Form(""), user: auth.User = Depends(require("selection.write")),
                   connection=Depends(database)):
        try:
            call(connection, "sp_add_selection", request_id=request_id, keyframe_id=keyframe_id,
                 note=note[:255])
        except pyodbc.IntegrityError as exc:
            if error_number(exc) != FOREIGN_KEY_VIOLATION:
                raise
            connection.rollback()
            return render(request, "message.html", user, title="Không lưu được",
                          message="Yêu cầu khai thác hoặc khung hình không tồn tại.")
        return RedirectResponse(back(request, f"/frames/{keyframe_id}"), status_code=303)

    @app.post("/selections/{selection_id}/delete")
    def remove_selection(request: Request, selection_id: int,
                      user: auth.User = Depends(require("selection.write")),
                      connection=Depends(database)):
        call(connection, "sp_remove_selection", selection_id=selection_id)
        return RedirectResponse(back(request, "/requests"), status_code=303)

    @app.get("/selections")
    def selections_page():
        return RedirectResponse("/requests", status_code=303)

    @app.get("/selections/export.csv")
    def export_selections(user: auth.User = Depends(require("search.run")),
                       connection=Depends(database)):
        # Same scope as the requests page: a reader exports their own requests only.
        mine, params = "", []
        if not user.can("license.grant"):
            mine = (" WHERE request_id IN (SELECT request_id FROM dbo.v_request_progress "
                    "WHERE requested_by = ?)")
            params.append(user.username)
        rows = query(
            connection,
            "SELECT request_id, rank_no, video_id, frame_idx, pts_time, selected_by "
            f"FROM dbo.v_selection_export{mine} ORDER BY request_id, rank_no",
            *params,
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["request_id", "rank", "video_id", "frame_idx", "pts_time", "selected_by"])
        for row in rows:
            writer.writerow([row["request_id"], row["rank_no"], row["video_id"],
                             row["frame_idx"], row["pts_time"], row["selected_by"] or ""])
        return Response(
            buffer.getvalue().encode("utf-8-sig"), media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="selections.csv"'},
        )

    # ------------------------------------------------------------------
    # Administration
    # ------------------------------------------------------------------

    def users_context(connection) -> dict:
        return {
            "users": query(connection, "SELECT * FROM dbo.v_user_overview ORDER BY username"),
            "roles": query(connection, "SELECT role_name, description FROM dbo.roles ORDER BY role_id"),
            "organizations": query(connection, "SELECT organization_id, org_name FROM dbo.organizations "
                                               "ORDER BY organization_id"),
            "audit": query(
                connection,
                "SELECT TOP (30) a.created_at, u.username, a.action, a.detail FROM dbo.audit_log a "
                "LEFT JOIN dbo.users u ON u.user_id = a.user_id ORDER BY a.log_id DESC",
            ),
        }

    @app.get("/admin/users", response_class=HTMLResponse)
    def users_page(request: Request, user: auth.User = Depends(require("user.manage")),
                   connection=Depends(database)):
        return render(request, "users.html", user, error=None, **users_context(connection))

    @app.post("/admin/users")
    def create_user(request: Request, username: str = Form(...), password: str = Form(...),
                    full_name: str = Form(""), roles: list[str] = Form([]),
                    organization_id: str = Form(""), user_type: str = Form("other"),
                    user: auth.User = Depends(require("user.manage")),
                    connection=Depends(database)):
        try:
            auth.create_user(connection, username, password, roles, full_name,
                             int(organization_id) if organization_id.isdigit() else None,
                             user_type if user_type in USER_TYPES else "other")
        except (ValueError, pyodbc.IntegrityError, pyodbc.ProgrammingError) as exc:
            connection.rollback()
            number = error_number(exc) if isinstance(exc, pyodbc.Error) else None
            if number in DUPLICATE_KEY or number == FOREIGN_KEY_VIOLATION:
                message = "Tên đăng nhập đã tồn tại hoặc không hợp lệ (3–32 ký tự a-z, 0-9, _ . -)."
            elif number in (50002, 50003):
                message = "Vai trò không hợp lệ: chọn ít nhất một vai trò có sẵn."
            elif isinstance(exc, ValueError):
                message = str(exc)
            else:
                raise
            return render(request, "users.html", user, error=message, **users_context(connection))
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}")
    def update_user(request: Request, user_id: int, roles: list[str] = Form([]),
                    is_active: str = Form(""), password: str = Form(""),
                    user: auth.User = Depends(require("user.manage")),
                    connection=Depends(database)):
        if user_id == user.user_id and (not is_active or "admin" not in roles):
            return render(request, "users.html", user,
                          error="Không thể tự khoá hoặc tự bỏ quyền admin của chính mình.",
                          **users_context(connection))
        try:
            execute(connection, "UPDATE dbo.users SET is_active = ? WHERE user_id = ?",
                    bool(is_active), user_id)
            auth.set_roles(connection, user_id, roles)
            if not is_active:
                execute(connection, "DELETE FROM dbo.sessions WHERE user_id = ?", user_id)
            if password:
                auth.set_password(connection, user_id, password)
        except ValueError as exc:
            connection.rollback()
            return render(request, "users.html", user, error=str(exc), **users_context(connection))
        return RedirectResponse("/admin/users", status_code=303)

    @app.get("/eval", response_class=HTMLResponse)
    def eval_page(request: Request, user: auth.User = Depends(require("eval.view")),
                  connection=Depends(database)):
        runs = query(connection, "SELECT * FROM dbo.v_eval_summary ORDER BY run_id DESC")
        searches = query_one(
            connection,
            "SELECT COUNT(*) AS searches, AVG(latency_ms) AS avg_ms, MAX(latency_ms) AS max_ms "
            "FROM dbo.search_log",
        )
        return render(request, "eval.html", user, runs=runs, searches=searches)

    @app.get("/healthz")
    def health():
        visual_status = app.state.visual.status if app.state.visual else "disabled"
        return {"ok": True, "visual": visual_status}

    return app
