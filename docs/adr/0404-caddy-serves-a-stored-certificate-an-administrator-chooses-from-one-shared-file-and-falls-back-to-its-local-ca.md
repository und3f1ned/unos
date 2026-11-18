---
status: accepted
date: 2026-11-18
area: security
decided-by: agent
supersedes: [ADR-0357]
---

# ADR-0404: Caddy serves a stored certificate that an administrator chooses, from one file shared only with the supervisor, and falls back to its local CA

## Context

ADR-0357 left steps 2 and 3 of `security.custom-certificates` open: the supervisor stores uploaded
and self-signed certificates in its own volume, and Caddy, in the web UI's container, serves only
its local CA's (ADR-0025). The owner delegated the decision, "safe above all", in translation: the
private key may reach only the web UI's Caddy, through files that only the two containers mount;
UNOS checks a certificate before Caddy gets it; one that Caddy fails to load must not leave the web
UI without HTTPS; the choice survives restarts and reboots and writes to the system disk only on a
real change; Caddy's admin API stays on the web UI container's loopback.

## Decision

- **The hand-over.** `POST /api/v1/security/certificates/{cert_id}/serve` (administrators) serves
  the stored certificate `custom_<name>`, or with `caddy_local_ca` the local CA's again
  (`supervisor/src/security/router.py`, `supervisor/src/security/serving.py`). The supervisor
  writes the certificate, its chain and its key (PKCS #8) into one file, `served.pem`, mode 0600,
  root's, in the Docker volume `served-certificate`, which only the supervisor (read-write) and the
  web UI (read-only) mount at `/served-certificate` (`deploy/docker-compose.yml`, the test stack
  too). It goes to a temporary file, is synced and renamed into place (`state_files.replace_file`):
  Caddy reads the old file or the new one, whole, never a certificate beside another's key. The
  key reaches no API answer, log line or command line.
- **The checks first** (`serving.prepare`, with `cryptography`): the certificate parses; the key
  reads without a passphrase, belongs to it and is of a kind that Caddy loads and every current
  browser takes in a handshake: RSA of at least 2048 bits, EC on P-256 or P-384 (not P-521, which
  Chrome does not offer in TLS 1.3, nor Ed25519, which no major browser offers: Caddy would load
  them, and every browser would fail the handshake without a warning to click through); its
  extended key usage, when it has one, names serverAuth, and its key usage, when it has one,
  digitalSignature; it is valid now; each certificate of the chain (the certificate file's further
  ones, then the chain file's, each once) is valid now, a CA when it says so, and issued the one
  before it, by name and signature. A refusal answers 409 with the reason and reaches no Caddy.
  A certificate that expires within 30 days (`EXPIRY_WARNING_DAYS`) is served and marked:
  `expires_soon` in the answer and the listing, a badge and a warning in the confirmation.
- **Caddy loads it** by `caddy reload --config /etc/caddy/Caddyfile --force` in the web UI's
  container (`UNOS_WEBUI_CONTAINER`, `unos-webui`), with `UNOS_TLS=served` or `local`, through the
  Docker socket that the supervisor has (`serving.reload_caddy`, in a worker thread). That
  reaches Caddy's admin endpoint on the container's own loopback, where Caddy's default puts it;
  nothing exposes it further. `webui/Caddyfile` holds the site's headers and routes in the snippet
  `unos-site` and two sites that import it: `tls-local` (`{$UNOS_HOSTNAME:unos}, https://`, `tls
  internal`) and `tls-served` (`https://` only, `tls /served-certificate/served.pem
  /served-certificate/served.pem`, so that every name and every client without one gets it), of
  which `import tls-{$UNOS_TLS:local}` takes one. `webui/Dockerfile` adapts both at build time.
  `ocsp_stapling off`: a served certificate that names an OCSP responder makes Caddy ask no
  responder and keep no staple in `caddy-data` (ADR-0214). When Caddy refuses the new
  configuration it keeps the one it runs; the supervisor puts the served file back as it was, if
  the request changed it, and answers 502 with Caddy's error, or 503 when the container or Docker
  cannot be reached. When Docker does not answer in time (`CaddyTimedOut`), Caddy may have loaded
  it: the file stays as the request wrote it, the answer is 503, and the status says what Caddy
  serves.
- **Restarts and reboots.** The web UI's command (`deploy/docker-compose.yml`) runs
  `caddy validate` with `UNOS_TLS=served` when the served file exists, and starts Caddy with
  `served` only when it passes, else with `local` and one log line with Caddy's reason. So a
  certificate that no longer loads leaves the local CA's certificate, never no HTTPS.
- **What is served** is what a TLS handshake shows: `serving.probe_fingerprint` connects to
  `webui:443` without SNI, as a browser that opens the NAS by its IP address does, and reads the
  certificate's SHA-256 fingerprint (5 s, nothing written or logged). `GET .../certificates/status`
  and the listing's `status` answer `served_by` (`caddy_local_ca`, `stored_certificate`, or
  `unknown` when Caddy did not answer), `served_id`, `chosen_id` (the stored certificate that the
  served file holds) and `fingerprint`; each listed certificate has `served`, `chosen` and
  `expires_soon`, and its `fingerprint` is SHA-256 (`openssl x509 -fingerprint -sha256`).
- **Only real changes.** Serving what the file holds while Caddy serves it, or cannot be asked,
  or the local CA while no file exists and Caddy serves no stored certificate, writes and reloads
  nothing and answers `changed: false`; the handshake that decides it is made under the lock, so
  that two requests at once reload Caddy once. A reload of the file as it is never writes it
  again. A store (upload or self-signed) under the served certificate's name is refused with 409,
  so that Caddy never serves a copy that no stored certificate matches. Deleting the served
  certificate first serves the local CA's (the delete fails with 502 or 503, deleting nothing,
  when Caddy does not take it); deleting one that Caddy serves although the file holds another
  (after a reload that Docker did not answer in time) reloads the file's first. So Caddy never
  keeps serving a deleted certificate, and a handshake's certificate that is neither stored nor
  chosen is the local CA's, the only other one that webui/Caddyfile serves. All of these, and
  every store and delete, hold `_CERTIFICATES_LOCK`.
- **Audit entries** (`AuditAction`, resource type `certificate`): `SSL_CERT_UPLOADED`
  (`certificate_uploaded`), `SSL_CERT_GENERATED` (`certificate_generated`), `SSL_CERT_DELETED`
  (`certificate_deleted`) and `SSL_CERT_SERVED` (`certificate_served`, with the certificate's id or
  `caddy_local_ca`, and its end date), only for changes that were made. The Audit Log page names
  them (`webui/src/routes/audit/+page.svelte`). This closes, for the certificates, the open item
  that ADR-0359 noted.
- **The card** (`webui/src/routes/system/tabs/SecurityTab.svelte`, `account.certs.*` in English and
  Russian): its badge and "HTTPS for browsers" name the served certificate or the local CA; each
  stored certificate shows SERVED, NOT LOADED (chosen, but Caddy serves another) or NOT SERVED, and
  EXPIRES SOON, and a valid one that is not served offers **Serve this certificate**; **Use Caddy's
  local CA** shows while one is chosen or served, with a warning when the chosen one was not
  loaded. Both ask first in a dialog that warns that every browser will see a new certificate,
  and show there why the server refused; deleting the served certificate asks with its own text.
- **What stays of ADR-0357.** The supervisor reads none of Caddy's certificates. openssl runs in a
  worker thread with a timeout, 10 s to read a certificate and 60 s to make one (ADR-0021). A store
  writes each file to `.<random>.tmp` in the certificates' directory with `O_EXCL`, `O_NOFOLLOW`
  and its final mode (keys 0600) and renames them only once all are written, removing a stored
  chain that the new certificate lacks; a rename replaces a symbolic link, never its target
  (ADR-0042). A self-signed certificate's organization has at most 64 characters, printable and
  UTF-8 encodable (no Unicode category C), and its country two letters, checked before openssl
  runs with `-utf8`. A listing that cannot be loaded is reported with **Retry**, and the dialog of
  **Generate Self-Signed** shows the server's refusal and takes a two-letter country only.
- Tests: `supervisor/tests/test_certificate_serving.py` (the checks, the API with a fake Caddy that
  loads the served file, the reload's `docker exec`, a real TLS handshake, the served file),
  `TestServedCertificate` of `supervisor/tests/test_deploy_scripts.py` (the volume's mounts, the web
  UI's start with a fake caddy, the Caddyfile, the build) and of `supervisor/tests/test_standin.py`,
  the tests of ADR-0357 in `supervisor/tests/test_security.py`; `webui/tests/security.spec.ts`,
  `webui/tests/language.spec.ts`, `webui/tests/mobile-layout.spec.ts`. On the stand-in
  `scripts/standin/front.mjs` serves the served file while it loads, as Caddy would, and the
  reload only refuses a file that Python's ssl cannot load (`scripts/standin/run_supervisor.py`).

## Consequences

- Browsers can get a certificate that they trust, such as one of the household's own CA; a
  self-signed one still shows a warning. Switching gives every browser a new certificate: an open
  page may stop answering until it is reloaded, which the confirmation says.
- The served file is a second copy of the chosen key, in a volume that the web UI's container
  mounts; it is in no backup (the stored keys are in the backup of `/data` that `deploy/update.sh`
  makes, as before).
- The supervisor relies on `docker exec` into its own web UI's container and on Caddy's local admin
  endpoint for a change at run time, and on the start command for restarts; the stand-in cannot
  show either, so the device check of the entry does (ADR-0018).
- A certificate that expires while served stays served (expired) until an administrator acts; a
  restart then loads it only if Caddy still validates it.
- Replacing the served certificate takes two steps (serve the local CA or another one, then store).

## Considered options

- Restarting the web UI's container instead of `caddy reload`, which needs no admin endpoint: every
  open connection and WebSocket drops for seconds, and a certificate that fails is noticed only
  after the restart; the start command's validation stays for restarts and reboots.
- Caddy's `--watch` of a configuration file in the shared volume: Caddy calls it a development
  feature, and it polls the file every second.
- Handing the certificate to Caddy over HTTP (`get_certificate http`): the key would cross the
  network between the containers in an answer of the supervisor.
- Mounting the supervisor's certificates directory into the web UI's container: Caddy would see
  every stored key, not only the chosen one.
