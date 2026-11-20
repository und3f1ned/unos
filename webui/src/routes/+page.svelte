<script lang="ts">
  import { onMount, tick } from "svelte";
  import { SlidersHorizontal } from "lucide-svelte";
  import { Alert, EmptyState, Skeleton } from "$lib/components/ui";
  import {
    AccountNotice, DashboardHeader, DashboardWidget, HiddenWidgets, WidgetToolbar
  } from "$lib/components/dashboard";
  import { dashboardIssues } from "$lib/components/dashboard/issues";
  import ProblemsBanner from "$lib/components/ProblemsBanner.svelte";

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
  import { stats, wsConnected, wsForbidden, wsSettled, type WebSocketStats } from "$lib/stores/stats";
  import { user } from "$lib/stores/auth";
  import { problems } from "$lib/stores/problems";
  import { t } from "$lib/i18n";
  import { messageOf } from "$lib/errors";

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
  // Why the pools could not be read: an issue of its own, so that the header never says
  // "No problems" about pools it does not know.
  let poolsError = $state("");
  let loading = $state(true);
  // The first load has answered: until then the page is its skeleton, in the default layout of the
  // widgets; a refresh keeps what is shown (ADR-0430).
  let loaded = $state(false);
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

  // What the dashboard knows besides the socket's problems: the pools that are not
  // healthy, the disks to replace soon and a failed check of them. With the socket's problems they
  // are one banner under the header, and the header counts them: never "No problems" beside them.
  const extraIssues = $derived(
    isAdmin && loaded ? dashboardIssues($t, pools, poolsError, $problems, toReplace, predictionsError) : []
  );
  // How many issues the header's chip counts, as the banner lists them; null until the page has
  // loaded. "No problems" also waits for the socket's list (the header reads problemsKnown).
  const issueCount = $derived(isAdmin && loaded ? $problems.length + extraIssues.length : null);


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
        isAdmin
          ? getPools().then(
              (answer) => {
                poolsError = "";
                return answer;
              },
              (e) => {
                poolsError = messageOf(e) || $t("dashboard.issue.poolsFailed");
                return [];
              }
            )
          : [],
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
      loaded = true;
    }
  }

  async function handleRefresh() {
    await loadData();
  }

  // A stats card waits for the live stats as a skeleton of its tile, so that they arrive in its
  // place (ADR-0430): while the socket's first attempt runs, or the socket is up and its first stats
  // have not come. Without stats it is left out: for an account the socket refuses, or once the
  // attempt ended without them.
  const waitingForStats = $derived(currentStats === null && !$wsForbidden && ($wsConnected || !$wsSettled));
  // A widget shows when it is not hidden, a stats card with the live stats or while it waits.
  const shown = $derived(layout.filter((e) => !e.hidden && (currentStats !== null || waitingForStats || widgetDef(e.id)?.adminOnly)));
  // The skeleton of the first load: the default layout of the account's widgets.
  const skeleton = $derived(defaultLayout(isAdmin).map((entry) => widgetDef(entry.id)?.size ?? "small"));
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
    customizable={!layoutFailed}
    pending={!loaded}
    {arranging}
    {issueCount}
    issueError={$problems.some((p) => p.severity === "error") || extraIssues.some((i) => i.severity === "error")}
    {loading}
    {saving}
    oncustomize={() => (arranging = !arranging)}
    onrefresh={handleRefresh}
  />

  {#if !loaded}
    <!-- The first load (ADR-0430): the widgets of the default layout as skeletons, in their places;
         the page's data replaces them all at once. -->
    <div class="grid grid-cols-2 md:grid-cols-6 xl:grid-cols-10 gap-4" aria-busy="true">
      {#each skeleton as size, index (index)}
        {#if size === "wide"}
          <div class="col-span-2 md:col-span-6 xl:col-span-5 card min-h-64" data-skeleton aria-hidden="true">
            <div class="card-header">
              <Skeleton size="base" class="w-40" />
              <Skeleton class="w-16" />
            </div>
            <Skeleton count={3} />
          </div>
        {:else}
          <div class="col-span-1 md:col-span-2 flex flex-col">
            <Skeleton shape="tile" class="flex-1" />
          </div>
        {/if}
      {/each}
    </div>
  {:else}
    <!-- The problems of the NAS, the disks to replace soon and the pools that are not healthy, in
         one banner above the widgets, always: a warning is not one of them. -->
    {#if isAdmin}
      <ProblemsBanner dashboard extra={extraIssues} />
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
    <div
      class="grid grid-cols-2 md:grid-cols-6 xl:grid-cols-10 gap-4"
      aria-busy={waitingForStats ? "true" : undefined}
    >
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
          <DashboardWidget id={entry.id} {pools} poolsFailed={poolsError !== ""} {containers} {notificationLogs} stats={currentStats} />
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
