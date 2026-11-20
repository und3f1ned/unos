---
status: superseded
superseded-by: ADR-0437
date: 2026-11-20
area: webui
decided-by: agent
supersedes: [ADR-0174, ADR-0234, ADR-0238, ADR-0337, ADR-0389, ADR-0395, ADR-0409, ADR-0420, ADR-0428]
---

# ADR-0431: Every account's own settings are on the Account page, System is the NAS's, and each setting, action and fact has one place

## Context

Superseded by ADR-0437: only in part, the Containers item: the state filter is one segmented control whose segments carry the counts, in place of the state select and the count cards; every other rule here stays in force.

The owner asked, in translation: "why are the theme and language settings in Security Settings? Go
through all the tabs and make the menus logical", and then: "check every duplicate of every menu
and remove them all". System > Security mixed the signed-in account's own settings (password,
two-factor authentication, API tokens, sign-in history, SMB access, theme, language) with the
NAS's (firewall, certificates, the two-factor requirement); a regular account saw System as its
Account page; `/settings` only led to System; and several settings, actions and facts had two or
more places (the version in General and Updates, the dashboard's Quick Actions beside the sidebar,
Live badges beside the sidebar's connection line). The owner delegated the decisions; the entry is
`webui.information-architecture`.

## Decision

- **The Account page** (`webui/src/routes/account/+page.svelte`) holds every signed-in account's
  own settings, in tabs in the System page's look (`webui/src/lib/components/layout/PageTabs.svelte`,
  a column from `xl`, chips above below it) with a hash each: **Sign-in and security**
  (`#security`, the default: Change Password, Two-Factor Authentication with its recovery codes
  and security keys, API Tokens, Login History), **File sharing** (`#sharing`: the account's SMB
  access, for regular accounts only, since an administrator never has SMB, ADR-0117) and
  **Appearance and language** (`#appearance`: Theme, Language). Tabs, not sections of one page:
  Sign-in and security alone is several screens long on a phone, and the owner's complaint was
  that theme and language were buried under it. Without a hash a page with tabs opens its first
  tab, and a hash the account has no tab for (`/account#sharing` of an administrator) shows no
  other (`webui/src/lib/components/layout/tabs.ts`). The account block at the foot of the sidebar is
  an administrator's link there ("Your account"); a regular account's navigation keeps its
  **Account** link, and its account block is no second one. The Account page that ADR-0287,
  ADR-0386, ADR-0392 and ADR-0405 name is this page; their rules stay.
- **Each card's real scope decided its place.** The account's own password, two-factor
  authentication and SMB access (`/auth/me/...`) and its API tokens (the server lists the caller's
  only) moved to the Account page. The login history answered an administrator every account's
  attempts: `GET /api/v1/security/login-history` takes `mine=true`, the caller's own attempts as a
  regular account always gets them (since the account has had its name, ADR-0263, ADR-0368), which
  the Account page asks for. Every account's sign-ins are the Audit Log's, whose `login_success`
  and `login_failed` entries are a second record of the same attempts, filtered by user there: so
  that it holds every failed one, `POST /api/v1/auth/login` now audits each refused second step
  (a wrong or reused two-factor code, a wrong recovery code, every refused security key) as a
  `login_failed` warning with the login history's reason and never the code, as it audited a wrong
  password and a key that may be copied. An administrator thus still sees an account whose
  password is known being probed; System > Security lists no sign-ins, which would repeat the Audit
  Log's. No list of the account's own sessions exists in the API; it is no part of this change.
- **System** (`webui/src/routes/system/+page.svelte`) is the NAS's and administrators' only
  (`/system` in `ADMIN_ONLY_ROUTES` of `webui/src/lib/stores/auth.ts`): **Settings**, what the NAS
  is set to (General, Network, Remote Access, Security, Notifications, which configures channels
  and moves here from Monitoring), **Monitoring**, what it does (Resource Monitor, System Logs,
  Disk Health), **Maintenance**, what is done to it (Updates, Backup & Restore, Scheduler, Power
  Management). Security holds the two-factor requirement for every account, the firewall and the
  HTTPS certificates under the heading "Security of the NAS". The apps' local names reach the LAN only, so they move from Remote Access to Network.
  `/settings` is removed (ADR-0207).
- **One place** for each setting, action and fact: one editable home for a setting or action, one
  home for a fact; the Dashboard may summarize a fact and link to its home (its stats tiles link to
  Resource Monitor and its host line to General, for administrators); the command palette is an
  index that opens places, one entry per place, and changes nothing itself. A value shown to use or
  explain another feature where it is (the time zone of a schedule, the MAC address a wake packet
  goes to, the device of a disk's health row, the two sides of a group's share access) is no second
  home. Removed as duplicates, with the home they keep:
  1. The palette's General (System opens it), its language switch (Account > Appearance and
     language, which a language's name finds in either language) and its Logout (the account
     block's).
  2. The dashboard's Quick Actions (File Browser, Manage Shares, App Store, Settings: the sidebar's
     links), and in its Containers card the rows' links, Create your first container and View all
     (its Manage link); its Stopped count now counts the exited containers, as the Containers page.
  3. The Trash's Files button (the sidebar's Files).
  4. On Containers: the count cards as filters (the state select; the counts stay), the empty
     state's Create Container (the header's), the drawer's Stop, Restart, Start, Remove and Open in
     browser (the row's or the card's, which gets the Restart a table row has); the drawer keeps the
     overview, the log and the ports.
  5. In the App Store: the header's installed count (the Installed tab's), Featured Apps (the
     catalog grid), a catalog card's running dot (its state badge) and Open (the Installed card's),
     the Installed tab's Browse Catalog (the Catalog tab).
  6. In General: the host name of System Information (the Name and time zone field), the UNOS
     Version card and its Check for Updates link (Updates), the Current User card and its Manage
     Users link (the account block and the sidebar's Users).
  7. In Network: the summary cards Default Gateway (Routes' default route), DNS Servers (DNS
     Configuration, now always shown) and Active Connections (the Connections view's count), and
     its Live badge and Resource Monitor's (the sidebar's connection line).
  8. In Updates: the version in "Update available" (the Current version card above).
  9. In Backup & Restore: the off-device copy's Add a daily copy at 03:00 (the Scheduler's New
     Task, which the card links to).
  10. In Users, the password fields of an administrator's own row (the Account page's Change
     Password, which asks for the current one; the row links there). The own row's rename stays:
     the Account page has none.
  11. The list of every account's sign-in attempts that an administrator's Login History was (the
     Audit Log's sign-in entries, which now hold the refused second steps too).
  `webui/tests/information-architecture.spec.ts` checks the places; the specs that reached a moved
  setting open its new place. The steps of `auth.login-history` (Account > Sign-in and security >
  Login History) and `webui.system-tabs` (`/system` opens General) name the new places.
- **Superseded in part.** ADR-0428: the System page's groups and its regular-account view,
  Security's two runs, the user block's link and the Quick Actions grid, with the rules of ADR-0393
  that it kept (the palette's own language and Logout actions, General's entry and a regular
  user's Account at `/system#security`). ADR-0174: the SMB card's place (now File sharing, regular
  accounts only). ADR-0234: "General shows the same version". ADR-0337: the button that adds a
  daily task. ADR-0389: the drawer's actions. ADR-0395: `quick-actions` in the widgets. ADR-0420:
  the local names' tab. ADR-0238 and ADR-0409: a refused recovery code and a refused key's answer
  went to the login history only; they are audited too. Every other rule of these stays.

## Consequences

- A new setting first answers whose it is: the account's goes to the Account page, the NAS's to a
  System tab; a new card that repeats another place's setting, action or fact needs the reason why
  it is no duplicate.
- The command palette offers one choice fewer per page with tabs, and none that acts by itself.
- An administrator reads the own sign-ins on the Account page and every account's in the Audit
  Log; a renamed administrator's own list starts with the current name, as a user's does. A refused
  second step writes one audit line more, as a wrong password does: an event, not a loop.
- The components move with the decision: `components/account/` holds the account's cards,
  `components/system/security/` the firewall, the requirement and the certificates.
- The web UI's image is rebuilt once on the dev device, and the supervisor's for `mine`, a query
  parameter that writes nothing, and the audit of the refused second steps.
