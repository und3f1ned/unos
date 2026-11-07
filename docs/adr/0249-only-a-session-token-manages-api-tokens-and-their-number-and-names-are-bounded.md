---
status: accepted
date: 2026-11-07
area: auth
decided-by: agent
---

# ADR-0249: Only a session token manages API tokens, and their number, names and permissions are bounded

## Context

The token routes took every credential that `verify_token` accepts, API tokens included, so a token could create tokens without an expiry, which outlast its own lifetime and its revocation (entry `auth.api-tokens`). `POST /api/v1/auth/tokens` also took a name and permissions of any length and any number of tokens per user, so every signed-in user could add to the database on the system disk without a limit (ADR-0214). The permissions are stored but not checked (`auth.api-token-scopes`): a token created with fewer would still act with all of its owner's rights.

## Decision

- `require_session` in `supervisor/src/auth/utils.py` guards `POST /api/v1/auth/tokens`, `POST /api/v1/auth/tokens/{id}/toggle` and `DELETE /api/v1/auth/tokens/{id}`. A valid API token gets 403 "An API token cannot create, enable, disable or delete API tokens: sign in to do it"; one that is invalid, disabled or expired keeps its 401 from `verify_token`. So only a session token, which needs the password and the second factor (ADR-0239), changes tokens. `GET /api/v1/auth/tokens` still takes an API token: it changes nothing and never shows a token.
- `APITokenCreate` in `supervisor/src/auth/schemas.py`: the name loses the white space around it and must then have 1 to 64 characters (`API_TOKEN_NAME_CHARS`), and the permissions take only `"*"` until `auth.api-token-scopes` checks scopes. Anything else is a 422, which does not echo the values (ADR-0065).
- A user has at most 100 tokens (`MAX_API_TOKENS_PER_USER` in `supervisor/src/auth/router.py`); one more answers 409 "You already have 100 API tokens, the most a user can have: delete one first". The count follows the insert's flush, which holds SQLite's write lock until the commit, so two requests at the same moment cannot both take the last place.
- Tests: `supervisor/tests/test_authz.py` (`TestApiTokensAreManagedWithASession`, `TestApiTokenBounds`) and `webui/tests/api-tokens.spec.ts`.

## Consequences

- A token no longer makes API tokens that outlast its lifetime or its revocation. The other changes that a token makes with its owner's rights last as they would if its owner had made them. A script can neither replace nor revoke its own token: its owner does that after signing in.
- A user's tokens are at most 100 rows of bounded size in the database.
- A client that asks for other permissions than `"*"` gets 422, instead of a token that can do more than it says.
- The token routes have no rate limit, like the other routes that a signed-in user writes with: the limit per user bounds the size of the database, not the number of its commits.

## Considered options

- Capping the expiry of a token made with a token at its maker's: such a token would still outlast a disable or a deletion of its maker.
- Refusing API tokens on `GET /api/v1/auth/tokens` too: a script could no longer read its own token's expiry.
- Only a maximum length for the permissions: a stored scope that is not checked misleads whoever reads it.
