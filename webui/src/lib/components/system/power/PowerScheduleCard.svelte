<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { CalendarClock } from "lucide-svelte";
  import { updatePowerConfig } from "$lib/api/power";
  import { Alert, Button, Card } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import PowerPlanList from "./PowerPlanList.svelte";
  import type { PowerPlan } from "./plan.svelte";

  // The power settings' scheduled shutdown, wake and idle shutdown, and the plan the server made
  // of them.
  type Props = {
    plan: PowerPlan;
    // The settings are saved: what the page says.
    onnotice: (message: string) => void;
  };

  let { plan, onnotice }: Props = $props();

  /** "HH:MM" as hour and minute; null for another text. */
  function parseTime(value: string): [number, number] | null {
    const match = /^(\d{2}):(\d{2})$/.exec(value);
    if (!match) return null;
    const [hour, minute] = [Number(match[1]), Number(match[2])];
    return hour < 24 && minute < 60 ? [hour, minute] : null;
  }

  async function saveSchedule() {
    if (!plan.config || plan.saving) return;
    const shutdownAt = parseTime(plan.form.shutdownTime);
    const wakeAt = parseTime(plan.form.wakeTime);
    const minutes = Number(plan.form.idleMinutes);
    if (!shutdownAt || !wakeAt || !Number.isInteger(minutes) || minutes < 20 || minutes > 1440) {
      plan.saveError = $t("power.schedule.badTime");
      return;
    }
    plan.saving = true;
    plan.saveError = "";
    try {
      plan.config = await updatePowerConfig({
        ...plan.config,
        auto_shutdown: plan.form.autoShutdown,
        shutdown_hour: shutdownAt[0],
        shutdown_minute: shutdownAt[1],
        auto_wakeup: plan.form.autoWakeup,
        wakeup_hour: wakeAt[0],
        wakeup_minute: wakeAt[1],
        idle_shutdown: plan.form.idleShutdown,
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
</script>

<Card>
  <h3 class="section-title mb-4">
    <CalendarClock class="w-5 h-5" />
    {$t("power.schedule.title")}
  </h3>
  <div class="space-y-4">
    <p class="text-sm text-secondary">{$t("power.schedule.hint")}</p>
    {#if plan.configError}
      <Alert variant="error" title={$t("power.schedule.configError")}>{plan.configError}</Alert>
    {:else if !plan.config}
      <p class="text-sm text-muted">{$t("common.loading")}</p>
    {:else}
      <form class="space-y-3" novalidate onsubmit={(e) => { e.preventDefault(); saveSchedule(); }}>
        {#if plan.schedule}
          <p class="text-sm text-muted">{$t("power.schedule.zone", { zone: plan.schedule.timezone })}</p>
        {/if}
        <div class="flex flex-wrap items-center gap-3">
          <input
            type="checkbox"
            id="power-auto-shutdown"
            bind:checked={plan.form.autoShutdown}
            class="w-4 h-4 rounded bg-surface border-border-strong text-brand-ring focus:ring-brand-ring"
          />
          <label for="power-auto-shutdown" class="text-sm">{$t("power.schedule.shutdownEvery")}</label>
          <input
            type="time"
            class="form-input w-32"
            aria-label={$t("power.schedule.shutdownTime")}
            bind:value={plan.form.shutdownTime}
          />
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <input
            type="checkbox"
            id="power-auto-wakeup"
            bind:checked={plan.form.autoWakeup}
            class="w-4 h-4 rounded bg-surface border-border-strong text-brand-ring focus:ring-brand-ring"
          />
          <label for="power-auto-wakeup" class="text-sm">{$t("power.schedule.wakeEvery")}</label>
          <input type="time" class="form-input w-32" aria-label={$t("power.schedule.wakeTime")} bind:value={plan.form.wakeTime} />
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <input
            type="checkbox"
            id="power-idle-shutdown"
            bind:checked={plan.form.idleShutdown}
            class="w-4 h-4 rounded bg-surface border-border-strong text-brand-ring focus:ring-brand-ring"
          />
          <label for="power-idle-shutdown" class="text-sm">{$t("power.schedule.idleAfter")}</label>
          <input
            type="number"
            min="20"
            max="1440"
            class="form-input w-24"
            aria-label={$t("power.schedule.idleMinutes")}
            bind:value={plan.form.idleMinutes}
          />
          <span class="text-sm text-secondary">{$t("power.schedule.minutes")}</span>
        </div>
        {#if plan.saveError}
          <div role="alert">
            <Alert variant="error" title={$t("power.schedule.saveFailed")}>{plan.saveError}</Alert>
          </div>
        {/if}
        <Button type="submit" variant="primary" loading={plan.saving}>{$t("power.schedule.save")}</Button>
      </form>
    {/if}


    {#if plan.scheduleError}
      <Alert variant="error" title={$t("power.schedule.readError")}>{plan.scheduleError}</Alert>
    {:else if plan.schedule}
      <PowerPlanList schedule={plan.schedule} />
    {/if}
  </div>
</Card>
