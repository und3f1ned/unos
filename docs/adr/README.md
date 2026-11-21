# Architecture decision records

Every decision about UNOS is recorded here as a short architecture decision record (ADR),
not only the decisions that are hard to reverse
([ADR-0001](0001-record-every-decision-as-an-adr.md)). To add one, follow the adr skill
(`.agents/skills/adr/SKILL.md`): copy [template.md](template.md) to `NNNN-slug.md`, fill it
in, and add its row to the table of its area below. NNNN is the next free number (four
digits, from 0001 without gaps, so an ADR is never deleted), and the slug is a few
lowercase words joined by hyphens. A decision that changes is not rewritten: a new ADR
lists the old one under `supersedes`, and the old file keeps its text, changes its status
to `superseded`, gains `superseded-by` and names its successor in one line. Only an ADR
written in the same uncommitted change as its decision, or a proposed ADR that is being
accepted with new evidence, is refined in place, and only until its first commit; a
decision that is already in force is superseded (ADR-0001). The statuses are `proposed`
(written down, not yet settled), `accepted` (in force) and `superseded` (replaced by the
ADR it names). `supervisor/tests/test_adrs.py` checks the files, the supersession links
and this index, and `make check-fast` runs it.

## Process

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-record-every-decision-as-an-adr.md) | Every decision is recorded as a short ADR in docs/adr | accepted | 2026-11-07 |
| [0003](0003-all-development-on-the-device-over-ssh.md) | All development happens on the device over SSH | superseded by [0016](0016-code-and-tests-on-the-workstation.md) | 2026-01-24 |
| [0004](0004-commit-and-push-to-main-from-the-device.md) | Commits are made on the device and pushed straight to main | superseded by [0208](0208-commits-and-pushes-only-when-the-owner-asks.md) | 2026-01-24 |
| [0007](0007-licensed-under-gpl-3.md) | UNOS is licensed under the GNU GPL version 3 | accepted | 2026-02-05 |
| [0008](0008-roadmap-in-improvements-md.md) | Work follows the feature roadmap in docs/IMPROVEMENTS.md | superseded by [0014](0014-severity-first-phased-plan.md) | 2026-02-05 |
| [0012](0012-coding-standards-and-shared-ui-components.md) | Code follows short written standards, and the UI uses only the shared components | accepted | 2026-02-09 |
| [0013](0013-security-decisions-delegated-to-the-agent.md) | The owner delegates security decisions to the agent, with safety as the criterion | accepted | 2026-11-05 |
| [0014](0014-severity-first-phased-plan.md) | Work follows a severity-first plan: Phase 0, Phase 1 in slices, then Phase 2 | accepted | 2026-11-05 |
| [0015](0015-phase-0-follow-ups.md) | Carry the Phase 0 follow-ups into Phase 1 | proposed | 2026-11-05 |
| [0016](0016-code-and-tests-on-the-workstation.md) | Code and tests run on the workstation; the device gets only scripted deploys and checks | accepted | 2026-11-05 |
| [0017](0017-adversarial-verification-of-agent-work.md) | Agent work is checked by independent agents that try to refute it | accepted | 2026-11-05 |
| [0018](0018-definition-of-done-for-host-changes.md) | Storage, share and host changes are done only after a device check, a reboot and API tests | accepted | 2026-11-05 |
| [0019](0019-upgrade-notes-for-1-0-0-installs.md) | Breaking changes get upgrade notes and migration paths for 1.0.0 installs | superseded by [0207](0207-no-backward-compatibility.md) | 2026-11-05 |
| [0057](0057-check-your-own-side-first.md) | When something breaks, the agent checks its own side first | accepted | 2026-11-06 |
| [0058](0058-phase-1-slice-1-scope.md) | Phase 1 slice 1 fixes the broken user flows and the needless system-disk writes | accepted | 2026-11-06 |
| [0059](0059-phase-1-slice-2-scope.md) | Phase 1 slice 2 gives UNOS users SMB access per share, with guest access off by default | accepted | 2026-11-06 |
| [0060](0060-decisions-kept-outside-the-repository.md) | Decisions are kept in session notes, audit notes and pull request bodies | superseded by [0001](0001-record-every-decision-as-an-adr.md) | 2026-11-06 |
| [0061](0061-docs-describe-what-the-code-does.md) | Docs describe only what the code does, checked against the code | accepted | 2026-11-06 |
| [0200](0200-all-repository-text-in-english.md) | All repository text is English, and agents answer the owner in Russian | accepted | 2026-11-07 |
| [0201](0201-one-fact-one-home.md) | Each kind of project knowledge has exactly one home | accepted | 2026-11-07 |
| [0202](0202-agents-md-is-the-project-memory.md) | AGENTS.md is the project memory, and CLAUDE.md only imports it | accepted | 2026-11-07 |
| [0203](0203-feature-list-json-is-the-feature-ledger.md) | The feature ledger is feature-list.json, and a feature passes only after its scenario | accepted | 2026-11-07 |
| [0204](0204-progress-md-is-the-progress-journal.md) | PROGRESS.md is the progress journal, and handoffs go to tmp/handoff | accepted | 2026-11-07 |
| [0205](0205-project-skills-in-agents-skills.md) | Project skills live in .agents/skills, linked from .claude/skills | accepted | 2026-11-07 |
| [0206](0206-triage-with-github-issues.md) | Work is triaged in GitHub Issues with five labels and two templates | accepted | 2026-11-07 |
| [0207](0207-no-backward-compatibility.md) | UNOS keeps no backward compatibility, because it has no older installs to migrate | accepted | 2026-11-07 |
| [0208](0208-commits-and-pushes-only-when-the-owner-asks.md) | Commits and pushes happen only when the owner asks, and the owner opens pull requests | accepted | 2026-11-07 |
| [0209](0209-conventional-commits-in-english.md) | Commit messages follow Conventional Commits in English | accepted | 2026-11-07 |
| [0210](0210-parallel-work-in-worktrees-with-a-device-lock.md) | Parallel agents work in separate worktrees, and one agent at a time uses the device | accepted | 2026-11-07 |
| [0211](0211-executable-guardrails-for-agents.md) | A committed settings file and a PreToolUse guard block risky agent commands | accepted | 2026-11-07 |
| [0335](0335-every-background-command-of-an-agent-has-a-deadline.md) | Every background command of an agent has a deadline | accepted | 2026-11-11 |

## Architecture

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0002](0002-two-containers-on-an-existing-linux-host.md) | UNOS runs on an existing Linux host as a web UI and a privileged supervisor container | accepted | 2026-01-23 |
| [0006](0006-real-time-data-over-websocket.md) | Real-time data goes over WebSocket, never HTTP polling | superseded by [0363](0363-repair-permissions-runs-as-a-background-job-that-reports-over-the-events-socket.md) | 2026-01-25 |
| [0020](0020-host-changes-through-host-py.md) | Host changes go through src/host.py, which runs argv lists in the host's namespaces | accepted | 2026-11-05 |
| [0021](0021-blocking-work-off-the-event-loop.md) | Blocking work runs off the event loop, with timeouts | accepted | 2026-11-05 |
| [0022](0022-database-migrations-with-alembic.md) | Adopt Alembic for database migrations | proposed | 2026-11-05 |
| [0023](0023-audit-quality-debt.md) | Pay down the audit's P2 quality and technical debt | proposed | 2026-11-05 |
| [0306](0306-the-database-writes-ahead-with-synchronous-normal.md) | The database uses SQLite's write-ahead log with synchronous NORMAL, and a power cut can roll back its last commits | accepted | 2026-11-08 |
| [0308](0308-a-sign-in-writes-at-most-0-1-mib-to-the-sd-card.md) | A sign-in in the middle of the write-ahead log writes at most 0.1 MiB to the SD card | accepted | 2026-11-09 |
| [0324](0324-json-state-files-are-replaced-through-a-rename-and-one-that-cannot-be-read-is-kept-as-bad.md) | JSON state files are replaced through a rename, and one that cannot be read is kept as .bad | accepted | 2026-11-10 |
| [0363](0363-repair-permissions-runs-as-a-background-job-that-reports-over-the-events-socket.md) | Repair permissions runs as a background job that reports over the events socket, where live data goes | accepted | 2026-11-12 |
| [0398](0398-the-supervisor-logs-one-json-object-per-line-from-one-setup-and-writes-nothing-while-it-idles.md) | The supervisor logs one JSON object per line from one setup, and writes nothing while it idles | accepted | 2026-11-18 |
| [0401](0401-every-time-of-the-api-carries-its-offset-and-stored-times-without-one-are-utc.md) | Every time of the API carries its offset, a stored time without one is UTC, and the web UI shows times in the browser's time zone | superseded by [0432](0432-the-web-uis-shared-components-carry-its-states-confirmations-menus-form-controls-and-tables-and-a-check-keeps-pages-on-them.md) | 2026-11-18 |
| [0416](0416-the-system-security-logs-and-nfs-routers-reach-the-host-only-through-host-py-and-the-firewall-is-the-hosts-ufw-turned-on-only-with-a-revert-armed.md) | The system, security, logs and NFS routers reach the host only through host.py, and the firewall is the host's ufw, changed only with the local networks let in and a revert armed | accepted | 2026-11-18 |
| [0417](0417-the-api-documentation-describes-every-route-field-example-and-error-status-and-a-test-derives-the-errors.md) | The API documentation describes every route, field, example and error status, and a test derives the errors from the code | accepted | 2026-11-19 |
| [0419](0419-every-data-path-of-the-supervisor-follows-unos-data-dir-read-once-at-its-start.md) | Every data path of the supervisor follows UNOS_DATA_DIR, read once at its start, and the stand-in patches no constant | accepted | 2026-11-19 |

## Security

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0005](0005-device-access-with-sshpass-on-the-command-line.md) | Device access uses sshpass with the password on the command line | superseded by [0212](0212-device-secrets-in-dev-env.md) | 2026-01-24 |
| [0024](0024-credentials-stay-with-the-owner.md) | Credentials and the device's security settings are changed only by the owner | accepted | 2026-11-05 |
| [0025](0025-https-from-a-local-ca-without-hsts.md) | The web UI is served over HTTPS from Caddy's local CA, with a CSP and without HSTS | accepted | 2026-11-05 |
| [0026](0026-pyjwt-and-bcrypt-with-pinned-dependencies.md) | PyJWT and bcrypt replace python-jose and passlib, and dependencies are pinned | superseded by [0315](0315-pyjwt-and-bcrypt-check-passwords-as-given-and-dependencies-are-pinned.md) | 2026-11-05 |
| [0027](0027-smaller-phase-0-hardening.md) | QR codes for 2FA are drawn locally, logs show as text, and token lifetimes are bounded | accepted | 2026-11-05 |
| [0062](0062-limits-before-authentication.md) | Request bodies and WebSocket messages are limited before authentication | accepted | 2026-11-06 |
| [0063](0063-uvicorn-header-and-connection-limits.md) | Limit uvicorn's request headers and connections after measuring the cost | proposed | 2026-11-06 |
| [0064](0064-test-secrets-in-temporary-session-files.md) | Test secrets live in 0600 files in a temporary session folder | superseded by [0212](0212-device-secrets-in-dev-env.md) | 2026-11-06 |
| [0065](0065-bounded-login-input-and-audit-entries.md) | Login input and audit entries are bounded | accepted | 2026-11-06 |
| [0066](0066-append-only-audit-log.md) | Make the audit log append-only; until then it is rewritten as a whole | superseded by [0224](0224-append-only-audit-log-in-json-lines.md) | 2026-11-06 |
| [0212](0212-device-secrets-in-dev-env.md) | Device settings and secrets live in the git-ignored dev.env, never on command lines | accepted | 2026-11-07 |
| [0213](0213-audit-notes-stay-out-of-the-repository.md) | Security audit notes and exploit details stay outside the repository | accepted | 2026-11-07 |
| [0224](0224-append-only-audit-log-in-json-lines.md) | The audit log is append-only JSON lines with size-based rotation | accepted | 2026-11-07 |
| [0236](0236-audit-log-filtered-alike-and-shown-on-an-administrator-page.md) | The audit log is listed, counted and exported with one set of filters and shown on an administrator page | superseded by [0237](0237-audit-log-filtered-by-whole-units-exported-without-formulas.md) | 2026-11-07 |
| [0237](0237-audit-log-filtered-by-whole-units-exported-without-formulas.md) | The audit log is filtered alike by whole units of time, exported without spreadsheet formulas and shown on an administrator page | superseded by [0401](0401-every-time-of-the-api-carries-its-offset-and-stored-times-without-one-are-utc.md) | 2026-11-07 |
| [0286](0286-the-audit-logs-lock-holds-across-processes.md) | The audit log's lock holds across processes, through a file lock that the emergency command line takes too | accepted | 2026-11-08 |
| [0315](0315-pyjwt-and-bcrypt-check-passwords-as-given-and-dependencies-are-pinned.md) | PyJWT and bcrypt check passwords as given, and dependencies are pinned | accepted | 2026-11-09 |
| [0328](0328-each-audit-entry-carries-the-hash-of-the-one-before-and-the-listing-says-where-the-chain-breaks.md) | Each audit entry carries the hash of the one before, and the listing says where the chain breaks | accepted | 2026-11-10 |
| [0333](0333-the-supervisor-serves-128-open-connections-and-caddy-16-kb-of-headers.md) | The supervisor serves at most 128 open connections and 64 sockets, and Caddy refuses header blocks over 16 KB | accepted | 2026-11-10 |
| [0343](0343-a-users-own-sign-out-password-and-two-factor-changes-are-audited-about-the-user.md) | A user's own sign-out, password change and two-factor changes are audited about the user, and answer when their entry cannot be written | accepted | 2026-11-11 |
| [0350](0350-pool-volume-snapshot-container-and-app-changes-are-audited-and-answer-when-their-entry-cannot-be-written.md) | Pool, volume, snapshot, container and app changes are audited, and answer when their entry cannot be written | accepted | 2026-11-12 |
| [0351](0351-backups-power-scheduled-tasks-and-the-hosts-settings-are-audited-after-they-succeed.md) | Backups, power, scheduled tasks and the host's network and system settings are audited after they succeed, and answer when their entry cannot be written | accepted | 2026-11-12 |
| [0357](0357-stored-certificates-are-not-served-yet-and-the-status-names-caddys-local-ca.md) | Stored certificates are not served yet, and the certificate status names Caddy's local CA | superseded by [0404](0404-caddy-serves-a-stored-certificate-an-administrator-chooses-from-one-shared-file-and-falls-back-to-its-local-ca.md) | 2026-11-12 |
| [0359](0359-the-audit-logs-action-filter-offers-only-written-actions-and-an-older-entrys-action-is-listed-as-recorded.md) | The audit log's Action filter offers only actions that the supervisor writes, and an older entry's action that AuditAction no longer has is listed as recorded | superseded by [0402](0402-the-firewall-the-login-history-pools-images-power-settings-and-channels-are-audited-and-the-action-filter-offers-what-is-written.md) | 2026-11-12 |
| [0402](0402-the-firewall-the-login-history-pools-images-power-settings-and-channels-are-audited-and-the-action-filter-offers-what-is-written.md) | The firewall, the login history, pool mounts, images, power and notification settings and the off-device copy are audited, and the Action filter offers what the supervisor writes | accepted | 2026-11-18 |
| [0404](0404-caddy-serves-a-stored-certificate-an-administrator-chooses-from-one-shared-file-and-falls-back-to-its-local-ca.md) | Caddy serves a stored certificate that an administrator chooses, from one file shared only with the supervisor, and falls back to its local CA | accepted | 2026-11-18 |

## Auth

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0009](0009-bearer-tokens-and-a-same-origin-api.md) | The API takes Bearer tokens, not cookies, and refuses cross-origin requests by default | superseded by [0397](0397-the-web-ui-s-session-is-an-httponly-cookie-accepted-only-from-its-own-origin.md) | 2026-02-05 |
| [0010](0010-login-rate-limit-per-client.md) | Login is limited to 5 attempts per minute per client IP | accepted | 2026-01-24 |
| [0028](0028-jwt-secret-generated-per-install.md) | Each install generates its own JWT secret, and published placeholders are refused | accepted | 2026-11-05 |
| [0029](0029-first-run-setup-creates-one-administrator.md) | There are no default credentials: the first visit creates exactly one administrator | accepted | 2026-11-05 |
| [0030](0030-administrator-only-routers.md) | Whole routers are administrator-only, and regular users get Dashboard and Account | accepted | 2026-11-05 |
| [0031](0031-websocket-auth-with-the-first-message.md) | WebSocket clients authenticate with their first message, never with a token in the URL | accepted | 2026-11-05 |
| [0032](0032-kept-authentication-practices.md) | Auth keeps bcrypt, one JWT algorithm, TOTP before any token, and hashed API tokens | superseded by [0239](0239-kept-authentication-practices-with-a-second-factor-before-any-token.md) | 2026-11-05 |
| [0033](0033-session-revocation-and-token-scopes.md) | Add session revocation, safer token storage, 2FA recovery codes and token scopes | proposed | 2026-11-05 |
| [0067](0067-two-factor-codes-are-single-use.md) | Two-factor codes are single-use, remembered in memory | accepted | 2026-11-06 |
| [0068](0068-own-smb-activation-needs-the-two-factor-code.md) | Turning on one's own SMB access needs the two-factor code | accepted | 2026-11-06 |
| [0191](0191-throttle-api-token-last-used-writes.md) | Throttle the commit of an API token's last use | superseded by [0252](0252-an-api-tokens-last-use-is-committed-at-most-once-an-hour.md) | 2026-11-07 |
| [0238](0238-recovery-codes-sign-in-once-in-place-of-a-two-factor-code.md) | Ten recovery codes each sign in once in place of a two-factor code, and end with two-factor authentication | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-07 |
| [0239](0239-kept-authentication-practices-with-a-second-factor-before-any-token.md) | Auth keeps bcrypt, one JWT algorithm, a second factor before any token, and hashed API tokens | superseded by [0409](0409-security-keys-sign-in-in-place-of-a-two-factor-code-verified-by-unos-itself.md) | 2026-11-07 |
| [0251](0251-a-sign-in-answers-while-data-is-full-and-its-lost-records-go-to-the-log.md) | A sign-in answers while /data is full, and the records it cannot write go to the supervisor's log | accepted | 2026-11-07 |
| [0252](0252-an-api-tokens-last-use-is-committed-at-most-once-an-hour.md) | An API token's last use is committed at most once an hour | accepted | 2026-11-07 |
| [0253](0253-only-a-session-token-manages-api-tokens-and-their-number-and-names-are-bounded.md) | Only a session token manages API tokens, and their number, names and permissions are bounded | accepted | 2026-11-07 |
| [0262](0262-login-history-times-in-utc-with-the-offset-newest-first.md) | The login history answers its times in UTC with the offset, newest first also within a second | accepted | 2026-11-07 |
| [0263](0263-a-users-login-history-starts-with-their-account.md) | A regular user's login history starts with their account | accepted | 2026-11-08 |
| [0284](0284-root-on-the-host-resets-an-administrators-password-and-two-factor-authentication.md) | Only root on the host resets an administrator's password and two-factor authentication, with the emergency command line | accepted | 2026-11-08 |
| [0312](0312-a-websockets-credential-re-check-in-progress-runs-to-its-end.md) | A WebSocket's credential re-check in progress runs to its end when the socket closes | accepted | 2026-11-09 |
| [0329](0329-a-session-is-a-row-and-ending-it-deletes-the-row.md) | A session is a row of user_sessions, and ending a session deletes its row | accepted | 2026-11-10 |
| [0386](0386-two-factor-authentication-required-for-every-account-after-a-grace-period.md) | An administrator with two-factor authentication requires it for every account, and after a grace period an account without it reaches only its setup | accepted | 2026-11-17 |
| [0392](0392-an-api-token-can-be-limited-to-reading-and-to-areas-of-the-api.md) | An API token can be limited to reading and to areas of the API, checked where every credential is checked | accepted | 2026-11-17 |
| [0397](0397-the-web-ui-s-session-is-an-httponly-cookie-accepted-only-from-its-own-origin.md) | The web UI's session is an HttpOnly cookie accepted only from its own origin, and programs keep Bearer tokens | accepted | 2026-11-17 |
| [0409](0409-security-keys-sign-in-in-place-of-a-two-factor-code-verified-by-unos-itself.md) | Security keys sign in in place of a two-factor code, beside the authenticator app, verified by UNOS itself | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-18 |

## Storage

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0011](0011-btrfs-raid1c3-and-raid1c4-instead-of-raid5-6.md) | Pools use btrfs RAID1C3/RAID1C4 instead of RAID5/6, with read-only snapshots | accepted | 2026-02-08 |
| [0034](0034-pools-mounted-by-uuid-from-managed-fstab.md) | Pools are mounted by UUID from managed fstab entries, and empty mountpoints are immutable | accepted | 2026-11-05 |
| [0035](0035-irreversible-storage-operations-need-confirmation.md) | Irreversible storage operations need a typed confirmation and device identity checks | accepted | 2026-11-05 |
| [0036](0036-pool-health-from-the-host.md) | Pool health is computed from the host and never assumed | accepted | 2026-11-05 |
| [0069](0069-volumes-are-top-level-subvolumes.md) | Volumes are top-level subvolumes, addressed by subvolume id | accepted | 2026-11-06 |
| [0070](0070-pools-respect-docker-volumes-and-odd-names.md) | Pool unmount and delete respect Docker volumes, and odd names do not break storage | superseded by [0344](0344-pool-unmount-and-delete-are-refused-when-docker-cannot-be-asked.md) | 2026-11-06 |
| [0071](0071-scrubs-return-once-started.md) | Scrubs return once started, and scheduled scrubs need a mounted pool | superseded by [0311](0311-a-scrub-starts-only-on-a-mounted-pool-through-one-helper.md) | 2026-11-06 |
| [0072](0072-smart-only-on-whole-host-disks.md) | SMART runs only on whole host disks, the set the Storage page lists | accepted | 2026-11-06 |
| [0073](0073-deferred-storage-edge-cases.md) | Fix the deferred storage and snapshot edge cases of slice 1 | superseded by [0352](0352-volume-reads-container-folders-disk-names-and-deletes-withstand-renames-odd-names-links-and-new-mounts.md) | 2026-11-06 |
| [0235](0235-disk-health-shows-smart-data-and-replace-soon-marks.md) | Disk Health opens each disk's SMART data, and disks whose SMART trend scores 50 are marked replace soon | accepted | 2026-11-07 |
| [0241](0241-snapshots-of-volumes-and-volume-quotas-in-the-web-ui.md) | The Snapshots page offers the volumes of mounted pools, and the Volumes dialog takes a quota in GiB | superseded by [0243](0243-the-snapshots-page-offers-mounted-pools-and-their-volumes.md) | 2026-11-07 |
| [0242](0242-snapshot-commands-act-on-the-folders-that-were-opened.md) | Snapshots are made, restored and deleted through the folders that were opened, and deleted by subvolume id | accepted | 2026-11-07 |
| [0243](0243-the-snapshots-page-offers-mounted-pools-and-their-volumes.md) | The Snapshots page offers mounted pools and their volumes, also a pool whose volumes cannot be read, and the Volumes dialog takes a quota in GiB | accepted | 2026-11-07 |
| [0269](0269-the-smart-history-is-kept-in-ram-and-written-when-it-changes.md) | The SMART monitor keeps its history in RAM and writes it only when a pass changes it, and at shutdown | accepted | 2026-11-07 |
| [0270](0270-smart-trends-report-every-tracked-disk.md) | GET /health/smart/trends reports every tracked disk, and trends read each counter from the snapshots that hold it | accepted | 2026-11-07 |
| [0272](0272-scrub-results-name-cancelled-scrubs-and-count-every-error.md) | A scrub's result names a cancelled or interrupted scrub and counts every error btrfs reports | superseded by [0313](0313-a-scrub-runs-from-its-start-and-its-result-names-how-it-ended.md) | 2026-11-07 |
| [0282](0282-a-filesystem-found-on-the-disks-is-imported-under-a-name-that-becomes-its-label.md) | A btrfs filesystem found on the disks is imported at its top level, under a name that becomes its label | superseded by [0283](0283-a-filesystem-found-on-the-disks-becomes-a-pool-only-from-data-disks-under-a-free-name.md) | 2026-11-08 |
| [0283](0283-a-filesystem-found-on-the-disks-becomes-a-pool-only-from-data-disks-under-a-free-name.md) | A filesystem found on the disks becomes a pool only from data disks and under a name that no earlier pool still uses | accepted | 2026-11-08 |
| [0285](0285-the-recovery-runbook-reads-a-pool-from-another-linux-machine-without-writing-to-it.md) | The recovery runbook reads a pool from another Linux machine without writing to it, degraded when a disk is missing | accepted | 2026-11-08 |
| [0288](0288-an-unmount-also-unmounts-the-copies-that-services-keep-in-their-mount-namespaces.md) | An unmount also unmounts the copies of a pool's mount that services keep in mount namespaces of their own, and refuses every other user as before | accepted | 2026-11-08 |
| [0289](0289-commands-in-the-container-are-killed-at-their-timeout-and-not-waited-for.md) | Commands of btrfs and smartctl in the container are killed at their timeout without a wait, and a command still stuck is not started again | superseded by [0290](0290-commands-in-the-container-are-killed-at-their-timeout-and-a-cleanup-without-an-answer-fails.md) | 2026-11-08 |
| [0290](0290-commands-in-the-container-are-killed-at-their-timeout-and-a-cleanup-without-an-answer-fails.md) | Commands of btrfs and smartctl in the container are killed at their timeout without a wait, a command still stuck is not started again, and a snapshot cleanup without an answer fails its run | accepted | 2026-11-08 |
| [0311](0311-a-scrub-starts-only-on-a-mounted-pool-through-one-helper.md) | A scrub starts only on a pool whose btrfs filesystem is mounted, through one helper | accepted | 2026-11-09 |
| [0313](0313-a-scrub-runs-from-its-start-and-its-result-names-how-it-ended.md) | A scrub runs from its start, before btrfs records it, and its result names how it ended | superseded by [0367](0367-the-storage-page-follows-a-running-scrub-over-the-events-socket.md) | 2026-11-09 |
| [0344](0344-pool-unmount-and-delete-are-refused-when-docker-cannot-be-asked.md) | Pool unmount and delete are refused when Docker cannot be asked, respect Docker volumes, and odd names do not break storage | accepted | 2026-11-11 |
| [0352](0352-volume-reads-container-folders-disk-names-and-deletes-withstand-renames-odd-names-links-and-new-mounts.md) | Volume reads, container folders, disk names and deletes withstand renames, odd names, links and new mounts | accepted | 2026-11-12 |
| [0358](0358-restored-copies-are-listed-and-deleted-on-the-snapshots-page-by-subvolume-id.md) | Restored copies are listed and deleted on the Snapshots page, by subvolume id after the checks of a volume's Delete | accepted | 2026-11-12 |
| [0360](0360-the-storage-page-loads-its-lists-again-when-the-block-devices-change.md) | The Storage page loads its lists again when the block devices of the stats stream change | accepted | 2026-11-12 |
| [0367](0367-the-storage-page-follows-a-running-scrub-over-the-events-socket.md) | A scrub runs from its start, its result names how it ended, and the Storage page follows it over the events socket | accepted | 2026-11-12 |
| [0378](0378-data-disks-get-an-idle-timeout-kept-in-a-udev-rule-and-nothing-periodic-wakes-a-sleeping-disk.md) | Data disks get an idle timeout that UNOS keeps in a udev rule, and nothing UNOS does periodically wakes a sleeping disk | accepted | 2026-11-15 |
| [0379](0379-a-balance-runs-in-a-host-unit-with-a-usage-limit-of-20-percent-and-a-full-balance-behind-a-confirmation.md) | A balance runs in a unit of the host, rewrites the chunks less than 20% full by default, and a full one needs a confirmation | accepted | 2026-11-15 |
| [0387](0387-volume-quota-use-is-read-from-sysfs-alerted-at-80-90-and-95-percent-and-kept-in-ram.md) | A volume's quota use is read from the kernel's sysfs, alerted once per level of 80, 90 and 95 percent, and its hourly history is kept in RAM and written at shutdown | accepted | 2026-11-17 |
| [0411](0411-a-disk-joins-a-pool-at-once-and-leaves-it-in-a-host-unit-after-typed-confirmations-and-checks-of-the-profile-and-the-space.md) | A disk joins a pool at once and leaves it in a unit of the host, after typed confirmations and checks of the profile and the space | superseded by [0429](0429-a-pool-on-an-nvme-drive-of-a-spacemit-k1-gets-trim-like-every-other-pool.md) | 2026-11-18 |
| [0414](0414-a-hot-plugged-disk-is-read-by-smartctl-through-a-node-the-supervisor-makes-in-its-own-dev.md) | A disk attached after the supervisor started is read by smartctl through a node that the supervisor makes in its own /dev from the host's sysfs | accepted | 2026-11-18 |
| [0429](0429-a-pool-on-an-nvme-drive-of-a-spacemit-k1-gets-trim-like-every-other-pool.md) | A pool on an NVMe drive of a SpacemiT K1 gets TRIM like every other pool | accepted | 2026-11-19 |

## Shares

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0037](0037-share-and-export-values-validated.md) | Share and export values are validated before they reach smb.conf or /etc/exports | superseded by [0293](0293-share-and-export-values-validated-and-web-exports-limited-to-local-networks.md) | 2026-11-05 |
| [0038](0038-share-name-rule.md) | Share names have 1 to 32 letters, digits, '_' or '-' and do not start with '-' | accepted | 2026-11-05 |
| [0074](0074-smb-state-in-sqlite-and-unos-writes-smb-conf.md) | Shares and SMB accounts live in SQLite, and UNOS alone writes smb.conf | accepted | 2026-11-06 |
| [0075](0075-per-user-share-access-levels.md) | Each share grants each user none, read or write, and new users get no access | accepted | 2026-11-06 |
| [0076](0076-no-group-principals-for-smb-yet.md) | Share access names individual users, not groups, for now | superseded by [0385](0385-groups-of-users-get-share-access-and-smb-conf-names-their-active-members.md) | 2026-11-06 |
| [0077](0077-smb-tables-through-create-all-without-migrations.md) | The SMB tables come from create_all, and existing tables are never altered | superseded by [0319](0319-a-restore-refuses-backups-without-smb-tables-and-a-backups-database-alone-holds-its-shares.md) | 2026-11-06 |
| [0078](0078-accept-the-sqlite-power-cut-window.md) | SQLite's power-cut rollback window is accepted, and the startup reconcile repairs the host | superseded by [0306](0306-the-database-writes-ahead-with-synchronous-normal.md) | 2026-11-06 |
| [0079](0079-sqlite-busy-timeout-and-unbounded-pool-overflow.md) | The SQLite engine waits 15 s for locks and has an unbounded pool overflow | accepted | 2026-11-06 |
| [0080](0080-one-lock-for-every-smb-change.md) | One lock, smb_lock, serializes every change of SMB state | accepted | 2026-11-06 |
| [0081](0081-reload-rows-after-taking-smb-lock.md) | SMB flows reload the user and the account after taking smb_lock | accepted | 2026-11-06 |
| [0082](0082-smb-code-writes-only-on-real-changes.md) | The SMB code writes to the system disk only when accounts or shares change | accepted | 2026-11-06 |
| [0083](0083-import-smb-conf-when-the-shares-table-is-empty.md) | Shares are imported from smb.conf whenever the shares table is empty | superseded by [0084](0084-import-the-existing-smb-conf-once.md) | 2026-11-06 |
| [0084](0084-import-the-existing-smb-conf-once.md) | The existing smb.conf is imported once per database, controlled by a marker | accepted | 2026-11-06 |
| [0085](0085-keep-the-original-smb-conf-as-import-backup.md) | The original smb.conf is kept once as smb.conf.unos-import-backup | superseded by [0362](0362-every-replaced-smb-conf-is-kept-in-a-dated-copy-and-the-newest-10-stay.md) | 2026-11-06 |
| [0086](0086-the-import-only-narrows-access.md) | The smb.conf import only narrows access and reports every option it drops | superseded by [0380](0380-a-share-can-require-encryption-serve-time-machine-and-list-snapshots-as-previous-versions.md) | 2026-11-06 |
| [0087](0087-hardened-pinned-global-section.md) | UNOS renders a hardened [global] section with pinned values | superseded by [0281](0281-hardened-pinned-global-section-with-auth-audit-logging.md) | 2026-11-06 |
| [0088](0088-fail-safe-share-sections.md) | Share sections never express a restriction by leaving a line out | accepted | 2026-11-06 |
| [0089](0089-apply-is-checked-by-testparm-and-atomic.md) | Every apply is checked by testparm and installed atomically | accepted | 2026-11-06 |
| [0090](0090-reload-all-and-retry-failed-reloads.md) | Samba is reloaded with smbcontrol all, and failed reloads and closes are retried | accepted | 2026-11-06 |
| [0091](0091-testparm-tolerates-two-unknown-parameters.md) | The testparm check tolerates only two parameters that older Samba lacks | accepted | 2026-11-06 |
| [0092](0092-copy-foreign-sections-before-overwriting.md) | Foreign share sections are saved to smb.conf.unos-replaced before an apply | superseded by [0362](0362-every-replaced-smb-conf-is-kept-in-a-dated-copy-and-the-newest-10-stay.md) | 2026-11-06 |
| [0093](0093-globals-is-a-reserved-share-name.md) | The share name 'globals' is reserved like 'global' | accepted | 2026-11-06 |
| [0094](0094-samba-cache-in-ram-and-syslog-logging.md) | Samba keeps its cache in /run/samba and logs to syslog | superseded by [0277](0277-samba-logs-failed-sign-ins-to-syslog-and-keeps-its-log-file-in-ram.md) | 2026-11-06 |
| [0095](0095-smb-answers-lan-addresses-only.md) | SMB answers only LAN addresses, through hosts allow in [global] | accepted | 2026-11-06 |
| [0096](0096-guest-access-off-and-guest-shares-read-only.md) | Guest access is off by default, and guest shares are read-only | accepted | 2026-11-06 |
| [0097](0097-restrict-anonymous-0-with-guest-shares.md) | A guest share sets restrict anonymous = 0 and map to guest = Bad User | superseded by [0098](0098-guest-globals-follow-guest-shares.md) | 2026-11-06 |
| [0098](0098-guest-globals-follow-guest-shares.md) | The guest-related globals follow whether any guest share exists | superseded by [0339](0339-the-guest-globals-follow-guest-shares-and-an-anonymous-share-list-names-no-share.md) | 2026-11-06 |
| [0099](0099-server-signing-at-samba-default.md) | Server signing stays at Samba's default | superseded by [0100](0100-signing-mandatory-unless-a-guest-share-exists.md) | 2026-11-06 |
| [0100](0100-signing-mandatory-unless-a-guest-share-exists.md) | Server signing is mandatory unless a guest share exists | accepted | 2026-11-06 |
| [0101](0101-smb3-unix-extensions-off.md) | SMB3 Unix extensions stay off, so app folders may be shared | accepted | 2026-11-06 |
| [0102](0102-accepted-risk-unthrottled-smb-logins.md) | SMB logins have no rate limit and no common-password check, as an accepted risk | superseded by [0280](0280-smb-logins-stay-unthrottled-until-the-owner-chooses-a-limit.md) | 2026-11-06 |
| [0103](0103-close-share-after-every-shrink.md) | Open connections to a share are closed after every shrink of its access | accepted | 2026-11-06 |
| [0104](0104-end-smb-sessions-on-password-change-and-revocation.md) | An account's SMB sessions end at every password change and revocation | accepted | 2026-11-06 |
| [0105](0105-smb-accounts-are-host-accounts-plus-tdbsam.md) | SMB accounts are real Linux host accounts plus a tdbsam entry | accepted | 2026-11-06 |
| [0106](0106-smb-uids-20000-to-29999.md) | SMB accounts use uids 20000-29999 and gid 20000 | superseded by [0107](0107-smb-uids-70000-to-79999-and-gid-70000.md) | 2026-11-06 |
| [0107](0107-smb-uids-70000-to-79999-and-gid-70000.md) | SMB accounts use uids 70000-79999 and gid 70000 | accepted | 2026-11-06 |
| [0108](0108-unos-touches-only-its-marked-accounts.md) | UNOS changes only its own marked host accounts and passdb entries | accepted | 2026-11-06 |
| [0109](0109-smb-uids-are-never-reused.md) | SMB uids are never handed out twice, also across reinstalls | accepted | 2026-11-06 |
| [0110](0110-no-uid-adoption.md) | A row never adopts a host account's uid, and a stale marked account is removed first | accepted | 2026-11-06 |
| [0111](0111-restored-row-keeps-its-uid.md) | In a restore carry-over the restored row keeps its uid | superseded by [0112](0112-one-uid-one-account.md) | 2026-11-06 |
| [0112](0112-one-uid-one-account.md) | A uid belongs to one account: restored rows and stale accounts lose conflicting uids | accepted | 2026-11-06 |
| [0113](0113-which-usernames-can-be-smb-names.md) | Only usernames that fit the Linux name rule and clash with nothing can be SMB names | accepted | 2026-11-06 |
| [0114](0114-sid-like-names-are-reserved.md) | Names that start with 's-' and a digit are reserved | accepted | 2026-11-06 |
| [0115](0115-new-usernames-follow-the-smb-name-rule.md) | New UNOS usernames follow the SMB name rule and are unique ignoring case | accepted | 2026-11-06 |
| [0116](0116-users-cannot-be-renamed.md) | UNOS users cannot be renamed yet | superseded by [0368](0368-a-rename-moves-the-users-smb-account-to-the-new-name-keeping-its-uid.md) | 2026-11-06 |
| [0117](0117-administrators-never-have-smb-access.md) | Administrators never have SMB access | accepted | 2026-11-06 |
| [0118](0118-smb-is-opt-in-per-user.md) | SMB is opt-in per user: an administrator allows it, and a password activates it | accepted | 2026-11-06 |
| [0119](0119-smb-password-is-the-unos-password.md) | The SMB password is the UNOS password, captured only where the plaintext is given | accepted | 2026-11-06 |
| [0120](0120-smb-password-set-wherever-the-plaintext-is-given.md) | The SMB password is set wherever the plaintext is available | superseded by [0121](0121-password-changes-update-only-active-accounts.md) | 2026-11-06 |
| [0121](0121-password-changes-update-only-active-accounts.md) | A password change updates Samba only for active accounts, and activation is explicit | accepted | 2026-11-06 |
| [0122](0122-smb-bound-password-rule.md) | SMB-bound passwords need at least 8 characters, at most 72 bytes, no control characters | superseded by [0279](0279-smb-bound-passwords-must-not-be-common-passwords.md) | 2026-11-06 |
| [0123](0123-samba-gets-the-password-before-the-hash.md) | Samba's copy of a password is written before the bcrypt hash | accepted | 2026-11-06 |
| [0124](0124-compensate-a-failed-commit-after-smbpasswd.md) | A failed commit after a successful smbpasswd is compensated | accepted | 2026-11-06 |
| [0125](0125-no-smb-password-in-argv-logs-or-answers.md) | No SMB password appears in argv, logs, errors, HTTP answers or the database | accepted | 2026-11-06 |
| [0126](0126-disabled-passdb-entries.md) | Accounts without a password get disabled passdb entries | superseded by [0127](0127-inactive-accounts-have-no-passdb-entry.md) | 2026-11-06 |
| [0127](0127-inactive-accounts-have-no-passdb-entry.md) | An account that is not active has no passdb entry, never a disabled one | accepted | 2026-11-06 |
| [0128](0128-reconcile-creates-all-host-accounts.md) | The reconcile creates host accounts for every SMB-enabled user | superseded by [0129](0129-host-accounts-created-at-first-activation.md) | 2026-11-06 |
| [0129](0129-host-accounts-created-at-first-activation.md) | Host accounts are created at the first activation | accepted | 2026-11-06 |
| [0130](0130-no-smb-activation-before-unos-smb-conf.md) | SMB cannot be turned on while Samba serves an smb.conf that UNOS did not write | superseded by [0249](0249-smb-status-names-an-smb-conf-replaced-after-unos-wrote-it.md) | 2026-11-06 |
| [0131](0131-self-flows-answer-fixed-texts.md) | A regular user's own SMB and password flows answer only fixed texts | accepted | 2026-11-06 |
| [0132](0132-new-users-never-inherit-smb-rows.md) | Creating a user clears the SMB rows left for its id | accepted | 2026-11-06 |
| [0133](0133-requests-by-user-id-carry-the-username.md) | Requests that name a user by id also carry the username | accepted | 2026-11-06 |
| [0134](0134-share-edits-are-patch-like.md) | Share edits change only the fields that are sent, and an unknown share is 404 | accepted | 2026-11-06 |
| [0135](0135-share-version-for-concurrent-edits.md) | Shares carry a version, and PATCH takes expected_version | accepted | 2026-11-06 |
| [0136](0136-dismiss-import-notes-of-unserved-shares.md) | Import notes can be dismissed for a share that is not served | accepted | 2026-11-06 |
| [0137](0137-purge-smb-rows-that-no-longer-match.md) | SMB rows whose username or role no longer matches are purged | accepted | 2026-11-06 |
| [0138](0138-startup-reconcile-in-the-background.md) | The startup reconcile runs in the background under smb_lock and ends with a sweep | accepted | 2026-11-06 |
| [0139](0139-restore-prepares-the-database-before-the-swap.md) | A restore prepares the extracted database before the swap, under smb_lock | superseded by [0319](0319-a-restore-refuses-backups-without-smb-tables-and-a-backups-database-alone-holds-its-shares.md) | 2026-11-06 |
| [0140](0140-restore-marks-every-smb-account-needed.md) | After a restore every SMB account needs its password again | superseded by [0141](0141-restored-accounts-stay-active-only-with-proof.md) | 2026-11-06 |
| [0141](0141-restored-accounts-stay-active-only-with-proof.md) | After a restore an SMB account stays active only with proof | accepted | 2026-11-06 |
| [0142](0142-old-backups-import-shares-without-guest-access.md) | Old backups import their shares from smb.conf, never with guest access | superseded by [0319](0319-a-restore-refuses-backups-without-smb-tables-and-a-backups-database-alone-holds-its-shares.md) | 2026-11-06 |
| [0143](0143-shares-are-part-of-the-database-backup.md) | Shares are part of the database, and include_shares only adds the old smb.conf copy | superseded by [0319](0319-a-restore-refuses-backups-without-smb-tables-and-a-backups-database-alone-holds-its-shares.md) | 2026-11-06 |
| [0144](0144-restored-databases-are-untrusted.md) | Restored databases are untrusted input | accepted | 2026-11-06 |
| [0145](0145-what-may-be-shared.md) | A share is a folder strictly inside a managed pool, never the pool root or apps/ | accepted | 2026-11-06 |
| [0146](0146-share-folders-do-not-overlap.md) | Share folders must not overlap | accepted | 2026-11-06 |
| [0147](0147-snapshots-hidden-from-smb.md) | Snapshot folders are hidden from SMB and never shared or repaired | accepted | 2026-11-06 |
| [0148](0148-only-managed-pools-hold-shares.md) | Only pools that UNOS manages can hold SMB shares, and the UI offers only those | superseded by [0294](0294-only-managed-pools-hold-shares-and-nfs-exports.md) | 2026-11-06 |
| [0149](0149-fail-closed-without-the-pool-list.md) | SMB changes fail closed when the pool list cannot be read | accepted | 2026-11-06 |
| [0150](0150-share-folders-root-unos-smb-2770.md) | Share folders belong to root and the group unos-smb with mode 2770 | accepted | 2026-11-06 |
| [0151](0151-no-folders-while-gid-70000-is-taken.md) | No share folder is created and no Repair runs while another group has gid 70000 | accepted | 2026-11-06 |
| [0152](0152-share-folders-created-through-the-pool-mount.md) | Share folders are created only through the pool's mount, checked on descriptors | accepted | 2026-11-06 |
| [0153](0153-share-folders-only-below-root-only-folders.md) | Share folders are created only below folders that only root can change | accepted | 2026-11-06 |
| [0154](0154-unos-never-changes-existing-folder-modes.md) | UNOS never changes the mode of an existing folder, and the owner runs the chmod | accepted | 2026-11-06 |
| [0155](0155-repair-permissions-only-on-request.md) | Guest-era permissions are fixed only by an explicit per-share Repair | superseded by [0363](0363-repair-permissions-runs-as-a-background-job-that-reports-over-the-events-socket.md) | 2026-11-06 |
| [0156](0156-repair-walks-with-descriptors.md) | Repair walks the folder tree with descriptors and opens nothing it must not | accepted | 2026-11-06 |
| [0157](0157-smb-audits-only-real-changes.md) | The SMB code audits only real changes, and problems go to the status | superseded by [0320](0320-smb-changes-are-audited-with-actions-of-their-own-and-the-rollback-era-safeguards-stay-for-restores-and-hand-edits.md) | 2026-11-06 |
| [0158](0158-netbios-stays-until-discovery.md) | NetBIOS stays until a discovery slice adds WS-Discovery and Bonjour | superseded by [0275](0275-windows-finds-the-nas-through-wsdd2-macos-through-bonjour-and-netbios-is-off.md) | 2026-11-06 |
| [0159](0159-passdb-stays-on-the-system-disk.md) | Samba's passdb stays on the system disk until its writes are measured | superseded by [0310](0310-a-failed-smb-sign-in-writes-nothing-to-passdb.md) | 2026-11-06 |
| [0160](0160-no-file-manager-or-app-group-access-to-shares.md) | The File Manager and apps get no group access to share folders yet | superseded by [0273](0273-file-manager-entries-in-share-folders-get-2770-and-0660.md) | 2026-11-06 |
| [0161](0161-no-per-share-protocol-options-yet.md) | Per-share protocol options wait for a later slice | superseded by [0380](0380-a-share-can-require-encryption-serve-time-machine-and-list-snapshots-as-previous-versions.md) | 2026-11-06 |
| [0162](0162-apparmor-profile-for-new-share-paths.md) | UNOS does not manage smbd's AppArmor profile while smbd runs unconfined | accepted | 2026-11-07 |
| [0163](0163-nfs-unchanged-in-slice-2.md) | NFS exports stay unchanged in slice 2 | superseded by [0292](0292-nfs-exports-in-their-own-file-read-as-exportfs-reads-them.md) | 2026-11-06 |
| [0164](0164-smb-and-audit-follow-ups-left-for-later.md) | Six SMB and audit follow-ups wait for later slices | accepted | 2026-11-06 |
| [0192](0192-reread-host-facts-before-destructive-commands.md) | Host facts are read again right before destructive commands, and doubt fails closed | accepted | 2026-11-07 |
| [0193](0193-acl-entries-and-sticky-folders-in-the-parent-chain.md) | ACL entries and sticky folders count in the parent-chain rule | proposed | 2026-11-07 |
| [0194](0194-reduce-samba-runtime-writes.md) | Reduce Samba's remaining writes to the system disk | superseded by [0278](0278-samba-keeps-a-password-history-of-three.md) | 2026-11-07 |
| [0195](0195-open-smb-follow-ups-after-slice-2.md) | Handle the SMB follow-ups left open after slice 2 | superseded by [0250](0250-smb-follow-ups-still-open-after-slice-2.md) | 2026-11-07 |
| [0248](0248-pool-changes-apply-smb-conf.md) | The storage endpoints apply smb.conf after a pool change, so an unmounted pool's shares stop being served at once | accepted | 2026-11-07 |
| [0249](0249-smb-status-names-an-smb-conf-replaced-after-unos-wrote-it.md) | SMB cannot be turned on while Samba serves an smb.conf that UNOS did not write, and the status names one replaced after UNOS wrote it | accepted | 2026-11-07 |
| [0250](0250-smb-follow-ups-still-open-after-slice-2.md) | Handle the SMB follow-ups still open after slice 2 | superseded by [0276](0276-smb-follow-ups-still-open-after-network-discovery.md) | 2026-11-07 |
| [0275](0275-windows-finds-the-nas-through-wsdd2-macos-through-bonjour-and-netbios-is-off.md) | Windows finds the NAS through wsdd2, macOS through Samba's Bonjour announcement, and NetBIOS is off | accepted | 2026-11-08 |
| [0276](0276-smb-follow-ups-still-open-after-network-discovery.md) | Handle the SMB follow-ups still open after network discovery | superseded by [0342](0342-samba-refuses-every-password-change-over-samr-so-smb-passwords-change-only-through-unos.md) | 2026-11-08 |
| [0277](0277-samba-logs-failed-sign-ins-to-syslog-and-keeps-its-log-file-in-ram.md) | Samba logs failed sign-ins through auth_audit to syslog and keeps its own log file in /run/samba | accepted | 2026-11-08 |
| [0278](0278-samba-keeps-a-password-history-of-three.md) | Samba keeps a password history of 3, so that retries with an account's previous passwords write nothing to passdb | accepted | 2026-11-08 |
| [0279](0279-smb-bound-passwords-must-not-be-common-passwords.md) | SMB-bound passwords need at least 8 characters, at most 72 bytes, no control characters, and must not be common passwords | accepted | 2026-11-08 |
| [0280](0280-smb-logins-stay-unthrottled-until-the-owner-chooses-a-limit.md) | SMB logins have no rate limit and no lockout, as an accepted risk until the owner chooses how to limit them | superseded by [0334](0334-samba-locks-an-smb-account-for-five-minutes-after-five-failed-sign-ins.md) | 2026-11-08 |
| [0281](0281-hardened-pinned-global-section-with-auth-audit-logging.md) | UNOS renders a hardened [global] section with pinned values, among them Samba's logging lines of ADR-0277 | accepted | 2026-11-08 |
| [0292](0292-nfs-exports-in-their-own-file-read-as-exportfs-reads-them.md) | UNOS keeps its NFS exports in its own export file on the host, reads every export file as exportfs reads it, and applies them with the host's exportfs | superseded by [0321](0321-nfs-exports-in-their-own-file-read-as-exportfs-reads-them-audited-with-actions-of-their-own.md) | 2026-11-08 |
| [0293](0293-share-and-export-values-validated-and-web-exports-limited-to-local-networks.md) | Share and export values are validated before they reach smb.conf or an export file, and an NFS export from the web interface is an existing folder for one local network, read-only or read-write | accepted | 2026-11-08 |
| [0294](0294-only-managed-pools-hold-shares-and-nfs-exports.md) | Only pools that UNOS manages can hold SMB shares and NFS exports, and the UI offers only those | accepted | 2026-11-08 |
| [0310](0310-a-failed-smb-sign-in-writes-nothing-to-passdb.md) | A failed SMB sign-in writes nothing to passdb.tdb while no lockout counts failures, so passdb stays on the system disk | superseded by [0334](0334-samba-locks-an-smb-account-for-five-minutes-after-five-failed-sign-ins.md) | 2026-11-09 |
| [0319](0319-a-restore-refuses-backups-without-smb-tables-and-a-backups-database-alone-holds-its-shares.md) | A restore refuses backups without SMB tables, and a backup's database alone holds its shares | accepted | 2026-11-10 |
| [0320](0320-smb-changes-are-audited-with-actions-of-their-own-and-the-rollback-era-safeguards-stay-for-restores-and-hand-edits.md) | SMB changes are audited with actions of their own, and the safeguards of the rollback era stay for restores and hand edits | accepted | 2026-11-10 |
| [0321](0321-nfs-exports-in-their-own-file-read-as-exportfs-reads-them-audited-with-actions-of-their-own.md) | UNOS keeps its NFS exports in its own export file on the host, reads every export file as exportfs reads it, applies them with the host's exportfs, and audits them with actions of their own | accepted | 2026-11-10 |
| [0334](0334-samba-locks-an-smb-account-for-five-minutes-after-five-failed-sign-ins.md) | Samba locks an SMB account for 5 minutes after 5 failed sign-ins, and passdb.tdb stays on the system disk | accepted | 2026-11-10 |
| [0339](0339-the-guest-globals-follow-guest-shares-and-an-anonymous-share-list-names-no-share.md) | The guest-related globals follow whether any guest share exists, and an anonymous share list names no share | accepted | 2026-11-11 |
| [0342](0342-samba-refuses-every-password-change-over-samr-so-smb-passwords-change-only-through-unos.md) | Samba refuses every password change over SAMR with a check password script that always fails, so SMB passwords change only through UNOS | accepted | 2026-11-11 |
| [0349](0349-a-restore-refuses-a-backup-whose-database-gives-one-smb-uid-to-two-accounts.md) | A restore refuses a backup whose database gives one SMB uid to two accounts | accepted | 2026-11-11 |
| [0353](0353-smb-uids-handed-out-are-recorded-and-a-restored-account-keeps-one-only-under-its-name.md) | SMB uids handed out are recorded for good, and a restored account keeps a recorded uid only under the name it went to | accepted | 2026-11-12 |
| [0362](0362-every-replaced-smb-conf-is-kept-in-a-dated-copy-and-the-newest-10-stay.md) | Every replaced smb.conf is kept in a copy named with its UTC time, and the newest 10 of each kind stay | accepted | 2026-11-12 |
| [0368](0368-a-rename-moves-the-users-smb-account-to-the-new-name-keeping-its-uid.md) | An administrator renames a user, and the user's SMB account moves to the new name with its uid | accepted | 2026-11-12 |
| [0380](0380-a-share-can-require-encryption-serve-time-machine-and-list-snapshots-as-previous-versions.md) | A share can require encryption, serve Time Machine and list snapshots as Previous Versions | accepted | 2026-11-15 |
| [0385](0385-groups-of-users-get-share-access-and-smb-conf-names-their-active-members.md) | Groups of users get share access in UNOS's database, and smb.conf names their active members | accepted | 2026-11-17 |
| [0391](0391-an-administrator-binds-smb-to-chosen-interfaces-by-name-with-the-loopback-and-a-restart-of-smbd.md) | An administrator binds SMB to chosen network interfaces by name, the loopback stays bound, and a change restarts smbd | accepted | 2026-11-17 |
| [0418](0418-an-iscsi-target-serves-a-sparse-file-of-a-volume-through-the-kernels-lio-with-mandatory-chap-restored-at-every-start.md) | An iSCSI target serves a sparse file of a volume through the kernel's LIO with mandatory CHAP, and UNOS restores its targets at every start | accepted | 2026-11-19 |

## Apps

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0039](0039-app-data-only-on-a-mounted-pool.md) | App data lives only inside a selected, mounted pool | superseded by [0318](0318-app-data-lives-only-on-a-mounted-pool-without-handling-for-apps-of-older-versions.md) | 2026-11-05 |
| [0219](0219-transmission-requires-a-web-login.md) | The transmission app requires a web login | accepted | 2026-11-07 |
| [0254](0254-docker-calls-run-in-worker-threads-and-conflicts-answer-409.md) | Docker calls of the containers and apps APIs run in worker threads within the Docker client's timeout, and the containers API answers Docker's conflicts with 409 | accepted | 2026-11-07 |
| [0255](0255-container-logs-stream-in-the-drawer-and-images-get-a-tab.md) | The container drawer follows the log over the logs socket, and the Images tab pulls by name and removes only what no container uses | accepted | 2026-11-07 |
| [0256](0256-the-apps-api-acts-only-on-apps-and-uninstall-keeps-their-data.md) | The Apps API starts, stops, restarts and uninstalls only the containers of apps, and uninstall keeps their data | accepted | 2026-11-07 |
| [0274](0274-apps-of-linuxserver-images-can-run-in-unos-smb-and-only-they-get-share-folders.md) | Apps of linuxserver.io images can be installed to run in the group unos-smb, and only such an app is given a share's folder | accepted | 2026-11-08 |
| [0318](0318-app-data-lives-only-on-a-mounted-pool-without-handling-for-apps-of-older-versions.md) | App data lives only on a mounted pool, without handling for apps of older versions | accepted | 2026-11-10 |
| [0323](0323-the-container-and-app-routes-answer-503-with-the-reason-when-docker-does-not-answer.md) | The container and app routes answer 503 with the reason when Docker does not answer | accepted | 2026-11-10 |
| [0327](0327-the-app-store-lists-its-catalog-when-the-installed-apps-cannot-be-loaded.md) | The App Store lists its catalog when the installed apps cannot be loaded | superseded by [0430](0430-the-web-ui-loads-behind-a-boot-screen-draws-its-shell-once-and-shows-skeletons-in-each-pages-final-layout.md) | 2026-11-10 |
| [0377](0377-on-an-rk3588-jellyfin-gets-the-video-hardware-without-privileges-through-hardware-profiles.md) | On an RK3588 Jellyfin gets the video hardware without privileges, through the hardware profiles of the catalog | superseded by [0382](0382-hardware-profiles-apply-per-container-may-choose-its-image-and-give-immichs-machine-learning-the-rk3588-npu.md) | 2026-11-15 |
| [0381](0381-an-app-of-several-containers-installs-through-the-docker-api-with-pinned-images-and-a-password-on-its-pool.md) | An app of several containers installs through the Docker API with pinned images and a password on its pool | superseded by [0384](0384-an-apps-install-runs-as-a-background-job-whose-progress-the-app-store-shows.md) | 2026-11-16 |
| [0382](0382-hardware-profiles-apply-per-container-may-choose-its-image-and-give-immichs-machine-learning-the-rk3588-npu.md) | Hardware profiles apply per container, may choose the container's image and give Immich's machine learning the RK3588's NPU | accepted | 2026-11-16 |
| [0384](0384-an-apps-install-runs-as-a-background-job-whose-progress-the-app-store-shows.md) | An app's install runs as a background job whose progress the App Store shows | superseded by [0437](0437-containers-and-the-app-store-get-one-filter-quiet-actions-and-plain-states-and-an-apps-container-is-removed-only-with-its-app.md) | 2026-11-17 |
| [0390](0390-each-container-of-an-app-gets-its-own-memory-and-cpu-limits-whose-use-is-read-from-its-cgroup.md) | Each container of an app gets its own memory and CPU limits, kept by Docker and for the next install, and its use is read from its cgroup and alerted | accepted | 2026-11-17 |
| [0403](0403-the-app-store-offers-only-apps-whose-images-publish-the-hosts-platform.md) | The App Store offers only apps whose images publish the host's platform, and the install refuses the others before any pull | accepted | 2026-11-18 |
| [0408](0408-an-installed-app-updates-to-the-catalogs-newer-pinned-images-with-a-copy-of-its-database-and-can-be-rolled-back.md) | An installed app updates to the catalog's newer pinned images with a copy of its database, and can be rolled back | accepted | 2026-11-18 |
| [0410](0410-every-catalog-image-is-pinned-by-a-version-tag-and-digest-and-a-weekly-audit-reports-moved-tags.md) | Every image of the catalog is pinned by a version tag and its index digest, and a weekly audit reports the tags that moved | accepted | 2026-11-18 |
| [0415](0415-bookstack-paperless-ngx-and-linkwarden-install-with-their-database-or-broker-as-companions.md) | BookStack, Paperless-ngx and Linkwarden install with their database or broker as companions, their secrets as generated files and their databases awaited by a command | accepted | 2026-11-18 |
| [0426](0426-the-catalogs-linuxserver-images-are-pinned-and-pulled-from-ghcr-io-not-lscr-io.md) | The catalog's LinuxServer images are pinned and pulled from ghcr.io, not lscr.io | accepted | 2026-11-19 |

## Backup

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0040](0040-restore-checks-archives-and-regenerates-smb-conf.md) | Restore checks archives and regenerates smb.conf instead of installing the archived file | superseded by [0319](0319-a-restore-refuses-backups-without-smb-tables-and-a-backups-database-alone-holds-its-shares.md) | 2026-11-05 |
| [0041](0041-off-device-backup-slice.md) | Give off-device backup of user data its own slice | superseded by [0337](0337-off-device-copies-go-to-one-restic-repository-on-a-disk-or-an-s3-store.md) | 2026-11-05 |
| [0165](0165-backup-restore-flows-and-unique-names.md) | Backups restore from the tab, accept encrypted uploads and get unique names | accepted | 2026-11-06 |
| [0166](0166-uploaded-backups-checked-in-one-pass.md) | Uploaded backups are checked in one streaming pass that writes nothing | superseded by [0345](0345-backups-hold-neither-fstab-nor-netplan-and-an-encrypted-upload-that-is-gzip-data-is-refused.md) | 2026-11-06 |
| [0167](0167-deferred-backup-and-upload-edge-cases.md) | Fix the deferred backup and upload edge cases of slice 1 | superseded by [0354](0354-leftovers-of-uploads-are-found-after-each-start-and-removed-only-when-an-administrator-asks.md) | 2026-11-06 |
| [0240](0240-scheduled-backups-encrypted-with-a-key-derived-from-the-passphrase.md) | Backup tasks encrypt every backup with a key derived from their passphrase and stored in a file of its own | superseded by [0325](0325-backup-tasks-keep-their-keys-in-a-file-of-their-own-that-is-set-aside-when-damaged.md) | 2026-11-07 |
| [0325](0325-backup-tasks-keep-their-keys-in-a-file-of-their-own-that-is-set-aside-when-damaged.md) | Backup tasks encrypt every backup with a key derived from their passphrase, stored in a file of its own that is set aside when damaged | accepted | 2026-11-10 |
| [0337](0337-off-device-copies-go-to-one-restic-repository-on-a-disk-or-an-s3-store.md) | Off-device copies go to one restic repository on a disk or an S3 store, and each copy is checked and its restore tested | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-11 |
| [0345](0345-backups-hold-neither-fstab-nor-netplan-and-an-encrypted-upload-that-is-gzip-data-is-refused.md) | Backups hold neither fstab nor netplan, and an encrypted upload that is gzip data is refused | accepted | 2026-11-11 |
| [0355](0355-the-backup-api-has-no-include-options-and-a-manifest-names-no-options.md) | The backup API has no include_ options, and a backup's manifest names no options | accepted | 2026-11-12 |
| [0364](0364-a-restore-checks-the-archive-and-extracts-its-database-in-one-streaming-pass.md) | A restore checks the archive and extracts its database in one streaming pass | accepted | 2026-11-12 |
| [0422](0422-off-device-copies-also-go-to-a-rest-server-and-to-sftp-through-the-hosts-ssh-with-the-host-key-pinned.md) | Off-device copies also go to a restic REST server over HTTPS and to an SFTP server through the host's ssh, with a key pair of UNOS and the host key pinned at the first test | accepted | 2026-11-19 |

## Files

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0042](0042-file-paths-stay-inside-the-pools.md) | File paths stay inside the pools and never follow symlinks | accepted | 2026-11-05 |
| [0168](0168-file-manager-download-delete-copy-move.md) | The file manager downloads with the session and never overwrites on copy or move | accepted | 2026-11-06 |
| [0169](0169-uploads-stream-into-their-folder.md) | Uploads stream into their folder and stay unnamed until complete | accepted | 2026-11-06 |
| [0170](0170-file-manager-protects-volumes-and-mounts.md) | The file manager never deletes, renames or moves volumes, subvolumes or mounts | superseded by [0348](0348-the-file-manager-refuses-bind-mounts-before-a-move-and-busy-entries-with-409.md) | 2026-11-06 |
| [0232](0232-file-manager-renames-in-the-row-and-never-replaces.md) | The file manager renames one selected item in its row and never replaces an entry | accepted | 2026-11-07 |
| [0259](0259-deleted-items-go-to-the-trash-of-their-pool-or-volume-for-30-days.md) | Deleted items go to the trash of their pool or volume for 30 days, unless they are restored or deleted for good | accepted | 2026-11-07 |
| [0273](0273-file-manager-entries-in-share-folders-get-2770-and-0660.md) | New folders and uploads of the file manager in a share folder get 2770 and 0660 | superseded by [0274](0274-apps-of-linuxserver-images-can-run-in-unos-smb-and-only-they-get-share-folders.md) | 2026-11-08 |
| [0347](0347-an-upload-that-gets-no-data-for-120-seconds-is-cut-off-with-408.md) | An upload that gets no data for 120 seconds is cut off with 408 | accepted | 2026-11-11 |
| [0348](0348-the-file-manager-refuses-bind-mounts-before-a-move-and-busy-entries-with-409.md) | The file manager never deletes, renames or moves volumes, subvolumes or mounts, bind mounts included, and a busy entry is a conflict | accepted | 2026-11-11 |
| [0354](0354-leftovers-of-uploads-are-found-after-each-start-and-removed-only-when-an-administrator-asks.md) | Leftovers of uploads are found after each start and removed only when an administrator asks | accepted | 2026-11-12 |
| [0396](0396-copies-and-moves-from-another-volume-into-a-share-folder-get-the-modes-of-repair-permissions.md) | Copies, and moves from another pool or volume, into a share folder get the modes of Repair permissions | accepted | 2026-11-17 |

## System

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0043](0043-background-loops-in-the-lifespan.md) | Background loops start and stop in the FastAPI lifespan | accepted | 2026-11-05 |
| [0044](0044-custom-tasks-administrator-only.md) | Custom shell tasks stay, but are administrator-only and validated | accepted | 2026-11-05 |
| [0045](0045-smtp-verifies-certificates.md) | E-mail notifications verify the mail server's certificate by default | accepted | 2026-11-05 |
| [0046](0046-telegram-alerts-over-ipv6-or-e-mail.md) | Deliver Telegram alerts over Docker IPv6, or send them by e-mail | superseded by [0336](0336-http-notifications-that-cannot-connect-from-the-container-go-once-more-from-the-hosts-network.md) | 2026-11-05 |
| [0047](0047-event-bus-for-alerts.md) | Send alerts to every channel through an event bus, with a health banner in the UI | superseded by [0332](0332-problems-are-kept-in-ram-alerted-once-to-every-channel-and-shown-in-a-banner.md) | 2026-11-05 |
| [0171](0171-supervisor-volatile-files-in-ram.md) | The supervisor keeps volatile files in RAM | accepted | 2026-11-06 |
| [0172](0172-unique-task-ids-and-bounded-smart-tests.md) | Scheduled tasks get unique ids, backups run concurrently, and SMART tests time out | accepted | 2026-11-06 |
| [0173](0173-in-app-updater-in-its-own-slice.md) | Redesign the in-app updater in its own slice; until then update.sh is the way | proposed | 2026-11-06 |
| [0233](0233-power-actions-run-on-the-host-and-are-off-where-host-commands-run-locally.md) | Power actions run on the host through host.py and are off where host commands run locally | accepted | 2026-11-07 |
| [0234](0234-the-image-carries-its-version-and-the-updates-tab-shows-only-what-works.md) | The supervisor image carries its version, and System > Updates shows only what works | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-07 |
| [0244](0244-a-disabled-interface-comes-back-on-unless-the-change-is-kept.md) | A disabled network interface comes back on by itself after 60 seconds unless the change is kept | accepted | 2026-11-07 |
| [0245](0245-system-network-shows-ipv6-link-speeds-and-the-administrative-state.md) | System > Network shows IPv6, link speeds from sysfs and each interface's administrative state | accepted | 2026-11-07 |
| [0246](0246-interfaces-with-vlans-stay-enabled-and-the-revert-follows-the-host.md) | An interface that carries VLANs is not disabled, and a disable's saved state and the page's reports follow what the host shows | accepted | 2026-11-07 |
| [0247](0247-wake-on-lan-from-power-off-depends-on-the-board.md) | Waking a host that is off by Wake-on-LAN depends on its board, and the Power tab says so | accepted | 2026-11-07 |
| [0257](0257-system-logs-reads-every-source-from-the-hosts-journal-newest-first.md) | System > Logs reads every source from the host's journal, newest first, with one set of filters | superseded by [0366](0366-system-logs-follows-new-journal-entries-over-the-journal-socket.md) | 2026-11-07 |
| [0258](0258-a-journal-read-ends-on-the-host-and-two-run-at-a-time.md) | A journal read of System > Logs ends on the host after 30 seconds, two run at a time, and a search without a match lists nothing | accepted | 2026-11-07 |
| [0260](0260-notification-channels-of-every-kind-name-failed-deliveries-and-keep-a-full-history.md) | Notification channels of every kind are set up in the web UI, a delivery counts only on success, and the history lists every notification sent | superseded by [0326](0326-notification-channels-their-history-and-damaged-files-of-them.md) | 2026-11-07 |
| [0264](0264-schedules-follow-the-clock-of-the-hosts-time-zone.md) | Schedules follow the clock of the host's time zone, read from its /etc/localtime link, with fixed rules for the changes of daylight saving time | superseded by [0266](0266-schedules-follow-the-hosts-time-zone-as-last-read.md) | 2026-11-07 |
| [0265](0265-custom-task-runs-record-output-and-exit-status-and-end-within-a-day.md) | A custom task's run records the command's output and exit status, and the command ends within 24 hours with what it started | superseded by [0267](0267-custom-task-commands-end-with-every-process-of-their-session.md) | 2026-11-07 |
| [0266](0266-schedules-follow-the-hosts-time-zone-as-last-read.md) | Schedules follow the host's time zone as last read from its /etc/localtime link, with fixed rules for the changes of daylight saving time | superseded by [0432](0432-the-web-uis-shared-components-carry-its-states-confirmations-menus-form-controls-and-tables-and-a-check-keeps-pages-on-them.md) | 2026-11-08 |
| [0267](0267-custom-task-commands-end-with-every-process-of-their-session.md) | A custom task's run records the command's output and exit status, and the command ends within 24 hours with every process of its session | accepted | 2026-11-08 |
| [0268](0268-a-run-that-a-stop-of-the-supervisor-interrupts-is-recorded-as-failed.md) | A task run that a stop of the supervisor interrupts is recorded as failed | accepted | 2026-11-08 |
| [0326](0326-notification-channels-their-history-and-damaged-files-of-them.md) | Notification channels of every kind are set up in the web UI, a delivery counts only on success, the history lists every notification sent, and damaged files of them are kept | accepted | 2026-11-10 |
| [0331](0331-the-time-zone-routes-run-timedatectl-on-the-host.md) | The time zone and hostname routes ask the host, and PUT /network/hostname alone sets the name | accepted | 2026-11-10 |
| [0332](0332-problems-are-kept-in-ram-alerted-once-to-every-channel-and-shown-in-a-banner.md) | The problems of the NAS are kept in RAM by source, alerted once to every enabled channel, and shown to administrators in a banner | accepted | 2026-11-10 |
| [0336](0336-http-notifications-that-cannot-connect-from-the-container-go-once-more-from-the-hosts-network.md) | An HTTP notification that cannot connect from the container goes once more from the host's network, with the host's curl | accepted | 2026-11-11 |
| [0346](0346-the-dashboard-shows-the-cpu-or-soc-temperature-chosen-by-sensor-name-in-one-place.md) | The dashboard shows the CPU's or SoC's temperature, chosen by the sensor's name in one place on the server | accepted | 2026-11-11 |
| [0356](0356-the-nut-password-is-write-only-and-upsc-runs-on-the-host.md) | The NUT password of the UPS settings is write-only, and upsc runs on the host, where its absence makes the UPS unavailable | accepted | 2026-11-12 |
| [0366](0366-system-logs-follows-new-journal-entries-over-the-journal-socket.md) | System > Logs reads every source from the host's journal, newest first, and its Auto follows new entries over the journal socket | accepted | 2026-11-12 |
| [0374](0374-unos-keeps-its-idle-load-within-a-budget-and-check-sh-reports-load-and-heat.md) | UNOS keeps its idle load within a budget, and check.sh reports load and heat | superseded by [0421](0421-check-sh-measures-heat-only-in-zones-that-slow-a-processor-and-unos-keeps-its-idle-budget.md) | 2026-11-13 |
| [0375](0375-the-supervisor-s-healthcheck-runs-bash-and-the-process-list-reads-ten-processes-in-full.md) | The supervisor's healthcheck runs bash, and the process list reads ten processes in full | accepted | 2026-11-13 |
| [0388](0388-the-dashboard-shows-disk-io-and-the-stats-network-is-the-hosts-interfaces.md) | The dashboard shows the disks' I/O, and the network of the stats message is the traffic of the host's interfaces | accepted | 2026-11-17 |
| [0399](0399-the-resource-monitor-keeps-an-hour-of-history-in-ram-and-only-administrators-get-the-hosts-processes.md) | The Resource Monitor keeps an hour of history in RAM, read once when its tab opens, and only administrators get the host's processes | accepted | 2026-11-18 |
| [0407](0407-the-power-settings-shut-the-host-down-at-a-set-time-or-when-idle-and-arm-the-rtc-to-wake-it.md) | The power settings shut the host down at a set time or when idle, and arm the RTC to wake it | accepted | 2026-11-18 |
| [0412](0412-the-load-check-fails-for-a-unos-container-it-could-not-measure-and-the-two-tab-measurement-is-the-owner-s.md) | The load check fails for a UNOS container it could not measure, and the two-tab measurement is the owner's | accepted | 2026-11-18 |
| [0420](0420-remote-access-is-a-wireguard-tunnel-to-the-nas-alone-and-apps-get-local-names-through-caddy.md) | Remote access is a WireGuard tunnel on the host that reaches the NAS alone, and apps get single-label local names through Caddy | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-19 |
| [0421](0421-check-sh-measures-heat-only-in-zones-that-slow-a-processor-and-unos-keeps-its-idle-budget.md) | The device check measures heat only in zones that slow a processor, and UNOS keeps its idle budget | accepted | 2026-11-19 |
| [0424](0424-bonds-vlans-and-bridges-are-systemd-networkd-files-and-a-change-that-may-cut-the-connection-waits-to-be-kept.md) | Bonds, VLANs and bridges are systemd-networkd files that UNOS writes, and a change that may cut the connection waits to be kept | accepted | 2026-11-19 |

## Web UI

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0174](0174-users-manage-their-smb-on-the-account-page.md) | Regular users manage their own SMB access on the Account page | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-06 |
| [0175](0175-deferred-ui-scheduler-and-tooling-items.md) | Fix the deferred UI, scheduler, container and tooling items | proposed | 2026-11-06 |
| [0196](0196-windows-hint-uses-ipv6-literal-net.md) | The Windows connection hint uses the ipv6-literal.net form for IPv6 hosts | accepted | 2026-11-07 |
| [0197](0197-accessible-dialogs-lists-and-hints.md) | Dialogs, lists and hints in the web UI work with keyboard and screen readers | superseded by [0361](0361-open-dialogs-form-a-stack-whose-top-one-alone-answers-escape.md) | 2026-11-07 |
| [0271](0271-the-storage-page-says-in-each-card-why-its-list-could-not-be-loaded.md) | The Storage page loads the disks and the pools apart and says in each card why its list could not be loaded | accepted | 2026-11-07 |
| [0287](0287-the-web-ui-takes-its-texts-from-catalogs-in-english-and-russian.md) | The web UI takes its texts from catalogs in English and Russian, chosen on the Account page and checked by make frontend | accepted | 2026-11-08 |
| [0291](0291-the-app-store-translates-its-own-texts-and-not-the-app-catalog.md) | The App Store gives each category one label and shows the app catalog's texts as the server answers them | proposed | 2026-11-08 |
| [0295](0295-the-network-tab-keeps-the-values-that-ip-and-ss-print-in-its-tables-as-they-come.md) | The Network tab keeps the values that ip and ss print in its tables as they come in every language | accepted | 2026-11-08 |
| [0296](0296-a-sentence-around-a-name-a-path-or-a-link-is-translated-in-parts-and-names-on-the-disk-stay.md) | A sentence around a name, a path or a link is translated in parts, and names on the disk stay as they are | accepted | 2026-11-08 |
| [0297](0297-the-dashboard-and-files-take-the-units-after-a-number-from-their-catalogs.md) | The dashboard and Files take the units they write after a number from their catalogs, and numbers keep their decimal point | accepted | 2026-11-08 |
| [0298](0298-a-russian-audit-log-names-actions-in-russian-and-its-search-finds-english-words.md) | A Russian Audit Log page names actions and resources in Russian, and its search still finds them by their English words | proposed | 2026-11-08 |
| [0299](0299-the-shares-page-writes-a-locations-kind-in-its-language-from-the-name-the-server-answers.md) | The Shares page writes a location's kind in its language from the name that the server answers | accepted | 2026-11-08 |
| [0300](0300-the-storage-page-writes-its-values-in-the-pages-language-and-keeps-the-servers-reasons.md) | The Storage page writes its sizes and the start of a scrub in the page's language and keeps the server's reasons and btrfs's names | superseded by [0401](0401-every-time-of-the-api-carries-its-offset-and-stored-times-without-one-are-utc.md) | 2026-11-08 |
| [0302](0302-below-the-md-breakpoint-the-sidebar-is-a-drawer-and-only-a-table-scrolls-sideways.md) | Below the md breakpoint the sidebar is a drawer, and on a phone only a table scrolls sideways | superseded by [0428](0428-the-web-ui-is-redesigned-on-a-graphite-base-with-an-indigo-brand-grouped-navigation-and-one-shape-per-pattern.md) | 2026-11-08 |
| [0361](0361-open-dialogs-form-a-stack-whose-top-one-alone-answers-escape.md) | Open dialogs form a stack whose top one alone answers Escape, and the focus moves into a dialog and back to its opener | accepted | 2026-11-12 |
| [0365](0365-a-check-of-webui-src-fails-for-a-setinterval-that-reaches-the-api.md) | A check of webui/src fails for a setInterval whose callback reaches the API | accepted | 2026-11-12 |
| [0389](0389-several-containers-act-one-after-another-through-their-own-endpoints-and-several-shares-are-deleted-with-one-apply.md) | Several containers act one after another through their own endpoints, and several shares are deleted with one request and one apply | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-17 |
| [0393](0393-ctrl-k-opens-a-command-palette-that-runs-only-actions-that-change-nothing-on-the-nas.md) | Ctrl+K opens a command palette of the pages and actions of the user's role, which runs only actions that change nothing on the NAS | superseded by [0428](0428-the-web-ui-is-redesigned-on-a-graphite-base-with-an-indigo-brand-grouped-navigation-and-one-shape-per-pattern.md) | 2026-11-17 |
| [0395](0395-each-user-arranges-hides-and-shows-the-dashboards-widgets-and-the-server-keeps-the-layout.md) | Each user arranges, hides and shows the dashboard's widgets, and the server keeps the layout | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-17 |
| [0400](0400-a-check-fails-for-raw-controls-and-raw-colours-in-the-web-uis-pages.md) | A check fails for raw controls and raw colours in the web UI's pages | superseded by [0428](0428-the-web-ui-is-redesigned-on-a-graphite-base-with-an-indigo-brand-grouped-navigation-and-one-shape-per-pattern.md) | 2026-11-18 |
| [0405](0405-each-user-chooses-a-dark-light-or-system-theme-and-the-colour-tokens-have-a-value-per-theme.md) | Each user chooses a dark, light or system theme, and the colour tokens have a value per theme | superseded by [0428](0428-the-web-ui-is-redesigned-on-a-graphite-base-with-an-indigo-brand-grouped-navigation-and-one-shape-per-pattern.md) | 2026-11-18 |
| [0427](0427-the-web-ui-runs-on-svelte-5-in-runes-mode-only-and-its-pages-are-composed-of-area-components.md) | The web UI runs on Svelte 5 in runes mode only, and its pages are composed of area components | accepted | 2026-11-19 |
| [0428](0428-the-web-ui-is-redesigned-on-a-graphite-base-with-an-indigo-brand-grouped-navigation-and-one-shape-per-pattern.md) | The web UI is redesigned on a graphite base with an indigo brand, grouped navigation and one shape per pattern | superseded by [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | 2026-11-19 |
| [0430](0430-the-web-ui-loads-behind-a-boot-screen-draws-its-shell-once-and-shows-skeletons-in-each-pages-final-layout.md) | The web UI loads behind a boot screen, draws its shell once and shows skeletons in each page's final layout | superseded by [0438](0438-the-design-check-keeps-no-baseline-and-fails-for-success-alerts-spinners-and-raw-details.md) | 2026-11-20 |
| [0431](0431-every-accounts-own-settings-are-on-the-account-page-system-is-the-nass-and-each-setting-action-and-fact-has-one-place.md) | Every account's own settings are on the Account page, System is the NAS's, and each setting, action and fact has one place | superseded by [0437](0437-containers-and-the-app-store-get-one-filter-quiet-actions-and-plain-states-and-an-apps-container-is-removed-only-with-its-app.md) | 2026-11-20 |
| [0432](0432-the-web-uis-shared-components-carry-its-states-confirmations-menus-form-controls-and-tables-and-a-check-keeps-pages-on-them.md) | The web UI's shared components carry its states, confirmations, menus, form controls and tables, and a check keeps pages on them | superseded by [0438](0438-the-design-check-keeps-no-baseline-and-fails-for-success-alerts-spinners-and-raw-details.md) | 2026-11-20 |
| [0434](0434-the-dashboard-counts-every-issue-it-knows-and-the-data-pages-keep-one-primary-action-each-with-the-rarer-ones-behind-more.md) | The dashboard counts every issue it knows, and the data pages keep one primary action each, with the rarer ones behind More | accepted | 2026-11-20 |
| [0435](0435-share-access-is-a-table-of-who-and-level-and-the-sharing-and-people-pages-say-each-state-once.md) | Share access is a table of who and level, and the sharing and people pages say each state once | accepted | 2026-11-20 |
| [0436](0436-the-system-tabs-put-status-before-settings-and-keep-one-primary-action-one-time-zone-and-one-pattern-per-job.md) | The System tabs put status before settings, and keep one primary action, one time zone and one pattern per job | accepted | 2026-11-20 |
| [0437](0437-containers-and-the-app-store-get-one-filter-quiet-actions-and-plain-states-and-an-apps-container-is-removed-only-with-its-app.md) | Containers and the App Store get one filter, quiet actions and plain states, and an app's container is removed only with its app | accepted | 2026-11-20 |
| [0438](0438-the-design-check-keeps-no-baseline-and-fails-for-success-alerts-spinners-and-raw-details.md) | The design check keeps no baseline and fails for a success alert, a hand-made spinner and a raw details element, and no line shows the live connection | accepted | 2026-11-21 |

## Deploy

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0048](0048-fixed-compose-subnet-and-trusted-proxy.md) | A fixed compose subnet lets uvicorn trust X-Forwarded-For only from the web UI | accepted | 2026-11-05 |
| [0049](0049-update-sh-with-backup-health-check-and-rollback.md) | Updates go through update.sh, with a backup, a health check and automatic rollback | accepted | 2026-11-05 |
| [0050](0050-releases-as-tags-and-multi-arch-images.md) | Release with git tags and multi-arch images on GHCR | proposed | 2026-11-05 |
| [0176](0176-docker-health-checks-and-ordered-start.md) | Both containers have health checks, and the web UI waits for a healthy supervisor | superseded by [0338](0338-health-checks-ordered-start-and-an-access-log-of-refused-and-failed-requests.md) | 2026-11-06 |
| [0177](0177-tmpfs-cap-for-small-boards.md) | Size the /tmp tmpfs for boards with 1 GB of RAM | superseded by [0406](0406-the-supervisors-tmpfs-is-192-mib-and-a-backup-may-hold-a-database-of-48-mib.md) | 2026-11-06 |
| [0178](0178-docker-log-rotation.md) | Rotate Docker's json-file logs when they grow | superseded by [0330](0330-docker-rotates-the-logs-of-every-container-unos-creates.md) | 2026-11-06 |
| [0179](0179-uninstall-removes-smb-accounts.md) | Uninstall removes the SMB host accounts | superseded by [0180](0180-uninstall-keeps-smb-host-accounts.md) | 2026-11-06 |
| [0180](0180-uninstall-keeps-smb-host-accounts.md) | Uninstall keeps the SMB host accounts and removes only their exact passdb entries | superseded by [0322](0322-uninstall-restarts-smbd-once-and-restores-smb-conf-through-a-temporary-file.md) | 2026-11-06 |
| [0181](0181-downgrade-guidance-for-the-smb-release.md) | Rolling back to the release before SMB accounts is documented, not engineered | superseded by [0207](0207-no-backward-compatibility.md) | 2026-11-06 |
| [0305](0305-base-images-pinned-by-tag-and-digest.md) | The Dockerfiles pin each base image by tag and digest, and Dependabot moves the digest | accepted | 2026-11-08 |
| [0307](0307-the-web-ui-waits-for-the-api-also-when-docker-starts-it-at-boot.md) | The web UI waits for the supervisor's API also when Docker starts it at boot | accepted | 2026-11-09 |
| [0316](0316-the-install-script-recognizes-the-smb-conf-of-unos-by-its-header-alone.md) | The install script recognizes the smb.conf of UNOS by its header alone | accepted | 2026-11-10 |
| [0322](0322-uninstall-restarts-smbd-once-and-restores-smb-conf-through-a-temporary-file.md) | Uninstall keeps the SMB host accounts, removes only their exact passdb entries, restarts smbd once and restores smb.conf through a temporary file | accepted | 2026-11-10 |
| [0330](0330-docker-rotates-the-logs-of-every-container-unos-creates.md) | Docker rotates the logs of every container that UNOS creates at 10 MB, keeping 3 files | accepted | 2026-11-10 |
| [0338](0338-health-checks-ordered-start-and-an-access-log-of-refused-and-failed-requests.md) | Both containers have health checks, the web UI waits for a healthy supervisor, and the access log keeps only refused and failed requests | accepted | 2026-11-11 |
| [0369](0369-unos-runs-on-the-banana-pi-bpi-f3-with-images-built-on-the-host-and-no-nvme-power-saving-or-trim-on-the-k1.md) | UNOS runs on the Banana Pi BPI-F3 with both images built on the host, and its SpacemiT K1 keeps NVMe drives without power saving or TRIM | superseded by [0370](0370-on-the-spacemit-k1-only-nvme-drives-go-without-power-saving-and-trim-and-sata-is-the-recommended-storage.md) | 2026-11-13 |
| [0370](0370-on-the-spacemit-k1-only-nvme-drives-go-without-power-saving-and-trim-and-sata-is-the-recommended-storage.md) | UNOS runs on the Banana Pi BPI-F3 with both images built on the host, and on its SpacemiT K1 only NVMe drives go without power saving and TRIM | superseded by [0429](0429-a-pool-on-an-nvme-drive-of-a-spacemit-k1-gets-trim-like-every-other-pool.md) | 2026-11-13 |
| [0371](0371-on-riscv64-the-web-ui-builder-runs-vite-without-the-maglev-compiler.md) | On riscv64 the web UI's builder runs vite without Node.js's Maglev compiler | superseded by [0373](0373-the-web-ui-image-of-a-spacemit-k1-is-built-on-another-machine.md) | 2026-11-13 |
| [0373](0373-the-web-ui-image-of-a-spacemit-k1-is-built-on-another-machine.md) | The web UI's image of a SpacemiT K1 is built on another machine, and install.sh and update.sh build only the supervisor there | proposed | 2026-11-13 |
| [0376](0376-the-banana-pi-bpi-w3-gets-no-code-of-its-own-for-the-rk3588.md) | The Banana Pi BPI-W3 gets no code of its own for the RK3588 | proposed | 2026-11-15 |
| [0406](0406-the-supervisors-tmpfs-is-192-mib-and-a-backup-may-hold-a-database-of-48-mib.md) | The supervisor's /tmp tmpfs is 192 MiB and a backup may hold a database of 48 MiB | accepted | 2026-11-18 |
| [0413](0413-install-sh-installs-only-the-packages-unos-runs-so-no-mdadm-assembles-an-old-nas.md) | The package list of install.sh holds only what UNOS runs, so no mdadm assembles the arrays of an old NAS | accepted | 2026-11-18 |

## Device

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0051](0051-scripted-measured-reversible-device-deploys.md) | Deploys to the device are scripted, measured and reversible | superseded by [0223](0223-device-deploys-keep-backups-and-rollback-copies-on-the-workstation.md) | 2026-11-05 |
| [0052](0052-device-image-checks-with-throwaway-containers.md) | Device image checks run throwaway containers | superseded by [0183](0183-device-checks-start-no-container.md) | 2026-11-05 |
| [0053](0053-device-uses-a-read-only-deploy-key.md) | The device has no GitHub token, only a read-only deploy key | accepted | 2026-11-05 |
| [0054](0054-device-keeps-the-test-user.md) | The device keeps the regular user 'test' for role checks | accepted | 2026-11-05 |
| [0055](0055-data-share-guest-off-with-a-hand-made-samba-user.md) | The DATA share on the device turns guest access off and relies on a hand-made Samba user | superseded by [0220](0220-dev-device-keeps-no-legacy-state.md) | 2026-11-05 |
| [0182](0182-device-is-the-owners-production-nas.md) | The device is treated as the owner's production NAS | superseded by [0220](0220-dev-device-keeps-no-legacy-state.md) | 2026-11-06 |
| [0183](0183-device-checks-start-no-container.md) | Device checks start no container | accepted | 2026-11-06 |
| [0184](0184-web-ui-static-files-built-off-the-device.md) | The web UI's static files are built on the workstation, not on the device | accepted | 2026-11-06 |
| [0185](0185-minimal-measured-device-verification.md) | Slice 2 is verified on the device with few, measured, reversible steps | accepted | 2026-11-06 |
| [0198](0198-device-api-checks-run-on-the-device.md) | API checks of the device run on the device itself, over https://127.0.0.1 | accepted | 2026-11-07 |
| [0199](0199-device-upgrade-order-for-slice-2.md) | The device is upgraded to slice 2 in a fixed order | superseded by [0220](0220-dev-device-keeps-no-legacy-state.md) | 2026-11-07 |
| [0214](0214-system-disk-write-budget.md) | Every device operation keeps system-disk writes minimal and reports them | accepted | 2026-11-07 |
| [0220](0220-dev-device-keeps-no-legacy-state.md) | The dev device keeps no legacy state | accepted | 2026-11-07 |
| [0221](0221-read-only-device-commands-in-a-read-only-mount-namespace.md) | Read-only device commands run in a read-only mount namespace | accepted | 2026-11-07 |
| [0222](0222-device-api-relay-caches-the-admin-token-in-ram.md) | The device API relay caches the admin token in the device's RAM | accepted | 2026-11-07 |
| [0223](0223-device-deploys-keep-backups-and-rollback-copies-on-the-workstation.md) | Device deploys keep backups and rollback copies on the workstation | accepted | 2026-11-07 |
| [0225](0225-the-device-runs-from-its-sd-card-and-never-writes-the-emmc.md) | The dev device runs from its SD card and never writes its eMMC | accepted | 2026-11-07 |
| [0226](0226-dev-device-ssh-by-key-only-with-rotated-passwords.md) | The dev device takes SSH by key only, and the passwords once published are rotated | accepted | 2026-11-07 |
| [0227](0227-no-ipv6-inbound-filter-on-the-dev-device-for-now.md) | No inbound IPv6 filter on the dev device for now; each service protects itself | superseded by [0301](0301-the-dev-device-admits-inbound-ipv6-to-its-services-only-from-the-lan.md) | 2026-11-07 |
| [0228](0228-docker-data-root-stays-on-the-sd-card.md) | Docker's data root stays on the dev device's SD card | accepted | 2026-11-07 |
| [0301](0301-the-dev-device-admits-inbound-ipv6-to-its-services-only-from-the-lan.md) | The dev device admits inbound IPv6 to its services only from the LAN | accepted | 2026-11-08 |
| [0303](0303-the-dev-device-runs-its-nfs-server.md) | The dev device runs its NFS server | accepted | 2026-11-08 |
| [0304](0304-the-scenario-checks-of-the-dev-device-are-scripts-that-remove-what-they-make.md) | The scenario checks of the dev device are scripts that remove what they make and report each step's SD writes | accepted | 2026-11-08 |
| [0309](0309-the-smbconf-scenario-check-edits-smb-conf-by-hand-and-restarts-the-supervisor.md) | The smbconf scenario check edits smb.conf by hand and restarts the supervisor | superseded by [0362](0362-every-replaced-smb-conf-is-kept-in-a-dated-copy-and-the-newest-10-stay.md) | 2026-11-09 |
| [0314](0314-the-scrub-scenario-check-scrubs-the-pool-cancels-a-scrub-and-runs-a-scheduled-one.md) | The scrub scenario check scrubs the pool, cancels a scrub and runs a scheduled one | accepted | 2026-11-09 |
| [0340](0340-the-shares-scenario-check-makes-a-guest-share-a-users-own-smb-activation-and-a-repair.md) | The shares scenario check makes a guest share, a user's own SMB activation and a repair | accepted | 2026-11-11 |
| [0341](0341-the-restore-scenario-check-restores-a-backup-it-made-moments-before.md) | The restore scenario check restores a backup it made moments before | accepted | 2026-11-11 |
| [0372](0372-the-bpi-f3-keeps-its-pool-docker-data-root-and-home-assistant-and-frigate-data-on-its-two-ssds.md) | The BPI-F3 keeps its pool, Docker's data root and the data of its Home Assistant and Frigate stack on its two SSDs | accepted | 2026-11-13 |
| [0394](0394-the-smart-check-skips-the-disk-s-sleep-while-apps-containers-use-the-pool.md) | The smart check skips the disk's sleep while the containers of apps use it, and names them | accepted | 2026-11-17 |

## Testing

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0186](0186-every-fix-comes-with-a-failing-test.md) | Every fix comes with a test that fails on the code before it | accepted | 2026-11-06 |
| [0187](0187-linux-only-tests-in-the-device-ram.md) | Linux-only tests run on the device entirely in RAM | accepted | 2026-11-06 |
| [0188](0188-test-environments-with-known-failure-baselines.md) | Tests run in fixed environments against lists of known failures | superseded by [0215](0215-e2e-only-against-a-local-stand-in.md) | 2026-11-06 |
| [0189](0189-smb-code-tested-against-a-fake-host.md) | SMB code is tested against a fake host layer, real testparm and the local stand-in | accepted | 2026-11-06 |
| [0215](0215-e2e-only-against-a-local-stand-in.md) | E2E tests run only against local targets, never against the device | accepted | 2026-11-07 |
| [0261](0261-the-stand-ins-test-administrator-is-created-through-the-setup-page.md) | The stand-in's test administrator is created through the setup page, by a first Playwright run | accepted | 2026-11-07 |
| [0317](0317-the-e2e-specs-skip-only-on-listed-conditions-of-the-local-target-and-expect-no-failure.md) | The E2E specs skip only on listed conditions of the local target, and expect no failure | accepted | 2026-11-10 |
| [0383](0383-backend-tests-run-in-parallel-with-paths-of-their-own-and-a-green-tree-skips-the-pre-commit-check.md) | Backend tests run in parallel with paths of their own, and a green tree skips the pre-commit check | accepted | 2026-11-17 |
| [0433](0433-a-feature-runs-the-e2e-specs-of-its-area-and-the-full-suite-runs-once-per-batch.md) | A feature runs the E2E specs of its area, and the full suite runs once per batch | accepted | 2026-11-20 |
| [0439](0439-each-worktree-runs-a-compose-test-stack-of-its-own-with-a-project-ports-and-a-subnet-derived-from-its-path.md) | Each worktree runs a compose test stack of its own, with a project, ports and a subnet derived from its path | accepted | 2026-11-21 |

## CI

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0056](0056-ship-with-existing-svelte-check-errors.md) | Changes may ship with the existing svelte-check errors | superseded by [0190](0190-ci-quality-gates.md) | 2026-11-05 |
| [0190](0190-ci-quality-gates.md) | CI fails on any test, lint, audit, build or check error, with a read-only token | superseded by [0423](0423-actions-are-pinned-by-commit-the-audits-tools-by-hash-and-pip-takes-wheels.md) | 2026-11-06 |
| [0216](0216-make-check-is-the-single-quality-gate.md) | One quality gate: make check runs what CI runs | superseded by [0423](0423-actions-are-pinned-by-commit-the-audits-tools-by-hash-and-pip-takes-wheels.md) | 2026-11-07 |
| [0217](0217-make-setup-bootstraps-a-checkout.md) | One command, make setup, bootstraps a checkout idempotently | accepted | 2026-11-07 |
| [0218](0218-scheduled-dependency-audits-and-maintenance.md) | Dependencies are audited weekly, and maintenance passes run monthly | superseded by [0423](0423-actions-are-pinned-by-commit-the-audits-tools-by-hash-and-pip-takes-wheels.md) | 2026-11-07 |
| [0229](0229-main-stays-without-branch-protection-on-the-free-plan.md) | The main branch stays without branch protection on GitHub's Free plan | accepted | 2026-11-07 |
| [0230](0230-the-ci-workflow-is-off-on-github.md) | The CI workflow is off on GitHub, and make check on the workstation is the only gate | accepted | 2026-11-07 |
| [0231](0231-dependabot-proposes-no-major-npm-versions-and-leaves-playwright-alone.md) | Dependabot proposes no major npm versions and leaves Playwright alone | superseded by [0427](0427-the-web-ui-runs-on-svelte-5-in-runes-mode-only-and-its-pages-are-composed-of-area-components.md) | 2026-11-07 |
| [0423](0423-actions-are-pinned-by-commit-the-audits-tools-by-hash-and-pip-takes-wheels.md) | Actions are pinned by commit, the audit's tools by hash, and pip takes wheels only | accepted | 2026-11-19 |
| [0425](0425-ruff-checks-the-rules-that-find-bugs-set-in-ruff-toml-and-a-finding-kept-in-the-code-says-why.md) | Ruff checks the rules that find bugs, set in ruff.toml, and a finding kept in the code says why | accepted | 2026-11-19 |
