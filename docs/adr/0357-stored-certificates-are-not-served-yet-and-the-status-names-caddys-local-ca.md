---
status: superseded
date: 2026-11-12
area: security
decided-by: agent
superseded-by: ADR-0402
---

# ADR-0357: Stored certificates are not served yet, and the certificate status names Caddy's local CA

## Context

Superseded by ADR-0402: Caddy serves a stored certificate that an administrator chooses, from one file shared only with the supervisor, and falls back to its local CA.

System > Security stores uploaded and self-signed certificates in the supervisor's data volume (`/data/certs`), but Caddy serves only the certificate of its local CA (ADR-0025), in the `unos-webui` container, the only one that mounts `caddy-data` (`deploy/docker-compose.yml`): nothing hands a stored certificate to it. Yet the status named the first valid stored certificate as the active one, and the listing looked for Caddy's certificates in a directory of the supervisor's own volume that nothing fills. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent made the status say what is served and left serving stored certificates for later.

## Decision

- Serving a stored certificate is deferred: steps 2 and 3 of `security.custom-certificates` stay open. Meanwhile `GET /api/v1/security/certificates/status` answers `{"served_by": "caddy_local_ca"}`, and `GET /api/v1/security/certificates` lists the stored certificates, each with `served: false`, beside the same `served_by` (`ServedCertificate`, `Certificate` and `CertificateListResponse` of `supervisor/src/security/schemas.py`).
- The supervisor reads none of Caddy's certificates: `CADDY_CERTS_DIR` and `get_caddy_certificates` are gone, and with them the type `lets_encrypt`, `auto_renew`, `active_certificate` and the unused `LetsEncryptRequest` (ADR-0207).
- The **SSL/TLS Certificates** card of `webui/src/routes/system/tabs/SecurityTab.svelte` shows **Caddy's local CA** as its badge; under **Manage** it says "HTTPS for browsers: Caddy's local CA", explains that uploaded and self-signed certificates are only stored, and marks each stored certificate **NOT SERVED** (`account.certs.*` of `webui/src/lib/i18n/catalog/account.ts`, in both languages). A listing that cannot be loaded is reported with a **Retry**, not shown as empty and not as the list of an earlier load; every load clears that report. The dialog of **Generate Self-Signed** shows why the server refused a generation inside it, not behind its overlay, and its country field takes two letters only (`minlength`, `maxlength`, `pattern`).
- Generating, uploading and deleting stored certificates stay. openssl runs in a worker thread with a timeout, 10 s to read a certificate and 60 s to make one (ADR-0021), and prints a self-signed certificate and its key, which the supervisor stores like an upload's (`_store_certificate` of `supervisor/src/security/router.py`). Each file goes first to a temporary file in the certificates' directory (`.<random>.tmp`, short enough for every name), created with `O_EXCL` and `O_NOFOLLOW` and its final mode, keys 0600; only once every write has succeeded are they renamed over the stored files, and a stored chain is removed when the new certificate comes without one. A failed write so leaves the stored certificate, key and chain as they were, and a rename replaces a symbolic link at a file's path, never its target (ADR-0042). Stores and deletes run in worker threads and hold one lock (`_CERTIFICATES_LOCK`), so that two of them never leave the certificate of one beside the key of another. A self-signed certificate's organization has at most 64 characters and its country two letters (RFC 5280), checked before openssl runs, which reads the subject as UTF-8 (`-utf8`) and so counts characters as the check does; the organization must also be text that UTF-8 can encode, without the characters of Unicode's category C (control, format, private-use, unassigned), which have no place in a name. The agent made these choices under the same delegation.
- Tests: `TestCertificateStatusNamesWhatIsServed`, `TestOpensslRunsOffTheEventLoop`, `TestCertificateFilesFollowNoLink`, `TestSelfSignedSubjectValidation`, `TestCertificateErrorAnswers`, `TestCertificateStoreIsAtomic` and `TestCertificateStoresAndDeletesDoNotMix` of `supervisor/tests/test_security.py`; `webui/tests/security.spec.ts` and `webui/tests/language.spec.ts`. The tests that run the real openssl skip unless it is OpenSSL's: LibreSSL's, macOS's `/usr/bin/openssl`, writes the key of `-keyout -` to a file named `-` (`docs/TESTING.md`).

## Consequences

- An administrator who stores a certificate sees that browsers still get the local CA's, instead of a status that names the stored one.
- The status cannot say when the local CA's certificate expires: the supervisor cannot read it, and Caddy renews it itself.
- Serving a stored certificate needs a way to hand it to Caddy (a volume both containers mount, or Caddy's admin API, and a Caddyfile that loads it), a way back to the local CA when it is deleted, and a device check across a reboot (ADR-0018): the rest of `security.custom-certificates`.

## Considered options

- Serving stored certificates in the same change: it changes the compose file and the Caddyfile and is host-level, far beyond a fix of what the page says.
- Hiding stored certificates until they can be served: they are on the device, and that later change would serve them, so the page lists them as stored.
- Writing the stored files in place, each with `O_NOFOLLOW`, as before: a write that failed after the first file left a new certificate beside the old key, and two stores in worker threads could mix their files. A symbolic link at a file's path then failed the write; a rename replaces the link instead, and its target stays as it was all the same.
