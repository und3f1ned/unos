---
status: accepted
date: 2026-11-05
area: security
decided-by: agent
---

# ADR-0026: PyJWT and bcrypt replace python-jose and passlib, and dependencies are pinned

## Context

In November 2026 pip-audit reported 25 unique CVEs in 7 Python packages, among them a denial of service before authentication (python-multipart 0.0.6 with Starlette 0.35.1) that the audit confirmed. python-jose and passlib were abandoned, docker 6.1.3 held requests below 2.32 with its CVEs, and the test packages were installed into the production image.

## Decision

- JWTs are signed and checked with PyJWT, and passwords are hashed with bcrypt directly, instead of python-jose and passlib (`supervisor/src/auth/utils.py`).
- Hashes made by the old code keep working: `verify_password` reads only the first 72 bytes of a password, as passlib's bcrypt did, and new passwords longer than 72 bytes get 422 (`MAX_PASSWORD_BYTES` in `supervisor/src/auth/schemas.py`).
- The runtime dependencies are pinned to exact, current versions in `supervisor/requirements.txt`, among them FastAPI 0.162, Starlette 1.10, Uvicorn 0.54, Pydantic 2.14, SQLAlchemy 2.1, cryptography 50, docker 7.2 and requests 2.34.
- Test and development tools live in `supervisor/requirements-dev.txt`; the production image installs only `requirements.txt` (`supervisor/Dockerfile`).
- The web UI image installs exactly what `webui/package-lock.json` pins (`npm ci` in `webui/Dockerfile`).
- Test: `supervisor/tests/test_authz.py` (`test_new_passwords_longer_than_bcrypt_limit_are_rejected`).

## Consequences

- An update is a deliberate change of a pin, checked by the tests: CI's pip-audit fails on a vulnerable pin (ADR-0190), and Dependabot proposes updates every week (ADR-0218).
- The truncation in `verify_password` serves only hashes that the passlib code made before Phase 0; under ADR-0207 it is legacy, and its removal is the `feature-list.json` entry `auth.no-legacy-password-truncation`. The 72-byte limit for new passwords stays.

## Considered options

- argon2, which the audit also named: not chosen; the audit confirmed the bcrypt hashing as correct (ADR-0032), and the existing hashes keep working with it.
