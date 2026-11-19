<script lang="ts">
  import { onMount, tick } from "svelte";
  import { SlidersHorizontal } from "lucide-svelte";
  import { Alert, EmptyState } from "$lib/components/ui";
  import {
    AccountNotice, DashboardHeader, DashboardWidget, HiddenWidgets, ReplaceDisksAlert, WidgetToolbar
  } from "$lib/components/dashboard";

  import { getSystemInfo, type SystemInfo } from "$lib/api/system";
  import { getPools, type Pool } from "$lib/api/storage";
  import { getContainers, type Container } from "$lib/api/containers";
  import { getLogs, type NotificationLog } from "$lib/api/notifications";
  import { getDiskPredictions, replaceSoon, type DiskPrediction } from "$lib/api/health";
  import {
    getDashboardLayout, saveDashboardLayout, resetDashboardLayout, type LayoutEntry
  } from "$lib/api/dashboard";
  import {
    defaultLayout, isDefaultLayout, moveTo, resolveLayout, setHidden, step, widgetDef
  } from "$lib/dashboard-layout";
  import { stats, type WebSocketStats } from "$lib/stores/stats";
  import { user } from "$lib/stores/auth";
  import { t } from "$lib/i18n";

  // The dashboard (ADR-0395): it reads its data on opening and on Refresh, and keeps the user's
  // layout of the widgets, which the user arranges here.

  // The answers of the API, each only ever replaced whole.
  let systemInfo = $state.raw<SystemInfo | null>(null);
  let pools = $state.raw<Pool[]>([]);
  let containers = $state.raw<Container[]>([]);
  let notificationLogs = $state.raw<NotificationLog[]>([]);
  // The disks whose SMART trend says replace soon (ADR-0235).
  let toReplace = $state.raw<DiskPrediction[]>([]);
  let predictionsError = $state("");
  let loading = $state(true);
  let lastUpdate = $state<Date | null>(null);

  // Subscribe to WebSocket stats
  const currentStats: WebSocketStats | null = $derived($stats);
  // Every message of the socket, before the markup updates; the time of the last one stays while
  // the store is empty.
  $effect.pre(() => {
    if ($stats) lastUpdate = new Date();
  });

  // The user's layout of the widgets, kept on the server (ADR-0395). It is read with the rest of the
  // page's data, on opening and on Refresh, and written only when the user changes it: nothing polls.
  // Only ever replaced whole: apply compares the next layout with it.
  let layout = $state.raw<LayoutEntry[]>(defaultLayout(false));
  let layoutFailed = $state(false);
  let layoutError = $state("");
  let arranging = $state(false);
  let saving = $state(false);
  // What a screen reader is told after a move, a hide or a show.
  let announcement = $state("");
  let draggedId = $state<string | null>(null);
  let overId = $state<string | null>(null);

  onMount(async () => {
    await loadData();
  });

  // The layout renders this page only after the user is known. Pools,
  // containers and notifications are administrator-only APIs; regular
  // accounts get the system overview and the live stats only.
  const isAdmin = $derived($user?.is_admin === true);

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

  // A widget shows when it is not hidden and has its data: the stats cards come with the live stats.
  const shown = $derived(layout.filter((e) => !e.hidden && (currentStats !== null || widgetDef(e.id)?.adminOnly)));
  const hiddenWidgets = $derived(layout.filter((e) => e.hidden));
  // Every widget hidden by the user, not only waiting for its data (ADR-0428).
  const allHidden = $derived(layout.length > 0 && layout.every((e) => e.hidden));
  // The name of a widget in the language of the page; it reads $t where it is called.
  function nameOf(id: string): string {
    const def = widgetDef(id);
    return def ? $t(def.nameKey) : id;
  }

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

<div class="page-container">
  <DashboardHeader
    {systemInfo}
    {lastUpdate}
    customizable={!layoutFailed && !loading}
    {arranging}
    {loading}
    {saving}
    oncustomize={() => (arranging = !arranging)}
    onrefresh={handleRefresh}
  />

  {#if loading && !currentStats}
    <!-- Skeleton tiles while the page's data loads; the text stays for screen readers. -->
    <div class="grid grid-cols-2 md:grid-cols-6 xl:grid-cols-10 gap-4" aria-busy="true">
      {#each [0, 1, 2, 3, 4] as tile (tile)}
        <div class="col-span-1 md:col-span-2 space-y-3 bg-surface-elevated border border-border rounded-card p-5">
          <div class="skeleton w-1/2"></div>
          <div class="skeleton h-6 w-2/3"></div>
          <div class="skeleton w-full"></div>
        </div>
      {/each}
      <p class="sr-only">{$t("common.loading")}</p>
    </div>
  {:else}
    <!-- The disks to replace stay above the widgets, always: a warning is not one of them. -->
    {#if isAdmin}
      <ReplaceDisksAlert {toReplace} error={predictionsError} />
    {/if}

    {#if layoutFailed}
      <Alert variant="warning">{$t("dashboard.layout.loadFailed")}</Alert>
    {/if}
    {#if layoutError}
      <Alert variant="error" dismissible ondismiss={() => (layoutError = "")}>{layoutError}</Alert>
    {/if}

    <p class="sr-only" role="status" data-layout-status>{announcement}</p>

    {#if arranging}
      <HiddenWidgets
        widgets={hiddenWidgets.map((entry) => ({ id: entry.id, name: nameOf(entry.id) }))}
        {saving}
        resetDisabled={isDefaultLayout(layout, isAdmin)}
        onshow={show}
        onreset={resetLayout}
      />
    {/if}

    {#if allHidden && !arranging}
      <!-- Every widget hidden: say so, and where the way back is, the header's Customize (ADR-0428). -->
      <EmptyState
        icon={SlidersHorizontal}
        title={$t("dashboard.layout.allHidden")}
        description={$t("dashboard.layout.allHiddenHint")}
      />
    {/if}

    <!-- The widgets: small cards five to a row on a wide screen, wide ones two to a row (ADR-0395).
         While they are arranged each has a dashed outline and its toolbar in room of its own at the
         top right, so that it covers nothing of the widget. -->
    <div class="grid grid-cols-2 md:grid-cols-6 xl:grid-cols-10 gap-4">
      {#each shown as entry, index (entry.id)}
        {@const def = widgetDef(entry.id)}
        <div
          data-widget={entry.id}
          class="widget relative min-w-0 flex flex-col {def?.size === 'wide' ? 'col-span-2 md:col-span-6 xl:col-span-5' : 'col-span-1 md:col-span-2'}
            {arranging ? 'pt-10 rounded-card outline-dashed outline-1 outline-offset-[3px] outline-border-strong' : ''}
            {overId === entry.id && draggedId !== entry.id ? 'ring-2 ring-brand-ring' : ''}
            {draggedId === entry.id ? 'opacity-50' : ''}"
          ondragover={(e) => onDragOver(e, entry.id)}
          ondrop={(e) => onDrop(e, entry.id)}
        >
          {#if arranging}
            <WidgetToolbar
              name={nameOf(entry.id)}
              first={index === 0}
              last={index === shown.length - 1}
              {saving}
              ondragstart={(e) => onDragStart(e, entry.id)}
              ondragend={onDragEnd}
              onmove={(direction) => move(entry.id, direction)}
              onhide={() => hide(entry.id)}
            />
          {/if}
          <DashboardWidget id={entry.id} {pools} {containers} {notificationLogs} stats={currentStats} />
        </div>
      {/each}
    </div>

    {#if !isAdmin}
      <AccountNotice username={$user?.username ?? ""} />
    {/if}
  {/if}
</div>

<style>
  /* A widget's card fills the cell of the grid, so that the cards of a row are as tall as the tallest. */
  .widget > :global(:not([data-toolbar])) {
    flex: 1 1 auto;
  }
</style>
