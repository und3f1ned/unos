---
status: proposed
date: 2026-11-05
area: system
decided-by: agent
---

# ADR-0047: Send alerts to every channel through an event bus, with a health banner in the UI

## Context

A NAS has to tell its owner when a disk fails. The roadmap of February 2026 already listed an event bus as an architecture idea, and the November 2026 audit found the alerts fake: nothing generated notification events (they went out only on an explicit `POST /api/v1/notifications/send`), automatic alerts could not reach Discord, Slack or webhooks, and Telegram rejects messages that contain `_` when they are sent with `parse_mode` Markdown, as the sender still does. Since Phase 0 the RAID and SMART monitors run (ADR-0043) and send their alerts directly to the enabled e-mail and Telegram channels (`_deliver_alert` in `supervisor/src/health/router.py`).

## Decision

Proposed for Phase 1: one event bus that sends notifications to every configured channel (e-mail, Telegram, Discord, Slack, webhook), and a persistent health banner in the web UI. Its signals: a pool not mounted or degraded, a failed SMART check, scrub errors, low space (on the system disk too) and missed scheduled tasks.

## Consequences

- Until then only RAID and SMART-trend alerts are generated, only the e-mail and Telegram channels receive them, and the web UI shows no health banner.
- New monitors should wait for this decision instead of adding another direct path to the channels.
- Telegram alerts from the dev device also depend on ADR-0046.
