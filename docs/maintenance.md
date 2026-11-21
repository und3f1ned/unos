# Maintenance calendar

Scheduled passes that catch drift between the code, the documents and settings that agents rely
on, the dependencies and the dev device (ADR-0218). Every pass ends in a change: a small pull
request, or an entry of `feature-list.json` for what needs more work. A report that changes
nothing is not a result.

## Weekly, automatic

| Pass | Who runs it | What it checks | What follows |
| --- | --- | --- | --- |
| Dependency audit | GitHub Actions: `.github/workflows/audit.yml`, every week and on demand (`workflow_dispatch`) | `scripts/audit-python.sh` (pip-audit of `supervisor/requirements.txt`, resolved in the audit environment, ADR-0423), and `npm audit --omit=dev --audit-level=high` in `webui/` | A failed run: the owner updates the dependency, or a feature-list entry when the update needs work |
| Dependency updates | Dependabot: `.github/dependabot.yml` | New versions for pip, npm, GitHub Actions (the commit of a newer release and its version comment, ADR-0423) and the Docker base images, grouped, after a cooldown, with Conventional Commit prefixes; no new major npm versions and no Playwright (ADR-0427), no ESLint or Prettier (ADR-0449) | Pull requests that the owner reviews and merges by hand after `make check` passes in a checkout set up from the pull request's branch, since CI is off (ADR-0230): never automatically, because the supervisor runs privileged with the Docker socket |
| Image vulnerabilities | GitHub Actions: the `images` job of `audit.yml` | `make image-scan`: both images built as an install builds them, then trivy, pinned by digest, without the network: fixable HIGH and CRITICAL vulnerabilities of their OS packages and libraries, minus `.trivyignore.yaml` (ADR-0446) | A failed run names the package and its fixed version: the owner moves the base image's digest or the package, or adds an entry with its reason and expiry to `.trivyignore.yaml`; an expired entry fails the run again |
| Catalog image platforms | GitHub Actions: the `platforms` job of `audit.yml` | `scripts/catalog-platforms.py check`: the platforms recorded for every pinned image of the app catalog against the registries' manifests of its index (ADR-0403, ADR-0410) | A failed run: a pin whose record was not rewritten, or an index that its registry no longer serves; the owner runs `make catalog-platforms`, then `scripts/catalog-platforms.py record` and reviews the diff |
| Catalog image tags | GitHub Actions: the `tags` job of `audit.yml` | `scripts/catalog-platforms.py tags`: the tag of every pinned image of the app catalog against the index its registry names now (ADR-0410) | A failed run: a publisher pushed a pinned version again or a line tag (Traefik's `v3.7`) moved on (`MOVED`, with the templates and both digests), or a pinned index is gone; the owner reviews the new index and pins it in `supervisor/src/apps/catalog.py` with its `VOLUME`s and size, then runs `scripts/catalog-platforms.py record`, or keeps the pin. Immich's five pins move only together with an Immich release: a re-pin offers the dev device's Immich an update |

`make audit` runs the same audit locally; it needs the network. The hashed lint tools of
`.github/lint-requirements.txt` are regenerated as `docs/TESTING.md` describes; the monthly pass
"The pinned check tools" moves them with the tools of the audit environment.

## Monthly, local

The owner starts an agent session in the repository and picks the passes; the agent runs them
and proposes the changes, and the owner reviews them.

1. **AGENTS.md and the nested files against the code.** For every line, name the mistake it
   prevents; delete lines that change nothing and lines that repeat what the code or a command
   shows. Every command and path still exists (`make help`, `ls`). Each AGENTS.md has a CLAUDE.md
   beside it that holds only the line `@AGENTS.md`: Claude Code reads CLAUDE.md files, a nested
   one when it works in that folder, and no AGENTS.md in a project that has a CLAUDE.md. Tool:
   mattpocock-skills:writing-for-agents.
2. **The skills against the scripts.** Each step of `.agents/skills/*/SKILL.md` still matches the
   script it calls (`--help`; for the device, `scripts/dev/deploy.sh status` and
   `scripts/dev/deploy.sh sync --dry-run`, which only read). Tool:
   mattpocock-skills:writing-for-agents.
3. **ADR statuses.** Each accepted ADR still describes the code; a proposed ADR that was done or
   dropped gets its new status, or a new ADR that supersedes it. `make test-fast` checks the
   format and the supersession links (`supervisor/tests/test_adrs.py`). Tool:
   mattpocock-skills:domain-modeling (its CONTEXT.md is GLOSSARY.md here).
4. **GLOSSARY.md.** The terms against the code and the month's changes
   (`git log --since="1 month ago" --stat`): new concepts, renamed ones, and synonyms that crept
   into code, docs or the web UI. A definition says what a word means: code pointers go to the
   AGENTS.md files, values and rules to the ADRs. Tool: mattpocock-skills:domain-modeling.
5. **feature-list.json against the tests.** Every `verified_by` file exists and its tests pass
   (`make test-fast` runs `supervisor/tests/test_feature_list.py`, `make check` the tests); an
   entry with `passes: true` still passes its steps; each entry for removing a legacy code path
   (ADR-0207) shows whether that path is still in the code.
6. **The dev device's SD-card writes and wear.** `scripts/dev/check.sh` prints the writes since
   boot and the card's wear data where the card reports it; compare them with the totals in the
   Dev device section of `PROGRESS.md`. A rise that nothing explains becomes a feature-list entry
   (ADR-0214).
7. **The agent setup.** The guard's denials of the month, one line each in the git-ignored
   `tmp/guard/denials.log` (the time in UTC, the rule and the tool, never the command): a false
   block gets a fix and a case in `supervisor/tests/test_agent_guard.py`. The permission rules of
   `.claude/settings.json` stay narrow, auto memory stays off, `tmp/handoff/` loses the handoffs
   whose goal is done, and the Log of `PROGRESS.md` keeps older phases to a few lines each.
8. **The device backups.** `scripts/dev/deploy.sh backup`, also the first step of `up`, copies the
   dev device's fstab, smb.conf and data volume into the main checkout's git-ignored
   `tmp/dev/backup`, one folder per backup, and deletes all but the newest 3 (ADR-0223). They
   hold the device's JWT secret, password hashes and database, so the pass looks only at names
   and modes (`ls -lR tmp/dev/backup`), never at the contents (ADR-0212): at most 3 backup
   folders, each with mode 700 and its files with mode 600. Anything else is a bug of
   `deploy.sh`, fixed with a test in `supervisor/tests/test_dev_scripts.py`.
9. **Playwright.** Dependabot leaves it alone (ADR-0427). Move `@playwright/test` in
   `webui/package.json` and `webui/package-lock.json` to its newest release, together with the
   tag of the image in `webui/Dockerfile.test` and the digest that tag names (ADR-0305;
   `test_image_has_the_playwright_version_of_package_lock` checks that they match), then
   `make setup` for the browsers (the network: the owner's machine) and `make e2e-local`.
10. **Outdated majors and the dev dependencies.** `npm outdated` in `webui/` names the new major
    versions that Dependabot leaves out (ADR-0427), and `npm audit --audit-level=high` there
    covers the dev dependencies that the weekly audit skips. Each finding becomes, or updates, an
    entry of `feature-list.json` (such as `webui.toolchain-major-versions`).
11. **The base images.** Every `FROM` line names its tag and a digest (ADR-0305). Dependabot
    proposes a tag's newer digest only after the tag has not moved for its cooldown of 7 days,
    which `python:3.11-slim` seldom does. Compare each pin with the digest its tag names now (for
    example `docker buildx imagetools inspect python:3.11-slim`); a pin that is older and that no
    open Dependabot pull request moves gets a pull request of its own. When the new digest's
    linux/arm64 image differs, the dev device's next supervisor build pulls the base and runs
    apt-get and pip again: say so in the pull request.
12. **The pinned check tools.** Dependabot leaves `.github/lint-requirements.txt` and
    `.github/audit-env-requirements.txt` alone (ADR-0423). Regenerate the first with `--upgrade`
    (docs/TESTING.md, "Updating the lint tools"), then move the second to the same pip and
    packaging and to the newest setuptools and wheel that are a week old ("Updating the audit
    environment"); `make test` compares pip's and packaging's hashes with those of the first,
    `make setup` installs the first and `make audit` the second on the owner's machine, and pip
    checks every hash there. The gitleaks image of `scripts/secret-scan.sh` moves to the digest
    of a newer release's index, then `make secret-scan` runs (ADR-0445). The trivy image of `scripts/image-scan.sh`
    moves to the digest of a newer release's index that is a week old, then `make image-scan` runs
    (ADR-0446). ESLint, Prettier and their plugins in `webui/package.json` move to their newest
    releases that are a week old, with what the new ESLint finds fixed and the new Prettier's
    format written in the same change (docs/TESTING.md, "Updating the lint tools"; ADR-0449).

## After a session that went badly

Right after it, while the details are fresh: what would have prevented the trouble (a check, a
line in an AGENTS.md file, a skill step) goes into a change in that same session.
