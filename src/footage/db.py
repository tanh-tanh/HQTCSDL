"""SQL Server connections, T-SQL script execution, and database setup."""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pyodbc

from footage.config import Settings

# A connection that ran EXECUTE AS ... WITH NO REVERT must never be handed
# to the next request, so ODBC connection pooling stays off.
pyodbc.pooling = False

_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_GO = re.compile(r"^\s*GO\s*$", re.IGNORECASE)
_ERROR_NUMBER = re.compile(r"\((\d+)\)\s*\(SQL")

# Scripts in db/ that build the schema (00 creates the database and is only
# used in the single-file full_setup.sql); 09 is the generated sample data.
SCHEMA_PARTS = ("01_fold_function.sql", "02_tables.sql", "03_functions.sql", "04_views.sql",
                "05_procedures.sql", "06_triggers.sql", "07_seed.sql", "08_security.sql")
SAMPLE_PART = "09_sample_data.sql"
FULL_SCRIPT = "full_setup.sql"


def _checked(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def connection_string(settings: Settings, database: str | None) -> str:
    parts = [
        f"DRIVER={{{settings.db_driver}}}",
        f"SERVER={settings.db_server}",
        # Local Express uses a self-signed certificate.
        "Encrypt=yes",
        "TrustServerCertificate=yes",
    ]
    if database:
        parts.append(f"DATABASE={_checked(database)}")
    if settings.db_login:
        parts += [f"UID={settings.db_login}", f"PWD={settings.db_password}"]
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts)


def connect(settings: Settings, *, admin: bool = False, database: bool = True,
            user_id: int | None = None) -> pyodbc.Connection:
    """Open one connection.

    Application connections drop to the least-privileged database user and
    carry the signed-in user's id in SESSION_CONTEXT, read-only, so stored
    procedures and triggers know who acts.  Admin connections (schema setup,
    import) keep the caller's own rights and have no query timeout.
    """
    connection = pyodbc.connect(
        connection_string(settings, settings.db_name if database else None), autocommit=True
    )
    if not admin:
        cursor = connection.cursor()
        cursor.execute(f"EXECUTE AS USER = N'{_checked(settings.app_user)}' WITH NO REVERT;")
        if user_id is not None:
            set_actor(connection, user_id)
        # ODBC cancels the statement on the server when this expires.
        connection.timeout = settings.search_timeout
        connection.autocommit = False
    return connection


def set_actor(connection, user_id: int) -> None:
    connection.cursor().execute(
        "EXEC sp_set_session_context @key = N'app_user_id', @value = ?, @read_only = 1;",
        user_id,
    )


@contextmanager
def transaction(settings: Settings, **kwargs) -> Iterator[pyodbc.Connection]:
    """A connection that commits on success and rolls back on error."""
    connection = connect(settings, **kwargs)
    try:
        yield connection
        if not connection.autocommit:
            connection.commit()
    except BaseException:
        if not connection.autocommit:
            connection.rollback()
        raise
    finally:
        connection.close()


def _rows(cursor) -> list[dict]:
    if cursor.description is None:
        return []
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def query(connection, sql: str, *params) -> list[dict]:
    cursor = connection.cursor()
    cursor.execute(sql, *params)
    rows = _rows(cursor)
    while cursor.nextset():
        pass
    return rows


def query_one(connection, sql: str, *params) -> dict | None:
    rows = query(connection, sql, *params)
    return rows[0] if rows else None


def execute(connection, sql: str, *params) -> int:
    cursor = connection.cursor()
    cursor.execute(sql, *params)
    count = cursor.rowcount
    while cursor.nextset():
        pass
    return count


def call(connection, procedure: str, **params) -> list[dict]:
    """EXEC a procedure with named parameters; return its first result set."""
    assignments = ", ".join(f"@{_checked(name)} = ?" for name in params)
    cursor = connection.cursor()
    cursor.execute(f"EXEC dbo.{_checked(procedure)} {assignments};", *params.values())
    rows = _rows(cursor)
    while cursor.nextset():
        pass
    return rows


def error_number(error: pyodbc.Error) -> int | None:
    """The SQL Server error number (e.g. 547, 2627, 50001) inside a pyodbc error."""
    match = _ERROR_NUMBER.search(str(error))
    return int(match.group(1)) if match else None


def split_batches(text: str) -> list[str]:
    """Split a T-SQL script on `GO` lines, like sqlcmd and SSMS do."""
    batches, buffer = [], []
    for line in text.splitlines():
        if _GO.match(line):
            batches.append("\n".join(buffer))
            buffer = []
        else:
            buffer.append(line)
    batches.append("\n".join(buffer))
    return [batch for batch in batches if _has_code(batch)]


def _has_code(batch: str) -> bool:
    return any(line.strip() and not line.strip().startswith("--") for line in batch.splitlines())


def run_script(connection, path: Path) -> int:
    batches = split_batches(Path(path).read_text(encoding="utf-8-sig"))
    cursor = connection.cursor()
    for batch in batches:
        cursor.execute(batch)
        while cursor.nextset():
            pass
    return len(batches)


def database_exists(settings: Settings) -> bool:
    connection = connect(settings, admin=True, database=False)
    try:
        return query_one(connection, "SELECT DB_ID(?) AS id", settings.db_name)["id"] is not None
    finally:
        connection.close()


def init_database(settings: Settings, *, reset: bool = False, sample: bool = True,
                  progress=print) -> None:
    """Create the database from db/*.sql (what full_setup.sql does, for any name)."""
    name = _checked(settings.db_name)
    master = connect(settings, admin=True, database=False)
    try:
        exists = query_one(master, "SELECT DB_ID(?) AS id", name)["id"] is not None
        if exists and not reset:
            raise RuntimeError(f"database {name} already exists (use --reset to recreate it)")
        if exists:
            execute(master, f"ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;")
            execute(master, f"DROP DATABASE [{name}];")
            progress(f"dropped database {name}")
        execute(master, f"CREATE DATABASE [{name}] COLLATE Latin1_General_100_CI_AS;")
        execute(master, f"ALTER DATABASE [{name}] SET COMPATIBILITY_LEVEL = 140;")
        progress(f"created database {name}")
    finally:
        master.close()
    parts = list(SCHEMA_PARTS) + ([SAMPLE_PART] if sample else [])
    connection = connect(settings, admin=True)
    try:
        for part in parts:
            path = settings.sql_dir / part
            if part == SAMPLE_PART and not path.is_file():
                progress(f"skip {part}: not generated yet (footage sample)")
                continue
            progress(f"{part}: {run_script(connection, path)} batches")
    finally:
        connection.close()


def build_full_script(sql_dir: Path) -> Path:
    """Concatenate db/00..09 into db/full_setup.sql for SSMS / sqlcmd users."""
    names = ["00_database.sql", *SCHEMA_PARTS, SAMPLE_PART]
    chunks = [
        "-- =====================================================================\n"
        "-- full_setup.sql: tạo CSDL video_archive (thư viện số nghe nhìn), bảng, hàm, view,\n"
        "-- thủ tục, trigger, phân quyền và chèn dữ liệu mẫu, theo đúng thứ tự.  File được\n"
        "-- ghép tự động từ db/00..09 bằng `footage db build-script`; sửa các file thành\n"
        "-- phần, không sửa file này.\n"
        "--   sqlcmd -S .\\SQLEXPRESS -E -C -f 65001 -I -b -i db\\full_setup.sql\n"
        "-- =====================================================================\n"
    ]
    for name in names:
        text = (sql_dir / name).read_text(encoding="utf-8-sig").rstrip()
        chunks.append(f"\n-- >>>>>>>>>>>>>>>>>>>> {name} >>>>>>>>>>>>>>>>>>>>\n{text}\nGO\n")
    out = sql_dir / FULL_SCRIPT
    out.write_text("".join(chunks), encoding="utf-8-sig")
    return out
