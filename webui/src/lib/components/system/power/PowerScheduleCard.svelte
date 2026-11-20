<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { updatePowerConfig } from "$lib/api/power";
  import { Alert, Button, Card, Skeleton } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { PowerPlan } from "./plan.svelte";

  // The power settings' daily shutdown, daily wake and idle shutdown (ADR-0407): three rows of one
  // shape, each an on/off with its title and one line of help on the left and, while it is on, its
  // field in one column on the right. The plan they make is the Status card's.
  type Props = {
    plan: PowerPlan;
    // The settings cannot be read, for the reason the page shows once.
    unavailable: boolean;
    // The settings are saved: what the page says.
    onnotice: (message: string) => void;
  };

  let { plan, unavailable, onnotice }: Props = $props();

  /** "HH:MM" as hour and minute; null for another text. */
  function parseTime(value: string): [number, number] | null {
    const match = /^(\d{2}):(\d{2})$/.exec(value);
    if (!match) return null;
    const [hour, minute] = [Number(match[1]), Number(match[2])];
    return hour < 24 && minute < 60 ? [hour, minute] : null;
  }

  async function saveSchedule() {
    const config = plan.config;
    if (!config || plan.saving) return;
    // Only the fields shown are checked: a row that is off hides its field, which keeps the stored
    // value when what it holds is not one.
    const { autoShutdown, autoWakeup, idleShutdown } = plan.form;
    const shutdownAt = parseTime(plan.form.shutdownTime) ?? (autoShutdown ? null : [config.shutdown_hour, config.shutdown_minute]);
    const wakeAt = parseTime(plan.form.wakeTime) ?? (autoWakeup ? null : [config.wakeup_hour, config.wakeup_minute]);
    const typed = Number(plan.form.idleMinutes);
    const minutesValid = String(plan.form.idleMinutes ?? "") !== "" && Number.isInteger(typed) && typed >= 20 && typed <= 1440;
    const minutes = minutesValid ? typed : idleShutdown ? null : config.idle_timeout_minutes;
    if (!shutdownAt || !wakeAt || minutes === null) {
      plan.saveError = $t("power.schedule.badTime");
      return;
    }
    plan.saving = true;
    plan.saveError = "";
    try {
      plan.config = await updatePowerConfig({
        ...config,
        auto_shutdown: autoShutdown,
        shutdown_hour: shutdownAt[0],
        shutdown_minute: shutdownAt[1],
        auto_wakeup: autoWakeup,
        wakeup_hour: wakeAt[0],
        wakeup_minute: wakeAt[1],
        idle_shutdown: idleShutdown,
        idle_timeout_minutes: minutes,
      });
      plan.fillForm(plan.config);
      onnotice($t("power.schedule.saved"));
      await plan.loadSchedule();
    } catch (e) {
      plan.saveError = messageOf(e);
    } finally {
      plan.saving = false;
    }
  }

  // The RTC of this host cannot wake it: said under the wake's row while that row is on.
  let rtcProblem = $derived(plan.schedule && !plan.schedule.wake.rtc.available ? (plan.schedule.wake.rtc.reason ?? "") : null);
</script>

<!-- The rows share one grid: the on/off, its title and help, and its field in a column of its own,
     so that the three fields stand on one line whatever the length of the titles. The checkbox is
     the place of the Switch of the design system. -->
<Card title={$t("power.schedule.title")} description={$t("power.schedule.hint")}>
  {#if unavailable}
    <p class="text-sm text-content-muted">{$t("power.unavailable")}</p>
  {:else if plan.configError}
    <Alert variant="error" size="sm" title={$t("power.schedule.configError")}>{plan.configError}</Alert>
  {:else if !plan.config}
    <div class="space-y-4" aria-busy="true">
      <Skeleton shape="line" count={3} />
      <Skeleton shape="block" class="h-9 w-36" />
    </div>
  {:else}
    <form
      novalidate
      onsubmit={(e) => {
        e.preventDefault();
        saveSchedule();
      }}
    >
      <div class="divide-y divide-border border-y border-border">
        <div class="grid grid-cols-[auto_minmax(0,1fr)] sm:grid-cols-[auto_minmax(0,1fr)_10.5rem] items-start gap-x-3 gap-y-2 py-3.5">
          <input id="power-auto-shutdown" type="checkbox" class="form-checkbox mt-0.5 h-4 w-4"
            aria-describedby="power-auto-shutdown-hint" bind:checked={plan.form.autoShutdown} />
          <div class="min-w-0">
            <label for="power-auto-shutdown" class="text-sm font-medium text-content">{$t("power.schedule.shutdownEvery")}</label>
            <p id="power-auto-shutdown-hint" class="mt-0.5 text-[13px] leading-[18px] text-content-muted">{$t("power.schedule.shutdownHint")}</p>
          </div>
          {#if plan.form.autoShutdown}
            <div class="col-start-2 sm:col-start-3 sm:row-start-1">
              <input type="time" class="form-input w-36" aria-label={$t("power.schedule.shutdownTime")}
                bind:value={plan.form.shutdownTime} />
            </div>
          {/if}
        </div>

        <div class="grid grid-cols-[auto_minmax(0,1fr)] sm:grid-cols-[auto_minmax(0,1fr)_10.5rem] items-start gap-x-3 gap-y-2 py-3.5">
          <input id="power-auto-wakeup" type="checkbox" class="form-checkbox mt-0.5 h-4 w-4"
            aria-describedby="power-auto-wakeup-hint" bind:checked={plan.form.autoWakeup} />
          <div class="min-w-0">
            <label for="power-auto-wakeup" class="text-sm font-medium text-content">{$t("power.schedule.wakeEvery")}</label>
            <p id="power-auto-wakeup-hint" class="mt-0.5 text-[13px] leading-[18px] text-content-muted">{$t("power.schedule.wakeHint")}</p>
          </div>
          {#if plan.form.autoWakeup}
            <div class="col-start-2 sm:col-start-3 sm:row-start-1">
              <input type="time" class="form-input w-36" aria-label={$t("power.schedule.wakeTime")}
                bind:value={plan.form.wakeTime} />
            </div>
            {#if rtcProblem !== null}
              <!-- A plain sentence first; the host's reason, technical, on demand. -->
              <div class="col-start-2 sm:col-span-2 text-[13px] leading-[18px]" data-testid="power-rtc-problem">
                <p class="text-status-warning">{$t("power.schedule.rtcCannotWake")}</p>
                {#if rtcProblem}
                  <details class="mt-1 text-content-secondary">
                    <summary class="link w-fit cursor-pointer">{$t("power.details")}</summary>
                    <p class="mt-1 mono text-xs break-words">{rtcProblem}</p>
                  </details>
                {/if}
              </div>
            {/if}
          {/if}
        </div>

        <div class="grid grid-cols-[auto_minmax(0,1fr)] sm:grid-cols-[auto_minmax(0,1fr)_10.5rem] items-start gap-x-3 gap-y-2 py-3.5">
          <input id="power-idle-shutdown" type="checkbox" class="form-checkbox mt-0.5 h-4 w-4"
            aria-describedby="power-idle-shutdown-hint" bind:checked={plan.form.idleShutdown} />
          <div class="min-w-0">
            <label for="power-idle-shutdown" class="text-sm font-medium text-content">{$t("power.schedule.idleAfter")}</label>
            <p id="power-idle-shutdown-hint" class="mt-0.5 text-[13px] leading-[18px] text-content-muted">{$t("power.schedule.idleHint")}</p>
          </div>
          {#if plan.form.idleShutdown}
            <div class="col-start-2 sm:col-start-3 sm:row-start-1 flex items-center gap-2">
              <input type="number" min="20" max="1440" class="form-input w-24 tabular"
                aria-label={$t("power.schedule.idleMinutes")} bind:value={plan.form.idleMinutes} />
              <span class="text-sm text-content-secondary">{$t("power.schedule.minutes")}</span>
            </div>
          {/if}
        </div>
      </div>

      <details class="mt-3 text-[13px] leading-[18px] text-content-secondary">
        <summary class="link w-fit cursor-pointer">{$t("power.schedule.more")}</summary>
        <p class="mt-2">{$t("power.schedule.moreJobs")}</p>
        {#if plan.schedule?.wake.rtc.available}
          <!-- Only for an RTC that takes the alarm: one that cannot is said under the wake's row. -->
          <p class="mt-2">{$t("power.schedule.rtcReady")}</p>
        {/if}
      </details>

      {#if plan.saveError}
        <div role="alert" class="mt-4">
          <Alert variant="error" size="sm" title={$t("power.schedule.saveFailed")}>{plan.saveError}</Alert>
        </div>
      {/if}
      <div class="mt-4 flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" loading={plan.saving} disabled={!plan.changed}
          aria-describedby={plan.changed ? undefined : "power-schedule-unchanged"}>
          {$t("power.schedule.save")}
        </Button>
        {#if !plan.changed}
          <span id="power-schedule-unchanged" class="text-[13px] text-content-muted">{$t("power.schedule.unchanged")}</span>
        {/if}
      </div>
    </form>
  {/if}
</Card>

