"""Settings read once from the environment (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _path(name: str, default: Path) -> Path:
    value = _env(name)
    return Path(value).expanduser() if value else default


@dataclass(frozen=True)
class Settings:
    db_server: str
    db_name: str
    db_driver: str
    # Empty -> Windows authentication (the SQL Server Express default).
    db_login: str
    db_password: str
    # Database user the web app impersonates (EXECUTE AS USER ... WITH NO REVERT).
    app_user: str
    data_dir: Path
    keyframes_dir: Path
    sql_dir: Path
    siglip_model: str
    siglip_index: Path
    visual_enabled: bool
    search_timeout: int
    search_pool: int
    session_hours: int
    secure_cookies: bool
    # Public /register page that creates reader accounts (sp_register_reader).
    self_registration: bool = True
    # Header label of the archive interface.
    archive_label: str = "THƯ VIỆN SỐ / VIDEO ARCHIVE"


def load_settings() -> Settings:
    data_dir = _path("ARCHIVE_DATA_DIR", PROJECT_ROOT / "data" / "archive")
    return Settings(
        db_server=_env("ARCHIVE_DB_SERVER", r".\SQLEXPRESS"),
        db_name=_env("ARCHIVE_DB_NAME", "video_archive"),
        db_driver=_env("ARCHIVE_DB_DRIVER", "ODBC Driver 18 for SQL Server"),
        db_login=_env("ARCHIVE_DB_LOGIN"),
        db_password=_env("ARCHIVE_DB_PASSWORD"),
        app_user=_env("ARCHIVE_DB_APP_USER", "archive_app"),
        data_dir=data_dir,
        keyframes_dir=_path("ARCHIVE_KEYFRAMES_DIR", data_dir / "keyframes"),
        sql_dir=_path("ARCHIVE_SQL_DIR", PROJECT_ROOT / "db"),
        siglip_model=_env("ARCHIVE_SIGLIP_MODEL",
                          str(PROJECT_ROOT / "models" / "siglip2-so400m-patch16-384")),
        siglip_index=_path("ARCHIVE_SIGLIP_INDEX", data_dir / "siglip2_subset.faiss"),
        visual_enabled=_env("ARCHIVE_VISUAL", "1") not in {"0", "false", "no"},
        # A search that cannot finish inside this budget is cancelled.
        search_timeout=int(_env("ARCHIVE_SEARCH_TIMEOUT", "30")),
        # Candidates each source contributes before fusion.
        search_pool=int(_env("ARCHIVE_SEARCH_POOL", "200")),
        session_hours=int(_env("ARCHIVE_SESSION_HOURS", "12")),
        secure_cookies=_env("ARCHIVE_SECURE_COOKIES", "0") in {"1", "true", "yes"},
        self_registration=_env("ARCHIVE_SELF_REGISTRATION", "1") not in {"0", "false", "no"},
        archive_label=_env("ARCHIVE_LABEL", "THƯ VIỆN SỐ / VIDEO ARCHIVE"),
    )
