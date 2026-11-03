"""Command line: database setup, sample data, users, evaluation, web server."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from footage.config import PROJECT_ROOT, load_settings
from footage.db import (SAMPLE_PART, build_full_script, connect, init_database, query,
                    query_one, transaction)
from footage.search import SOURCES


def _sources(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    unknown = sorted(set(names) - set(SOURCES))
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown source: {', '.join(unknown)}")
    return names


def _batches(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def cmd_db_init(args, settings) -> int:
    if args.reset and not args.yes:
        print("--reset drops the whole database; add --yes to confirm", file=sys.stderr)
        return 2
    init_database(settings, reset=args.reset, sample=not args.no_sample)
    return 0


def cmd_db_build(args, settings) -> int:
    print(f"wrote {build_full_script(settings.sql_dir)}")
    return 0


def cmd_sample(args, settings) -> int:
    from footage.sample import generate, write_sample

    data = settings.data_dir
    text, counts = generate(
        frames_csv=args.frames or data / "frames.csv",
        map_dir=args.map_dir,
        ocr_sqlite=args.ocr or data / "ocr.sqlite",
        asr_sqlite=args.asr or data / "asr.sqlite",
        labels_csv=args.labels,
        batches=args.batches,
    )
    out = settings.sql_dir / SAMPLE_PART
    write_sample(out, text)
    print(f"wrote {out}: {counts}")
    return 0


def cmd_fetch_index(args, settings) -> int:
    from footage.fetch_index import fetch_subset

    fetch_subset(args.url, args.frames or settings.data_dir / "frames.csv", args.batches,
                 args.out or settings.siglip_index)
    return 0


def _read_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Mật khẩu: ")
    if first != getpass.getpass("Nhập lại: "):
        raise SystemExit("Hai lần nhập không khớp.")
    return first


def cmd_user_create(args, settings) -> int:
    from footage.auth import create_user

    password = _read_password(args)
    # The administrator's own connection: sp_create_user accepts db_owner.
    with transaction(settings, admin=True) as connection:
        organization_id = None
        if args.org:
            row = query_one(connection, "SELECT organization_id FROM dbo.organizations "
                                        "WHERE org_code = ?", args.org)
            if row is None:
                print(f"no organization with code {args.org}", file=sys.stderr)
                return 1
            organization_id = row["organization_id"]
        user_id = create_user(connection, args.username, password, args.role, args.full_name,
                              organization_id, args.type)
    print(f"created user {args.username} (id {user_id}) with roles {', '.join(args.role)}")
    return 0


def cmd_user_password(args, settings) -> int:
    from footage.auth import set_password

    password = _read_password(args)
    with transaction(settings, admin=True) as connection:
        row = query_one(connection, "SELECT user_id FROM dbo.users WHERE username = ?",
                        args.username)
        if row is None:
            print(f"no user {args.username}", file=sys.stderr)
            return 1
        set_password(connection, row["user_id"], password)
    print(f"password changed for {args.username}")
    return 0


def _visual(settings, sources):
    if "visual" not in sources or not settings.siglip_index.is_file():
        return None
    from footage.visual import VisualSearcher

    searcher = VisualSearcher(settings.siglip_model, settings.siglip_index)
    with transaction(settings) as connection:
        ids = [row["keyframe_id"] for row in query(connection, "SELECT keyframe_id FROM dbo.keyframes")]
    print("loading SigLIP2 ...", flush=True)
    searcher.load(ids)
    print(f"visual: {searcher.status}")
    return searcher


def cmd_search(args, settings) -> int:
    from footage.search import run_search

    visual = _visual(settings, args.sources)
    with transaction(settings) as connection:
        outcome = run_search(connection, args.text, args.sources, args.top_k,
                             settings.search_pool, visual)
    for note in outcome.notes:
        print(note)
    for row in outcome.results:
        print(f"{row['rank']:>3}  {row['video_id']}  {float(row['pts_time']):8.2f}s  "
              f"frame_idx={row['frame_idx']:<7} {row['evidence']}")
    print(f"{len(outcome.results)} results in {outcome.latency_ms} ms")
    return 0


def cmd_eval(args, settings) -> int:
    from footage.evaluate import run_evaluation

    visual = _visual(settings, args.sources)
    connection = connect(settings)
    try:
        run_id = run_evaluation(connection, args.name, args.sources, args.top_k,
                                settings.search_pool, args.tolerance, visual, args.limit)
        summary = query_one(connection, "SELECT * FROM dbo.v_eval_summary WHERE run_id = ?", run_id)
    finally:
        connection.close()
    for key, value in summary.items():
        print(f"{key:>16}: {value}")
    return 0


def cmd_serve(args, settings) -> int:
    import uvicorn

    from footage.web import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="footage", description="Digital audiovisual library on SQL Server")
    commands = parser.add_subparsers(dest="command", required=True)

    db = commands.add_parser("db", help="create the database or the single-file script")
    db_commands = db.add_subparsers(dest="db_command", required=True)
    init = db_commands.add_parser("init", help="create the database from db/*.sql")
    init.add_argument("--reset", action="store_true", help="drop the database first")
    init.add_argument("--yes", action="store_true")
    init.add_argument("--no-sample", action="store_true", help="skip 09_sample_data.sql")
    init.set_defaults(handler=cmd_db_init)
    build = db_commands.add_parser("build-script", help="write db/full_setup.sql")
    build.set_defaults(handler=cmd_db_build)

    sample = commands.add_parser("sample", help="generate db/09_sample_data.sql from the archive source files")
    sample.add_argument("--batches", type=_batches, default={"L23"})
    sample.add_argument("--frames", type=Path)
    sample.add_argument("--map-dir", type=Path,
                        default=PROJECT_ROOT / "data" / "map-keyframes" / "map-keyframes")
    sample.add_argument("--ocr", type=Path)
    sample.add_argument("--asr", type=Path)
    sample.add_argument("--labels", type=Path, default=PROJECT_ROOT / "labels" / "l23_requests.csv",
                        help="sample access requests with their verified moment")
    sample.set_defaults(handler=cmd_sample)

    fetch = commands.add_parser(
        "fetch-index", help="download only the SigLIP2 vectors of some batches")
    fetch.add_argument("--batches", type=_batches, default={"L23"})
    fetch.add_argument("--frames", type=Path)
    fetch.add_argument("--out", type=Path)
    fetch.add_argument("--url", default=(
        "https://huggingface.co/datasets/oh-i-ace/aic-indexes/resolve/main/"
        "siglip2/siglip2_so400m_patch16_384_fp16.faiss"))
    fetch.set_defaults(handler=cmd_fetch_index)

    users = commands.add_parser("user", help="manage accounts").add_subparsers(
        dest="user_command", required=True)
    create = users.add_parser("create")
    create.add_argument("username")
    create.add_argument("--role", action="append", choices=("reader", "librarian", "admin"),
                        required=True)
    create.add_argument("--full-name", default="")
    create.add_argument("--org", help="organisation code, e.g. LIB")
    create.add_argument("--type", default="other",
                        choices=("staff", "journalist", "researcher", "student", "other"))
    create.add_argument("--password-stdin", action="store_true")
    create.set_defaults(handler=cmd_user_create)
    password = users.add_parser("password")
    password.add_argument("username")
    password.add_argument("--password-stdin", action="store_true")
    password.set_defaults(handler=cmd_user_password)

    search = commands.add_parser("search", help="run one search from the terminal")
    search.add_argument("text")
    search.add_argument("--sources", type=_sources, default=list(SOURCES))
    search.add_argument("--top-k", type=int, default=20)
    search.set_defaults(handler=cmd_search)

    evaluate = commands.add_parser("eval", help="score every labelled query")
    evaluate.add_argument("--name", required=True)
    evaluate.add_argument("--sources", type=_sources, default=list(SOURCES))
    evaluate.add_argument("--top-k", type=int, default=100)
    evaluate.add_argument("--tolerance", type=float, default=3.0,
                          help="seconds around the labelled moment that count as a hit")
    evaluate.add_argument("--limit", type=int)
    evaluate.set_defaults(handler=cmd_eval)

    serve = commands.add_parser("serve", help="start the web interface")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, load_settings())


if __name__ == "__main__":
    sys.exit(main())
