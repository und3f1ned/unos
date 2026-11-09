---
status: accepted
date: 2026-11-09
area: security
decided-by: agent
supersedes: [ADR-0026]
---

# ADR-0315: PyJWT and bcrypt check passwords as given, and dependencies are pinned

## Context

ADR-0026 replaced python-jose and passlib with PyJWT and bcrypt and pinned the dependencies; for
the hashes that the passlib code made before Phase 0, which cut longer passwords to bcrypt's 72
bytes, `verify_password` cut every password the same way. UNOS has no installation with such hashes
(ADR-0207): the dev device's passwords were set again since (ADR-0226), and the cut let a password
longer than 72 bytes sign in with any ending after its 72nd byte. Working on through the features
under the owner's delegation ("work on through the features, ask no questions; I allow
everything", in translation, 2026-11-09), the agent removed the cut
(`auth.no-legacy-password-truncation`).

## Decision

- JWTs are signed and checked with PyJWT, and passwords are hashed with bcrypt directly
  (`supervisor/src/auth/utils.py`).
- New passwords longer than 72 bytes (UTF-8) get 422 (`MAX_PASSWORD_BYTES` in
  `supervisor/src/auth/schemas.py`). `verify_password` checks a password as given: one longer than
  72 bytes matches no hash and fails like a wrong password, also at the sign-in, whose request
  takes passwords of up to 1024 characters (ADR-0062).
- The runtime dependencies are pinned to exact versions in `supervisor/requirements.txt`; test and
  development tools live in `supervisor/requirements-dev.txt`, and the production image installs
  only `requirements.txt` (`supervisor/Dockerfile`). The web UI image installs exactly what
  `webui/package-lock.json` pins (`npm ci` in `webui/Dockerfile`).
- Tests: `supervisor/tests/test_auth.py` (`TestPasswordHashing`, among them
  `test_a_password_longer_than_72_bytes_matches_no_hash`, and
  `test_a_sign_in_with_a_password_longer_than_72_bytes_fails_like_a_wrong_one`), and
  `supervisor/tests/test_authz.py` (`test_new_passwords_longer_than_bcrypt_limit_are_rejected`).

## Consequences

- A hash that the passlib code made of a password longer than 72 bytes no longer signs in; the
  emergency command line sets a new password (ADR-0284).
- An update is a deliberate change of a pin, checked by the tests: pip-audit fails on a vulnerable
  pin (ADR-0190), and Dependabot proposes updates every week (ADR-0218).

## Considered options

- argon2, which the audit also named: not chosen; the audit confirmed the bcrypt hashing as correct
  (ADR-0239), and the existing hashes keep working with it.
