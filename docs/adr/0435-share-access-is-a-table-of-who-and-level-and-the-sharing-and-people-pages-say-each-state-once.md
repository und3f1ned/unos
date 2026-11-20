---
status: accepted
date: 2026-11-20
area: webui
decided-by: agent
---

# ADR-0435: Share access is a table of who and level, and the sharing and people pages say each state once

## Context

The owner found the redesign careless ("the design is slapdash, the UX limps") and called the
share dialog's Access picker "a mess": bare user names over "No access" selects, an unreadable
"Off" badge, two "Everyone" buttons and Clear, and a standing paragraph about dropped connections.
A UX audit of the web UI listed the same kind of fault on every page of sharing and people: alert
stacks that name one cause three times, coloured badges for features, struck-through users, a red
disabled bulk Delete, a Users page that says "No users found" after a failed load and pushes its
actions off a phone's screen, an Audit Log headed "Other" with full user agents, and sign-in pages
in Title Case with the browser's own validation bubble. The owner delegated the decisions, asking
above all that they be safe; ADR-0431's rule that each setting, action and fact has one place binds
them. The entry is `webui.ux-sharing`.

## Decision

- **The share dialog's Access is a table** (`webui/src/lib/components/shares/ShareAccessTable.svelte`):
  one row per user and per group. The left column says who: an icon, the name, and its kind or
  why its access does not count yet ("SMB off" with "Turn SMB on in Users →", "SMB not on yet",
  "No SMB" with the server's reason); such a row is muted, never struck through. The right column
  is the level, None / Read / Read & write, as a radio group in the segmented look
  (`AccessLevelControl.svelte`): every row shows every choice, one Tab stop per row, the arrows
  move the choice. An **All users** row replaces the Everyone buttons and Clear: it sets every user
  who can be given access (not the groups, as the Everyone buttons did, ADR-0385) and shows their
  common level, none while they differ. Clear, which also cleared the groups, is gone: a group's
  level is set in its own row. The administrators' note is one line under the table; empty users
  and groups parts link to Users and to its Groups card (`/users#groups`). The table's links open
  in a new tab, so that the share's form stays as it is. On a phone the level goes under
  the name and its three segments fill the row. The dialog no longer scrolls inside a fixed box.
- **Dropped connections are asked about at Save, when they happen.** ADR-0103's standing note is
  gone; saving a served share that lowers or removes a user's or a group's level, turns guest
  access off, requires encryption or turns Previous Versions off first opens "Close open
  connections?", which lists each such change (`loweredAccess` in `shares/access.ts`); Cancel goes
  back to the form. As the server closes them only when smb.conf shrinks, the question counts only
  what smb.conf has: a user whose SMB is active, a group with such a member, a guest access or
  Previous Versions that smb.conf did not leave out, on a share someone can connect to. Giving
  access, and any save of a share that is not served or that nobody can connect to, asks nothing.
- **One cause, said once, on what it concerns.** A share that is not served says why on its card
  only: the status panel drops the server's "Share <name> is not served:" problem for a share the
  list shows. What needs no action (nobody can connect yet, Samba accounts UNOS did not make, the
  import's report, folded with its Dismiss) is one info panel. A card's states keep their colours
  (Not served, Pool not mounted, Permissions need repair, and "Nobody can connect yet" in place of
  a red "No access"); its features are neutral chips; a stored setting that smb.conf leaves out is
  one warning line with the server's reason under it. Connect addresses have copy buttons, which say when the browser refuses to copy. The
  SMB and NFS lists have no card whose title repeats the tab, and the bulk Delete shows only with a
  selection, as a secondary button.
- **The page header holds each tab's one main action**: Add SMB share, Add NFS export, and Add
  target, which moves out of the iSCSI card. A disabled one says why under it (no pool, no volume,
  no iSCSI in the kernel). Empty tabs offer no second Add button (ADR-0431): their text names the
  header's button and, without a pool or volume, links to Storage.
- **NFS, iSCSI and SMB interfaces in the same shape.** An export is a card of its folder and each
  client network with its access, the raw exportfs options under Options. A target leads with its
  state and its logged-in initiators, has Edit and a trash icon as a share has, and lists its facts
  as `kv`. The SMB interfaces card puts Choose interfaces… and Restart SMB… in its header as
  secondary buttons, says "SMB answers on", and its dialog folds its three notes into one
  disclosure; its Save is disabled only with nothing to save, and says so.
- **Users and groups.** A failed load shows the error with Retry, never an empty list; once loaded,
  the list stays under a later error. On a phone a row is the name with its actions, then the role,
  two-factor and SMB badges under it. Badges read alone ("SMB off", "2FA on"); the User role has a
  user icon; icon buttons have tooltips; the date is the day, the exact time its hover text. The
  About card is gone: the password rule, SMB's lack of two-factor and the role of a new user are
  hints in the create dialog. Create user and a changed Edit are never disabled for what they lack:
  pressed, each field says what is missing; Edit's Save is disabled only while nothing changed, and
  says so. Deleting a user names what it does, from the server's `delete_user`: signed out
  everywhere with the API tokens and security keys, share access and groups removed, the SMB
  account removed when there is one, the files kept. Groups get one line of help and an Add group
  button of the card header's size.
- **Audit Log.** An entry the server files under "other" (an API token's) is headed by its details;
  resource types and action names are the recorded words with spaces for underscores, in sentence
  case with acronyms in capitals ("API token", "SMB restarted", "TOTP enabled"), so that the
  search still finds an entry by what the page shows (ADR-0401): the server's search now also
  matches a resource type with spaces for its underscores. The user agent is the browser and
  system ("Firefox 144 on Android"), the whole of it the hover text; a failed entry carries one
  mark, Failed (and Critical when it is critical). On a phone the filters fold behind "Filters (n)". The
  User field suggests the NAS's users and still takes any name. Export is disabled after a failed
  load, saying why.
- **Sign-in pages.** Their texts are in sentence case; the password has a Show/Hide button. First-run
  setup validates in the page, not with the browser's bubble: hints under the fields from the
  server's rules, and each field's error under it. An account that can only set up two-factor
  authentication gets that page as the sign-in card, without the shell's empty navigation; the
  app is named once, and six digits are sent at once.

## Consequences

- Chunk 1 of the UX work (the design foundations) builds ConfirmDialog, Sheet, Menu, Toast and
  the form controls in `ui`; the pages above are built so that each swap is one change: the Save
  question and the delete dialogs to ConfirmDialog, the share dialog to a Sheet, the users' row
  icons to an action menu, the outcome alerts to toasts.
- `webui/tests/shares.spec.ts` chooses levels by radio and checks the question at Save; the other
  specs of the area follow the texts; `tests/mobile-layout.spec.ts` keeps walking the dialogs.
- No new endpoint or field and no new runtime dependency; the audit search finds a little more
  ("api token" finds `api_token` entries). The web UI's and the supervisor's images are rebuilt
  once.
