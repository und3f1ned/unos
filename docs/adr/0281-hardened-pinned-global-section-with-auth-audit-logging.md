---
status: accepted
date: 2026-11-08
area: shares
decided-by: agent
supersedes: [ADR-0087]
---

# ADR-0281: UNOS renders a hardened [global] section with pinned values, among them Samba's logging lines of ADR-0277

## Context

ADR-0087 pinned every line of the rendered `[global]`, among them `log level = 1` and `logging = syslog@1` of ADR-0094. ADR-0277 replaced ADR-0094 with other logging lines and added three, so ADR-0087 no longer listed what UNOS writes. This ADR restates ADR-0087 with the lines of ADR-0277; nothing else changes.

## Decision

UNOS writes `[global]` itself (`global_settings` and `render_global` in `supervisor/src/shares/smbconf.py`), in this order:

- `workgroup = WORKGROUP`, `server string = UNOS NAS` (kept as written: `deploy/install.sh` recognizes a file that UNOS manages by this line), `server role = standalone server`, `security = user`, `passdb backend = tdbsam`;
- `map to guest` and `restrict anonymous`, which depend on guest shares (ADR-0098), `guest account = nobody`, `invalid users = root`, `hosts allow` with LAN ranges only (ADR-0095);
- `server min protocol = SMB2_02` (SMB1 off), `ntlm auth = ntlmv2-only`, `server signing` depending on guest shares (ADR-0100), `server smb encrypt = default`, `smb3 unix extensions = no` (ADR-0101), `access based share enum = yes`, so a user sees only the shares the user can open;
- `load printers = no`, `printing = bsd`, `printcap name = /dev/null`, `disable spoolss = yes`, `usershare max shares = 0`, `unix password sync = no`, `pam password change = no`, `obey pam restrictions = no`;
- `cache directory = /run/samba`, `log file = /run/samba/log.samba`, `max log size = 100`, `log level = 1 auth_audit:2`, `logging = syslog@2` and `debug timestamp = no` (ADR-0277), then `dns proxy = no`.

Several lines restate Samba 4.22's defaults on purpose, and `interfaces` is not set. Tests: `supervisor/tests/test_smbconf.py` (`TestGlobal`), `supervisor/tests/test_smbconf_testparm.py` (both variants through the real testparm), and `supervisor/tests/test_deploy_scripts.py` (the file that `install.sh` writes equals `render_config([])`).

## Consequences

- A distribution or Samba default that changes cannot change these settings.
- SMB1 clients, printing through Samba and usershares do not work.
- Older Samba releases may not know two of the lines; ADR-0091 says how the testparm check treats them.
