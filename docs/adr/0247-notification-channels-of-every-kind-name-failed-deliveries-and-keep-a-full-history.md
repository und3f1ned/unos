---
status: accepted
date: 2026-11-07
area: system
decided-by: agent
---

# ADR-0247: Notification channels of every kind are set up in the web UI, a delivery counts only on success, and the history lists every notification sent

## Context

System > Notifications let an administrator add e-mail and Telegram channels only; webhook, Discord and Slack channels existed in the API alone. A test's result was put into a dialog that was already closed, a webhook that answered with a redirect counted as delivered, Telegram refused a text with a single "_" (parse_mode Markdown), the history left out the monitors' alerts and listed the oldest first, and two channels added in the same second shared an id. The certificate check of ADR-0045 was on the branch `smtp-verify-tls` only; this change brings it into main with the rest.

## Decision

- `webui/src/routes/system/tabs/NotificationsTab.svelte` has a form for each kind: e-mail (with "Verify the server's certificate", on by default, ADR-0045), Telegram, webhook (its URL, and POST or PUT; UNOS sends its own JSON body), Discord and Slack (the webhook URL). Saving a form keeps what only the API sets: a webhook's headers, body template and content type, Discord's and Slack's names and icons. The list shows only the host of a webhook URL, whose path carries its secret.
- A test's outcome shows under its channel: "Test notification sent", or the server's answer `Failed to send notification: <error>`. Send test works for a disabled channel too, so that a channel can be checked before it receives notifications. With "Use TLS/STARTTLS" off on a port other than 465, the form warns that the password and the messages cross the network unencrypted, and the certificate check has nothing to check.
- `supervisor/src/notifications/router.py`: a webhook, Discord or Slack answer counts as delivered only with a 2xx status. Any other, a redirect included (the senders follow none), is a failure named `HTTP <status>: <start of the body>`. Telegram messages use parse_mode HTML, with the title and the message escaped.
- The history, `/data/notification_logs.json`, keeps the latest 100 notifications sent: each test, each `POST /api/v1/notifications/send` and each alert of the monitors (`_deliver_alert` in `supervisor/src/health/router.py`), with the channel's name, the time with its UTC offset, the result and the error. `GET /api/v1/notifications/logs` lists the newest first, and the tab reads it again after each test. A notification is recorded for all its channels in one write, to a staged file that replaces the history in one step, so a write that fails, as on a full system disk, keeps the old history; it is logged, and the notification counts as sent all the same.
- Channels and history entries get random ids (uuid4). Deleting an unknown channel answers 404 and writes nothing.
- Tests: `supervisor/tests/test_notifications.py` (real SMTP conversations, by STARTTLS and SMTPS, with a local mail server whose certificate is trusted, self-signed or for another host; a local webhook receiver; the API) and `webui/tests/notifications.spec.ts` (the route fakes of `webui/tests/notifications-fakes.ts`, whose texts the backend test compares with the server's, and one test against the stand-in's own supervisor, with a mail server that is not there).

## Consequences

- Each notification sent rewrites the history file on the system disk once, whatever the number of its channels; tests are rare, and an alert goes out once per new problem.
- A webhook URL that redirects, such as an http:// address of an https:// service, now fails its test with "HTTP 301" or "HTTP 308": the channel needs the final URL.
- The deliveries are proven against local servers and route fakes only: no real mail server, Telegram or webhook service has been reached from UNOS (Telegram from the dev device: ADR-0046).
- `GET /api/v1/notifications/configs` still returns the channels' secrets (the SMTP password, the bot token, the webhook URLs) to administrators, as before.
- Plain SMTP still sends the password unencrypted, with a warning only: whether UNOS refuses a login without TLS is the owner's decision.
- The alerts still reach e-mail and Telegram channels only; webhooks, Discord and Slack get them with `system.alert-events` (ADR-0047).
