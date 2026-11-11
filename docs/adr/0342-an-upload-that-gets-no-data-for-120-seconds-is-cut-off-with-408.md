---
status: accepted
date: 2026-11-11
area: files
decided-by: agent
---

# ADR-0342: An upload that gets no data for 120 seconds is cut off with 408

## Context

Neither uvicorn nor Caddy limits the time a request body takes, and Caddy streams the body to the supervisor. A client that stopped sending in the middle of a file manager or backup upload, its connection still open, held the request and the upload's destination file, with the space of what had arrived, for as long as that connection stayed open (ADR-0167). The entry `files.upload-edge-cases` asks for a read timeout, but not its value or what it covers.

## Decision

- `receive_upload` in `supervisor/src/request_limits.py` waits at most `UPLOAD_IDLE_TIMEOUT` (120) seconds for each chunk of an upload's body, from the first chunk to the end of the body. It is an idle timeout, not a limit on the whole upload: the time between two chunks counts, the disk writes between them do not, and a client that keeps sending, however slowly, is not cut off. nginx's `client_body_timeout` works the same way, with 60 seconds by default.
- When it expires, the upload answers HTTP 408 "The upload timed out: the client sent nothing for 120 seconds" with `Connection: close` (RFC 9110), and the sink's `discard()` runs as after any other failure: the unnamed or hidden file of a file manager upload, or the temporary file of a backup upload, is gone. The rest of the body is never read.
- It applies to both uploads, since both go through `receive_upload` (ADR-0169), and to no other request: their bodies are limited to 1 MiB before the credentials are checked (ADR-0062).
- 120 seconds leaves room for a Wi-Fi roam, a short network outage that TCP retransmissions cover, or a busy client, and frees a stalled upload's space within minutes.
- Tests: `supervisor/tests/test_upload_streaming.py` (a client that stops before the body or in the file gets 408 and leaves nothing, also through uvicorn; one that sends a chunk every quarter of the timeout is stored).

## Consequences

- A stalled upload ends 2 minutes after its last data, and nothing of it is left; the user uploads the file again from the start.
- A client that sends a few bytes at least every 120 seconds still holds its upload and its connection. Only an administrator's session gets that far, since the body is read after the credentials are checked (ADR-0062), and the connection limit of uvicorn bounds how many can (ADR-0333).
- A client that pauses for longer than 120 seconds, for example a laptop that sleeps in the middle of an upload, loses that upload.
