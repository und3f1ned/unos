<script lang="ts">
  import { Server, RefreshCw, Clock, UserCog, SlidersHorizontal, Check, GripVertical, ArrowUp, ArrowDown, EyeOff, Eye, RotateCcw } from "lucide-svelte";
  import { onMount, tick } from "svelte";
  import { Alert, Button, Card } from "$lib/components/ui";
  import SystemStatsWidget from "$lib/components/widgets/SystemStatsWidget.svelte";
  import StorageWidget from "$lib/components/widgets/StorageWidget.svelte";
  import ContainersWidget from "$lib/components/widgets/ContainersWidget.svelte";
  import QuickActionsWidget from "$lib/components/widgets/QuickActionsWidget.svelte";
  import RecentActivityWidget from "$lib/components/widgets/RecentActivityWidget.svelte";

  import { getSystemInfo, type SystemInfo } from "$lib/api/system";
  import { getPools, type Pool } from "$lib/api/storage";
  import { getContainers, type Container } from "$lib/api/containers";
  import { getLogs, type NotificationLog } from "$lib/api/notifications";
  import { getDiskPredictions, replaceSoon, type DiskPrediction } from "$lib/api/health";
  import {
    getDashboardLayout, saveDashboardLayout, resetDashboardLayout, type LayoutEntry
  } from "$lib/api/dashboard";
  import {
    WIDGETS, defaultLayout, isDefaultLayout, moveTo, resolveLayout, setHidden, step, widgetDef
  } from "$lib/dashboard-layout";
  import { stats, wsConnected, wsForbidden, type WebSocketStats } from "$lib/stores/stats";
  import { user } from "$lib/stores/auth";
  import { intlLocale, t } from "$lib/i18n";
  import { apiTime } from "$lib/time";

  let systemInfo: SystemInfo | null = null;
  let pools: Pool[] = [];
  let containers: Container[] = [];
  let notificationLogs: NotificationLog[] = [];
  // The disks whose SMART trend says replace soon (ADR-0235).
  let toReplace: DiskPrediction[] = [];
  let predictionsError = "";
  let loading = true;
  let lastUpdate: Date | null = null;

  // Subscribe to WebSocket stats
  let currentStats: WebSocketStats | null = null;
  $: currentStats = $stats;
  $: if ($stats) {
    lastUpdate = new Date();
  }

  // The user's layout of the widgets, kept on the server (ADR-0395). It is read with the rest of the
  // page's data, on opening and on Refresh, and written only when the user changes it: nothing polls.
  let layout: LayoutEntry[] = defaultLayout(false);
  let layoutFailed = false;
  let layoutError = "";
  let arranging = false;
  let saving = false;
  // What a screen reader is told after a move, a hide or a show.
  let announcement = "";
  let draggedId: string | null = null;
  let overId: string | null = null;

  onMount(async () => {
    await loadData();
  });

  // The layout renders this page only after the user is known. Pools,
  // containers and notifications are administrator-only APIs; regular
  // accounts get the system overview and the live stats only.
  $: isAdmin = $user?.is_admin === true;

  // The predictions read the stored SMART history only: no disk is woken. A
  // failure is shown, unlike those of the widgets, so that a disk to replace is
  // not taken for one without a mark.
  async function loadPredictions() {
    if (!isAdmin) return;
    try {
      toReplace = replaceSoon((await getDiskPredictions()).predictions);
      predictionsError = "";
    } catch (e) {
      toReplace = [];
      predictionsError =
        e instanceof Error && e.message ? e.message : $t("dashboard.replace.loadFailed");
    }
  }

  async function loadData() {
    loading = true;
    try {
      const [infoData, poolsData, containersData, logsData, , layoutData] = await Promise.all([
        getSystemInfo().catch((e) => {
          console.error("Failed to load system info:", e);
          return null;
        }),
        isAdmin ? getPools().catch(() => []) : [],
        isAdmin ? getContainers().catch(() => []) : [],
        isAdmin ? getLogs().catch(() => []) : [],
        loadPredictions(),
        getDashboardLayout().catch((e) => {
          console.error("Failed to load the dashboard layout:", e);
          return null;
        })
      ]);

      // A layout that cannot be read shows the default and cannot be changed, so that the stored one
      // is not overwritten by a guess.
      layoutFailed = layoutData === null;
      layout = resolveLayout(layoutData ?? [], isAdmin);
      if (layoutFailed) arranging = false;

      systemInfo = infoData;
      pools = Array.isArray(poolsData) ? poolsData : [];
      containers = Array.isArray(containersData) ? containersData : [];
      notificationLogs = Array.isArray(logsData) ? logsData : [];
    } finally {
      loading = false;
    }
  }

  async function handleRefresh() {
    await loadData();
  }

  $: formatUptime = (bootTime: string): string => {
    // bootTime is when the host booted, ISO 8601 with its offset (ADR-0398)
    const booted = apiTime(bootTime);
    if (!booted) return bootTime;
    const seconds = Math.floor((Date.now() - booted.getTime()) / 1000);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) return $t("dashboard.uptime.days", { days, hours });
    if (hours > 0) return $t("dashboard.uptime.hours", { hours, minutes });
    return $t("dashboard.uptime.minutes", { minutes });
  };

  // In the language of the page.
  $: formatTime = (date: Date): string => date.toLocaleTimeString($intlLocale);

  // The CPU's temperature, which the server chooses among the host's sensors (ADR-0346), in whole
  // degrees; null on a host without sensors.
  function wholeDegrees(celsius: number | null | undefined): number | null {
    return celsius == null ? null : Math.round(celsius);
  }

  // Transform pools for widget
  $: poolsForWidget = pools.map(p => ({
    name: p.name,
    size: p.total_size || 0,
    used: p.used_size || 0,
    free: (p.total_size || 0) - (p.used_size || 0),
    health: p.health ?? "unknown",
    mounted: p.mounted ?? !!p.mountpoint,
    missingDevices: p.missing_devices ?? 0,
    deviceErrors: p.device_errors ?? 0,
    readOnly: p.read_only ?? false,
    forcedReadOnly: p.forced_read_only ?? false
  }));

  // Transform containers for widget
  $: containersForWidget = containers.map(c => ({
    id: c.id,
    name: c.name,
    state: c.state,
    image: c.image
  }));

  // Transform notification logs for widget
  $: notificationsForWidget = notificationLogs.map(log => ({
    id: log.id,
    type: log.event?.type || "info",
    title: log.event?.title || $t("dashboard.notification"),
    message: log.event?.message || "",
    created_at: log.sent_at,
    read: log.success
  }));
  // A widget shows when it is not hidden and has its data: the stats cards come with the live stats.
  $: shown = layout.filter((e) => !e.hidden && (currentStats !== null || widgetDef(e.id)?.adminOnly));
  $: hiddenWidgets = layout.filter((e) => e.hidden);
  $: nameOf = (id: string): string => {
    const def = widgetDef(id);
    return def ? $t(def.nameKey) : id;
  };

  async function apply(next: LayoutEntry[], message: string, reset = false): Promise<boolean> {
    if (saving || next === layout) return false;
    saving = true;
    const before = layout;
    layout = next;
    layoutError = "";
    try {
      if (reset) await resetDashboardLayout();
      else await saveDashboardLayout(next);
      announcement = message;
      return true;
    } catch (e) {
      layout = before;
      layoutError = $t("dashboard.layout.saveFailed", {
        error: e instanceof Error && e.message ? e.message : ""
      });
      announcement = layoutError;
      return false;
    } finally {
      saving = false;
    }
  }

  // A button of a widget's toolbar keeps the focus after the widget moved: the element is moved in
  // the page, which drops the focus. 0 is Move up, 1 Move down; a disabled one passes it on.
  async function focusToolbar(id: string, preferred: 0 | 1) {
    await tick();
    const buttons = [...document.querySelectorAll<HTMLButtonElement>(`[data-widget="${id}"] [data-toolbar] button`)];
    (buttons[preferred]?.disabled ? buttons[1 - preferred] : buttons[preferred])?.focus();
  }

  async function move(id: string, direction: -1 | 1) {
    const next = step(layout, id, direction, (other) => shown.some((e) => e.id === other));
    const done = await apply(next, "");
    if (!done) return;
    const position = shown.findIndex((e) => e.id === id) + 1;
    announcement = $t("dashboard.layout.moved", { name: nameOf(id), position, total: shown.length });
    await focusToolbar(id, direction === -1 ? 0 : 1);
  }

  // The button that was pressed goes away or is disabled by the change: the focus goes to where the
  // widget went, so that a keyboard user does not start again from the top of the page.
  async function hide(id: string) {
    if (!(await apply(setHidden(layout, id, true), $t("dashboard.layout.hidden", { name: nameOf(id) })))) return;
    await tick();
    document.querySelectorAll<HTMLElement>("[data-hidden-list] button")[hiddenWidgets.findIndex((e) => e.id === id)]?.focus();
  }

  async function show(id: string) {
    if (!(await apply(setHidden(layout, id, false), $t("dashboard.layout.shown", { name: nameOf(id) })))) return;
    await focusToolbar(id, 0);
  }

  async function resetLayout() {
    if (!(await apply(defaultLayout(isAdmin), $t("dashboard.layout.wasReset"), true))) return;
    await tick();
    document.querySelector<HTMLElement>("[data-customize] button")?.focus();
  }

  function onDragStart(event: DragEvent, id: string) {
    draggedId = id;
    if (!event.dataTransfer) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", id);
    const card = (event.currentTarget as HTMLElement).closest("[data-widget]");
    if (card) event.dataTransfer.setDragImage(card, 16, 16);
  }

  function onDragOver(event: DragEvent, id: string) {
    if (draggedId === null) return;
    event.preventDefault();
    overId = id;
  }

  async function onDrop(event: DragEvent, id: string) {
    event.preventDefault();
    const dragged = draggedId;
    draggedId = overId = null;
    if (dragged === null || dragged === id) return;
    const done = await apply(moveTo(layout, dragged, id), "");
    if (done) {
      announcement = $t("dashboard.layout.moved", {
        name: nameOf(dragged), position: shown.findIndex((e) => e.id === dragged) + 1, total: shown.length
      });
    }
  }

  function onDragEnd() {
    draggedId = overId = null;
  }
</script>

<div class="space-y-6">
  <!-- Header: on a narrow screen the live stats' time and Refresh go below the host name, and a
       long host name breaks (ADR-0302). -->
  <div class="flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-3 min-w-0">
      <Server class="w-8 h-8 text-blue-500 flex-shrink-0" />
      <div class="min-w-0">
        <h1 class="text-2xl font-bold break-words">{systemInfo?.hostname || $t("common.product")}</h1>
        {#if systemInfo}
          <p class="text-sm text-slate-400">
            {$t("dashboard.osUptime", { os: systemInfo.os, uptime: formatUptime(systemInfo.boot_time) })}
          </p>
        {/if}
      </div>
    </div>
    <div class="flex items-center gap-4">
      {#if $wsForbidden}
        <span class="text-xs text-warning">{$t("dashboard.liveStatsForbidden")}</span>
      {:else if !$wsConnected}
        <span class="text-xs text-warning">{$t("dashboard.disconnected")}</span>
      {:else if lastUpdate}
        <span class="text-xs text-slate-500 flex items-center gap-1">
          <Clock class="w-3 h-3" />
          {$t("dashboard.updated", { time: formatTime(lastUpdate) })}
        </span>
      {/if}
      {#if !layoutFailed && !loading}
        <span data-customize>
          <Button variant="secondary" size="sm" icon={arranging ? Check : SlidersHorizontal} pressed={arranging}
            on:click={() => (arranging = !arranging)}>
            {arranging ? $t("dashboard.layout.done") : $t("dashboard.layout.customize")}
          </Button>
        </span>
      {/if}
      <Button variant="secondary" size="icon" on:click={handleRefresh} disabled={loading || saving}>
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
        <span class="sr-only">{$t("common.refresh")}</span>
      </Button>
    </div>
  </div>

  {#if loading && !currentStats}
    <div class="flex items-center justify-center py-12">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
    </div>
  {:else}
    <!-- The disks to replace stay above the widgets, always: a warning is not one of them. -->
    {#if isAdmin}
      {#if toReplace.length > 0}
        <Alert variant="error" title={$t("dashboard.replace.title")}>
          <ul class="space-y-1 text-sm">
            {#each toReplace as prediction}
              <li>
                <span class="mono">{prediction.device}</span>
                {$t("dashboard.replace.disk", { serial: prediction.serial, reasons: prediction.risk_factors.join("; ") })}
              </li>
            {/each}
          </ul>
          <a href="/system#health" class="text-sm text-info hover:underline">{$t("dashboard.replace.diskHealth")}</a>
        </Alert>
      {:else if predictionsError}
        <Alert variant="warning" title={$t("dashboard.replace.checkFailed")}>
          {predictionsError}
        </Alert>
      {/if}
    {/if}

    {#if layoutFailed}
      <Alert variant="warning">{$t("dashboard.layout.loadFailed")}</Alert>
    {/if}
    {#if layoutError}
      <Alert variant="error" dismissible on:dismiss={() => (layoutError = "")}>{layoutError}</Alert>
    {/if}

    <p class="sr-only" role="status" data-layout-status>{announcement}</p>

    {#if arranging}
      <Card>
        <p class="text-sm text-secondary mb-3">{$t("dashboard.layout.hint")}</p>
        <section aria-label={$t("dashboard.layout.hiddenTitle")} class="mb-3" data-hidden-list>
          <h2 class="section-title mb-2">{$t("dashboard.layout.hiddenTitle")}</h2>
          {#if hiddenWidgets.length === 0}
            <p class="text-sm text-muted">{$t("dashboard.layout.noneHidden")}</p>
          {:else}
            <div class="flex flex-wrap gap-2">
              {#each hiddenWidgets as entry (entry.id)}
                <Button variant="secondary" size="sm" icon={Eye} disabled={saving} on:click={() => show(entry.id)}>
                  {$t("dashboard.layout.show", { name: nameOf(entry.id) })}
                </Button>
              {/each}
            </div>
          {/if}
        </section>
        <Button variant="ghost" size="sm" icon={RotateCcw} disabled={saving || isDefaultLayout(layout, isAdmin)}
          on:click={resetLayout}>
          {$t("dashboard.layout.reset")}
        </Button>
      </Card>
    {/if}

    <!-- The widgets: small cards five to a row on a wide screen, wide ones two to a row (ADR-0395). -->
    <div class="grid grid-cols-2 md:grid-cols-6 xl:grid-cols-10 gap-4">
      {#each shown as entry, index (entry.id)}
        {@const def = widgetDef(entry.id)}
        <div
          data-widget={entry.id}
          class="widget min-w-0 flex flex-col {def?.size === 'wide' ? 'col-span-2 md:col-span-6 xl:col-span-5' : 'col-span-1 md:col-span-2'}
            {arranging ? 'rounded-lg border border-dashed border-border p-1' : ''}
            {overId === entry.id && draggedId !== entry.id ? 'ring-2 ring-blue-500' : ''}
            {draggedId === entry.id ? 'opacity-50' : ''}"
          on:dragover={(e) => onDragOver(e, entry.id)}
          on:drop={(e) => onDrop(e, entry.id)}
        >
          {#if arranging}
            <div data-toolbar class="flex flex-wrap items-center gap-1 mb-1">
              <!-- Dragging is for a mouse; the buttons are for the keyboard and a finger. -->
              <span
                data-drag-handle
                draggable="true"
                role="presentation"
                title={$t("dashboard.layout.drag")}
                class="p-1.5 cursor-grab text-secondary"
                on:dragstart={(e) => onDragStart(e, entry.id)}
                on:dragend={onDragEnd}
              >
                <GripVertical class="w-4 h-4" />
              </span>
              <Button variant="secondary" size="icon-sm" disabled={saving || index === 0}
                on:click={() => move(entry.id, -1)}>
                <ArrowUp class="w-4 h-4" />
                <span class="sr-only">{$t("dashboard.layout.moveUp", { name: nameOf(entry.id) })}</span>
              </Button>
              <Button variant="secondary" size="icon-sm" disabled={saving || index === shown.length - 1}
                on:click={() => move(entry.id, 1)}>
                <ArrowDown class="w-4 h-4" />
                <span class="sr-only">{$t("dashboard.layout.moveDown", { name: nameOf(entry.id) })}</span>
              </Button>
              <Button variant="secondary" size="icon-sm" disabled={saving} on:click={() => hide(entry.id)}>
                <EyeOff class="w-4 h-4" />
                <span class="sr-only">{$t("dashboard.layout.hide", { name: nameOf(entry.id) })}</span>
              </Button>
            </div>
          {/if}
          {#if entry.id === "storage"}
            <StorageWidget pools={poolsForWidget} />
          {:else if entry.id === "containers"}
            <ContainersWidget containers={containersForWidget} limit={5} />
          {:else if entry.id === "quick-actions"}
            <QuickActionsWidget />
          {:else if entry.id === "recent-activity"}
            <RecentActivityWidget notifications={notificationsForWidget} limit={5} />
          {:else if currentStats}
            <SystemStatsWidget
              only={entry.id === "memory" || entry.id === "network" || entry.id === "disk-io" || entry.id === "temperature" ? entry.id : "cpu"}
              cpuPercent={currentStats.cpu?.percent || 0}
              memoryPercent={currentStats.memory?.percent || 0}
              memoryUsed={currentStats.memory?.used || 0}
              memoryTotal={currentStats.memory?.total || 0}
              networkUp={currentStats.network?.bytes_sent_rate || 0}
              networkDown={currentStats.network?.bytes_recv_rate || 0}
              diskRead={currentStats.disk_io?.read_rate || 0}
              diskWrite={currentStats.disk_io?.write_rate || 0}
              temperature={wholeDegrees(currentStats.cpu?.temperature)}
            />
          {/if}
        </div>
      {/each}
    </div>

    {#if !isAdmin}
      <Card>
        <div class="flex items-start gap-3">
          <UserCog class="w-5 h-5 text-info mt-0.5" />
          <div>
            <p class="font-medium">{$t("dashboard.user.signedInAs", { name: $user?.username ?? "" })}</p>
            <p class="text-sm text-secondary">
              {$t("dashboard.user.managed")}
              {$t("dashboard.user.account")}
              <a href="/system#security" class="text-info hover:underline">{$t("dashboard.user.accountLink")}</a>.
            </p>
          </div>
        </div>
      </Card>
    {/if}
  {/if}
</div>

<style>
  /* A widget's card fills the cell of the grid, so that the cards of a row are as tall as the tallest. */
  .widget > :global(:not([data-toolbar])) {
    flex: 1 1 auto;
  }
</style>
