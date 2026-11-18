<script lang="ts">
  import { onMount } from "svelte";
  import { CalendarClock, Moon, Network, Power, RotateCcw } from "lucide-svelte";
  import {
    cancelShutdown,
    disableWol,
    enableWol,
    getPendingAction,
    getPowerConfig,
    getPowerSchedule,
    getWolStatus,
    rebootSystem,
    shutdownSystem,
    suspendSystem,
    updatePowerConfig,
    type PendingAction,
    type PowerAnswer,
    type PowerConfig,
    type PowerFinding,
    type PowerSchedule,
    type WolInterface,
  } from "$lib/api/power";
  import { Alert, Badge, Button, Card, EmptyState, Modal } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime, formatTimeOfDay } from "$lib/time";
  import { paletteRequest, type PaletteDialog, type PaletteRequest } from "$lib/stores/palette";

  // Reboot, shutdown, suspend and Wake-on-LAN act on the host (ADR-0233):
  // reboot and shutdown one minute after the confirmation, so that they can
  // still be cancelled here; suspend at once.

  type Action = "reboot" | "shutdown" | "suspend";

  // The confirmation of each action: the keys of its texts in the catalog.
  const ACTIONS: Record<
    Action,
    { titleKey: string; textKey: string; confirmKey: string; failedKey: string; variant: "warning" | "danger"; run: () => Promise<PowerAnswer> }
  > = {
    reboot: {
      titleKey: "power.reboot.title",
      textKey: "power.reboot.text",
      confirmKey: "power.reboot.confirm",
      failedKey: "power.reboot.failed",
      variant: "warning",
      run: rebootSystem,
    },
    shutdown: {
      titleKey: "power.shutdown.title",
      textKey: "power.shutdown.text",
      confirmKey: "power.shutdown.confirm",
      failedKey: "power.shutdown.failed",
      variant: "danger",
      run: shutdownSystem,
    },
    suspend: {
      titleKey: "power.suspend.title",
      textKey: "power.suspend.text",
      confirmKey: "power.suspend.confirm",
      failedKey: "power.suspend.failed",
      variant: "danger",
      run: suspendSystem,
    },
  };

  let pending: PendingAction | null = null;
  let pendingError = "";
  let interfaces: WolInterface[] = [];
  let wolLoading = true;
  let wolError = "";

  let asking: Action | null = null;
  let busy = false;
  let confirmError = "";
  let cancelling = false;
  let wolBusy: string | null = null;
  let notice = "";
  let actionError = "";

  // The scheduled shutdown, the wake by the RTC and the idle shutdown of the power settings
  // (ADR-0407): their times are on the host's clock, and the server acts on them.
  let config: PowerConfig | null = null;
  let configError = "";
  let schedule: PowerSchedule | null = null;
  let scheduleError = "";
  let form = { autoShutdown: false, shutdownTime: "23:00", autoWakeup: false, wakeTime: "07:00", idleShutdown: false, idleMinutes: 30 };
  let saving = false;
  let saveError = "";

  onMount(() => {
    void loadPending();
    void loadWol();
    void loadConfig();
    void loadSchedule();
  });

  function clockTime(hour: number, minute: number): string {
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }

  function fillForm(settings: PowerConfig) {
    form = {
      autoShutdown: settings.auto_shutdown,
      shutdownTime: clockTime(settings.shutdown_hour, settings.shutdown_minute),
      autoWakeup: settings.auto_wakeup,
      wakeTime: clockTime(settings.wakeup_hour, settings.wakeup_minute),
      idleShutdown: settings.idle_shutdown,
      idleMinutes: settings.idle_timeout_minutes,
    };
  }

  async function loadConfig() {
    try {
      config = await getPowerConfig();
      fillForm(config);
      configError = "";
    } catch (e) {
      configError = messageOf(e);
    }
  }

  async function loadSchedule() {
    try {
      schedule = await getPowerSchedule();
      scheduleError = "";
    } catch (e) {
      scheduleError = messageOf(e);
    }
  }

  /** "HH:MM" as hour and minute; null for another text. */
  function parseTime(value: string): [number, number] | null {
    const match = /^(\d{2}):(\d{2})$/.exec(value);
    if (!match) return null;
    const [hour, minute] = [Number(match[1]), Number(match[2])];
    return hour < 24 && minute < 60 ? [hour, minute] : null;
  }

  async function saveSchedule() {
    if (!config || saving) return;
    const shutdownAt = parseTime(form.shutdownTime);
    const wakeAt = parseTime(form.wakeTime);
    const minutes = Number(form.idleMinutes);
    if (!shutdownAt || !wakeAt || !Number.isInteger(minutes) || minutes < 20 || minutes > 1440) {
      saveError = $t("power.schedule.badTime");
      return;
    }
    saving = true;
    saveError = "";
    try {
      config = await updatePowerConfig({
        ...config,
        auto_shutdown: form.autoShutdown,
        shutdown_hour: shutdownAt[0],
        shutdown_minute: shutdownAt[1],
        auto_wakeup: form.autoWakeup,
        wakeup_hour: wakeAt[0],
        wakeup_minute: wakeAt[1],
        idle_shutdown: form.idleShutdown,
        idle_timeout_minutes: minutes,
      });
      fillForm(config);
      notice = $t("power.schedule.saved");
      actionError = "";
      await loadSchedule();
    } catch (e) {
      saveError = messageOf(e);
    } finally {
      saving = false;
    }
  }

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

  $: describe = (finding: PowerFinding): string => {
    const key = (finding.subject === null && FOUND_WITHOUT_SUBJECT[finding.kind]) || FOUND[finding.kind] || FOUND.unreadable;
    const mib = ((finding.amount ?? 0) / 1048576).toLocaleString($intlLocale, { maximumFractionDigits: 1 });
    return $t(key, { subject: finding.subject ?? "", detail: finding.detail ?? "", n: finding.amount ?? 0, mib });
  };
  $: when = (at: string | null): string =>
    formatDateTime(at, $intlLocale, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  function messageOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function loadPending() {
    try {
      pending = await getPendingAction();
      pendingError = "";
    } catch (e) {
      pendingError = messageOf(e);
    }
  }

  async function loadWol() {
    try {
      interfaces = (await getWolStatus()).interfaces;
      wolError = "";
    } catch (e) {
      wolError = messageOf(e);
    } finally {
      wolLoading = false;
    }
  }

  function ask(action: Action) {
    asking = action;
    confirmError = "";
  }

  // The command palette's Reboot, Shut down and Suspend open their confirmation here, which acts
  // only once confirmed (ADR-0393). A function, so that the statement depends on the request alone.
  const REQUESTED: Partial<Record<PaletteDialog, Action>> = { reboot: "reboot", shutdown: "shutdown", suspend: "suspend" };
  $: openRequested($paletteRequest);

  function openRequested(request: PaletteRequest | null) {
    const action = request ? REQUESTED[request.dialog] : undefined;
    if (!action) return;
    paletteRequest.set(null);
    if (!busy) ask(action);
  }

  function closeConfirm() {
    if (!busy) {
      asking = null;
      confirmError = "";
    }
  }

  async function confirmAction() {
    if (!asking || busy) return;
    const action = ACTIONS[asking];
    busy = true;
    confirmError = "";
    try {
      const answer = await action.run();
      notice = answer.message;
      actionError = "";
      if (answer.pending) {
        pending = answer.pending;
        pendingError = "";
      }
      asking = null;
    } catch (e) {
      // The dialog stays open with the host's answer.
      confirmError = messageOf(e);
    } finally {
      busy = false;
    }
  }

  async function cancelPending() {
    if (cancelling) return;
    cancelling = true;
    try {
      const answer = await cancelShutdown();
      notice = answer.message;
      actionError = "";
      pending = answer.pending;
    } catch (e) {
      notice = "";
      actionError = messageOf(e);
      await loadPending();
    } finally {
      cancelling = false;
    }
  }

  async function setWol(iface: WolInterface, on: boolean) {
    if (wolBusy) return;
    wolBusy = iface.name;
    try {
      const updated = on ? await enableWol(iface.name) : await disableWol(iface.name);
      interfaces = interfaces.map((i) => (i.name === updated.name ? updated : i));
      notice = $t(updated.enabled ? "power.wol.turnedOn" : "power.wol.turnedOff", { name: updated.name });
      actionError = "";
    } catch (e) {
      notice = "";
      actionError = messageOf(e);
      // Show what the host has now.
      await loadWol();
    } finally {
      wolBusy = null;
    }
  }

  // In the language of the page; reactive, so that a pending action follows a change of the language.
  $: timeOf = (at: string): string => formatTimeOfDay(at, $intlLocale, { hour: "2-digit", minute: "2-digit" });

  $: confirmation = asking ? ACTIONS[asking] : null;
</script>

<div class="space-y-6">
  <h2 class="text-xl font-bold">{$t("power.title")}</h2>

  {#if notice}
    <div role="status">
      <Alert variant="success" dismissible on:dismiss={() => (notice = "")}>{notice}</Alert>
    </div>
  {/if}
  {#if actionError}
    <div role="alert">
      <Alert variant="error" dismissible on:dismiss={() => (actionError = "")}>{actionError}</Alert>
    </div>
  {/if}

  <Card>
    <h3 class="section-title mb-4">
      <Power class="w-5 h-5" />
      {$t("power.actions.title")}
    </h3>
    <div class="space-y-4">
      {#if pendingError}
        <Alert variant="error" title={$t("power.pending.readError")}>{pendingError}</Alert>
      {:else if pending?.action}
        {@const reboot = pending.action === "reboot"}
        <Alert variant="warning" title={reboot ? $t("power.pending.reboot") : $t("power.pending.shutdown")}>
          <div class="flex flex-wrap items-center gap-3">
            <p>
              {#if pending.at}
                {$t(reboot ? "power.pending.rebootAt" : "power.pending.shutdownAt", { time: timeOf(pending.at) })}
              {:else}
                {reboot ? $t("power.pending.rebootSoon") : $t("power.pending.shutdownSoon")}
              {/if}
            </p>
            <Button variant="secondary" size="sm" loading={cancelling} on:click={cancelPending}>
              {reboot ? $t("power.pending.cancelReboot") : $t("power.pending.cancelShutdown")}
            </Button>
          </div>
        </Alert>
      {/if}
      <p class="text-sm text-secondary">{$t("power.actions.hint")}</p>
      <div class="flex flex-wrap gap-3">
        <Button variant="warning" icon={RotateCcw} on:click={() => ask("reboot")}>{$t("power.reboot.button")}</Button>
        <Button variant="danger" icon={Power} on:click={() => ask("shutdown")}>{$t("power.shutdown.button")}</Button>
        <Button variant="secondary" icon={Moon} on:click={() => ask("suspend")}>{$t("power.suspend.button")}</Button>
      </div>
    </div>
  </Card>

  <Card>
    <h3 class="section-title mb-4">
      <CalendarClock class="w-5 h-5" />
      {$t("power.schedule.title")}
    </h3>
    <div class="space-y-4">
      <p class="text-sm text-secondary">{$t("power.schedule.hint")}</p>
      {#if configError}
        <Alert variant="error" title={$t("power.schedule.configError")}>{configError}</Alert>
      {:else if !config}
        <p class="text-sm text-muted">{$t("common.loading")}</p>
      {:else}
        <form class="space-y-3" novalidate on:submit|preventDefault={saveSchedule}>
          {#if schedule}
            <p class="text-sm text-muted">{$t("power.schedule.zone", { zone: schedule.timezone })}</p>
          {/if}
          <div class="flex flex-wrap items-center gap-3">
            <input
              type="checkbox"
              id="power-auto-shutdown"
              bind:checked={form.autoShutdown}
              class="w-4 h-4 rounded bg-surface border-border-strong text-brand-ring focus:ring-brand-ring"
            />
            <label for="power-auto-shutdown" class="text-sm">{$t("power.schedule.shutdownEvery")}</label>
            <input
              type="time"
              class="form-input w-32"
              aria-label={$t("power.schedule.shutdownTime")}
              bind:value={form.shutdownTime}
            />
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <input
              type="checkbox"
              id="power-auto-wakeup"
              bind:checked={form.autoWakeup}
              class="w-4 h-4 rounded bg-surface border-border-strong text-brand-ring focus:ring-brand-ring"
            />
            <label for="power-auto-wakeup" class="text-sm">{$t("power.schedule.wakeEvery")}</label>
            <input type="time" class="form-input w-32" aria-label={$t("power.schedule.wakeTime")} bind:value={form.wakeTime} />
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <input
              type="checkbox"
              id="power-idle-shutdown"
              bind:checked={form.idleShutdown}
              class="w-4 h-4 rounded bg-surface border-border-strong text-brand-ring focus:ring-brand-ring"
            />
            <label for="power-idle-shutdown" class="text-sm">{$t("power.schedule.idleAfter")}</label>
            <input
              type="number"
              min="20"
              max="1440"
              class="form-input w-24"
              aria-label={$t("power.schedule.idleMinutes")}
              bind:value={form.idleMinutes}
            />
            <span class="text-sm text-secondary">{$t("power.schedule.minutes")}</span>
          </div>
          {#if saveError}
            <div role="alert">
              <Alert variant="error" title={$t("power.schedule.saveFailed")}>{saveError}</Alert>
            </div>
          {/if}
          <Button type="submit" variant="primary" loading={saving}>{$t("power.schedule.save")}</Button>
        </form>
      {/if}

      {#if scheduleError}
        <Alert variant="error" title={$t("power.schedule.readError")}>{scheduleError}</Alert>
      {:else if schedule}
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
                <ul class="list-disc pl-5">
                  {#each schedule.shutdown.held_by as finding}
                    <li class="break-words">{describe(finding)}</li>
                  {/each}
                </ul>
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
              <ul class="list-disc pl-5">
                {#each schedule.idle.found as finding}
                  <li class="break-words">{describe(finding)}</li>
                {/each}
              </ul>
            {/if}
            {#if !schedule.idle.enabled}
              <p class="text-muted">{$t("power.schedule.idleOff")}</p>
            {/if}
          </dd>
        </dl>
      {/if}
    </div>
  </Card>

  <Card>
    <h3 class="section-title mb-4">
      <Network class="w-5 h-5" />
      {$t("power.wol.title")}
    </h3>
    {#if wolLoading}
      <p class="text-sm text-muted">{$t("common.loading")}</p>
    {:else if wolError}
      <Alert variant="error" title={$t("power.wol.loadError")}>{wolError}</Alert>
    {:else if interfaces.length === 0}
      <EmptyState icon={Network} title={$t("power.wol.none")} />
    {:else}
      <div class="table-frame">
        <table class="table">
          <thead class="table-header">
            <tr>
              <th>{$t("power.wol.interface")}</th>
              <th>{$t("power.wol.mac")}</th>
              <th>{$t("power.wol.title")}</th>
              <th><span class="sr-only">{$t("power.wol.change")}</span></th>
            </tr>
          </thead>
          <tbody>
            {#each interfaces as iface (iface.name)}
              <tr class="table-row">
                <td class="mono">{iface.name}</td>
                <td class="mono">{iface.mac_address ?? $t("power.wol.unknownMac")}</td>
                <td>
                  {#if !iface.supported}
                    <Badge variant="neutral">{$t("power.wol.notSupported")}</Badge>
                  {:else if iface.enabled}
                    <Badge variant="success">{$t("power.wol.on")}</Badge>
                  {:else}
                    <Badge variant="neutral">{$t("power.wol.off")}</Badge>
                  {/if}
                </td>
                <td class="text-right">
                  {#if iface.supported}
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={wolBusy === iface.name}
                      disabled={wolBusy !== null && wolBusy !== iface.name}
                      on:click={() => setWol(iface, !iface.enabled)}
                    >
                      {iface.enabled ? $t("power.wol.turnOff") : $t("power.wol.turnOn")}<span class="sr-only"
                        >{$t("power.wol.forInterface", { name: iface.name })}</span
                      >
                    </Button>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="text-sm text-muted mt-3">
        {$t("power.wol.hintBefore")}
        <span class="mono">{$t("power.wol.command")}</span>{$t("power.wol.hintAfter")}
      </p>
    {/if}
  </Card>
</div>

<Modal
  open={confirmation !== null}
  title={confirmation ? $t(confirmation.titleKey) : ""}
  size="md"
  danger
  closable={!busy}
  on:close={closeConfirm}
>
  <p>{confirmation ? $t(confirmation.textKey) : ""}</p>
  {#if confirmError && confirmation}
    <div role="alert" class="mt-4">
      <Alert variant="error" title={$t(confirmation.failedKey)}>{confirmError}</Alert>
    </div>
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" disabled={busy} on:click={closeConfirm}>{$t("common.cancel")}</Button>
    <Button variant={confirmation?.variant ?? "danger"} class="flex-1" loading={busy} on:click={confirmAction}>
      {confirmation ? $t(confirmation.confirmKey) : ""}
    </Button>
  </svelte:fragment>
</Modal>
