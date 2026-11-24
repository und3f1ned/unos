---
status: accepted
date: 2026-11-24
area: process
decided-by: owner
---

# ADR-0498: The repository goes public with its history as it is, and a tree that holds no address or hardware identifier of the owner

## Context

The owner makes the repository public after the release v0.1.2. A read-only audit of the tree and its history found no live secret, but the tree named the owner's LAN (the dev subnet of `dev.env.example` and the tests), real MAC addresses and disk serials of the dev device, the dev network's past weaknesses in PROGRESS.md, a stale base branch and a link to Discussions, which are off. It had no security policy and no code of conduct, and GitHub named no license, as LICENSE began with the project's notice before the text of the GPL. The owner decided on 2026-11-24 that the history stays as it is and that the clean-up on GitHub's side (CI logs, pull request texts, stale branches) is done; the agent decided the rest, as the owner delegated it ("go on without me", in translation).

## Decision

- **The history stays as it is** (ADR-0208): the two passwords that an old CLAUDE.md published were rotated on 2026-11-07 and are used nowhere (ADR-0226), and `make secret-scan` lets exactly their commits pass (`.gitleaks.toml`, ADR-0445). Only the current tree changes.
- **No address of the owner's LAN and no hardware identifier of the dev device in the tree.** Tests, fakes, comments, docs, PROGRESS.md and the ledger's notes use RFC 5737's 192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24, RFC 3849's 2001:db8::/32, RFC 7042's MAC addresses 00:00:5e:00:53:xx or locally administered ones (with `enx<mac>` names to match), made-up serials and UUIDs, or a placeholder (the LAN's /24, its router, its IPv6 tunnel). Where the code admits only a private address (Samba's `hosts allow`, the SMB interfaces of `supervisor/tests/test_smb_interfaces.py`), a test uses 10.0.0.0/24 and fd00::/64, the networks that `supervisor/tests/test_openapi.py` already lets examples use. `dev.env.example` holds 192.0.2.103 as the device's address.
- `supervisor/tests/test_repo_tooling.py` checks every text file that git would commit: no address whose /24 is the dev subnet (dotted, hyphenated, as a.b.c/24 or a reverse zone), and no token that is, in any case, a serial of the dev device's disks or the UUID of its root file system, all kept as SHA-256 digests, as the published passwords are, so that no file spells them out; and no universally administered MAC address (also in an `enx` or `wlx` name, or dotted): only RFC 7042's block, locally administered addresses, group addresses and all zeros pass. The digests hide nothing from someone who tries every private /24; they keep the values out of the text. The MAC rule cannot tell a board's own locally administered address, such as the one Rockchip's U-Boot derives from the SoC, from a made-up one: such an address is written as RFC 7042's, by review.
- **PROGRESS.md's Dev device section** says in one line that the inbound IPv6 filter is in place since 2026-11-08 (ADR-0301); it no longer details the dev network's past weaknesses. Accepted ADRs keep their text (ADR-0001).
- **`SECURITY.md`**: report a vulnerability through GitHub's private vulnerability reporting of the repository (`/security/advisories/new`, which `.github/ISSUE_TEMPLATE/config.yml` links), only the latest release gets fixes, a first answer usually within 7 days, and a GitHub security advisory with credit after the release that fixes it. **`CODE_OF_CONDUCT.md`** is the Contributor Covenant 2.1 with its attribution; reports go through the same private reporting or the owner's GitHub profile, with no e-mail address.
- **LICENSE holds the plain text of the GNU GPL version 3** as the FSF publishes it, so that GitHub names the license; the copyright line and the notice that granted version 3 or any later version, which stood above it, move to README.md's License section. `webui/package.json` (and the root of `webui/package-lock.json`) say `"license": "GPL-3.0-or-later"`. This changes only where ADR-0007's notice stands: UNOS stays licensed under the GPL version 3 or any later version, so ADR-0007 is not superseded.
- `AGENTS.md` and `CONTRIBUTING.md` name `origin/main`, or the batch branch that the owner names, as a worktree's base, not the stale `origin/phase1-broken-flows`; the issue chooser links no Discussions.
- `docs/maintenance.md`, "Once the repository is public", lists the GitHub settings that the owner turns on then: branch protection of `main` as `ci.branch-protection` gives it (possible on the Free plan once public, ADR-0229), secret scanning with push protection, Dependabot alerts and security updates, private vulnerability reporting, approval of the workflows of fork pull requests from all external contributors (once called outside collaborators), actions pinned to a full-length commit SHA (ADR-0423), and the wiki off; and the history decision with its reason.

## Consequences

- Anyone can read the history, with the rotated passwords and the dev network's old addresses and serials, and the accepted ADRs and the journal's Log, which keep the dev network's former weaknesses as they stood then (ADR-0227, ADR-0301): the passwords open nothing, and the inbound IPv6 filter closed what the probes of ADR-0301 reached. The authors' e-mail addresses of the commits are public with the history. The audit's notes stay private (ADR-0213).
- A test or a note that names an address of the dev subnet, a serial or the root UUID of the dev device, or a universally administered MAC fails `make test-fast`; the digests change only by a new decision, when the dev network, its disks or its system change.
- The ledger entry `process.public-repository` keeps `passes` false until the repository is public; the GitHub settings of `docs/maintenance.md` are the owner's, and no test of the repository can see them.

## Considered options

- Rewriting the history (git filter-repo) before going public: it would change every commit id that the ADRs, the ledger and the journal cite, contradict ADR-0208, and reach no clone or fork made before it, for passwords that open nothing.
- A plain pattern of the dev subnet in the test or in `.gitleaks.toml`: it would spell the subnet out in the tree, and a gitleaks rule would fail on the history, which keeps it.
