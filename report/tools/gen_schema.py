"""Generate the report's table/column and constraint listings from the live
`video_archive` catalog, so the report cannot drift from db/02_tables.sql.

    uv run --env-file .env python report/tools/gen_schema.py
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from footage.config import load_settings
from footage.db import connect, query

OUT = Path(__file__).resolve().parents[1] / "chapters" / "generated"

GROUPS = [
    ("Nhóm danh mục thư viện", ["organizations", "collections", "topics", "video_topics"]),
    ("Nhóm kho dữ liệu video", ["videos", "keyframes", "ocr_texts", "asr_segments"]),
    ("Nhóm chỉ mục văn bản", ["stopwords", "terms", "ocr_postings", "asr_postings"]),
    ("Nhóm tài khoản và phân quyền",
     ["users", "roles", "permissions", "user_roles", "role_permissions", "sessions"]),
    ("Nhóm yêu cầu khai thác và cấp phép",
     ["requests", "ground_truth", "selections", "usage_licenses"]),
    ("Nhóm nhật ký và đánh giá",
     ["sources", "audit_log", "search_log", "search_log_sources", "eval_runs",
      "eval_run_sources", "eval_requests", "eval_results"]),
]

TABLES = {
    "organizations": "Đơn vị thành viên: đóng góp bộ sưu tập và/hoặc có bạn đọc khai thác.",
    "collections": "Bộ sưu tập do một đơn vị đóng góp và giữ bản quyền.",
    "topics": "Cây chủ đề phân loại video (quan hệ đệ quy qua parent\\_topic\\_id).",
    "video_topics": "Quan hệ nhiều--nhiều giữa video và chủ đề.",
    "videos": "Video trong kho, thuộc một bộ sưu tập.",
    "keyframes": "Khung hình được trích từ video; khoá chính trùng số thứ tự vector trong FAISS.",
    "ocr_texts": "Chữ nhận dạng được trên khung hình (OCR).",
    "asr_segments": "Đoạn lời nói nhận dạng được (ASR) theo khoảng thời gian.",
    "stopwords": "Hư từ bị bỏ qua khi tách từ.",
    "terms": "Từ điển các từ khoá đã bỏ dấu.",
    "ocr_postings": "Chỉ mục ngược: từ khoá xuất hiện ở khung hình nào.",
    "asr_postings": "Chỉ mục ngược: từ khoá xuất hiện ở đoạn lời nói nào.",
    "requests": "Yêu cầu khai thác: bạn đọc mô tả đoạn tư liệu cần dùng, mục đích và trạng thái xử lý.",
    "ground_truth": "Vị trí đúng (xác minh thủ công) của đoạn tư liệu mà yêu cầu mẫu cần tìm.",
    "selections": "Đoạn tư liệu (khung hình) cán bộ thư viện chọn cho một yêu cầu.",
    "usage_licenses": "Giấy phép sử dụng cấp cho một yêu cầu đã duyệt.",
    "users": "Tài khoản đăng nhập: bạn đọc và cán bộ thư viện.",
    "roles": "Vai trò (reader, librarian, admin).",
    "permissions": "Quyền nghiệp vụ.",
    "user_roles": "Quan hệ nhiều--nhiều giữa người dùng và vai trò.",
    "role_permissions": "Quan hệ nhiều--nhiều giữa vai trò và quyền.",
    "sessions": "Phiên đăng nhập (chỉ lưu băm của cookie).",
    "sources": "Danh mục nguồn tìm kiếm (visual, ocr, asr).",
    "audit_log": "Nhật ký thao tác, do trigger và ứng dụng ghi.",
    "search_log": "Nhật ký các lượt tìm kiếm và độ trễ.",
    "search_log_sources": "Nguồn được dùng trong mỗi lượt tìm kiếm.",
    "eval_runs": "Lượt đánh giá trên tập yêu cầu mẫu đã xác minh.",
    "eval_run_sources": "Nguồn được dùng trong mỗi lượt đánh giá.",
    "eval_requests": "Từng yêu cầu được chạy trong một lượt đánh giá và độ trễ.",
    "eval_results": "Danh sách kết quả xếp hạng của từng yêu cầu trong lượt đánh giá.",
}

COLUMNS = {
    ("organizations", "organization_id"): "Mã đơn vị (tự tăng)",
    ("organizations", "org_code"): "Mã viết tắt, vd. LIB",
    ("organizations", "org_name"): "Tên đơn vị",
    ("organizations", "org_type"): "Loại: library, tv, press, school, museum",
    ("organizations", "created_at"): "Thời điểm tham gia",
    ("collections", "collection_id"): "Mã bộ sưu tập (tự tăng)",
    ("collections", "collection_code"): "Mã viết tắt, vd. DXD",
    ("collections", "title"): "Tên bộ sưu tập",
    ("collections", "organization_id"): "Đơn vị đóng góp, giữ bản quyền",
    ("collections", "access_level"): "open: dùng tự do; restricted: cần giấy phép",
    ("collections", "description"): "Mô tả, nguồn gốc",
    ("collections", "created_at"): "Thời điểm tạo",
    ("topics", "topic_id"): "Mã chủ đề (tự tăng)",
    ("topics", "topic_name"): "Tên chủ đề",
    ("topics", "parent_topic_id"): "Chủ đề cha (NULL nếu là gốc)",
    ("video_topics", "video_id"): "Video",
    ("video_topics", "topic_id"): "Chủ đề gắn cho video",
    ("videos", "video_id"): "Mã video, dạng Lxx\\_Vyyy",
    ("videos", "batch"): "Lô dữ liệu (L23...), cột tính toán từ video\\_id",
    ("videos", "collection_id"): "Bộ sưu tập chứa video",
    ("videos", "title"): "Nhan đề trong phiếu mô tả",
    ("videos", "fps"): "Số khung hình mỗi giây của video gốc",
    ("keyframes", "keyframe_id"): "Mã khung hình, trùng id vector FAISS",
    ("keyframes", "video_id"): "Video chứa khung hình",
    ("keyframes", "sample_n"): "Số thứ tự khung hình trong video (tên file ảnh)",
    ("keyframes", "pts_time"): "Thời điểm trong video (giây)",
    ("keyframes", "frame_idx"): "Chỉ số khung hình trong video gốc (dùng khi trích đoạn)",
    ("ocr_texts", "keyframe_id"): "Khung hình chứa chữ",
    ("ocr_texts", "ocr_text"): "Chữ nhận dạng được",
    ("ocr_texts", "text_folded"): "Chữ đã bỏ dấu, viết thường (cột tính toán)",
    ("asr_segments", "segment_id"): "Mã đoạn lời nói (tự tăng)",
    ("asr_segments", "video_id"): "Video chứa đoạn lời nói",
    ("asr_segments", "start_sec"): "Thời điểm bắt đầu (giây)",
    ("asr_segments", "end_sec"): "Thời điểm kết thúc (giây)",
    ("asr_segments", "asr_text"): "Lời nói nhận dạng được",
    ("asr_segments", "text_folded"): "Lời nói đã bỏ dấu (cột tính toán)",
    ("stopwords", "word"): "Hư từ đã bỏ dấu",
    ("terms", "term_id"): "Mã từ khoá (tự tăng)",
    ("terms", "term"): "Từ khoá đã bỏ dấu, so khớp nhị phân",
    ("ocr_postings", "term_id"): "Từ khoá",
    ("ocr_postings", "keyframe_id"): "Khung hình có từ khoá trong chữ OCR",
    ("asr_postings", "term_id"): "Từ khoá",
    ("asr_postings", "segment_id"): "Đoạn lời nói có từ khoá",
    ("requests", "request_id"): "Mã yêu cầu, vd. YC-047, YC-U0001",
    ("requests", "request_text"): "Mô tả đoạn tư liệu cần dùng",
    ("requests", "origin"): "Nguồn: sample (mẫu đã xác minh) hoặc user (gửi qua web)",
    ("requests", "requested_by"): "Bạn đọc gửi yêu cầu",
    ("requests", "purpose"): "Mục đích: broadcast, research, education, exhibition",
    ("requests", "status"): "pending, approved, rejected; chỉ đổi qua thủ tục",
    ("requests", "created_at"): "Thời điểm gửi",
    ("ground_truth", "request_id"): "Yêu cầu mẫu",
    ("ground_truth", "video_id"): "Video chứa cảnh đúng",
    ("ground_truth", "start_sec"): "Bắt đầu khoảng thời gian đúng (giây)",
    ("ground_truth", "end_sec"): "Kết thúc khoảng thời gian đúng (giây)",
    ("selections", "selection_id"): "Mã lựa chọn (tự tăng)",
    ("selections", "request_id"): "Yêu cầu khai thác",
    ("selections", "keyframe_id"): "Đoạn tư liệu (khung hình) được chọn",
    ("selections", "user_id"): "Người chọn (NULL nếu tài khoản đã bị xoá)",
    ("selections", "note"): "Ghi chú",
    ("selections", "created_at"): "Thời điểm chọn; quyết định thứ tự trong danh sách xuất",
    ("users", "user_id"): "Mã người dùng (tự tăng)",
    ("users", "username"): "Tên đăng nhập: 3--32 ký tự a-z, 0-9, \\_ . -",
    ("users", "full_name"): "Họ tên hiển thị",
    ("users", "password_hash"): "Mật khẩu băm scrypt (kèm muối)",
    ("users", "organization_id"): "Đơn vị công tác (NULL: bạn đọc cá nhân)",
    ("users", "user_type"): "Đối tượng: staff, journalist, researcher, student, other",
    ("users", "is_active"): "Tài khoản còn hoạt động",
    ("users", "created_at"): "Thời điểm tạo",
    ("users", "last_login_at"): "Lần đăng nhập gần nhất",
    ("roles", "role_id"): "Mã vai trò (tự tăng)",
    ("roles", "role_name"): "Tên vai trò: reader, librarian, admin",
    ("roles", "description"): "Mô tả",
    ("permissions", "permission_code"): "Mã quyền, vd. selection.write",
    ("permissions", "description"): "Mô tả",
    ("user_roles", "user_id"): "Người dùng",
    ("user_roles", "role_id"): "Vai trò được gán",
    ("role_permissions", "role_id"): "Vai trò",
    ("role_permissions", "permission_code"): "Quyền của vai trò",
    ("usage_licenses", "license_id"): "Mã giấy phép (tự tăng)",
    ("usage_licenses", "request_id"): "Yêu cầu được cấp phép (duy nhất)",
    ("usage_licenses", "licensee_org_id"): "Đơn vị được cấp phép tại thời điểm cấp (NULL: cá nhân)",
    ("usage_licenses", "granted_by"): "Cán bộ cấp phép",
    ("usage_licenses", "granted_at"): "Thời điểm cấp",
    ("usage_licenses", "valid_from"): "Ngày bắt đầu hiệu lực",
    ("usage_licenses", "valid_to"): "Ngày hết hiệu lực (sau valid\\_from)",
    ("usage_licenses", "conditions"): "Điều kiện sử dụng",
    ("sessions", "session_hash"): "SHA-256 của cookie đăng nhập",
    ("sessions", "user_id"): "Chủ phiên",
    ("sessions", "created_at"): "Thời điểm đăng nhập",
    ("sessions", "expires_at"): "Thời điểm hết hạn",
    ("sources", "source_code"): "Mã nguồn: visual, ocr, asr",
    ("sources", "description"): "Mô tả",
    ("audit_log", "log_id"): "Mã dòng nhật ký (tự tăng)",
    ("audit_log", "user_id"): "Người thực hiện",
    ("audit_log", "action"): "Hành động, vd. selection.add, request.approve",
    ("audit_log", "detail"): "Chi tiết dạng văn bản",
    ("audit_log", "created_at"): "Thời điểm",
    ("search_log", "search_id"): "Mã lượt tìm (tự tăng)",
    ("search_log", "user_id"): "Người tìm",
    ("search_log", "request_text"): "Nội dung tìm",
    ("search_log", "top_k"): "Số kết quả yêu cầu",
    ("search_log", "result_count"): "Số kết quả trả về",
    ("search_log", "latency_ms"): "Thời gian xử lý (ms)",
    ("search_log", "created_at"): "Thời điểm",
    ("search_log_sources", "search_id"): "Lượt tìm",
    ("search_log_sources", "source_code"): "Nguồn được dùng",
    ("eval_runs", "run_id"): "Mã lượt đánh giá (tự tăng)",
    ("eval_runs", "run_name"): "Tên lượt",
    ("eval_runs", "top_k"): "Số kết quả xét cho mỗi yêu cầu",
    ("eval_runs", "tolerance_sec"): "Sai lệch thời gian cho phép (giây)",
    ("eval_runs", "created_at"): "Thời điểm chạy",
    ("eval_run_sources", "run_id"): "Lượt đánh giá",
    ("eval_run_sources", "source_code"): "Nguồn được dùng",
    ("eval_requests", "run_id"): "Lượt đánh giá",
    ("eval_requests", "request_id"): "Yêu cầu mẫu được chạy",
    ("eval_requests", "latency_ms"): "Thời gian xử lý (ms)",
    ("eval_results", "run_id"): "Lượt đánh giá",
    ("eval_results", "request_id"): "Yêu cầu được chạy",
    ("eval_results", "rank_no"): "Thứ hạng của kết quả",
    ("eval_results", "keyframe_id"): "Khung hình được trả về",
}


_ESCAPES = {"\\": r"\textbackslash{}", "_": r"\_", "%": r"\%", "&": r"\&", "#": r"\#",
            "{": r"\{", "}": r"\}", "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
            "$": r"\$"}


def tex(text: str) -> str:
    return "".join(_ESCAPES.get(char, char) for char in str(text))


def type_name(row) -> str:
    name = row["type_name"]
    if name in ("varchar", "char", "varbinary"):
        return f"{name}({'max' if row['max_length'] == -1 else row['max_length']})"
    if name in ("nvarchar", "nchar"):
        return f"{name}({'max' if row['max_length'] == -1 else row['max_length'] // 2})"
    if name in ("decimal", "numeric"):
        return f"{name}({row['precision']},{row['scale']})"
    if name == "datetime2":
        return f"datetime2({row['scale']})"
    return name


def clean_definition(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return text


def main() -> None:
    settings = load_settings()
    connection = connect(settings, admin=True)
    columns = query(connection, """
        SELECT t.name AS table_name, c.name AS column_name, c.column_id, ty.name AS type_name,
               c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity,
               cc.definition AS computed, dc.definition AS default_value
        FROM sys.tables t
        JOIN sys.columns c ON c.object_id = t.object_id
        JOIN sys.types ty ON ty.user_type_id = c.user_type_id
        LEFT JOIN sys.computed_columns cc ON cc.object_id = c.object_id AND cc.column_id = c.column_id
        LEFT JOIN sys.default_constraints dc ON dc.parent_object_id = c.object_id
                                            AND dc.parent_column_id = c.column_id
        ORDER BY t.name, c.column_id""")
    keys = query(connection, """
        SELECT t.name AS table_name, c.name AS column_name, k.type AS key_type
        FROM sys.key_constraints k
        JOIN sys.tables t ON t.object_id = k.parent_object_id
        JOIN sys.index_columns ic ON ic.object_id = k.parent_object_id AND ic.index_id = k.unique_index_id
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id""")
    fks = query(connection, """
        SELECT tp.name AS table_name, cp.name AS column_name, tr.name AS ref_table
        FROM sys.foreign_key_columns fkc
        JOIN sys.tables tp ON tp.object_id = fkc.parent_object_id
        JOIN sys.columns cp ON cp.object_id = fkc.parent_object_id AND cp.column_id = fkc.parent_column_id
        JOIN sys.tables tr ON tr.object_id = fkc.referenced_object_id""")
    constraints = query(connection, """
        SELECT OBJECT_NAME(parent_object_id) AS table_name, name, type_desc,
               CAST(OBJECT_DEFINITION(object_id) AS NVARCHAR(MAX)) AS definition
        FROM sys.objects
        WHERE type IN ('PK', 'UQ', 'F', 'C', 'D') AND is_ms_shipped = 0
        ORDER BY type_desc, table_name, name""")
    connection.close()

    marks = defaultdict(list)
    for row in keys:
        marks[(row["table_name"], row["column_name"])].append("PK" if row["key_type"] == "PK" else "UQ")
    for row in fks:
        marks[(row["table_name"], row["column_name"])].append(f"FK -> {row['ref_table']}")
    by_table = defaultdict(list)
    for row in columns:
        by_table[row["table_name"]].append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    lines = []
    number = 0
    for group, tables in GROUPS:
        lines.append(f"\\subsection{{{group}}}\n")
        for table in tables:
            number += 1
            lines += [
                f"\\textbf{{Bảng \\texttt{{{tex(table)}}}}} -- {TABLES[table]}\n",
                "\\setlength{\\tabcolsep}{4pt}",
                "\\begin{longtable}{>{\\raggedright\\arraybackslash}p{0.21\\textwidth}"
                ">{\\raggedright\\arraybackslash}p{0.19\\textwidth}"
                ">{\\centering\\arraybackslash}p{0.07\\textwidth}"
                ">{\\raggedright\\arraybackslash}p{0.17\\textwidth}"
                ">{\\raggedright\\arraybackslash}p{0.26\\textwidth}}",
                f"\\caption{{Cấu trúc bảng \\texttt{{{tex(table)}}}}}\\label{{tab:col_{table}}}\\\\",
                "\\toprule \\textbf{Cột} & \\textbf{Kiểu} & \\textbf{Null} & \\textbf{Khoá / mặc định} "
                "& \\textbf{Mô tả} \\\\ \\midrule \\endfirsthead",
                "\\toprule \\textbf{Cột} & \\textbf{Kiểu} & \\textbf{Null} & \\textbf{Khoá / mặc định} "
                "& \\textbf{Mô tả} \\\\ \\midrule \\endhead",
                "\\bottomrule \\endfoot",
            ]
            for row in by_table[table]:
                extra = list(dict.fromkeys(marks[(table, row["column_name"])]))
                if row["is_identity"]:
                    extra.append("IDENTITY")
                if row["default_value"]:
                    extra.append("DF " + clean_definition(row["default_value"]))
                kind = "tính toán" if row["computed"] else type_name(row)
                lines.append(
                    f"\\texttt{{{tex(row['column_name'])}}} & {{\\footnotesize\\texttt{{{tex(kind)}}}}} & "
                    f"{'có' if row['is_nullable'] else 'không'} & "
                    f"{tex(', '.join(extra)).replace(' -> ', r' $\rightarrow$ ')} & "
                    f"{COLUMNS[(table, row['column_name'])]} \\\\"
                )
            lines.append("\\end{longtable}\n")
    (OUT / "tables.tex").write_text("\n".join(lines), encoding="utf-8")

    counts = defaultdict(int)
    checks, defaults = [], []
    for row in constraints:
        counts[row["type_desc"]] += 1
        if row["type_desc"] == "CHECK_CONSTRAINT":
            checks.append(row)
        elif row["type_desc"] == "DEFAULT_CONSTRAINT":
            defaults.append(row)
    labels = {
        "PRIMARY_KEY_CONSTRAINT": "PRIMARY KEY (PK\\_)",
        "FOREIGN_KEY_CONSTRAINT": "FOREIGN KEY (FK\\_)",
        "UNIQUE_CONSTRAINT": "UNIQUE (UQ\\_)",
        "CHECK_CONSTRAINT": "CHECK (CK\\_)",
        "DEFAULT_CONSTRAINT": "DEFAULT (DF\\_)",
    }
    summary = ["\\begin{tabular}{lr}", "\\toprule \\textbf{Loại ràng buộc} & \\textbf{Số lượng} \\\\ \\midrule"]
    for kind, label in labels.items():
        summary.append(f"{label} & {counts[kind]} \\\\")
    summary.append("NOT NULL (cột) & " + str(sum(1 for row in columns if not row["is_nullable"])) + " \\\\")
    summary += ["\\bottomrule", "\\end{tabular}"]
    (OUT / "constraint_counts.tex").write_text("\n".join(summary), encoding="utf-8")

    check_lines = [
        "\\begin{longtable}{>{\\raggedright\\arraybackslash}p{0.33\\textwidth}"
        ">{\\raggedright\\arraybackslash}p{0.6\\textwidth}}",
        "\\caption{Các ràng buộc CHECK}\\label{tab:checks}\\\\",
        "\\toprule \\textbf{Tên} & \\textbf{Điều kiện} \\\\ \\midrule \\endfirsthead",
        "\\toprule \\textbf{Tên} & \\textbf{Điều kiện} \\\\ \\midrule \\endhead",
        "\\bottomrule \\endfoot",
    ]
    for row in checks:
        check_lines.append(f"\\texttt{{{tex(row['name'])}}} & \\texttt{{{tex(clean_definition(row['definition']))}}} \\\\")
    check_lines.append("\\end{longtable}")
    (OUT / "checks.tex").write_text("\n".join(check_lines), encoding="utf-8")
    write_test_excerpt()
    print(f"wrote {number} tables, {len(checks)} checks, {len(defaults)} defaults to {OUT}")


def write_test_excerpt() -> None:
    """Sections A-C of docs/output/11_tests.txt, wrapped for a Verbatim block."""
    import textwrap

    source = Path(__file__).resolve().parents[2] / "docs" / "output" / "11_tests.txt"
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    end = next(i for i, line in enumerate(lines) if line.startswith("== D."))
    wrapped = []
    for line in lines[:end]:
        wrapped += textwrap.wrap(line, 86, subsequent_indent="    ") or [""]
    (OUT / "tests_excerpt.txt").write_text("\n".join(wrapped) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
