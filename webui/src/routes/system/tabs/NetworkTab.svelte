<script lang="ts">
  import { onDestroy, onMount, tick } from "svelte";
  import type { ComponentType } from "svelte";
  import {
    getInterfaces,
    getDNSConfig,
    getDefaultGateway,
    getRoutes,
    getConnections,
    bringInterfaceUp,
    bringInterfaceDown,
    keepInterfaceDown,
    type NetworkInterface,
    type DNSConfig,
    type Route,
    type GatewayInfo,
    type Connection,
  } from "$lib/api/network";
  import { ApiError } from "$lib/api/client";
  import { Alert, Badge, Button, EmptyState, Modal } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { stats, wsConnected, type NetworkInterface as WSNetworkInterface } from "$lib/stores/stats";
  import {
    Network,
    Cable,
    RefreshCw,
    Wifi,
    Globe,
    Router,
    Server,
    Activity,
    ArrowDown,
    ArrowUp,
    Power,
    PowerOff,
    ChevronDown,
    ChevronUp,
    Link2,
  } from "lucide-svelte";

  // Once the time to keep a disable has run out, the page reads the interfaces
  // again after the host's revert had a few seconds to turn it back on (ADR-0244).
  const REVERT_GRACE_MS = 5000;

  type View = "interfaces" | "routes" | "connections";
  const VIEWS: { id: View; icon: ComponentType }[] = [
    { id: "interfaces", icon: Cable },
    { id: "routes", icon: Router },
    { id: "connections", icon: Activity },
  ];
  // The name of each part of the page, a view or what could not be loaded: keys of the catalog.
  const PARTS: Record<string, string> = {
    interfaces: "network.interfaces",
    dns: "network.dns",
    gateway: "network.gateway",
    routes: "network.routes",
    connections: "network.connections",
  };
  // The operational states that the kernel reports for an interface (operstate), which its badge
  // shows: keys of the catalog. The values that ip and ss print in the tables (link types, route
  // and socket protocols, socket states) are shown as they come (ADR-0295).
  const STATES: Record<string, string> = {
    UP: "network.state.up",
    DOWN: "network.state.down",
    LOWERLAYERDOWN: "network.state.lowerLayerDown",
    DORMANT: "network.state.dormant",
    TESTING: "network.state.testing",
    NOTPRESENT: "network.state.notPresent",
    UNKNOWN: "network.state.unknown",
  };
  // The units of a count of bytes, one for each power of 1024: keys of the catalog.
  const BYTE_UNITS = ["network.bytes.b", "network.bytes.kb", "network.bytes.mb", "network.bytes.gb", "network.bytes.tb"];

  let interfaces: NetworkInterface[] = [];
  let dnsConfig: DNSConfig | null = null;
  let gateway: GatewayInfo | null = null;
  let routes: Route[] = [];
  let connections: Connection[] = [];
  // What could not be loaded: part of the page (a key of PARTS) -> the server's message.
  let loadErrors: Record<string, string> = {};

  let loading = true;
  let activeTab: View = "interfaces";
  let expandedInterface: string | null = null;

  // How long a disable waits to be kept (the server's value).
  let revertSeconds = 60;
  // Disables that wait to be kept: interface -> when it comes back on by itself (Date.now()).
  let deadlines: Record<string, number> = {};
  // Disables whose time has run out, until the page has read the interfaces again.
  let expiring: string[] = [];
  let now = Date.now();
  let ticker: ReturnType<typeof setInterval> | null = null;
  const timeouts = new Set<ReturnType<typeof setTimeout>>();
  // The countdowns: the focus goes there after a Disable, one Tab away from its Keep.
  let pendingRegion: HTMLElement | null = null;

  // The interface the Disable dialog asks about, and the one being changed.
  let confirming: string | null = null;
  let busy: string | null = null;
  let actionError: string | null = null;
  let notice: { variant: "success" | "info"; text: string } | null = null;

  // Get WebSocket interface stats for rates
  $: wsInterfaces = $stats?.network?.interfaces ?? [];
  $: syncTicker(Object.keys(deadlines).length > 0);
  // The newest countdown first: after a Disable, one Tab from the focus reaches the
  // Keep of the interface just disabled (ADR-0246).
  $: countdowns = Object.entries(deadlines).sort(([, a], [, b]) => b - a);

  // The texts that the markup gets from functions are reactive, so that they follow a change of
  // the language.

  /** The text of `value`'s key in `keys`, in the language of the page; a value without one as it comes. */
  $: labelOf = (keys: Record<string, string>, value: string): string =>
    Object.prototype.hasOwnProperty.call(keys, value) ? $t(keys[value]) : value;

  // The speed of a link in the units and the number format of the page.
  $: formatSpeed = (mbps: number): string =>
    mbps >= 1000
      ? $t("network.speed.gbps", { speed: (mbps / 1000).toLocaleString($intlLocale) })
      : $t("network.speed.mbps", { speed: mbps.toLocaleString($intlLocale) });

  // The traffic of the stats stream in the units and the number format of the page: a count of
  // bytes in the largest unit that keeps it at 1 or more, with up to two decimals, as formatBytes
  // of $lib/stores/stats writes it in English.
  $: formatSize = (bytes: number): string => {
    const power =
      bytes >= 1 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), BYTE_UNITS.length - 1) : 0;
    const value = Number((bytes / 1024 ** power).toFixed(2));
    return $t("network.bytes.size", {
      value: value.toLocaleString($intlLocale, { maximumFractionDigits: 2, useGrouping: false }),
      unit: $t(BYTE_UNITS[power]),
    });
  };
  $: formatRate = (bytesPerSecond: number): string =>
    $t("network.bytes.perSecond", { size: formatSize(bytesPerSecond) });

  onMount(() => {
    loadAll();
  });

  onDestroy(() => {
    if (ticker) clearInterval(ticker);
    for (const timeout of timeouts) clearTimeout(timeout);
  });

  function message(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  function without<T>(record: Record<string, T>, key: string): Record<string, T> {
    const copy = { ...record };
    delete copy[key];
    return copy;
  }

  async function load(part: string, read: () => Promise<void>): Promise<boolean> {
    try {
      await read();
      loadErrors = without(loadErrors, part);
      return true;
    } catch (e) {
      loadErrors = { ...loadErrors, [part]: message(e) };
      return false;
    }
  }

  async function loadAll() {
    loading = true;
    await Promise.all([
      loadInterfaces(),
      load("dns", async () => { dnsConfig = await getDNSConfig(); }),
      load("gateway", async () => { gateway = await getDefaultGateway(); }),
      load("routes", async () => { routes = (await getRoutes()).routes; }),
      loadConnections(),
    ]);
    loading = false;
  }

  function loadInterfaces(): Promise<boolean> {
    return load("interfaces", async () => {
      const result = await getInterfaces();
      interfaces = result.interfaces;
      revertSeconds = result.revert_seconds;
      followReverts(result.interfaces);
    });
  }

  function loadConnections(): Promise<boolean> {
    return load("connections", async () => { connections = (await getConnections()).connections; });
  }

  // The countdowns follow the server: a disable kept or undone elsewhere ends its
  // countdown, and one found after a reload starts it. A running countdown keeps its
  // own deadline, counted from when the page sent the Disable.
  function followReverts(list: NetworkInterface[]) {
    const next: Record<string, number> = {};
    for (const iface of list) {
      if (iface.revert_in === null) continue;
      if (iface.name in deadlines) next[iface.name] = deadlines[iface.name];
      else if (iface.revert_in > 0) next[iface.name] = Date.now() + iface.revert_in * 1000;
    }
    deadlines = next;
  }

  // Runs whenever the deadlines change: a countdown that starts while another one
  // runs counts from this moment, not from the last tick.
  function syncTicker(active: boolean) {
    now = Date.now();
    if (active && !ticker) {
      // polling-ignore: a countdown; when a disable's time has run out, the interfaces are read
      // once to see whether the host turned it back on (ADR-0363).
      ticker = setInterval(onTick, 1000);
    } else if (!active && ticker) {
      clearInterval(ticker);
      ticker = null;
    }
  }

  function onTick() {
    now = Date.now();
    for (const [name, deadline] of Object.entries(deadlines)) {
      if (deadline <= now && !expiring.includes(name)) {
        expiring = [...expiring, name];
        later(() => checkRevert(name), REVERT_GRACE_MS);
      }
    }
  }

  function later(run: () => void, ms: number) {
    const timeout = setTimeout(() => {
      timeouts.delete(timeout);
      run();
    }, ms);
    timeouts.add(timeout);
  }

  function secondsLeft(deadline: number, at: number): number {
    return Math.max(0, Math.ceil((deadline - at) / 1000));
  }

  // A countdown that this page's Keep or Enable ended.
  function endCountdown(name: string) {
    deadlines = without(deadlines, name);
    expiring = expiring.filter((n) => n !== name);
  }

  // The page's time to keep a disable ran out a few seconds ago: report what the
  // host shows now, whatever it is (ADR-0244, ADR-0246).
  async function checkRevert(name: string) {
    const loaded = await loadInterfaces();
    // Kept or enabled from this page in the meantime.
    if (!expiring.includes(name)) return;
    expiring = expiring.filter((n) => n !== name);
    const iface = interfaces.find((i) => i.name === name);
    if (!loaded) {
      actionError = $t("network.revert.unchecked", { name });
    } else if (!iface) {
      actionError = $t("network.revert.gone", { name });
    } else if (iface.enabled) {
      notice = { variant: "info", text: $t("network.revert.back", { name }) };
    } else if (iface.revert_in !== null && iface.revert_in > 0) {
      // The host counts a little later than the page.
      deadlines = { ...deadlines, [name]: Date.now() + iface.revert_in * 1000 };
      return;
    } else if (iface.revert_in !== null) {
      actionError = $t("network.revert.notYet", { name });
    } else {
      // Nothing waits to be kept: the revert took its marker but could not turn the
      // interface on, or the change was kept elsewhere.
      actionError = $t("network.revert.stillOff", { name });
    }
    deadlines = without(deadlines, name);
  }

  // Get WebSocket stats for an interface
  function getWsInterface(name: string): WSNetworkInterface | undefined {
    return wsInterfaces.find(i => i.name === name);
  }

  // A default route: ip names its destination "default".
  function isDefault(route: Route): boolean {
    return !route.dst || route.dst === "default";
  }

  function startChange(name: string) {
    busy = name;
    actionError = null;
    notice = null;
  }

  async function disable() {
    const name = confirming;
    if (!name) return;
    startChange(name);
    const started = Date.now();
    let counting = false;
    try {
      const result = await bringInterfaceDown(name);
      const deadline = started + result.revert_in * 1000;
      if (deadline > Date.now()) {
        deadlines = { ...deadlines, [name]: deadline };
        // A check of an earlier disable of it that is still to come has nothing left to report.
        expiring = expiring.filter((n) => n !== name);
        counting = true;
      } else {
        notice = { variant: "info", text: $t("network.pending.late", { name }) };
      }
    } catch (e) {
      actionError = e instanceof ApiError ? e.message : $t("network.pending.lost", { name, n: revertSeconds });
    } finally {
      busy = null;
      confirming = null;
    }
    if (counting) {
      await tick();
      pendingRegion?.focus();
    }
    await loadInterfaces();
  }

  async function keep(name: string) {
    startChange(name);
    let lost = false;
    try {
      await keepInterfaceDown(name);
      notice = { variant: "success", text: $t("network.pending.kept", { name }) };
      endCountdown(name);
    } catch (e) {
      if (e instanceof ApiError) {
        actionError = e.message;
        endCountdown(name);
      } else {
        // The Keep may have reached UNOS: only the interfaces can tell.
        lost = true;
        actionError = $t("network.pending.keepLost", { name });
      }
    } finally {
      busy = null;
    }
    const loaded = await loadInterfaces();
    const iface = interfaces.find((i) => i.name === name);
    if (lost && loaded && iface && !iface.enabled && iface.revert_in === null) {
      actionError = null;
      notice = { variant: "success", text: $t("network.pending.kept", { name }) };
      endCountdown(name);
    }
  }

  async function enable(name: string) {
    startChange(name);
    try {
      await bringInterfaceUp(name);
      endCountdown(name);
    } catch (e) {
      actionError = message(e);
    } finally {
      busy = null;
    }
    await loadInterfaces();
  }

  function toggleExpand(name: string) {
    expandedInterface = expandedInterface === name ? null : name;
  }
</script>

<div class="space-y-6">
  <!-- Header -->
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold text-content flex items-center gap-2">
      <Network class="w-6 h-6 icon-network" />
      {$t("network.title")}
    </h2>
    <div class="flex items-center gap-2">
      {#if $wsConnected}
        <span class="text-xs text-status-success">{$t("network.live")}</span>
      {/if}
      <Button variant="secondary" size="icon" icon={RefreshCw} {loading} on:click={loadAll}>
        <span class="sr-only">{$t("common.refresh")}</span>
      </Button>
    </div>
  </div>

  {#if actionError}
    <Alert variant="error" dismissible on:dismiss={() => (actionError = null)}>{actionError}</Alert>
  {/if}
  {#if notice}
    <Alert variant={notice.variant} dismissible on:dismiss={() => (notice = null)}>{notice.text}</Alert>
  {/if}
  {#if Object.keys(loadErrors).length > 0}
    <Alert variant="error" title={$t("network.loadError")}>
      {#each Object.entries(loadErrors) as [part, text] (part)}
        <p>{labelOf(PARTS, part)}: {text}</p>
      {/each}
    </Alert>
  {/if}

  <!-- Disables that come back on by themselves unless they are kept (ADR-0244) -->
  {#if countdowns.length > 0}
    <section
      class="space-y-3"
      aria-label={$t("network.pending.list")}
      tabindex="-1"
      bind:this={pendingRegion}
    >
      {#each countdowns as [name, deadline] (name)}
        {@const left = secondsLeft(deadline, now)}
        <Alert variant="warning" title={$t("network.pending.title", { name })}>
          {#if left > 0}
            <p>{$t("network.pending.countdown", { name, seconds: left })}</p>
            <Button
              variant="warning"
              size="sm"
              class="mt-3"
              loading={busy === name}
              disabled={busy !== null && busy !== name}
              on:click={() => keep(name)}
            >
              {$t("network.pending.keep", { name })}
            </Button>
          {:else}
            <p>{$t("network.pending.expired", { name })}</p>
          {/if}
        </Alert>
      {/each}
    </section>
  {/if}

  <!-- Summary Cards -->
  <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
    <div class="card">
      <div class="flex items-center gap-2 text-content-secondary text-sm mb-2">
        <Globe class="w-4 h-4" />
        {$t("network.summary.gateway")}
      </div>
      <p class="text-lg font-semibold text-content break-all">{gateway?.gateway || $t("network.summary.notSet")}</p>
      {#if gateway?.interface}
        <p class="text-xs text-content-muted">{$t("network.summary.via", { name: gateway.interface })}</p>
      {/if}
    </div>

    <div class="card">
      <div class="flex items-center gap-2 text-content-secondary text-sm mb-2">
        <Server class="w-4 h-4" />
        {$t("network.summary.dns")}
      </div>
      {#if dnsConfig?.nameservers?.length}
        <p class="text-lg font-semibold text-content break-all">{dnsConfig.nameservers[0]}</p>
        {#if dnsConfig.nameservers.length > 1}
          <p class="text-xs text-content-muted">{$t("network.more", { n: dnsConfig.nameservers.length - 1 })}</p>
        {/if}
      {:else}
        <p class="text-content-muted">{$t("network.summary.notConfigured")}</p>
      {/if}
    </div>

    <div class="card">
      <div class="flex items-center gap-2 text-content-secondary text-sm mb-2">
        <Link2 class="w-4 h-4" />
        {$t("network.summary.connections")}
      </div>
      <p class="text-lg font-semibold text-content">{connections.length}</p>
      <p class="text-xs text-content-muted">{$t("network.summary.sockets")}</p>
    </div>
  </div>

  <!-- Views -->
  <div class="flex flex-wrap gap-2 border-b border-border pb-2">
    {#each VIEWS as view (view.id)}
      <Button
        variant={activeTab === view.id ? "secondary" : "ghost"}
        size="sm"
        icon={view.icon}
        on:click={() => (activeTab = view.id)}
      >
        {$t(PARTS[view.id])}
      </Button>
    {/each}
  </div>

  <!-- Interfaces -->
  {#if activeTab === "interfaces"}
    <div class="space-y-3">
      {#each interfaces as iface (iface.name)}
        {@const wsIface = getWsInterface(iface.name)}
        {@const ifaceIsUp = iface.state === "UP"}
        <section class="card p-0 overflow-hidden" aria-label={$t("network.interface.label", { name: iface.name })}>
          <!-- Interface Header -->
          <button
            class="w-full p-4 flex items-center justify-between hover:bg-surface-overlay transition-colors"
            aria-expanded={expandedInterface === iface.name}
            on:click={() => toggleExpand(iface.name)}
          >
            <div class="flex items-center gap-4">
              <div class="p-2 rounded-lg {ifaceIsUp ? 'bg-status-success/20' : 'bg-surface-overlay'}">
                {#if iface.type === "ether" || iface.type === "ethernet"}
                  <Cable class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
                {:else if iface.type === "wlan" || iface.name.startsWith("wlan")}
                  <Wifi class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
                {:else}
                  <Network class="w-5 h-5 {ifaceIsUp ? 'text-status-success' : 'text-content-muted'}" />
                {/if}
              </div>
              <div class="text-left">
                <div class="flex items-center gap-2">
                  <span class="font-semibold text-content">{iface.name}</span>
                  <Badge variant={!iface.enabled ? "neutral" : ifaceIsUp ? "success" : "warning"}>
                    {iface.enabled ? labelOf(STATES, iface.state) : $t("network.interface.disabled")}
                  </Badge>
                  {#if iface.speed}
                    <span class="text-xs text-content-secondary">{formatSpeed(iface.speed)}</span>
                  {/if}
                </div>
                <p class="text-sm text-content-secondary">
                  {iface.ip_addresses.length ? iface.ip_addresses[0] : $t("network.interface.noIp")}
                  {#if iface.ip_addresses.length > 1}
                    <span class="text-content-muted">{$t("network.more", { n: iface.ip_addresses.length - 1 })}</span>
                  {/if}
                </p>
              </div>
            </div>

            <div class="flex items-center gap-6">
              <!-- Live rates from the WebSocket -->
              {#if wsIface && ifaceIsUp}
                <div class="flex items-center gap-4 text-sm">
                  <span class="flex items-center gap-1 text-status-success">
                    <ArrowDown class="w-3 h-3" />
                    {formatRate(wsIface.bytes_recv_rate)}
                  </span>
                  <span class="flex items-center gap-1 text-status-info">
                    <ArrowUp class="w-3 h-3" />
                    {formatRate(wsIface.bytes_sent_rate)}
                  </span>
                </div>
              {/if}

              {#if expandedInterface === iface.name}
                <ChevronUp class="w-5 h-5 text-content-muted" />
              {:else}
                <ChevronDown class="w-5 h-5 text-content-muted" />
              {/if}
            </div>
          </button>

          <!-- Expanded Details -->
          {#if expandedInterface === iface.name}
            <div class="px-4 pb-4 border-t border-border pt-4">
              <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <p class="text-xs text-content-muted mb-1">{$t("network.interface.mac")}</p>
                  <p class="text-sm font-mono">{iface.mac || $t("network.interface.noMac")}</p>
                </div>
                <div>
                  <p class="text-xs text-content-muted mb-1">{$t("network.interface.mtu")}</p>
                  <p class="text-sm">{iface.mtu}</p>
                </div>
                <div>
                  <p class="text-xs text-content-muted mb-1">{$t("network.interface.type")}</p>
                  <p class="text-sm">{iface.type}</p>
                </div>
                <div>
                  <p class="text-xs text-content-muted mb-1">{$t("network.interface.speed")}</p>
                  <p class="text-sm">{iface.speed ? formatSpeed(iface.speed) : $t("common.unknown")}</p>
                </div>
                <div class="col-span-2 md:col-span-4">
                  <p class="text-xs text-content-muted mb-1">{$t("network.interface.addresses")}</p>
                  {#each iface.ip_addresses as ip}
                    <p class="text-sm font-mono break-all">{ip}</p>
                  {:else}
                    <p class="text-sm text-content-muted">{$t("network.interface.noAddresses")}</p>
                  {/each}
                </div>
              </div>

              <!-- Traffic from the WebSocket -->
              {#if wsIface}
                <div class="mt-4 pt-4 border-t border-border">
                  <p class="text-xs text-content-muted mb-3">{$t("network.interface.traffic")}</p>
                  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <p class="text-xs text-content-muted">{$t("network.interface.rxBytes")}</p>
                      <p class="text-sm text-status-success">{formatSize(wsIface.bytes_recv)}</p>
                    </div>
                    <div>
                      <p class="text-xs text-content-muted">{$t("network.interface.txBytes")}</p>
                      <p class="text-sm text-status-info">{formatSize(wsIface.bytes_sent)}</p>
                    </div>
                    <div>
                      <p class="text-xs text-content-muted">{$t("network.interface.rxRate")}</p>
                      <p class="text-sm text-status-success">{formatRate(wsIface.bytes_recv_rate)}</p>
                    </div>
                    <div>
                      <p class="text-xs text-content-muted">{$t("network.interface.txRate")}</p>
                      <p class="text-sm text-status-info">{formatRate(wsIface.bytes_sent_rate)}</p>
                    </div>
                  </div>
                </div>
              {/if}

              <!-- Actions -->
              <div class="mt-4 pt-4 border-t border-border flex gap-2">
                {#if iface.enabled}
                  <Button
                    variant="danger"
                    size="sm"
                    icon={PowerOff}
                    disabled={busy !== null}
                    on:click={() => (confirming = iface.name)}
                  >
                    {$t("network.interface.disable")}
                  </Button>
                {:else}
                  <Button
                    variant="success"
                    size="sm"
                    icon={Power}
                    loading={busy === iface.name}
                    disabled={busy !== null}
                    on:click={() => enable(iface.name)}
                  >
                    {$t("network.interface.enable")}
                  </Button>
                {/if}
              </div>
            </div>
          {/if}
        </section>
      {:else}
        {#if !loading && !loadErrors.interfaces}
          <EmptyState icon={Network} title={$t("network.interface.none")} />
        {/if}
      {/each}
    </div>
  {/if}

  <!-- Routes -->
  {#if activeTab === "routes"}
    <div class="card table-frame">
      <table class="table">
        <thead class="table-header">
          <tr>
            <th class="pr-4">{$t("network.routes.destination")}</th>
            <th class="pr-4">{$t("network.gateway")}</th>
            <th class="pr-4">{$t("network.routes.interface")}</th>
            <th>{$t("network.protocol")}</th>
          </tr>
        </thead>
        <tbody>
          {#each routes as route}
            <tr class="table-row">
              <td class="pr-4 font-mono break-all">{isDefault(route) ? $t("network.routes.default") : route.dst}</td>
              <td class="pr-4 font-mono break-all">{route.gateway || "-"}</td>
              <td class="pr-4">{route.dev}</td>
              <td class="text-content-secondary">{route.protocol || "-"}</td>
            </tr>
          {:else}
            <tr>
              <td colspan="4" class="py-8 text-center text-content-secondary">{$t("network.routes.none")}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <!-- Connections -->
  {#if activeTab === "connections"}
    <div class="card p-0 overflow-hidden">
      <div class="p-3 bg-surface-overlay flex justify-between items-center">
        <span class="text-sm text-content-secondary">{$t("network.connections.count", { n: connections.length })}</span>
        <Button variant="ghost" size="sm" on:click={loadConnections}>{$t("network.connections.refresh")}</Button>
      </div>
      <div class="table-frame max-h-96 overflow-y-auto px-3">
        <table class="table">
          <thead class="table-header sticky top-0 bg-surface-elevated">
            <tr>
              <th class="pt-3 pr-4">{$t("network.protocol")}</th>
              <th class="pt-3 pr-4">{$t("network.connections.state")}</th>
              <th class="pt-3 pr-4">{$t("network.connections.local")}</th>
              <th class="pt-3">{$t("network.connections.remote")}</th>
            </tr>
          </thead>
          <tbody>
            {#each connections as conn}
              <tr class="table-row">
                <td class="pr-4">
                  <Badge variant={conn.protocol === "tcp" ? "info" : "purple"}>{conn.protocol}</Badge>
                </td>
                <td class="pr-4">
                  <span class="text-xs {conn.state === 'ESTAB' ? 'text-status-success' : 'text-content-secondary'}">
                    {conn.state}
                  </span>
                </td>
                <td class="pr-4 font-mono text-xs break-all">{conn.local}</td>
                <td class="font-mono text-xs break-all">{conn.remote || "-"}</td>
              </tr>
            {:else}
              <tr>
                <td colspan="4" class="py-8 text-center text-content-secondary">{$t("network.connections.none")}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </div>
  {/if}

  <!-- DNS Details -->
  {#if dnsConfig && (dnsConfig.nameservers?.length > 1 || dnsConfig.search_domains?.length)}
    <section class="card" aria-labelledby="network-dns-title">
      <h3 id="network-dns-title" class="card-title mb-3">
        <Server class="w-4 h-4 text-content-secondary" />
        {$t("network.dnsConfig.title")}
      </h3>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <p class="text-xs text-content-muted mb-2">{$t("network.dnsConfig.servers")}</p>
          {#each dnsConfig.nameservers as ns}
            <p class="text-sm font-mono break-all">{ns}</p>
          {/each}
        </div>
        {#if dnsConfig.search_domains?.length}
          <div>
            <p class="text-xs text-content-muted mb-2">{$t("network.dnsConfig.domains")}</p>
            {#each dnsConfig.search_domains as domain}
              <p class="text-sm font-mono">{domain}</p>
            {/each}
          </div>
        {/if}
      </div>
    </section>
  {/if}
</div>

<!-- Disable asks first: the connection to UNOS may go through the interface (ADR-0244) -->
<Modal
  open={confirming !== null}
  title={$t("network.disable.title", { name: confirming ?? "" })}
  size="md"
  danger
  closable={busy === null}
  on:close={() => (confirming = null)}
>
  {#if confirming}
    <p>
      {$t("network.disable.dropBefore")} <strong class="text-content">{confirming}</strong>
      {$t("network.disable.dropAfter")}
    </p>
    <p class="mt-3">{$t("network.disable.revert", { n: revertSeconds, name: confirming })}</p>
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" disabled={busy !== null} on:click={() => (confirming = null)}>
      {$t("common.cancel")}
    </Button>
    <Button variant="danger" class="flex-1" loading={busy !== null} on:click={disable}>
      {$t("network.disable.confirm", { name: confirming ?? "" })}
    </Button>
  </svelte:fragment>
</Modal>
