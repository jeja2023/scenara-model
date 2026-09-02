from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from scenara_model.contracts import validate_models_fragment, validate_release_decision
from scenara_model.datasets.manifest import validate_manifest
from scenara_model.packaging.model_package import create_model_package, validate_model_package
from scenara_model.settings import load_settings
from scenara_model.storage import metadata_store_from_uri
from scenara_model.utils import sha256_file


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _cmd_hash(args: argparse.Namespace) -> int:
    print(sha256_file(args.path))
    return 0


def _cmd_validate_package(args: argparse.Namespace) -> int:
    package_dir = Path(args.package_dir)
    if not args.model_id and not list(package_dir.glob("*.onnx")):
        model_files = sorted(package_dir.rglob("*.onnx"))
        if model_files:
            results = [
                validate_model_package(
                    model_file.parent,
                    model_id=model_file.name,
                    strict_hash=args.strict_hash,
                    strict_sidecars=not args.allow_missing_sidecars,
                    strict_examples=not args.allow_missing_examples,
                    strict_onnx=args.strict_onnx,
                    strict_provenance=args.strict_provenance,
                )
                for model_file in model_files
            ]
            payload = {"root": str(package_dir), "ok": all(result.ok for result in results), "packages": [result.to_dict() for result in results]}
            if args.json:
                _print_json(payload)
            else:
                print(f"{'OK' if payload['ok'] else 'FAILED'}: {package_dir}")
                for result in results:
                    model_name = result.model_file.name if result.model_file else str(result.package_dir)
                    print(f"- {'OK' if result.ok else 'FAILED'}: {model_name}")
                    if result.sha256:
                        print(f"  sha256: {result.sha256}")
                    for issue in result.issues:
                        print(f"  [{issue.severity}] {issue.code}: {issue.message} {issue.path}".rstrip())
            return 0 if payload["ok"] else 2
    result = validate_model_package(
        args.package_dir,
        model_id=args.model_id,
        strict_hash=args.strict_hash,
        strict_sidecars=not args.allow_missing_sidecars,
        strict_examples=not args.allow_missing_examples,
        strict_onnx=args.strict_onnx,
        strict_provenance=args.strict_provenance,
    )
    if args.json:
        _print_json(result.to_dict())
    else:
        status = "OK" if result.ok else "FAILED"
        print(f"{status}: {result.package_dir}")
        if result.sha256:
            print(f"sha256: {result.sha256}")
        for issue in result.issues:
            print(f"[{issue.severity}] {issue.code}: {issue.message} {issue.path}".rstrip())
    return 0 if result.ok else 2


def _cmd_create_package(args: argparse.Namespace) -> int:
    labels = args.label or None
    package_dir = create_model_package(
        output_root=args.output_root,
        project_name=args.project,
        artifact_name=args.artifact_name,
        model_file=args.model_file,
        labels_file=args.labels_file,
        labels=labels,
        task=args.task,
        architecture=args.architecture,
        examples_dir=args.examples_dir,
        model_card=args.model_card,
        overwrite=args.overwrite,
    )
    result = validate_model_package(package_dir, model_id=args.artifact_name, strict_hash=True, strict_examples=False)
    _print_json({"package_dir": str(package_dir), "validation": result.to_dict()})
    return 0 if result.ok else 2


def _cmd_validate_manifest(args: argparse.Namespace) -> int:
    result = validate_manifest(args.path, check_local_files=args.check_local_files)
    if args.json:
        _print_json(result.to_dict())
    else:
        status = "OK" if result.ok else "FAILED"
        print(f"{status}: {result.path}")
        print(f"rows: {result.total_rows}")
        print(f"splits: {result.split_counts}")
        for issue in result.issues:
            location = f" line={issue.line}" if issue.line else ""
            field = f" field={issue.field}" if issue.field else ""
            print(f"[error] {issue.code}:{location}{field} {issue.message}")
    return 0 if result.ok else 2


def _cmd_validate_contract(args: argparse.Namespace) -> int:
    if args.kind == "models-fragment":
        result = validate_models_fragment(args.path)
    else:
        result = validate_release_decision(args.path)
    if args.json:
        _print_json(result.to_dict())
    else:
        status = "OK" if result.ok else "FAILED"
        print(f"{status}: {result.path}")
        for issue in result.issues:
            print(f"[error] {issue.code}: {issue.message} {issue.path}".rstrip())
    return 0 if result.ok else 2


def _sqlite_path_from_uri(uri: str) -> Path | None:
    """返回 SQLite 文件路径；PG DSN 或 :memory: 返回 None。"""
    if uri == ":memory:" or "://" in uri:
        return None
    path = Path(uri)
    if not path.is_absolute():
        workspace = Path(os.environ.get("SCENARA_MODEL_WORKSPACE", Path.cwd())).resolve()
        path = workspace / path
    return path


def _needs_alembic_stamp(uri: str) -> bool:
    """存量库检测：基础表已由旧版内置 DDL 建好、但 Alembic 版本戳缺失或为空。

    这种库直接 upgrade 会因 'table already exists' 失败，需要先 stamp head。
    （失败的 upgrade 可能留下空的 alembic_version 表，同样按缺戳处理。）
    """
    db_path = _sqlite_path_from_uri(uri)
    if db_path is None or not db_path.exists() or db_path.stat().st_size == 0:
        return False
    import sqlite3

    try:
        with sqlite3.connect(db_path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "experiments" not in tables:
                return False
            if "alembic_version" not in tables:
                return True
            stamped = connection.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0]
            return int(stamped) == 0
    except sqlite3.Error:
        return False


def _cmd_storage_migrate(args: argparse.Namespace) -> int:
    settings = load_settings()
    uri = args.uri or settings.metadata_db
    # Alembic 可用时优先走正式迁移链（schema 单一权威）；否则回落到内置建表。
    migrated_via = "builtin"
    if uri != ":memory:":
        try:
            import alembic.command
            import alembic.config

            root = Path(__file__).resolve().parents[2]
            ini_path = root / "alembic.ini"
            if ini_path.exists():
                os.environ["SCENARA_MODEL_METADATA_DB"] = uri
                config = alembic.config.Config(str(ini_path))
                config.set_main_option("script_location", str(root / "migrations"))
                if _needs_alembic_stamp(uri):
                    # 旧版内置 DDL 创建的存量库：schema 与基线一致，补记版本戳后再升级。
                    alembic.command.stamp(config, "head")
                    migrated_via = "alembic (stamped existing schema)"
                else:
                    migrated_via = "alembic"
                alembic.command.upgrade(config, "head")
        except ImportError:
            pass
    store = metadata_store_from_uri(uri)
    store.initialize()
    _print_json({"ok": True, "metadata_db": uri, "migrated_via": migrated_via})
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("scenara_model.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _cmd_user_set_password(args: argparse.Namespace) -> int:
    from scenara_model.auth import hash_password

    settings = load_settings()
    store = metadata_store_from_uri(args.uri or settings.metadata_db)
    salt, digest = hash_password(args.password)
    try:
        store.get_user_by_username(args.username)
        store.update_user_password(args.username, salt, digest)
        created = False
    except KeyError:
        if not args.create:
            print(f"用户 {args.username!r} 不存在；使用 --create 创建。", flush=True)
            return 1
        store.create_user(args.username, salt, digest, role=args.role)
        created = True
    store.record_audit_event(
        actor="cli",
        action="user.create" if created else "user.set-password",
        target=args.username,
        detail={},
    )
    _print_json({"ok": True, "username": args.username, "created": created})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scenara-model", description="Scenara Model ????")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hash_parser = subparsers.add_parser("hash", help="????? sha256 ??")
    hash_parser.add_argument("path", type=Path)
    hash_parser.set_defaults(func=_cmd_hash)

    package_parser = subparsers.add_parser("package", help="?????")
    package_subparsers = package_parser.add_subparsers(dest="package_command", required=True)

    validate_parser = package_subparsers.add_parser("validate", help="?????")
    validate_parser.add_argument("package_dir", type=Path)
    validate_parser.add_argument("--model-id", help="??? package_dir ? ONNX ???")
    validate_parser.add_argument("--strict-hash", action="store_true")
    validate_parser.add_argument("--strict-onnx", action="store_true")
    validate_parser.add_argument("--strict-provenance", action="store_true")
    validate_parser.add_argument("--allow-missing-sidecars", action="store_true")
    validate_parser.add_argument("--allow-missing-examples", action="store_true")
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.set_defaults(func=_cmd_validate_package)

    create_parser = package_subparsers.add_parser("create", help="???????")
    create_parser.add_argument("--output-root", type=Path, default=Path("shared-models"))
    create_parser.add_argument("--project", required=True)
    create_parser.add_argument("--artifact-name", required=True)
    create_parser.add_argument("--model-file", type=Path, required=True)
    create_parser.add_argument("--labels-file", type=Path)
    create_parser.add_argument("--label", action="append")
    create_parser.add_argument("--task", required=True)
    create_parser.add_argument("--architecture", required=True)
    create_parser.add_argument("--examples-dir", type=Path)
    create_parser.add_argument("--model-card", type=Path)
    create_parser.add_argument("--overwrite", action="store_true")
    create_parser.set_defaults(func=_cmd_create_package)

    manifest_parser = subparsers.add_parser("manifest", help="???????")
    manifest_subparsers = manifest_parser.add_subparsers(dest="manifest_command", required=True)
    manifest_validate_parser = manifest_subparsers.add_parser("validate", help="?? JSONL ?????")
    manifest_validate_parser.add_argument("path", type=Path)
    manifest_validate_parser.add_argument("--json", action="store_true")
    manifest_validate_parser.add_argument("--check-local-files", action="store_true")
    manifest_validate_parser.set_defaults(func=_cmd_validate_manifest)

    contract_parser = subparsers.add_parser("contract", help="??????")
    contract_subparsers = contract_parser.add_subparsers(dest="contract_command", required=True)
    contract_validate_parser = contract_subparsers.add_parser("validate", help="???? YAML ??")
    contract_validate_parser.add_argument("kind", choices=["models-fragment", "release-decision"])
    contract_validate_parser.add_argument("path", type=Path)
    contract_validate_parser.add_argument("--json", action="store_true")
    contract_validate_parser.set_defaults(func=_cmd_validate_contract)

    storage_parser = subparsers.add_parser("storage", help="???????")
    storage_subparsers = storage_parser.add_subparsers(dest="storage_command", required=True)
    storage_migrate_parser = storage_subparsers.add_parser("migrate", help="???????????")
    storage_migrate_parser.add_argument("--uri", help="?? SCENARA_MODEL_METADATA_DB ????")
    storage_migrate_parser.set_defaults(func=_cmd_storage_migrate)

    serve_parser = subparsers.add_parser("serve", help="???? API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.set_defaults(func=_cmd_serve)

    user_parser = subparsers.add_parser("user", help="???????")
    user_subparsers = user_parser.add_subparsers(dest="user_command", required=True)
    set_password_parser = user_subparsers.add_parser("set-password", help="?????? --create ??????????")
    set_password_parser.add_argument("--username", required=True)
    set_password_parser.add_argument("--password", required=True)
    set_password_parser.add_argument("--create", action="store_true", help="???????????")
    set_password_parser.add_argument("--role", default="admin")
    set_password_parser.add_argument("--uri", help="?? SCENARA_MODEL_METADATA_DB ????")
    set_password_parser.set_defaults(func=_cmd_user_set_password)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
