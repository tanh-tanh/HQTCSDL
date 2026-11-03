"""Generate db/09_sample_data.sql from the archive's source files.

    frames.csv              -> videos, keyframes   (frame_id = FAISS id)
    map-keyframes/*.csv     -> videos.fps
    ocr.sqlite              -> ocr_texts           (SQLite FTS5 export)
    asr.sqlite              -> asr_segments
    labels/l23_requests.csv -> requests, ground_truth (sample access requests
                               whose correct moment was verified by hand)

The library catalogue around the footage (member organisations, the empty
collections of the other members, readers, request status and licences) is
illustrative: the names say "mẫu" and nothing in it describes a real body.
Topics are tagged by rules over the real transcripts, so every tag can be
traced back to the words that produced it.

The output is plain INSERT statements, so the sample data loads in SSMS or
sqlcmd without Python.  Only the chosen batches are exported (L23 by default).
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Iterable

from footage.auth import hash_password

MAX_ROWS = 1000  # SQL Server's limit for one INSERT ... VALUES list
TEXT_LIMIT = 2000

# (code, name, type).  Only the operating library holds footage; the other
# members are placeholders that show how the catalogue scales.
ORGANIZATIONS = (
    ("LIB", "Thư viện số tư liệu nghe nhìn (đơn vị vận hành)", "library"),
    ("TV-A", "Đài truyền hình mẫu A", "tv"),
    ("PRESS-B", "Báo điện tử mẫu B", "press"),
    ("UNI-C", "Trường đại học mẫu C", "school"),
    ("MUS-D", "Bảo tàng mẫu D", "museum"),
)
# (code, title, organisation, access level, description)
FOOTAGE_COLLECTION = "DXD"
COLLECTIONS = (
    (FOOTAGE_COLLECTION, "Tường thuật đua xe đạp truyền hình", "LIB", "restricted",
     "Lô L23 của một bộ dữ liệu video tiếng Việt công khai: các đoạn tường thuật một giải "
     "đua xe đạp. Khung hình, chữ trên hình (OCR) và lời nói (ASR) do nhóm trích xuất."),
    ("TV-A-TS", "Bản tin thời sự lưu trữ (minh hoạ, chưa nạp video)", "TV-A", "restricted", ""),
    ("PRESS-B-PS", "Phóng sự video (minh hoạ, chưa nạp video)", "PRESS-B", "restricted", ""),
    ("UNI-C-BG", "Bài giảng video (minh hoạ, chưa nạp video)", "UNI-C", "open", ""),
    ("MUS-D-HV", "Hiện vật số hoá (minh hoạ, chưa nạp video)", "MUS-D", "open", ""),
)
# A video gets a topic when at least one of its speech segments (folded to
# ASCII) matches one of the patterns.  Topic names come from 07_seed.sql.
TOPIC_RULES = (
    ("Đua xe đạp", ("%chang dua%", "%vong dua%", "%tay dua%")),
    ("Nước rút", ("%nuoc rut%",)),
    ("Tấn công và bứt phá", ("%tan cong%", "%but pha%")),
    ("Chiến thuật đồng đội", ("%may keo%", "%chien thuat%")),
    ("Về đích", ("%ve dich%", "%vach dich%")),
    ("Áo vàng", ("%ao vang%",)),
    ("Vĩnh Long", ("%vinh long%",)),
    ("TP. Hồ Chí Minh", ("%ho chi minh%",)),
    ("An Giang", ("%an giang%",)),
    ("Đồng Tháp", ("%dong thap%",)),
    ("Đồng Nai", ("%dong nai%",)),
    ("Quay flycam", ("%flycam%",)),
    ("Đồ hoạ theo dõi chuyển động", ("%cong nghe theo doi%", "%theo doi chuyen dong%")),
)

# Demo accounts for the class demo; change the passwords before any real use.
# (username, full name, role, password, organisation, reader type)
LIBRARIAN = "thuthu1"
DEMO_USERS = (
    ("admin", "Quản trị thư viện số", "admin", "Admin@123", "LIB", "staff"),
    (LIBRARIAN, "Cán bộ thư viện 1", "librarian", "Thuthu@123", "LIB", "staff"),
    ("bandoc1", "Phóng viên, Báo điện tử mẫu B", "reader", "Bandoc@123", "PRESS-B", "journalist"),
    ("nghiencuu1", "Nghiên cứu viên, Trường đại học mẫu C", "reader", "Nghiencuu@123", "UNI-C",
     "researcher"),
    ("sinhvien1", "Sinh viên, Trường đại học mẫu C", "reader", "Sinhvien@123", "UNI-C", "student"),
)
# Sample requests are spread over these readers in turn, each with a usual purpose.
REQUESTERS = (("bandoc1", "broadcast"), ("nghiencuu1", "research"), ("sinhvien1", "education"))
LICENSE_DAYS = (30, 90, 180, 365)
CONDITIONS = {
    "broadcast": "Ghi nguồn khi phát sóng; không bán lại tư liệu.",
    "research": "Chỉ dùng cho nghiên cứu; trích dẫn nguồn trong công bố.",
    "education": "Chỉ dùng trong lớp học, không đăng công khai.",
    "exhibition": "Trình chiếu tại không gian trưng bày; ghi nguồn.",
}
LABEL_COLUMNS = {"request_id", "request_text", "video_id", "start_sec", "end_sec"}


def batch_of(video_id: str) -> str:
    return video_id.split("_", 1)[0]


def sql_text(value: str) -> str:
    """An N'...' literal on one line (quotes doubled, whitespace collapsed)."""
    cleaned = " ".join(str(value).split())[:TEXT_LIMIT]
    return "N'" + cleaned.replace("'", "''") + "'"


def sql_ascii(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def insert_statements(table: str, columns: Iterable[str], rows: list[tuple]) -> list[str]:
    head = f"INSERT INTO dbo.{table} ({', '.join(columns)}) VALUES\n"
    statements = []
    for start in range(0, len(rows), MAX_ROWS):
        chunk = rows[start:start + MAX_ROWS]
        body = ",\n".join("    (" + ", ".join(row) + ")" for row in chunk)
        statements.append(head + body + ";")
    return statements


def values_list(rows: list[tuple]) -> str:
    return ",\n".join("    (" + ", ".join(row) + ")" for row in rows)


def read_fps(map_dir: Path) -> dict[str, float]:
    rates = {}
    for path in sorted(Path(map_dir).glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as file:
            first = next(csv.DictReader(file), None)
        if first and first.get("fps"):
            rates[path.stem] = float(first["fps"])
    return rates


def read_frames(frames_csv: Path, batches: set[str]) -> list[dict]:
    with Path(frames_csv).open(newline="", encoding="utf-8") as file:
        return [
            {
                "keyframe_id": int(row["frame_id"]),
                "video_id": row["video_id"],
                "sample_n": int(row["keyframe_n"]),
                "pts_time": round(float(row["timestamp_sec"]), 3),
                "frame_idx": int(row["frame_idx"]),
                "frame_path": row["frame_path"],
            }
            for row in csv.DictReader(file)
            if batch_of(row["video_id"]) in batches
        ]


def _sqlite_rows(path: Path, sql: str):
    connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        yield from connection.execute(sql)
    finally:
        connection.close()


def read_labels(labels_csv: Path, videos: set[str]) -> list[dict]:
    """Verified sample requests on the exported videos, in request_id order."""
    with Path(labels_csv).open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        missing = LABEL_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{labels_csv} lacks columns: {', '.join(sorted(missing))}")
        labels = []
        for row in reader:
            if row["video_id"] not in videos:
                continue
            start = float(row["start_sec"])
            end = float(row["end_sec"] or start)
            labels.append({
                "request_id": row["request_id"].strip(),
                "request_text": row["request_text"].strip(),
                "video_id": row["video_id"],
                "start_sec": round(start, 3),
                "end_sec": round(max(start, end), 3),
            })
    return sorted(labels, key=lambda label: label["request_id"])


def nearest(frames: list[dict], seconds: float) -> dict:
    return min(frames, key=lambda frame: abs(frame["pts_time"] - seconds))


def plan_requests(labels: list[dict]) -> list[dict]:
    """Who asked, why, when, and how far each sample request has got.

    Request i was sent (125 - i) days before the data is loaded.  Every
    second request has a chosen clip (on day 1); half of those are approved
    2-8 days after sending, with a licence of 30/90/180/365 days, so some
    licences have already expired.  A few requests without a clip were
    rejected; the rest are pending.
    """
    plans = []
    for i, label in enumerate(labels):
        username, purpose = REQUESTERS[i % len(REQUESTERS)]
        if i % 10 == 8:
            purpose = "exhibition"
        selected = i % 2 == 0
        if selected and i % 4 == 0:
            status = "approved"
        elif not selected and i % 20 == 1:
            status = "rejected"
        else:
            status = "pending"
        plans.append({
            **label, "username": username, "purpose": purpose, "status": status,
            "age_days": 125 - i, "selected": selected,
            "license_days": LICENSE_DAYS[(i // 4) % len(LICENSE_DAYS)] if status == "approved" else None,
            "grant_after": 2 + i % 7,
        })
    return plans


def generate(frames_csv: Path, map_dir: Path, ocr_sqlite: Path, asr_sqlite: Path,
             labels_csv: Path, batches: set[str]) -> tuple[str, dict]:
    frames = read_frames(frames_csv, batches)
    if not frames:
        raise ValueError(f"no frames for batches {sorted(batches)} in {frames_csv}")
    rates = read_fps(map_dir)
    videos = sorted({frame["video_id"] for frame in frames})
    by_video: dict[str, list[dict]] = {}
    for frame in frames:
        by_video.setdefault(frame["video_id"], []).append(frame)
    by_path = {frame["frame_path"]: frame["keyframe_id"] for frame in frames}

    ocr = sorted(
        (by_path[path], text)
        for path, text in _sqlite_rows(ocr_sqlite, "SELECT frame_path, text FROM ocr")
        if path in by_path and text and text.strip()
    )
    asr = sorted(
        (video_id, round(float(start), 3), round(float(end), 3), text)
        for video_id, start, end, text in _sqlite_rows(
            asr_sqlite, "SELECT video_id, start_sec, end_sec, text FROM asr")
        if video_id in by_video and text and text.strip() and float(end) >= float(start)
    )
    plans = plan_requests(read_labels(labels_csv, set(videos)))
    # The chosen clip of a selected request is the keyframe at its verified moment.
    selected = [
        (plan["request_id"], nearest(by_video[plan["video_id"]], plan["start_sec"])["keyframe_id"])
        for plan in plans if plan["selected"]
    ]
    licensed = [plan for plan in plans if plan["status"] == "approved"]

    statements = []
    statements += insert_statements("organizations", ("org_code", "org_name", "org_type"), [
        (sql_ascii(code), sql_text(name), sql_ascii(kind)) for code, name, kind in ORGANIZATIONS
    ])
    statements.append(
        "INSERT INTO dbo.collections (collection_code, title, organization_id, access_level, description)\n"
        "SELECT v.collection_code, v.title, o.organization_id, v.access_level, v.description\n"
        "FROM (VALUES\n" + values_list([
            (sql_ascii(code), sql_text(title), sql_ascii(org), sql_ascii(access), sql_text(text))
            for code, title, org, access, text in COLLECTIONS
        ]) + "\n) AS v (collection_code, title, org_code, access_level, description)\n"
        "JOIN dbo.organizations o ON o.org_code = v.org_code;"
    )
    statements.append(
        "DECLARE @collection INT = (SELECT collection_id FROM dbo.collections "
        f"WHERE collection_code = {sql_ascii(FOOTAGE_COLLECTION)});\n"
        "INSERT INTO dbo.videos (video_id, collection_id, title, fps) VALUES\n" + values_list([
            (sql_ascii(video_id), "@collection",
             sql_text(f"Tường thuật đua xe đạp, đoạn {n:02d}"),
             str(rates[video_id]) if video_id in rates else "NULL")
            for n, video_id in enumerate(videos, start=1)
        ]) + ";"
    )
    statements += insert_statements(
        "keyframes", ("keyframe_id", "video_id", "sample_n", "pts_time", "frame_idx"), [
            (str(f["keyframe_id"]), sql_ascii(f["video_id"]), str(f["sample_n"]),
             f"{f['pts_time']:.3f}", str(f["frame_idx"]))
            for f in frames
        ])
    statements += insert_statements("ocr_texts", ("keyframe_id", "ocr_text"), [
        (str(keyframe_id), sql_text(text)) for keyframe_id, text in ocr
    ])
    statements += insert_statements(
        "asr_segments", ("video_id", "start_sec", "end_sec", "asr_text"), [
            (sql_ascii(video_id), f"{start:.3f}", f"{end:.3f}", sql_text(text))
            for video_id, start, end, text in asr
        ])
    statements.append(
        "-- Gắn chủ đề theo luật: video có ít nhất một đoạn lời nói (đã bỏ dấu)\n"
        "-- khớp mẫu của chủ đề.\n"
        "INSERT INTO dbo.video_topics (video_id, topic_id)\n"
        "SELECT DISTINCT a.video_id, t.topic_id\n"
        "FROM (VALUES\n" + values_list([
            (sql_text(topic), sql_text(pattern))
            for topic, patterns in TOPIC_RULES for pattern in patterns
        ]) + "\n) AS rule_list (topic_name, pattern)\n"
        "JOIN dbo.topics t        ON t.topic_name = rule_list.topic_name\n"
        "JOIN dbo.asr_segments a  ON a.text_folded LIKE rule_list.pattern;"
    )
    statements.append(
        "INSERT INTO dbo.users (username, full_name, password_hash, organization_id, user_type)\n"
        "SELECT v.username, v.full_name, v.password_hash, o.organization_id, v.user_type\n"
        "FROM (VALUES\n" + values_list([
            (sql_ascii(username), sql_text(full_name), sql_ascii(hash_password(password)),
             sql_ascii(org), sql_ascii(kind))
            for username, full_name, _, password, org, kind in DEMO_USERS
        ]) + "\n) AS v (username, full_name, password_hash, org_code, user_type)\n"
        "LEFT JOIN dbo.organizations o ON o.org_code = v.org_code;"
    )
    statements.append(
        "INSERT INTO dbo.user_roles (user_id, role_id)\n"
        "SELECT u.user_id, r.role_id\n"
        "FROM (VALUES\n" + values_list([
            (sql_ascii(username), sql_ascii(role)) for username, _, role, *_ in DEMO_USERS
        ]) + "\n) AS v (username, role_name)\n"
        "JOIN dbo.users u ON u.username = v.username\n"
        "JOIN dbo.roles r ON r.role_name = v.role_name;"
    )
    statements.append(
        "INSERT INTO dbo.requests (request_id, request_text, origin, requested_by, purpose, status, created_at)\n"
        "SELECT v.request_id, v.request_text, 'sample', u.user_id, v.purpose, v.status,\n"
        "       DATEADD(DAY, -v.age_days, CAST(SYSDATETIME() AS DATETIME2(0)))\n"
        "FROM (VALUES\n" + values_list([
            (sql_ascii(p["request_id"]), sql_text(p["request_text"]), sql_ascii(p["username"]),
             sql_ascii(p["purpose"]), sql_ascii(p["status"]), str(p["age_days"]))
            for p in plans
        ]) + "\n) AS v (request_id, request_text, username, purpose, status, age_days)\n"
        "JOIN dbo.users u ON u.username = v.username;"
    )
    statements += insert_statements(
        "ground_truth", ("request_id", "video_id", "start_sec", "end_sec"), [
            (sql_ascii(p["request_id"]), sql_ascii(p["video_id"]),
             f"{p['start_sec']:.3f}", f"{p['end_sec']:.3f}")
            for p in plans
        ])
    if selected:
        statements.append(
            "INSERT INTO dbo.selections (request_id, keyframe_id, user_id, note, created_at)\n"
            "SELECT v.request_id, v.keyframe_id, u.user_id,\n"
            "       N'Dữ liệu mẫu: đoạn tại thời điểm đã xác minh', DATEADD(DAY, 1, r.created_at)\n"
            "FROM (VALUES\n" + values_list([(sql_ascii(r), str(k)) for r, k in selected]) +
            "\n) AS v (request_id, keyframe_id)\n"
            "JOIN dbo.requests r ON r.request_id = v.request_id\n"
            f"JOIN dbo.users u ON u.username = {sql_ascii(LIBRARIAN)};"
        )
    if licensed:
        statements.append(
            "-- Giấy phép cấp sau khi gửi yêu cầu v.delay_days ngày; đơn vị được cấp phép\n"
            "-- là đơn vị của người yêu cầu tại thời điểm cấp.\n"
            "INSERT INTO dbo.usage_licenses (request_id, licensee_org_id, granted_by, granted_at,\n"
            "                                valid_from, valid_to, conditions)\n"
            "SELECT r.request_id, ru.organization_id, g.user_id, DATEADD(DAY, v.delay_days, r.created_at),\n"
            "       CAST(DATEADD(DAY, v.delay_days, r.created_at) AS DATE),\n"
            "       DATEADD(DAY, v.days, CAST(DATEADD(DAY, v.delay_days, r.created_at) AS DATE)), v.conditions\n"
            "FROM (VALUES\n" + values_list([
                (sql_ascii(p["request_id"]), str(p["grant_after"]), str(p["license_days"]),
                 sql_text(CONDITIONS[p["purpose"]]))
                for p in licensed
            ]) + "\n) AS v (request_id, delay_days, days, conditions)\n"
            "JOIN dbo.requests r ON r.request_id = v.request_id\n"
            "JOIN dbo.users ru   ON ru.user_id = r.requested_by\n"
            f"JOIN dbo.users g    ON g.username = {sql_ascii(LIBRARIAN)};"
        )

    counts = {
        "organizations": len(ORGANIZATIONS), "collections": len(COLLECTIONS),
        "videos": len(videos), "keyframes": len(frames), "ocr_texts": len(ocr),
        "asr_segments": len(asr), "requests": len(plans), "users": len(DEMO_USERS),
        "selections": len(selected), "licenses": len(licensed),
    }
    summary = ", ".join(f"{name} {count}" for name, count in counts.items())
    header = (
        "-- =====================================================================\n"
        f"-- 09. Dữ liệu mẫu, lô {', '.join(sorted(batches))} (sinh tự động bởi `footage sample`)\n"
        f"--   {summary}\n"
        "-- Nguồn video: bộ video tiếng Việt công khai (tường thuật đua xe đạp); khung\n"
        "-- hình, chữ trên hình (OCR) và lời nói (ASR) do nhóm trích xuất; yêu cầu mẫu\n"
        "-- do nhóm viết và xác minh vị trí đúng.\n"
        "-- Đơn vị, bộ sưu tập khác, bạn đọc, trạng thái và giấy phép là dữ liệu MINH HOẠ\n"
        "-- (tên có chữ \"mẫu\"); chủ đề được gắn theo luật trên lời nói thật.\n"
        "-- Tài khoản demo: " + ", ".join(f"{u} / {p}" for u, _, _, p, *_ in DEMO_USERS) + "\n"
        "--   (chỉ để demo, đổi mật khẩu trước khi dùng thật)\n"
        "-- =====================================================================\n"
        "SET QUOTED_IDENTIFIER ON;\nSET ANSI_NULLS ON;\nSET NOCOUNT ON;\nGO\n\n"
    )
    body = "\nGO\n\n".join(statements)
    footer = "\nGO\n\n-- Lập chỉ mục ngược cho OCR và ASR vừa chèn.\nEXEC dbo.sp_rebuild_text_index;\nGO\n"
    return header + body + footer, counts


def write_sample(path: Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8-sig")
