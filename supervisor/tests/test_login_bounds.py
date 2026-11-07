"""Bounded login and audit input (ADR-0065).

A failed login needs no authentication, yet it adds an entry to audit_log.json (which
every audit write rewrites whole) and a row to login_attempts. LoginRequest bounds its
fields, and log_action and record_login_attempt bound what they store.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.audit import router as audit_router
from src.audit.schemas import AuditAction
from src.security.models import LoginAttempt

from .test_smb_accounts import API, World


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    w = World(tmp_path, monkeypatch, caplog)
    try:
        yield w
    finally:
        w.close()


def attempts(world: World) -> list[str]:
    with Session(world.engine) as session:
        return list(session.scalars(select(LoginAttempt.username).order_by(LoginAttempt.id)))


class TestLoginRequest:
    def test_one_failed_login_with_a_huge_username(self, world):
        """About 1 MiB of non-ASCII username (6 bytes each in the
        file) and an 8000-character User-Agent once grew audit_log.json by 3 MB."""
        body = json.dumps({"username": "é" * 520_000, "password": "x"}, ensure_ascii=False).encode()

        response = world.client.post(f"{API}/auth/login", content=body,
                                     headers={"content-type": "application/json", "user-agent": "A" * 8000})

        assert response.status_code in (401, 422)
        assert not world.audit_file.exists() or world.audit_file.stat().st_size < 16_384
        assert len(response.content) < 4096  # the 422 does not echo the value

    @pytest.mark.parametrize("field, value", [
        ("username", "a" * 256),
        ("password", "p" * 1025),
        ("totp_code", "1" * 17),
    ])
    def test_limits(self, world, field, value):
        body = {"username": "alice", "password": "Wrong-pass-2026", field: value}

        response = world.client.post(f"{API}/auth/login", json=body)

        assert response.status_code == 422
        assert value not in response.text
        assert not world.audit_file.exists()
        assert attempts(world) == []

    def test_values_at_the_limits_are_a_plain_failed_login(self, world):
        body = {"username": "é" * 255, "password": "p" * 1024, "totp_code": "1" * 16}

        response = world.client.post(f"{API}/auth/login", json=body, headers={"user-agent": "A" * 8000})

        assert response.status_code == 401
        [entry] = json.loads(world.audit_file.read_text())
        assert entry["action"] == "login_failed"
        assert entry["username"] == "é" * 128
        assert entry["user_agent"] == "A" * 256
        assert attempts(world) == ["é" * 128]
        assert world.audit_file.stat().st_size < 4096


class TestLogAction:
    def test_every_caller_is_bounded(self, world):
        entry = asyncio.run(audit_router.log_action(
            action=AuditAction.OTHER,
            username="u" * 1000,
            resource_type="share",
            resource_id="r" * 1000,
            details="d" * 100_000,
        ))

        assert (len(entry.username), len(entry.resource_id), len(entry.details)) == (128, 256, 1024)
        [stored] = json.loads(world.audit_file.read_text())
        assert (stored["username"], stored["resource_id"], stored["details"]) == ("u" * 128, "r" * 256, "d" * 1024)

    def test_short_values_are_kept_as_they_are(self, world):
        entry = asyncio.run(audit_router.log_action(
            action=AuditAction.OTHER, username="admin", resource_id="media", details="Created share media",
        ))

        assert (entry.username, entry.resource_id, entry.details, entry.user_agent) == (
            "admin", "media", "Created share media", None
        )
