<script lang="ts">
  // System > Network (ADR-0244): the host's interfaces with Disable, which comes back on by itself
  // unless it is kept, and Enable; bonds, VLANs and bridges; the routes, the sockets and DNS.
  import { onDestroy, onMount, tick, untrack } from "svelte";
  import {
    bringInterfaceUp,
    bringInterfaceDown,
    keepInterfaceDown,
    type NetworkInterface,
  } from "$lib/api/network";
  import { ApiError } from "$lib/api/client";
  import { Alert, Button, EmptyState, Skeleton, Tabs, confirm, toast } from "$lib/components/ui";
  import ConnectionsTable from "$lib/components/network/ConnectionsTable.svelte";
  import { NetworkData } from "$lib/components/network/data.svelte";
  import DnsDetails from "$lib/components/network/DnsDetails.svelte";
  import InterfaceCard from "$lib/components/network/InterfaceCard.svelte";
  import { labelOf, messageOf, without } from "$lib/components/network/network";
  import PendingDisables from "$lib/components/network/PendingDisables.svelte";
  import RoutesTable from "$lib/components/network/RoutesTable.svelte";
  import VirtualInterfacesCard from "$lib/components/network/VirtualInterfacesCard.svelte";
  import AppNamesCard from "$lib/components/remote/AppNamesCard.svelte";
  import { t } from "$lib/i18n";
  import { stats, type NetworkInterface as WSNetworkInterface } from "$lib/stores/stats";
  import { Network, RefreshCw } from "lucide-svelte";

  // Once the time to keep a disable has run out, the page reads the interfaces
  // again after the host's revert had a few seconds to turn it back on (ADR-0244).
  const REVERT_GRACE_MS = 5000;

  type View = "interfaces" | "routes" | "connections";
  const VIEWS: { id: View }[] = [{ id: "interfaces" }, { id: "routes" }, { id: "connections" }];
  // The name of each part of the page, a view or what could not be loaded: keys of the catalog.
  const PARTS: Record<string, string> = {
    interfaces: "network.interfaces",
    dns: "network.dns",
    routes: "network.routes",
    connections: "network.connections",
  };

  // What the page reads from the host; every list of interfaces it reads moves the countdowns.
  const net = new NetworkData(followReverts);

  let activeTab = $state<View>("interfaces");
  let expandedInterface = $state<string | null>(null);

  // Disables that wait to be kept: interface -> when it comes back on by itself (Date.now()).
  let deadlines = $state<Record<string, number>>({});
  // Disables whose time has run out, until the page has read the interfaces again.
  let expiring: string[] = [];
  let now = $state(Date.now());
  let ticker: ReturnType<typeof setInterval> | null = null;
  const timeouts = new Set<ReturnType<typeof setTimeout>>();
  // The countdowns: the focus goes there after a Disable, one Tab away from its Keep.
  let pendingList = $state.raw<PendingDisables | null>(null);

  // The interface the Disable dialog asks about, and the one being changed.
  let busy = $state<string | null>(null);
  let actionError = $state<string | null>(null);
  // What the host did with a disable that was not kept: read on the page, by the countdown's place.
  let notice = $state<string | null>(null);

  // Get WebSocket interface stats for rates
  let wsInterfaces = $derived($stats?.network?.interfaces ?? []);
  // Runs whenever the deadlines are set, before the page is drawn (as the statement of Svelte 4
  // did), so that a countdown shows the seconds counted from this moment.
  $effect.pre(() => {
    const active = Object.keys(deadlines).length > 0;
    untrack(() => syncTicker(active));
  });
  // The newest countdown first: after a Disable, one Tab from the focus reaches the
  // Keep of the interface just disabled (ADR-0246).
  let countdowns = $derived(Object.entries(deadlines).sort(([, a], [, b]) => b - a));

  onMount(() => {
    net.loadAll();
  });

  onDestroy(() => {
    if (ticker) clearInterval(ticker);
    for (const timeout of timeouts) clearTimeout(timeout);
  });

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
      // once to see whether the host turned it back on (ADR-0365).
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

  // A countdown that this page's Keep or Enable ended.
  function endCountdown(name: string) {
    deadlines = without(deadlines, name);
    expiring = expiring.filter((n) => n !== name);
  }

  // The page's time to keep a disable ran out a few seconds ago: report what the
  // host shows now, whatever it is (ADR-0244, ADR-0246).
  async function checkRevert(name: string) {
    const loaded = await net.loadInterfaces();
    // Kept or enabled from this page in the meantime.
    if (!expiring.includes(name)) return;
    expiring = expiring.filter((n) => n !== name);
    const iface = net.interfaces.find((i) => i.name === name);
    if (!loaded) {
      actionError = $t("network.revert.unchecked", { name });
    } else if (!iface) {
      actionError = $t("network.revert.gone", { name });
    } else if (iface.enabled) {
      notice = $t("network.revert.back", { name });
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

  function startChange(name: string) {
    busy = name;
    actionError = null;
    notice = null;
  }

  // Disable asks first: the connection to UNOS may go through the interface, and the host turns it
  // back on by itself unless the disable is kept in time (ADR-0244).
  async function disable(name: string) {
    const yes = await confirm({
      title: $t("network.disable.title", { name }),
      message: $t("network.disable.drop", { name }),
      consequences: [$t("network.disable.revert", { n: net.revertSeconds, name })],
      confirmLabel: $t("network.disable.confirm", { name })
    });
    if (!yes) return;
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
        notice = $t("network.pending.late", { name });
      }
    } catch (e) {
      actionError = e instanceof ApiError ? e.message : $t("network.pending.lost", { name, n: net.revertSeconds });
    } finally {
      busy = null;
    }
    if (counting) {
      await tick();
      pendingList?.focus();
    }
    await net.loadInterfaces();
  }

  async function keep(name: string) {
    startChange(name);
    let lost = false;
    try {
      await keepInterfaceDown(name);
      toast.success($t("network.pending.kept", { name }));
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
    const loaded = await net.loadInterfaces();
    const iface = net.interfaces.find((i) => i.name === name);
    if (lost && loaded && iface && !iface.enabled && iface.revert_in === null) {
      actionError = null;
      toast.success($t("network.pending.kept", { name }));
      endCountdown(name);
    }
  }

  async function enable(name: string) {
    startChange(name);
    try {
      await bringInterfaceUp(name);
      endCountdown(name);
    } catch (e) {
      actionError = messageOf(e);
    } finally {
      busy = null;
    }
    await net.loadInterfaces();
  }

  function toggleExpand(name: string) {
    expandedInterface = expandedInterface === name ? null : name;
  }
</script>

<div class="space-y-6">
  <!-- Header -->
  <div class="flex items-center justify-between gap-3">
    <h2 class="text-xl font-semibold">{$t("network.title")}</h2>
    <Button variant="ghost" size="icon" icon={RefreshCw} loading={net.loading} title={$t("common.refresh")} onclick={() => net.loadAll()}>
      <span class="sr-only">{$t("common.refresh")}</span>
    </Button>
  </div>

  {#if actionError}
    <Alert variant="error" dismissible ondismiss={() => (actionError = null)}>{actionError}</Alert>
  {/if}
  {#if notice}
    <Alert variant="info" dismissible ondismiss={() => (notice = null)}>{notice}</Alert>
  {/if}
  {#if Object.keys(net.loadErrors).length > 0}
    <Alert variant="error" title={$t("network.loadError")}>
      {#each Object.entries(net.loadErrors) as [part, text] (part)}
        <p>{labelOf($t, PARTS, part)}: {text}</p>
      {/each}
    </Alert>
  {/if}

  <!-- Disables that come back on by themselves unless they are kept (ADR-0244) -->
  {#if countdowns.length > 0}
    <PendingDisables bind:this={pendingList} {countdowns} {now} {busy} onkeep={keep} />
  {/if}

  <!-- Views: page tabs (Tabs, ADR-0432), each view the panel below. -->
  <Tabs
    tabs={VIEWS.map((view) => ({ id: view.id, label: $t(PARTS[view.id]) }))}
    active={activeTab}
    label={$t("network.title")}
    tabId={(id) => `network-tab-${id}`}
    panelId={(id) => `network-panel-${id}`}
    onselect={(id) => (activeTab = id)}
  />

  <div role="tabpanel" id="network-panel-{activeTab}" aria-labelledby="network-tab-{activeTab}" class="space-y-6">
  <!-- Interfaces -->
  {#if activeTab === "interfaces"}
    <div class="space-y-3">
      {#each net.interfaces as iface (iface.name)}
        <InterfaceCard
          {iface}
          wsIface={getWsInterface(iface.name)}
          expanded={expandedInterface === iface.name}
          {busy}
          ontoggle={() => toggleExpand(iface.name)}
          ondisable={() => disable(iface.name)}
          onenable={() => enable(iface.name)}
        />
      {:else}
        {#if net.loading && !net.loadErrors.interfaces}
          <!-- The first load (ADR-0430): the interfaces' cards; a reload keeps them. -->
          <div class="space-y-3" aria-busy="true">
            <Skeleton shape="block" class="h-[72px] w-full rounded-card" />
            <Skeleton shape="block" class="h-[72px] w-full rounded-card" />
          </div>
        {:else if !net.loadErrors.interfaces}
          <EmptyState icon={Network} title={$t("network.interface.none")} />
        {/if}
      {/each}
    </div>

    <!-- Bonds, VLANs and bridges (ADR-0424): a change lists the interfaces again -->
    <VirtualInterfacesCard onchanged={() => net.loadInterfaces()} />
  {/if}

  <!-- Routes -->
  {#if activeTab === "routes"}
    <RoutesTable routes={net.routes} />
  {/if}

  <!-- Connections -->
  {#if activeTab === "connections"}
    <ConnectionsTable connections={net.connections} onrefresh={() => net.loadConnections()} />
  {/if}
  </div>

  <!-- The host's name servers and search domains: their one place (ADR-0431). The default gateway
       is the default route of Routes, the number of sockets the head of Connections. -->
  {#if net.dnsConfig}
    <DnsDetails dnsConfig={net.dnsConfig} />
  {/if}

  <!-- The apps' names on the LAN (ADR-0420), which reach the LAN only: no remote access. -->
  <AppNamesCard />
</div>
