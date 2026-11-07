"""422 answers never repeat the values that were sent (spec 5.1, I2).

FastAPI's default answer to a request that fails validation lists, for each
error, the rejected value (``input``) and the error context (``ctx``): a
password that is too long came back in the body, and so in every log or
proxy that records answers.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.auth.utils import require_admin, verify_token
from src.main import app

ADMIN = SimpleNamespace(id=1, username="admin", is_admin=True)


@pytest.fixture
def client():
    app.dependency_overrides[verify_token] = lambda: ADMIN
    app.dependency_overrides[require_admin] = lambda: ADMIN
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_token, None)
        app.dependency_overrides.pop(require_admin, None)


def _keys(value) -> set[str]:
    """Every key of every object in a JSON value."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def test_a_rejected_password_is_not_in_the_answer(client):
    password = "Sekr1t-" + "p" * 1993  # 2000 characters, more than bcrypt's 72 bytes

    response = client.post("/api/v1/auth/users", json={"username": "carol", "password": password})

    assert response.status_code == 422
    body = response.json()
    assert password not in response.text and "Sekr1t" not in response.text
    assert not {"input", "ctx"} & _keys(body)
    (error,) = body["detail"]
    assert error["loc"] == ["body", "password"]
    assert error["msg"] == "Value error, Password must not be longer than 72 bytes (UTF-8 encoded)"
    assert error["type"] == "value_error"


def test_a_rejected_share_body_is_not_in_the_answer(client):
    comment = "secret-comment-" + "c" * 1100  # longer than the 1024 characters a comment may have

    response = client.post(
        "/api/v1/shares/smb", json={"name": "docs", "path": "/mnt/pools/data/docs", "comment": comment}
    )

    assert response.status_code == 422
    assert "secret-comment" not in response.text
    assert not {"input", "ctx"} & _keys(response.json())


def test_every_error_is_kept_without_its_value(client):
    response = client.patch(
        "/api/v1/shares/smb/docs",
        content=json.dumps({"browseable": "not-a-boolean", "readonly": "secret-value"}),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert {(tuple(error["loc"]), error["type"]) for error in detail} == {
        (("body", "browseable"), "bool_parsing"),
        (("body", "readonly"), "extra_forbidden"),
    }
    assert all(set(error) <= {"type", "loc", "msg", "url"} for error in detail)
    assert "not-a-boolean" not in response.text and "secret-value" not in response.text
