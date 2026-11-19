<script lang="ts">
  import type { PowerFinding, PowerSchedule } from "$lib/api/power";
  import { Badge } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";

  // The plan of the power settings: the next shutdown and what holds it back, the next wake and
  // the RTC's alarm, and whether the NAS is idle.
  type Props = {
    schedule: PowerSchedule;
  };

  let { schedule }: Props = $props();

  // The text of each finding: keys of the catalog.
  const FOUND: Record<PowerFinding["kind"], string> = {
    web_session: "power.found.web_session",
    login_session: "power.found.login_session",
    smb_client: "power.found.smb_client",
    nfs_client: "power.found.nfs_client",
    disk_activity: "power.found.disk_activity",
    task: "power.found.task",
    task_due: "power.found.task_due",
    backup: "power.found.backup",
    restore: "power.found.restore",
    scrub: "power.found.scrub",
    balance: "power.found.balance",
    disk_replace: "power.found.disk_replace",
    app_install: "power.found.app_install",
    app_update: "power.found.app_update",
    app_rollback: "power.found.app_rollback",
    offsite: "power.found.offsite",
    share_repair: "power.found.share_repair",
    update: "power.found.update",
    wake_alarm: "power.found.wake_alarm",
    unreadable: "power.found.unreadable",
  };
  const FOUND_WITHOUT_SUBJECT: Partial<Record<PowerFinding["kind"], string>> = {
    scrub: "power.found.scrubAny",
    balance: "power.found.balanceAny",
    disk_replace: "power.found.disk_replaceAny",
  };
  // The times of the plan are shown in this browser's zone, which may not be the host's.
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const STATES: Record<string, string> = {
    planned: "power.schedule.state.planned",
    announced: "power.schedule.state.announced",
    postponed: "power.schedule.state.postponed",
    issued: "power.schedule.state.issued",
  };

  let describe = $derived((finding: PowerFinding): string => {
    const key = (finding.subject === null && FOUND_WITHOUT_SUBJECT[finding.kind]) || FOUND[finding.kind] || FOUND.unreadable;
    const mib = ((finding.amount ?? 0) / 1048576).toLocaleString($intlLocale, { maximumFractionDigits: 1 });
    return $t(key, { subject: finding.subject ?? "", detail: finding.detail ?? "", n: finding.amount ?? 0, mib });
  });
  let when = $derived((at: string | null): string =>
    formatDateTime(at, $intlLocale, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }));
</script>

{#snippet findingList(findings: PowerFinding[])}
  <ul class="list-disc pl-5">
    {#each findings as finding}
      <li class="break-words">{describe(finding)}</li>
    {/each}
  </ul>
{/snippet}

<p class="text-sm text-muted">{$t("power.schedule.browserZone", { zone: browserZone })}</p>
<dl class="grid gap-3 text-sm sm:grid-cols-[auto_1fr]" aria-label={$t("power.schedule.planLabel")}>
  <dt class="text-secondary">{$t("power.schedule.nextShutdown")}</dt>
  <dd data-testid="power-next-shutdown">
    {#if schedule.shutdown.enabled && schedule.shutdown.next}
      <span>{when(schedule.shutdown.next)}</span>
      <Badge variant={schedule.shutdown.state === "postponed" ? "warning" : "info"}>
        {$t(STATES[schedule.shutdown.state] ?? STATES.planned)}
      </Badge>
      {#if schedule.shutdown.held_by.length}
        <p class="mt-1 text-secondary">{$t("power.schedule.heldBy")}</p>
        {@render findingList(schedule.shutdown.held_by)}
      {/if}
    {:else}
      {$t("power.schedule.noShutdown")}
    {/if}
  </dd>
  <dt class="text-secondary">{$t("power.schedule.nextWake")}</dt>
  <dd data-testid="power-next-wake">
    {#if !schedule.wake.rtc.available}
      <p class="text-status-warning break-words">
        {$t("power.schedule.noWakePromised", { reason: schedule.wake.rtc.reason ?? "" })}
      </p>
    {:else}
      <p>{schedule.wake.enabled && schedule.wake.next ? when(schedule.wake.next) : $t("power.schedule.noWake")}</p>
      <p class="text-secondary">
        {schedule.wake.rtc.alarm
          ? $t("power.schedule.alarmAt", { time: when(schedule.wake.rtc.alarm) })
          : $t("power.schedule.noAlarm")}
      </p>
      <p class="text-muted">{$t("power.schedule.rtcReady")}</p>
    {/if}
  </dd>
  <dt class="text-secondary">{$t("power.schedule.idleTitle")}</dt>
  <dd data-testid="power-idle">
    {#if schedule.idle.idle}
      {#if schedule.idle.since && schedule.idle.shutdown_at}
        {$t("power.schedule.idleSince", { since: when(schedule.idle.since), at: when(schedule.idle.shutdown_at) })}
      {:else}
        {$t("power.schedule.idleNow")}
      {/if}
    {:else}
      <p>{$t("power.schedule.notIdle")}</p>
      {@render findingList(schedule.idle.found)}
    {/if}
    {#if !schedule.idle.enabled}
      <p class="text-muted">{$t("power.schedule.idleOff")}</p>
    {/if}
  </dd>
</dl>
