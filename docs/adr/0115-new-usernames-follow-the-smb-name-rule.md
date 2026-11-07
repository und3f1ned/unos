---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0115: New UNOS usernames follow the SMB name rule and are unique ignoring case

## Context

Before slice 2, user creation accepted any name, including an empty one, `root`, `-o` and
names with line breaks. With SMB a username becomes a host account name and a tdbsam key,
and tdbsam treats Bob and bob as one account.

## Decision

`security.validators.validate_new_username` applies `constants.linux_name_problem`
(ADR-0113, ADR-0114) to every new UNOS user: `POST /auth/setup` and `POST /auth/users`
answer 400 with "Invalid username." followed by the rule. It runs in the handlers, not in a
Pydantic validator, so that the answer is a 400 with a plain message. `POST /auth/users`
also refuses a name that equals an existing one ignoring case (400 "Username already
exists"). Existing users keep their names and can still log in; those whose names break the
rule cannot use SMB.

Implemented in `supervisor/src/security/validators.py` and `supervisor/src/auth/router.py`;
tested in `supervisor/tests/test_usernames.py`.

## Consequences

Every new user who is not an administrator can have SMB, and no two UNOS users collide in
passdb. Names with capitals, dots or other characters can no longer be created.
