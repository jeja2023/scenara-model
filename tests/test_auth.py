from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import scenara_model.api as api
from scenara_model.auth import hash_password, token_digest, verify_password
from scenara_model.storage import MetadataStore

TEST_ADMIN_PASSWORD = "test-admin-password"


@pytest.fixture(autouse=True)
def isolated_api_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    previous_store = api.STORE
    api.STORE = MetadataStore(":memory:")
    # 引导口令已改为随机生成，测试注入固定值以保持可预期。
    monkeypatch.setattr(api, "SETTINGS", replace(api.SETTINGS, admin_password=TEST_ADMIN_PASSWORD))
    try:
        yield
    finally:
        api.STORE = previous_store


def _client() -> TestClient:
    client = TestClient(api.app)
    api._bootstrap_admin_user()
    return client


def _login(client: TestClient, username: str = api.DEFAULT_ADMIN_USERNAME, password: str = TEST_ADMIN_PASSWORD) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["token"]


def test_password_hash_roundtrip() -> None:
    salt, digest = hash_password("s3cret!")

    assert verify_password("s3cret!", salt, digest) is True
    assert verify_password("wrong", salt, digest) is False
    assert verify_password("s3cret!", "not-hex", digest) is False


def test_bootstrap_creates_default_admin() -> None:
    _client()

    user = api.STORE.get_user_by_username(api.DEFAULT_ADMIN_USERNAME)

    assert user["role"] == "admin"
    # 引导幂等：再次调用不会重复创建。
    api._bootstrap_admin_user()
    assert api.STORE.count_users() == 1


def test_login_success_returns_session_token() -> None:
    client = _client()

    response = client.post("/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert body["token"]
    assert body["expires_at"] > "2026"
    # DB 中只存令牌摘要，不存原文。
    session = api.STORE.get_auth_session(token_digest(body["token"]))
    assert session is not None


def test_login_rejects_bad_password_and_unknown_user() -> None:
    client = _client()

    bad_password = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    unknown_user = client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})

    assert bad_password.status_code == 401
    assert unknown_user.status_code == 401
    # 防枚举：两种失败返回相同提示。
    assert bad_password.json()["detail"] == unknown_user.json()["detail"]


def test_api_requires_auth() -> None:
    client = _client()

    assert client.get("/api/experiments").status_code == 401
    assert client.get("/api/pipelines/runs").status_code == 401
    assert client.post("/api/manifests/validate", json={"path": "x"}).status_code == 401
    assert client.get("/api/experiments", headers={"Authorization": "Bearer bogus"}).status_code == 401
    # /health 保持公开（容器健康检查）。
    assert client.get("/health").status_code == 200


def test_session_token_grants_access() -> None:
    client = _client()
    token = _login(client)

    response = client.get("/api/experiments", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


def test_static_token_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    # SETTINGS 是 frozen dataclass，整体替换而非原地修改。
    monkeypatch.setattr(api, "SETTINGS", replace(api.SETTINGS, auth_token="static-token"))

    response = client.get("/api/experiments", headers={"Authorization": "Bearer static-token"})

    assert response.status_code == 200


def test_logout_revokes_session() -> None:
    client = _client()
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    logout_response = client.post("/api/auth/logout", headers=headers)

    assert logout_response.status_code == 200
    assert client.get("/api/experiments", headers=headers).status_code == 401


def test_me_returns_current_identity() -> None:
    client = _client()
    token = _login(client)

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert body["expires_at"] is not None


def test_password_change_revokes_existing_sessions() -> None:
    client = _client()
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/experiments", headers=headers).status_code == 200

    salt, digest = hash_password("new-password")
    api.STORE.update_user_password("admin", salt, digest)

    assert client.get("/api/experiments", headers=headers).status_code == 401
    new_token = _login(client, password="new-password")
    assert client.get("/api/experiments", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200


def test_login_is_throttled_after_repeated_failures() -> None:
    """回归：登录失败必须限流——PBKDF2 校验也是一条无需认证的 DoS 放大路径。"""
    client = _client()
    throttle_key = "admin|testclient"
    api.LOGIN_THROTTLE.reset(throttle_key)
    try:
        for _ in range(api.SETTINGS.login_max_failures):
            assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
        blocked = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})

        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"]
        # 限流期间即使口令正确也拒绝。
        assert client.post("/api/auth/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}).status_code == 429
    finally:
        api.LOGIN_THROTTLE.reset(throttle_key)


def test_bootstrap_generates_password_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：未配置 SCENARA_MODEL_ADMIN_PASSWORD 时不得回落到固定弱口令。"""
    monkeypatch.setattr(api, "SETTINGS", replace(api.SETTINGS, admin_password=None))

    generated = api._bootstrap_admin_user()

    assert generated and len(generated) >= 12
    assert not hasattr(api, "DEFAULT_ADMIN_PASSWORD")


def test_expired_session_rejected() -> None:
    _client()
    user = api.STORE.get_user_by_username("admin")
    api.STORE.create_auth_session("expired-hash", int(user["id"]), "admin", "2020-01-01T00:00:00.000Z")

    assert api.STORE.get_auth_session("expired-hash") is None
    purged = api.STORE.purge_expired_auth_sessions()
    assert purged >= 1
