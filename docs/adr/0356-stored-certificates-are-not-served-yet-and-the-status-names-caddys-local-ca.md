---
status: accepted
date: 2026-11-12
area: security
decided-by: agent
---

# ADR-0356: Stored certificates are not served yet, and the certificate status names Caddy's local CA

## Context

System > Security stores uploaded and self-signed certificates in the supervisor's data volume (`/data/certs`), but Caddy serves only the certificate of its local CA (ADR-0025), in the `unos-webui` container, the only one that mounts `caddy-data` (`deploy/docker-compose.yml`): nothing hands a stored certificate to it. Yet the status named the first valid stored certificate as the active one, and the listing looked for Caddy's certificates in a directory of the supervisor's own volume that nothing fills. Working on through the features under the owner's delegation ("work on through the features, ask no questions; I allow everything", in translation), the agent made the status say what is served and left serving stored certificates for later.

## Decision

- Serving a stored certificate is deferred: steps 2 and 3 of `security.custom-certificates` stay open. Meanwhile `GET /api/v1/security/certificates/status` answers `{"served_by": "caddy_local_ca"}`, and `GET /api/v1/security/certificates` lists the stored certificates, each with `served: false`, beside the same `served_by` (`ServedCertificate`, `Certificate` and `CertificateListResponse` of `supervisor/src/security/schemas.py`).
- The supervisor reads none of Caddy's certificates: `CADDY_CERTS_DIR` and `get_caddy_certificates` are gone, and with them the type `lets_encrypt`, `auto_renew`, `active_certificate` and the unused `LetsEncryptRequest` (ADR-0207).
- The **SSL/TLS Certificates** card of `webui/src/routes/system/tabs/SecurityTab.svelte` shows **Caddy's local CA** as its badge; under **Manage** it says "HTTPS for browsers: Caddy's local CA", explains that uploaded and self-signed certificates are only stored, and marks each stored certificate **NOT SERVED** (`account.certs.*` of `webui/src/lib/i18n/catalog/account.ts`, in both languages). A listing that cannot be loaded is reported, not shown as empty.
- Generating, uploading and deleting stored certificates stay. openssl runs in a worker thread with a timeout, 10 s to read a certificate and 60 s to make one (ADR-0021), and prints a self-signed certificate and its key, which the supervisor writes like an upload's: with `O_NOFOLLOW`, so a symbolic link at a file's path fails the write (ADR-0042), keys 0600. A self-signed certificate's organization has at most 64 characters and its country two letters (RFC 5280), checked before openssl runs, which reads the subject as UTF-8 (`-utf8`) and so counts characters as the check does.
- Tests: `TestCertificateStatusNamesWhatIsServed`, `TestOpensslRunsOffTheEventLoop`, `TestCertificateFilesFollowNoLink`, `TestSelfSignedSubjectValidation` and `TestCertificateErrorAnswers` of `supervisor/tests/test_security.py`; `webui/tests/security.spec.ts` and `webui/tests/language.spec.ts`.

## Consequences

- An administrator who stores a certificate sees that browsers still get the local CA's, instead of a status that names the stored one.
- The status cannot say when the local CA's certificate expires: the supervisor cannot read it, and Caddy renews it itself.
- Serving a stored certificate needs a way to hand it to Caddy (a volume both containers mount, or Caddy's admin API, and a Caddyfile that loads it), a way back to the local CA when it is deleted, and a device check across a reboot (ADR-0018): the rest of `security.custom-certificates`.

## Considered options

- Serving stored certificates in the same change: it changes the compose file and the Caddyfile and is host-level, far beyond a fix of what the page says.
- Hiding stored certificates until they can be served: they are on the device, and that later change would serve them, so the page lists them as stored.
