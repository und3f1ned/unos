# Security policy

## Reporting a vulnerability

Report a vulnerability privately, through GitHub's private vulnerability reporting of this
repository: <https://github.com/und3f1ned/unos/security/advisories/new> (the **Security** tab,
**Report a vulnerability**; the issue chooser links the same form). Do not open a public issue or
pull request for it.

A report helps most when it names:

- the UNOS version (the release tag, or System > Updates) and the host: distribution, version and
  architecture;
- what an attacker gains, and from where: the LAN, the internet, or a signed-in user, with the
  role (administrator or regular user);
- the steps that show it, and anything you found that limits it.

## Supported versions

| Version | Security fixes |
| --- | --- |
| The latest release ([Releases](https://github.com/und3f1ned/unos/releases/latest)) | Yes |
| Older releases | No: update to the latest one |

A fix ships in a new release, never by moving the tag of an old one. System > Updates and
`deploy/update.sh` install it ([Upgrading](docs/INSTALL.md#upgrading)).

## What to expect

UNOS is maintained by one person, with coding agents. After a report:

- a first answer as soon as the maintainer can give one, saying whether the report is confirmed
  or what is still needed;
- for a confirmed vulnerability, the fix in a release, and then a published GitHub security
  advisory that credits you, unless you ask not to be named;
- please keep the details private until that advisory is published.

The apps of the App Store run their publishers' images: report a vulnerability of an app itself
to its publisher. How UNOS installs, configures or exposes an app is in scope here.
