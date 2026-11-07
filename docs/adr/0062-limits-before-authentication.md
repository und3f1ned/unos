---
status: accepted
date: 2026-11-06
area: security
decided-by: agent
---

# ADR-0062: Request bodies and WebSocket messages are limited before authentication

## Context

FastAPI reads and parses a request body before it runs the dependencies that check the credentials, so anyone who could reach the API could make the supervisor hold a body of any size, in memory or in a temporary file, before it answered 401. uvicorn reads a whole WebSocket message before the application gets it, including the first message of a socket, which comes before authentication (ADR-0031): up to 16 MiB by default, and far more through compression.

## Decision

- `RequestBodyLimitMiddleware` in `supervisor/src/request_limits.py`, installed in `supervisor/src/main.py`, limits every HTTP request body to 1 MiB (`MAX_REQUEST_BODY`) and answers 413: for a declared Content-Length over the limit before anything is read, and for a chunked body as soon as it passes the limit.
- The two uploads have their own limits and read their form only after the credentials are checked: they take the request itself instead of an UploadFile parameter and stream their one file into its destination (ADR-0169). File manager uploads (`/api/v1/files/upload`) have no size limit; backup uploads (`/api/v1/backup/upload`) are limited to the archive limit of 256 MiB plus 1 MiB of multipart framing (ADR-0166). A form carries one file, at most 16 small fields, and part headers of at most 8 KiB.
- uvicorn runs with `--ws-max-size 65536` and `--ws-per-message-deflate false` in `supervisor/Dockerfile` and `supervisor/Dockerfile.test`: a larger message closes the socket with 1009, and compressed messages are not accepted.
- Tests: `supervisor/tests/test_request_limits.py`, `supervisor/tests/test_upload_streaming.py`.

## Consequences

- No JSON body of the API comes near 1 MiB (the largest are a certificate with its key and chain, and the path lists of the file manager); a new endpoint that needs more must get its own path limit in `main.py`.
- The only message that clients send on a WebSocket is the authentication message, well under 1 KiB.
- uvicorn still has no limit on the size of request headers and no connection limit (ADR-0063).
