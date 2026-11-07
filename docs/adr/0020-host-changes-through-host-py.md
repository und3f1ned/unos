---
status: accepted
date: 2026-11-05
area: architecture
decided-by: agent
---

# ADR-0020: Host changes go through src/host.py, which runs argv lists in the host's namespaces

## Context

The supervisor runs in a container (ADR-0002), so a plain `subprocess.run` or file write changes only the container. In 1.0.0 a pool's fstab entry was written into the container's `/etc/fstab` and lost, NFS exports went to the container's file, and reboot and shutdown could not work; some modules used `nsenter -t 1`, others did not, and there was no single place to audit, test or mock host changes.

## Decision

- Every command or file write that must change the host (mounts, `/etc/fstab`, Samba reloads, the SMB host accounts, ...) goes through `supervisor/src/host.py`: `run_on_host`, `run_on_host_async`, `read_host_file`, `write_host_file_atomic` and `sync_host_path`.
- `run_on_host` enters PID 1's mount, uts, ipc, net and pid namespaces with `nsenter`; the user and cgroup namespaces stay the container's. It takes argument lists only, never a shell string, and refuses arguments with NUL bytes; callers still validate untrusted values. `UNOS_HOST_EXEC=local` runs the commands directly, for a supervisor that runs natively on the host.
- `HOST_TOOLS` names the host programs that UNOS runs, with their Debian packages; at startup the supervisor logs which of them the host lacks (`check_host_tools` in `supervisor/src/main.py`). It is the list for that check, not an enforced allowlist.
- Tests mock this one module; `supervisor/tests/test_host_tools.py` checks the nsenter prefix, the argument handling and the tool check.

## Consequences

- One module removes shell injection and gives one place to audit, but every host command still runs with full privileges, so callers must validate what they pass.
- Its users today: `storage/pools.py`, `storage/disks.py`, `shares/smb.py`, `shares/accounts.py`, `health/router.py` and `main.py`. Not migrated yet: the network and system modules call `nsenter` themselves, and NFS exports, power, the time zone, the system logs, UPS, the firewall and smartctl still run inside the container (ADR-0015).

## Considered options

- A narrow privileged host agent (a systemd service with a small RPC) behind an unprivileged web API: open, a possible later step (ADR-0002).
- `nsenter` with an enforced allowlist of commands: not done; `HOST_TOOLS` is only a presence check.
