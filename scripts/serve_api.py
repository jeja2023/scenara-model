from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description="?? Scenara Model ?? API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--metadata-db", default=os.environ.get("SCENARA_MODEL_METADATA_DB", "artifacts/scenara_model.sqlite3"))
    args = parser.parse_args()

    os.environ.setdefault("SCENARA_MODEL_WORKSPACE", str(Path(__file__).resolve().parents[1]))
    os.environ["SCENARA_MODEL_METADATA_DB"] = args.metadata_db
    if args.metadata_db == ":memory:":
        print("[警告] 元数据库为 :memory:，服务重启后所有实验/审批/审计记录将全部丢失。", flush=True)

    import uvicorn

    uvicorn.run(
        "scenara_model.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(Path(__file__).resolve().parents[1] / "src")] if args.reload else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

