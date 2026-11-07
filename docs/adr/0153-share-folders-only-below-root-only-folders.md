---
status: accepted
date: 2026-11-06
area: shares
decided-by: agent
---

# ADR-0153: Share folders are created only below folders that only root can change

## Context

Whoever can change a folder between a pool root and a share folder can rename the share's
folder away and put another folder or a symlink in its place, and smbd and Repair
permissions follow the path. Such accounts could also swap in a folder between UNOS's
`mkdir` and its `fchown` and `fchmod`. Guest shares of earlier releases left folders that
every account can change, among them the device's pool root (mode 0777).

## Decision

A folder is unsafe when it is not owned by uid 0 or by the supervisor's own user, or when
"other", or a group other than gid 0, can write it, unless the sticky bit is set; the sticky
bit never excuses another owner (`service._unsafe_folder`). The chain is every folder from
the pool root `<pools base>/<pool>` down to the share folder's parent. It is read only
through descriptors (`open_dir_nofollow` and `fstat`), and no mode is ever changed
(ADR-0154).

- A create or PATCH that must create the folder answers 409 when a folder of the chain is
  unsafe, and nothing is created: "Share folders must be inside folders that only root can
  change: DIR can be changed by other accounts (mode 0777). Run 'chmod 755 DIR' on the NAS,
  then try again.", where DIR is the folder. For another owner the text adds
  `chown root:root DIR`; a path that needs shell quoting is quoted.
- A create or PATCH onto an existing folder is allowed, with the same reason in the answer's
  warnings.
- Every served share below an unsafe folder is a problem line of `GET /shares/smb-status`,
  or "the folders above its folder could not be checked".
- Repair permissions answers 409 before any walk (ADR-0155).

The suggested command is `chmod 755 <dir>` only when that removes nothing but write access,
that is when the mode has every bit of 0755 (the device's 0777 case); otherwise it is
`chmod go-w <dir>`, which never adds access. ACL entries and sticky folders refine the rule
(ADR-0193).

Implemented in `supervisor/src/shares/service.py` (`UnsafeFolder`, `_unsafe_folder`,
`_unsafe_parent`, `_open_new_folder_parent`) and `supervisor/src/shares/repair_router.py`;
tested in `supervisor/tests/test_share_api.py` (TestFoldersAboveAShare),
`supervisor/tests/test_share_repair.py` (a share below a folder that others can change is
refused) and `webui/tests/shares.spec.ts` (answers about parent folders that other accounts
can change are shown).

## Consequences

With a safe chain only root can swap a share's folder. A folder that others can change
blocks new share folders and Repair below it until the owner runs the command, while
existing shares below it keep working, with a warning and a status line.

## Considered options

- Always suggesting `chmod 755`: it would make a former share folder (2770) readable by
  every account.
- Fixing the folder's mode automatically: UNOS never changes the mode of an existing folder
  (ADR-0154).
