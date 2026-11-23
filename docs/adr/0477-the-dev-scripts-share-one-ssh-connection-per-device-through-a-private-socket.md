---
status: accepted
date: 2026-11-23
area: device
decided-by: agent
---

# ADR-0477: The dev scripts share one SSH connection per device, through a private socket that ssh.sh --close stops

## Context

`scripts/dev/ssh.sh` opened a new SSH connection for every call, and api.py's relay,
scenario.py, check.sh, sd.sh and deploy.sh call it many times. Each connection pays a key
exchange, a login and a PAM session: on both boards `/etc/pam.d/sshd` runs pam_motd, which runs
Armbian's `/etc/update-motd.d` scripts at every connection, whatever the command (sshd's
`UsePAM yes`). On the BPI-F3 (K1) a call took 1.6 s and more, which made the scrub and balance
checks of ADR-0473 lose their races on its NVMe pool (the balance sizes its work by a trial, and
calls slower than the trial's break it), and every device check was slow on both boards.

## Decision

- Every call of `scripts/dev/ssh.sh` passes `-o ControlMaster=auto -o ControlPath=<socket>
  -o ControlPersist=300`: the first call opens a master connection, which stays in the background
  until 300 s after its last session ended, and the calls after it run their commands over it. Every
  script that goes through ssh.sh (env.sh's `dev_ssh`, `dev_remote`, the device lock, api.py,
  scenario.py, tree.py) shares it.
- The socket is `${TMPDIR:-/tmp}/unos-ssh-<uid>/<user>@<host>`: one per device host and user,
  outside the repository. ssh.sh makes the folder with `mkdir -m 700` and uses it only when it is
  a folder of the current user, not a symlink, with mode 700. The path must be absolute, of
  ASCII letters, digits and `._/@:-` only (ssh splits ControlPath at spaces and expands `%` in
  it, and a relative path would follow each caller's working folder), and fit in 86 bytes
  (macOS's `sun_path` holds 104, and ssh first binds the socket under its name and 17 more
  characters). Otherwise each call connects on its own, as before, with a warning on standard
  error; it never changes a folder it did not make.
- Everything else of ssh.sh stays: the host key check, the key and askpass of ADR-0212, standard
  input passing through (no `-n`), `-t` only for an interactive shell.
- A stale or broken master falls back on OpenSSH's own handling, which ControlMaster=auto turns
  on: a socket whose master died is removed and the call opens a new master; a session the master
  cannot open (its connection died, or the boards' `MaxSessions 10` is reached) makes the call
  connect on its own, with ssh's lines on standard error ("session request failed", "read from
  master failed", "ControlSocket ... already exists, disabling multiplexing", the last also when
  two first calls log in at once). ssh.sh does not retry a call that failed: it cannot tell
  whether the command ran.
- `scripts/dev/ssh.sh --close` runs `ssh -O stop`: the master takes no new session, removes its
  socket and ends when the commands running over it end. It removes no socket itself (a dead
  master's socket is removed by the next call's ssh; one removed by hand could be a new master's)
  and does nothing without a master; it takes no other argument.
- Over a master, a call proves no new login: it uses the TCP connection made before, which a
  firewall lets through as established. The device-checks skill runs `--close` before a check
  that the device takes a new SSH login (its first step, and the LAN checks after a change of the
  device's firewall or network, whose revert windows a dead master would also eat), before the
  device reboots (an owner action, ADR-0211; no script of `scripts/dev` reboots it) and before a
  change of dev.env's SSH settings, which a running master does not see.
- No client-side option skips the login scripts: pam_motd runs in the connection's PAM session,
  before any command. The shared connection runs them once per master instead of once per call.
- Tests (`supervisor/tests/test_dev_scripts.py`, against the stub ssh):
  `test_ssh_sh_shares_one_connection_per_device_host_and_user`,
  `test_ssh_sh_without_tmpdir_puts_the_folder_in_tmp`,
  `test_ssh_sh_connects_on_its_own_when_the_folder_is_not_private`,
  `test_ssh_sh_connects_on_its_own_when_the_socket_path_is_too_long`,
  `test_ssh_sh_connects_on_its_own_when_tmpdir_is_not_a_plain_absolute_path`,
  `test_ssh_sh_close_stops_the_shared_connection` and `test_ssh_sh_help_names_close`.

## Consequences

- Measured from the workstation on 2026-11-23, the median of ten calls before and after:
  `scripts/dev/ssh.sh true` 0.81 s to 0.08 s on the BPI-W3 and 1.62 s to 0.10 s on the BPI-F3;
  `scripts/dev/api.py GET /health` 1.15 s to 0.34 s and 2.39 s to 0.81 s (the relay's Python start
  on the device is most of what is left). The first call after `--close` or an idle master's end
  still pays the whole login (0.94 s on the BPI-W3, 2.96 s on the BPI-F3).
- The socket gives a shell on the device as `UNOS_DEV_SSH_USER` without authentication to whoever
  can connect to it: only the workstation's user (folder 700, socket 600), who can already run
  ssh.sh, whose key and dev.env it reads.
- A master keeps the settings of its start: a new key, known_hosts file or password in dev.env,
  or the device's SSH settings (ADR-0226), apply to the call after `--close`, which logs in anew.
- A master holds one TCP connection and one sshd session on the device for at most 300 idle
  seconds. It lives in a session of its own: Ctrl-C or the end of the script that opened it does
  not end it, and one that the device lock's subshell opened (`trap '' HUP TERM`) ignores TERM
  too; `--close` ends it.
- The device lock's session (ADR-0210) is one session over the master: closing its standard
  input still releases the lock (`deploy.sh status` twice in a row on the BPI-W3: both took the
  lock, and no owner file or lock process stayed).
- A master whose connection died without the device answering (the device unplugged) holds a
  new call until its ServerAlive check gives up (60 s) before the call connects on its own.
- The fallbacks of a dead or full master are OpenSSH's, shown with the real ssh on the BPI-W3
  (the feature's notes), not by the tests, whose ssh is a stub.

## Considered options

- `ssh -O exit` for `--close`: it cuts the commands running over the master, such as another
  script's device lock, which would then be released while that script still works.
- A retry in ssh.sh when ssh exits 255: a command can exit 255 too, and a retry could run a
  command twice.
- OpenSSH's `%C` (40 hex characters) as the socket's name: too long under macOS's TMPDIR.
- Turning pam_motd off on the boards: a change of the device's settings, not of the scripts.
