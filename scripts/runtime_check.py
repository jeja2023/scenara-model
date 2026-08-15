from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def fetch(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        # 服务未启动/连接拒绝是本脚本最核心的检测场景，必须输出合法报告而非崩溃。
        return 0, f"connection failed: {exc.reason}"
    except OSError as exc:
        return 0, f"connection failed: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查正在运行的视觉模型研发平台实例")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    checks: list[dict[str, object]] = []
    health_status, health_body = fetch(f"{base_url}/health")
    checks.append(
        {
            "name": "health",
            "ok": health_status == 200 and '"status"' in health_body and '"ok"' in health_body,
            "status": health_status,
        }
    )

    home_status, home_body = fetch(f"{base_url}/")
    has_brand = "视觉模型研发平台" in home_body or "Scenara Model" in home_body
    has_frontend_asset = "/assets/" in home_body or "模型交付控制台" in home_body or "Delivery Console" in home_body
    checks.append(
        {
            "name": "frontend",
            "ok": home_status == 200 and has_brand and has_frontend_asset,
            "status": home_status,
        }
    )

    docs_status, docs_body = fetch(f"{base_url}/docs")
    checks.append(
        {
            "name": "openapi_docs",
            "ok": docs_status == 200 and "swagger" in docs_body.lower(),
            "status": docs_status,
        }
    )

    report = {"ok": all(bool(check["ok"]) for check in checks), "base_url": base_url, "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
