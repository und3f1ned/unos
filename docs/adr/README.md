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

## Architecture

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0002](0002-two-containers-on-an-existing-linux-host.md) | UNOS runs on an existing Linux host as a web UI and a privileged supervisor container | accepted | 2026-01-23 |
| [0006](0006-real-time-data-over-websocket.md) | Real-time data goes over WebSocket, never HTTP polling | accepted | 2026-01-25 |
| [0020](0020-host-changes-through-host-py.md) | Host changes go through src/host.py, which runs argv lists in the host's namespaces | accepted | 2026-11-05 |
| [0021](0021-blocking-work-off-the-event-loop.md) | Blocking work runs off the event loop, with timeouts | accepted | 2026-11-05 |
| [0022](0022-database-migrations-with-alembic.md) | Adopt Alembic for database migrations | proposed | 2026-11-05 |
| [0023](0023-audit-quality-debt.md) | Pay down the audit's P2 quality and technical debt | proposed | 2026-11-05 |

## Security

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0005](0005-device-access-with-sshpass-on-the-command-line.md) | Device access uses sshpass with the password on the command line | superseded by [0212](0212-device-secrets-in-dev-env.md) | 2026-01-24 |
| [0024](0024-credentials-stay-with-the-owner.md) | Credentials and the device's security settings are changed only by the owner | accepted | 2026-11-05 |
| [0025](0025-https-from-a-local-ca-without-hsts.md) | The web UI is served over HTTPS from Caddy's local CA, with a CSP and without HSTS | accepted | 2026-11-05 |
| [0026](0026-pyjwt-and-bcrypt-with-pinned-dependencies.md) | PyJWT and bcrypt replace python-jose and passlib, and dependencies are pinned | accepted | 2026-11-05 |
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
| [0237](0237-audit-log-filtered-by-whole-units-exported-without-formulas.md) | The audit log is filtered alike by whole units of time, exported without spreadsheet formulas and shown on an administrator page | accepted | 2026-11-07 |
| [0286](0286-the-audit-logs-lock-holds-across-processes.md) | The audit log's lock holds across processes, through a file lock that the emergency command line takes too | accepted | 2026-11-08 |

## Auth

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0009](0009-bearer-tokens-and-a-same-origin-api.md) | The API takes Bearer tokens, not cookies, and refuses cross-origin requests by default | accepted | 2026-02-05 |
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
| [0238](0238-recovery-codes-sign-in-once-in-place-of-a-two-factor-code.md) | Ten recovery codes each sign in once in place of a two-factor code, and end with two-factor authentication | accepted | 2026-11-07 |
| [0239](0239-kept-authentication-practices-with-a-second-factor-before-any-token.md) | Auth keeps bcrypt, one JWT algorithm, a second factor before any token, and hashed API tokens | accepted | 2026-11-07 |
| [0251](0251-a-sign-in-answers-while-data-is-full-and-its-lost-records-go-to-the-log.md) | A sign-in answers while /data is full, and the records it cannot write go to the supervisor's log | accepted | 2026-11-07 |
| [0252](0252-an-api-tokens-last-use-is-committed-at-most-once-an-hour.md) | An API token's last use is committed at most once an hour | accepted | 2026-11-07 |
| [0253](0253-only-a-session-token-manages-api-tokens-and-their-number-and-names-are-bounded.md) | Only a session token manages API tokens, and their number, names and permissions are bounded | accepted | 2026-11-07 |
| [0262](0262-login-history-times-in-utc-with-the-offset-newest-first.md) | The login history answers its times in UTC with the offset, newest first also within a second | accepted | 2026-11-07 |
| [0263](0263-a-users-login-history-starts-with-their-account.md) | A regular user's login history starts with their account | accepted | 2026-11-08 |
| [0284](0284-root-on-the-host-resets-an-administrators-password-and-two-factor-authentication.md) | Only root on the host resets an administrator's password and two-factor authentication, with the emergency command line | accepted | 2026-11-08 |

## Storage

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0011](0011-btrfs-raid1c3-and-raid1c4-instead-of-raid5-6.md) | Pools use btrfs RAID1C3/RAID1C4 instead of RAID5/6, with read-only snapshots | accepted | 2026-02-08 |
| [0034](0034-pools-mounted-by-uuid-from-managed-fstab.md) | Pools are mounted by UUID from managed fstab entries, and empty mountpoints are immutable | accepted | 2026-11-05 |
| [0035](0035-irreversible-storage-operations-need-confirmation.md) | Irreversible storage operations need a typed confirmation and device identity checks | accepted | 2026-11-05 |
| [0036](0036-pool-health-from-the-host.md) | Pool health is computed from the host and never assumed | accepted | 2026-11-05 |
| [0069](0069-volumes-are-top-level-subvolumes.md) | Volumes are top-level subvolumes, addressed by subvolume id | accepted | 2026-11-06 |
| [0070](0070-pools-respect-docker-volumes-and-odd-names.md) | Pool unmount and delete respect Docker volumes, and odd names do not break storage | accepted | 2026-11-06 |
| [0071](0071-scrubs-return-once-started.md) | Scrubs return once started, and scheduled scrubs need a mounted pool | accepted | 2026-11-06 |
| [0072](0072-smart-only-on-whole-host-disks.md) | SMART runs only on whole host disks, the set the Storage page lists | accepted | 2026-11-06 |
| [0073](0073-deferred-storage-edge-cases.md) | Fix the deferred storage and snapshot edge cases of slice 1 | proposed | 2026-11-06 |
| [0235](0235-disk-health-shows-smart-data-and-replace-soon-marks.md) | Disk Health opens each disk's SMART data, and disks whose SMART trend scores 50 are marked replace soon | accepted | 2026-11-07 |
| [0241](0241-snapshots-of-volumes-and-volume-quotas-in-the-web-ui.md) | The Snapshots page offers the volumes of mounted pools, and the Volumes dialog takes a quota in GiB | superseded by [0243](0243-the-snapshots-page-offers-mounted-pools-and-their-volumes.md) | 2026-11-07 |
| [0242](0242-snapshot-commands-act-on-the-folders-that-were-opened.md) | Snapshots are made, restored and deleted through the folders that were opened, and deleted by subvolume id | accepted | 2026-11-07 |
| [0243](0243-the-snapshots-page-offers-mounted-pools-and-their-volumes.md) | The Snapshots page offers mounted pools and their volumes, also a pool whose volumes cannot be read, and the Volumes dialog takes a quota in GiB | accepted | 2026-11-07 |
| [0269](0269-the-smart-history-is-kept-in-ram-and-written-when-it-changes.md) | The SMART monitor keeps its history in RAM and writes it only when a pass changes it, and at shutdown | accepted | 2026-11-07 |
| [0270](0270-smart-trends-report-every-tracked-disk.md) | GET /health/smart/trends reports every tracked disk, and trends read each counter from the snapshots that hold it | accepted | 2026-11-07 |
| [0272](0272-scrub-results-name-cancelled-scrubs-and-count-every-error.md) | A scrub's result names a cancelled or interrupted scrub and counts every error btrfs reports | accepted | 2026-11-07 |
| [0282](0282-a-filesystem-found-on-the-disks-is-imported-under-a-name-that-becomes-its-label.md) | A btrfs filesystem found on the disks is imported at its top level, under a name that becomes its label | superseded by [0283](0283-a-filesystem-found-on-the-disks-becomes-a-pool-only-from-data-disks-under-a-free-name.md) | 2026-11-08 |
| [0283](0283-a-filesystem-found-on-the-disks-becomes-a-pool-only-from-data-disks-under-a-free-name.md) | A filesystem found on the disks becomes a pool only from data disks and under a name that no earlier pool still uses | accepted | 2026-11-08 |
| [0285](0285-the-recovery-runbook-reads-a-pool-from-another-linux-machine-without-writing-to-it.md) | The recovery runbook reads a pool from another Linux machine without writing to it, degraded when a disk is missing | accepted | 2026-11-08 |

## Shares

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0037](0037-share-and-export-values-validated.md) | Share and export values are validated before they reach smb.conf or /etc/exports | accepted | 2026-11-05 |
| [0038](0038-share-name-rule.md) | Share names have 1 to 32 letters, digits, '_' or '-' and do not start with '-' | accepted | 2026-11-05 |
| [0074](0074-smb-state-in-sqlite-and-unos-writes-smb-conf.md) | Shares and SMB accounts live in SQLite, and UNOS alone writes smb.conf | accepted | 2026-11-06 |
| [0075](0075-per-user-share-access-levels.md) | Each share grants each user none, read or write, and new users get no access | accepted | 2026-11-06 |
| [0076](0076-no-group-principals-for-smb-yet.md) | Share access names individual users, not groups, for now | accepted | 2026-11-06 |
| [0077](0077-smb-tables-through-create-all-without-migrations.md) | The SMB tables come from create_all, and existing tables are never altered | accepted | 2026-11-06 |
| [0078](0078-accept-the-sqlite-power-cut-window.md) | SQLite's power-cut rollback window is accepted, and the startup reconcile repairs the host | accepted | 2026-11-06 |
| [0079](0079-sqlite-busy-timeout-and-unbounded-pool-overflow.md) | The SQLite engine waits 15 s for locks and has an unbounded pool overflow | accepted | 2026-11-06 |
| [0080](0080-one-lock-for-every-smb-change.md) | One lock, smb_lock, serializes every change of SMB state | accepted | 2026-11-06 |
| [0081](0081-reload-rows-after-taking-smb-lock.md) | SMB flows reload the user and the account after taking smb_lock | accepted | 2026-11-06 |
| [0082](0082-smb-code-writes-only-on-real-changes.md) | The SMB code writes to the system disk only when accounts or shares change | accepted | 2026-11-06 |
| [0083](0083-import-smb-conf-when-the-shares-table-is-empty.md) | Shares are imported from smb.conf whenever the shares table is empty | superseded by [0084](0084-import-the-existing-smb-conf-once.md) | 2026-11-06 |
| [0084](0084-import-the-existing-smb-conf-once.md) | The existing smb.conf is imported once per database, controlled by a marker | accepted | 2026-11-06 |
| [0085](0085-keep-the-original-smb-conf-as-import-backup.md) | The original smb.conf is kept once as smb.conf.unos-import-backup | accepted | 2026-11-06 |
| [0086](0086-the-import-only-narrows-access.md) | The smb.conf import only narrows access and reports every option it drops | accepted | 2026-11-06 |
| [0087](0087-hardened-pinned-global-section.md) | UNOS renders a hardened [global] section with pinned values | superseded by [0281](0281-hardened-pinned-global-section-with-auth-audit-logging.md) | 2026-11-06 |
| [0088](0088-fail-safe-share-sections.md) | Share sections never express a restriction by leaving a line out | accepted | 2026-11-06 |
| [0089](0089-apply-is-checked-by-testparm-and-atomic.md) | Every apply is checked by testparm and installed atomically | accepted | 2026-11-06 |
| [0090](0090-reload-all-and-retry-failed-reloads.md) | Samba is reloaded with smbcontrol all, and failed reloads and closes are retried | accepted | 2026-11-06 |
| [0091](0091-testparm-tolerates-two-unknown-parameters.md) | The testparm check tolerates only two parameters that older Samba lacks | accepted | 2026-11-06 |
| [0092](0092-copy-foreign-sections-before-overwriting.md) | Foreign share sections are saved to smb.conf.unos-replaced before an apply | accepted | 2026-11-06 |
| [0093](0093-globals-is-a-reserved-share-name.md) | The share name 'globals' is reserved like 'global' | accepted | 2026-11-06 |
| [0094](0094-samba-cache-in-ram-and-syslog-logging.md) | Samba keeps its cache in /run/samba and logs to syslog | superseded by [0277](0277-samba-logs-failed-sign-ins-to-syslog-and-keeps-its-log-file-in-ram.md) | 2026-11-06 |
| [0095](0095-smb-answers-lan-addresses-only.md) | SMB answers only LAN addresses, through hosts allow in [global] | accepted | 2026-11-06 |
| [0096](0096-guest-access-off-and-guest-shares-read-only.md) | Guest access is off by default, and guest shares are read-only | accepted | 2026-11-06 |
| [0097](0097-restrict-anonymous-0-with-guest-shares.md) | A guest share sets restrict anonymous = 0 and map to guest = Bad User | superseded by [0098](0098-guest-globals-follow-guest-shares.md) | 2026-11-06 |
| [0098](0098-guest-globals-follow-guest-shares.md) | The guest-related globals follow whether any guest share exists | accepted | 2026-11-06 |
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
| [0116](0116-users-cannot-be-renamed.md) | UNOS users cannot be renamed yet | accepted | 2026-11-06 |
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
| [0139](0139-restore-prepares-the-database-before-the-swap.md) | A restore prepares the extracted database before the swap, under smb_lock | accepted | 2026-11-06 |
| [0140](0140-restore-marks-every-smb-account-needed.md) | After a restore every SMB account needs its password again | superseded by [0141](0141-restored-accounts-stay-active-only-with-proof.md) | 2026-11-06 |
| [0141](0141-restored-accounts-stay-active-only-with-proof.md) | After a restore an SMB account stays active only with proof | accepted | 2026-11-06 |
| [0142](0142-old-backups-import-shares-without-guest-access.md) | Old backups import their shares from smb.conf, never with guest access | accepted | 2026-11-06 |
| [0143](0143-shares-are-part-of-the-database-backup.md) | Shares are part of the database, and include_shares only adds the old smb.conf copy | accepted | 2026-11-06 |
| [0144](0144-restored-databases-are-untrusted.md) | Restored databases are untrusted input | accepted | 2026-11-06 |
| [0145](0145-what-may-be-shared.md) | A share is a folder strictly inside a managed pool, never the pool root or apps/ | accepted | 2026-11-06 |
| [0146](0146-share-folders-do-not-overlap.md) | Share folders must not overlap | accepted | 2026-11-06 |
| [0147](0147-snapshots-hidden-from-smb.md) | Snapshot folders are hidden from SMB and never shared or repaired | accepted | 2026-11-06 |
| [0148](0148-only-managed-pools-hold-shares.md) | Only pools that UNOS manages can hold SMB shares, and the UI offers only those | accepted | 2026-11-06 |
| [0149](0149-fail-closed-without-the-pool-list.md) | SMB changes fail closed when the pool list cannot be read | accepted | 2026-11-06 |
| [0150](0150-share-folders-root-unos-smb-2770.md) | Share folders belong to root and the group unos-smb with mode 2770 | accepted | 2026-11-06 |
| [0151](0151-no-folders-while-gid-70000-is-taken.md) | No share folder is created and no Repair runs while another group has gid 70000 | accepted | 2026-11-06 |
| [0152](0152-share-folders-created-through-the-pool-mount.md) | Share folders are created only through the pool's mount, checked on descriptors | accepted | 2026-11-06 |
| [0153](0153-share-folders-only-below-root-only-folders.md) | Share folders are created only below folders that only root can change | accepted | 2026-11-06 |
| [0154](0154-unos-never-changes-existing-folder-modes.md) | UNOS never changes the mode of an existing folder, and the owner runs the chmod | accepted | 2026-11-06 |
| [0155](0155-repair-permissions-only-on-request.md) | Guest-era permissions are fixed only by an explicit per-share Repair | accepted | 2026-11-06 |
| [0156](0156-repair-walks-with-descriptors.md) | Repair walks the folder tree with descriptors and opens nothing it must not | accepted | 2026-11-06 |
| [0157](0157-smb-audits-only-real-changes.md) | The SMB code audits only real changes, and problems go to the status | accepted | 2026-11-06 |
| [0158](0158-netbios-stays-until-discovery.md) | NetBIOS stays until a discovery slice adds WS-Discovery and Bonjour | superseded by [0275](0275-windows-finds-the-nas-through-wsdd2-macos-through-bonjour-and-netbios-is-off.md) | 2026-11-06 |
| [0159](0159-passdb-stays-on-the-system-disk.md) | Samba's passdb stays on the system disk until its writes are measured | accepted | 2026-11-06 |
| [0160](0160-no-file-manager-or-app-group-access-to-shares.md) | The File Manager and apps get no group access to share folders yet | superseded by [0273](0273-file-manager-entries-in-share-folders-get-2770-and-0660.md) | 2026-11-06 |
| [0161](0161-no-per-share-protocol-options-yet.md) | Per-share protocol options wait for a later slice | accepted | 2026-11-06 |
| [0162](0162-apparmor-profile-for-new-share-paths.md) | UNOS does not manage smbd's AppArmor profile while smbd runs unconfined | accepted | 2026-11-07 |
| [0163](0163-nfs-unchanged-in-slice-2.md) | NFS exports stay unchanged in slice 2 | accepted | 2026-11-06 |
| [0164](0164-smb-and-audit-follow-ups-left-for-later.md) | Six SMB and audit follow-ups wait for later slices | accepted | 2026-11-06 |
| [0192](0192-reread-host-facts-before-destructive-commands.md) | Host facts are read again right before destructive commands, and doubt fails closed | accepted | 2026-11-07 |
| [0193](0193-acl-entries-and-sticky-folders-in-the-parent-chain.md) | ACL entries and sticky folders count in the parent-chain rule | proposed | 2026-11-07 |
| [0194](0194-reduce-samba-runtime-writes.md) | Reduce Samba's remaining writes to the system disk | superseded by [0278](0278-samba-keeps-a-password-history-of-three.md) | 2026-11-07 |
| [0195](0195-open-smb-follow-ups-after-slice-2.md) | Handle the SMB follow-ups left open after slice 2 | superseded by [0250](0250-smb-follow-ups-still-open-after-slice-2.md) | 2026-11-07 |
| [0248](0248-pool-changes-apply-smb-conf.md) | The storage endpoints apply smb.conf after a pool change, so an unmounted pool's shares stop being served at once | accepted | 2026-11-07 |
| [0249](0249-smb-status-names-an-smb-conf-replaced-after-unos-wrote-it.md) | SMB cannot be turned on while Samba serves an smb.conf that UNOS did not write, and the status names one replaced after UNOS wrote it | accepted | 2026-11-07 |
| [0250](0250-smb-follow-ups-still-open-after-slice-2.md) | Handle the SMB follow-ups still open after slice 2 | superseded by [0276](0276-smb-follow-ups-still-open-after-network-discovery.md) | 2026-11-07 |
| [0275](0275-windows-finds-the-nas-through-wsdd2-macos-through-bonjour-and-netbios-is-off.md) | Windows finds the NAS through wsdd2, macOS through Samba's Bonjour announcement, and NetBIOS is off | accepted | 2026-11-08 |
| [0276](0276-smb-follow-ups-still-open-after-network-discovery.md) | Handle the SMB follow-ups still open after network discovery | proposed | 2026-11-08 |
| [0277](0277-samba-logs-failed-sign-ins-to-syslog-and-keeps-its-log-file-in-ram.md) | Samba logs failed sign-ins through auth_audit to syslog and keeps its own log file in /run/samba | accepted | 2026-11-08 |
| [0278](0278-samba-keeps-a-password-history-of-three.md) | Samba keeps a password history of 3, so that retries with an account's previous passwords write nothing to passdb | accepted | 2026-11-08 |
| [0279](0279-smb-bound-passwords-must-not-be-common-passwords.md) | SMB-bound passwords need at least 8 characters, at most 72 bytes, no control characters, and must not be common passwords | accepted | 2026-11-08 |
| [0280](0280-smb-logins-stay-unthrottled-until-the-owner-chooses-a-limit.md) | SMB logins have no rate limit and no lockout, as an accepted risk until the owner chooses how to limit them | accepted | 2026-11-08 |
| [0281](0281-hardened-pinned-global-section-with-auth-audit-logging.md) | UNOS renders a hardened [global] section with pinned values, among them Samba's logging lines of ADR-0277 | accepted | 2026-11-08 |

## Apps

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0039](0039-app-data-only-on-a-mounted-pool.md) | App data lives only inside a selected, mounted pool | accepted | 2026-11-05 |
| [0219](0219-transmission-requires-a-web-login.md) | The transmission app requires a web login | accepted | 2026-11-07 |
| [0254](0254-docker-calls-run-in-worker-threads-and-conflicts-answer-409.md) | Docker calls of the containers and apps APIs run in worker threads within the Docker client's timeout, and the containers API answers Docker's conflicts with 409 | accepted | 2026-11-07 |
| [0255](0255-container-logs-stream-in-the-drawer-and-images-get-a-tab.md) | The container drawer follows the log over the logs socket, and the Images tab pulls by name and removes only what no container uses | accepted | 2026-11-07 |
| [0256](0256-the-apps-api-acts-only-on-apps-and-uninstall-keeps-their-data.md) | The Apps API starts, stops, restarts and uninstalls only the containers of apps, and uninstall keeps their data | accepted | 2026-11-07 |
| [0274](0274-apps-of-linuxserver-images-can-run-in-unos-smb-and-only-they-get-share-folders.md) | Apps of linuxserver.io images can be installed to run in the group unos-smb, and only such an app is given a share's folder | accepted | 2026-11-08 |

## Backup

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0040](0040-restore-checks-archives-and-regenerates-smb-conf.md) | Restore checks archives and regenerates smb.conf instead of installing the archived file | accepted | 2026-11-05 |
| [0041](0041-off-device-backup-slice.md) | Give off-device backup of user data its own slice | proposed | 2026-11-05 |
| [0165](0165-backup-restore-flows-and-unique-names.md) | Backups restore from the tab, accept encrypted uploads and get unique names | accepted | 2026-11-06 |
| [0166](0166-uploaded-backups-checked-in-one-pass.md) | Uploaded backups are checked in one streaming pass that writes nothing | accepted | 2026-11-06 |
| [0167](0167-deferred-backup-and-upload-edge-cases.md) | Fix the deferred backup and upload edge cases of slice 1 | proposed | 2026-11-06 |
| [0240](0240-scheduled-backups-encrypted-with-a-key-derived-from-the-passphrase.md) | Backup tasks encrypt every backup with a key derived from their passphrase and stored in a file of its own | accepted | 2026-11-07 |

## Files

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0042](0042-file-paths-stay-inside-the-pools.md) | File paths stay inside the pools and never follow symlinks | accepted | 2026-11-05 |
| [0168](0168-file-manager-download-delete-copy-move.md) | The file manager downloads with the session and never overwrites on copy or move | accepted | 2026-11-06 |
| [0169](0169-uploads-stream-into-their-folder.md) | Uploads stream into their folder and stay unnamed until complete | accepted | 2026-11-06 |
| [0170](0170-file-manager-protects-volumes-and-mounts.md) | The file manager never deletes, renames or moves volumes, subvolumes or mounts | accepted | 2026-11-06 |
| [0232](0232-file-manager-renames-in-the-row-and-never-replaces.md) | The file manager renames one selected item in its row and never replaces an entry | accepted | 2026-11-07 |
| [0259](0259-deleted-items-go-to-the-trash-of-their-pool-or-volume-for-30-days.md) | Deleted items go to the trash of their pool or volume for 30 days, unless they are restored or deleted for good | accepted | 2026-11-07 |
| [0273](0273-file-manager-entries-in-share-folders-get-2770-and-0660.md) | New folders and uploads of the file manager in a share folder get 2770 and 0660 | superseded by [0274](0274-apps-of-linuxserver-images-can-run-in-unos-smb-and-only-they-get-share-folders.md) | 2026-11-08 |

## System

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0043](0043-background-loops-in-the-lifespan.md) | Background loops start and stop in the FastAPI lifespan | accepted | 2026-11-05 |
| [0044](0044-custom-tasks-administrator-only.md) | Custom shell tasks stay, but are administrator-only and validated | accepted | 2026-11-05 |
| [0045](0045-smtp-verifies-certificates.md) | E-mail notifications verify the mail server's certificate by default | accepted | 2026-11-05 |
| [0046](0046-telegram-alerts-over-ipv6-or-e-mail.md) | Deliver Telegram alerts over Docker IPv6, or send them by e-mail | proposed | 2026-11-05 |
| [0047](0047-event-bus-for-alerts.md) | Send alerts to every channel through an event bus, with a health banner in the UI | proposed | 2026-11-05 |
| [0171](0171-supervisor-volatile-files-in-ram.md) | The supervisor keeps volatile files in RAM | accepted | 2026-11-06 |
| [0172](0172-unique-task-ids-and-bounded-smart-tests.md) | Scheduled tasks get unique ids, backups run concurrently, and SMART tests time out | accepted | 2026-11-06 |
| [0173](0173-in-app-updater-in-its-own-slice.md) | Redesign the in-app updater in its own slice; until then update.sh is the way | proposed | 2026-11-06 |
| [0233](0233-power-actions-run-on-the-host-and-are-off-where-host-commands-run-locally.md) | Power actions run on the host through host.py and are off where host commands run locally | accepted | 2026-11-07 |
| [0234](0234-the-image-carries-its-version-and-the-updates-tab-shows-only-what-works.md) | The supervisor image carries its version, and System > Updates shows only what works | accepted | 2026-11-07 |
| [0244](0244-a-disabled-interface-comes-back-on-unless-the-change-is-kept.md) | A disabled network interface comes back on by itself after 60 seconds unless the change is kept | accepted | 2026-11-07 |
| [0245](0245-system-network-shows-ipv6-link-speeds-and-the-administrative-state.md) | System > Network shows IPv6, link speeds from sysfs and each interface's administrative state | accepted | 2026-11-07 |
| [0246](0246-interfaces-with-vlans-stay-enabled-and-the-revert-follows-the-host.md) | An interface that carries VLANs is not disabled, and a disable's saved state and the page's reports follow what the host shows | accepted | 2026-11-07 |
| [0247](0247-wake-on-lan-from-power-off-depends-on-the-board.md) | Waking a host that is off by Wake-on-LAN depends on its board, and the Power tab says so | accepted | 2026-11-07 |
| [0257](0257-system-logs-reads-every-source-from-the-hosts-journal-newest-first.md) | System > Logs reads every source from the host's journal, newest first, with one set of filters | accepted | 2026-11-07 |
| [0258](0258-a-journal-read-ends-on-the-host-and-two-run-at-a-time.md) | A journal read of System > Logs ends on the host after 30 seconds, two run at a time, and a search without a match lists nothing | accepted | 2026-11-07 |
| [0260](0260-notification-channels-of-every-kind-name-failed-deliveries-and-keep-a-full-history.md) | Notification channels of every kind are set up in the web UI, a delivery counts only on success, and the history lists every notification sent | accepted | 2026-11-07 |
| [0264](0264-schedules-follow-the-clock-of-the-hosts-time-zone.md) | Schedules follow the clock of the host's time zone, read from its /etc/localtime link, with fixed rules for the changes of daylight saving time | superseded by [0266](0266-schedules-follow-the-hosts-time-zone-as-last-read.md) | 2026-11-07 |
| [0265](0265-custom-task-runs-record-output-and-exit-status-and-end-within-a-day.md) | A custom task's run records the command's output and exit status, and the command ends within 24 hours with what it started | superseded by [0267](0267-custom-task-commands-end-with-every-process-of-their-session.md) | 2026-11-07 |
| [0266](0266-schedules-follow-the-hosts-time-zone-as-last-read.md) | Schedules follow the host's time zone as last read from its /etc/localtime link, with fixed rules for the changes of daylight saving time | accepted | 2026-11-08 |
| [0267](0267-custom-task-commands-end-with-every-process-of-their-session.md) | A custom task's run records the command's output and exit status, and the command ends within 24 hours with every process of its session | accepted | 2026-11-08 |
| [0268](0268-a-run-that-a-stop-of-the-supervisor-interrupts-is-recorded-as-failed.md) | A task run that a stop of the supervisor interrupts is recorded as failed | accepted | 2026-11-08 |

## Web UI

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0174](0174-users-manage-their-smb-on-the-account-page.md) | Regular users manage their own SMB access on the Account page | accepted | 2026-11-06 |
| [0175](0175-deferred-ui-scheduler-and-tooling-items.md) | Fix the deferred UI, scheduler, container and tooling items | proposed | 2026-11-06 |
| [0196](0196-windows-hint-uses-ipv6-literal-net.md) | The Windows connection hint uses the ipv6-literal.net form for IPv6 hosts | accepted | 2026-11-07 |
| [0197](0197-accessible-dialogs-lists-and-hints.md) | Dialogs, lists and hints in the web UI work with keyboard and screen readers | accepted | 2026-11-07 |
| [0271](0271-the-storage-page-says-in-each-card-why-its-list-could-not-be-loaded.md) | The Storage page loads the disks and the pools apart and says in each card why its list could not be loaded | accepted | 2026-11-07 |
| [0287](0287-the-web-ui-takes-its-texts-from-catalogs-in-english-and-russian.md) | The web UI takes its texts from catalogs in English and Russian, chosen on the Account page and checked by make frontend | accepted | 2026-11-08 |

## Deploy

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0048](0048-fixed-compose-subnet-and-trusted-proxy.md) | A fixed compose subnet lets uvicorn trust X-Forwarded-For only from the web UI | accepted | 2026-11-05 |
| [0049](0049-update-sh-with-backup-health-check-and-rollback.md) | Updates go through update.sh, with a backup, a health check and automatic rollback | accepted | 2026-11-05 |
| [0050](0050-releases-as-tags-and-multi-arch-images.md) | Release with git tags and multi-arch images on GHCR | proposed | 2026-11-05 |
| [0176](0176-docker-health-checks-and-ordered-start.md) | Both containers have health checks, and the web UI waits for a healthy supervisor | accepted | 2026-11-06 |
| [0177](0177-tmpfs-cap-for-small-boards.md) | Size the /tmp tmpfs for boards with 1 GB of RAM | proposed | 2026-11-06 |
| [0178](0178-docker-log-rotation.md) | Rotate Docker's json-file logs when they grow | proposed | 2026-11-06 |
| [0179](0179-uninstall-removes-smb-accounts.md) | Uninstall removes the SMB host accounts | superseded by [0180](0180-uninstall-keeps-smb-host-accounts.md) | 2026-11-06 |
| [0180](0180-uninstall-keeps-smb-host-accounts.md) | Uninstall keeps the SMB host accounts and removes only their exact passdb entries | accepted | 2026-11-06 |
| [0181](0181-downgrade-guidance-for-the-smb-release.md) | Rolling back to the release before SMB accounts is documented, not engineered | superseded by [0207](0207-no-backward-compatibility.md) | 2026-11-06 |

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
| [0227](0227-no-ipv6-inbound-filter-on-the-dev-device-for-now.md) | No inbound IPv6 filter on the dev device for now; each service protects itself | accepted | 2026-11-07 |
| [0228](0228-docker-data-root-stays-on-the-sd-card.md) | Docker's data root stays on the dev device's SD card | accepted | 2026-11-07 |

## Testing

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0186](0186-every-fix-comes-with-a-failing-test.md) | Every fix comes with a test that fails on the code before it | accepted | 2026-11-06 |
| [0187](0187-linux-only-tests-in-the-device-ram.md) | Linux-only tests run on the device entirely in RAM | accepted | 2026-11-06 |
| [0188](0188-test-environments-with-known-failure-baselines.md) | Tests run in fixed environments against lists of known failures | superseded by [0215](0215-e2e-only-against-a-local-stand-in.md) | 2026-11-06 |
| [0189](0189-smb-code-tested-against-a-fake-host.md) | SMB code is tested against a fake host layer, real testparm and the local stand-in | accepted | 2026-11-06 |
| [0215](0215-e2e-only-against-a-local-stand-in.md) | E2E tests run only against local targets, never against the device | accepted | 2026-11-07 |
| [0261](0261-the-stand-ins-test-administrator-is-created-through-the-setup-page.md) | The stand-in's test administrator is created through the setup page, by a first Playwright run | accepted | 2026-11-07 |

## CI

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0056](0056-ship-with-existing-svelte-check-errors.md) | Changes may ship with the existing svelte-check errors | superseded by [0190](0190-ci-quality-gates.md) | 2026-11-05 |
| [0190](0190-ci-quality-gates.md) | CI fails on any test, lint, audit, build or check error, with a read-only token | accepted | 2026-11-06 |
| [0216](0216-make-check-is-the-single-quality-gate.md) | One quality gate: make check runs what CI runs | accepted | 2026-11-07 |
| [0217](0217-make-setup-bootstraps-a-checkout.md) | One command, make setup, bootstraps a checkout idempotently | accepted | 2026-11-07 |
| [0218](0218-scheduled-dependency-audits-and-maintenance.md) | Dependencies are audited weekly, and maintenance passes run monthly | accepted | 2026-11-07 |
| [0229](0229-main-stays-without-branch-protection-on-the-free-plan.md) | The main branch stays without branch protection on GitHub's Free plan | accepted | 2026-11-07 |
| [0230](0230-the-ci-workflow-is-off-on-github.md) | The CI workflow is off on GitHub, and make check on the workstation is the only gate | accepted | 2026-11-07 |
| [0231](0231-dependabot-proposes-no-major-npm-versions-and-leaves-playwright-alone.md) | Dependabot proposes no major npm versions and leaves Playwright alone | accepted | 2026-11-07 |
