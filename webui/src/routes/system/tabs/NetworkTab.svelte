<script lang="ts">
  import { onMount } from "svelte";
  import {
    getInterfaces,
    getDNSConfig,
    getDefaultGateway,
    getRoutes,
    getConnections,
    bringInterfaceUp,
    bringInterfaceDown,
    type NetworkInterface,
    type DNSConfig,
    type Route,
    type GatewayInfo,
    type Connection,
  } from "$lib/api/network";
  import {
    stats,
    wsConnected,
    formatBytes,
    formatBytesPerSecond,
    type NetworkInterface as WSNetworkInterface
  } from "$lib/stores/stats";
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

  let interfaces: NetworkInterface[] = [];
  let dnsConfig: DNSConfig | null = null;
  let gateway: GatewayInfo | null = null;
  let routes: Route[] = [];
  let connections: Connection[] = [];

  let loading = true;
  let activeTab: "interfaces" | "routes" | "connections" = "interfaces";
  let expandedInterface: string | null = null;

  // Get WebSocket interface stats for rates
  $: wsInterfaces = $stats?.network?.interfaces ?? [];

  onMount(() => {
    loadAll();
  });

  async function loadAll() {
    loading = true;
    await Promise.all([
      loadInterfaces(),
      loadDNS(),
      loadGateway(),
      loadRoutes(),
      loadConnections(),
    ]);
    loading = false;
  }

  async function loadInterfaces() {
    try {
      const result = await getInterfaces();
      interfaces = result.interfaces;
    } catch (e) {
      console.error(e);
    }
  }

  async function loadDNS() {
    try {
      dnsConfig = await getDNSConfig();
    } catch (e) {
      console.error(e);
    }
  }

  async function loadGateway() {
    try {
      gateway = await getDefaultGateway();
    } catch (e) {
      console.error(e);
    }
  }

  async function loadRoutes() {
    try {
      const result = await getRoutes();
      routes = result.routes;
    } catch (e) {
      console.error(e);
    }
  }

  async function loadConnections() {
    try {
      const result = await getConnections();
      connections = result.connections;
    } catch (e) {
      console.error(e);
    }
  }

  // Get WebSocket stats for an interface
  function getWsInterface(name: string): WSNetworkInterface | undefined {
    return wsInterfaces.find(i => i.name === name);
  }

  function isUp(state: string): boolean {
    return state === "UP" || state === "up";
  }

  async function toggleInterface(name: string, currentState: string) {
    try {
      if (isUp(currentState)) {
        await bringInterfaceDown(name);
      } else {
        await bringInterfaceUp(name);
      }
      await loadInterfaces();
    } catch (e) {
      console.error(e);
    }
  }

  function toggleExpand(name: string) {
    expandedInterface = expandedInterface === name ? null : name;
  }
</script>

<div class="space-y-6">
  <!-- Header -->
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold flex items-center gap-2">
      <Network class="w-6 h-6 text-blue-400" />
      Network
    </h2>
    <div class="flex items-center gap-2">
      {#if $wsConnected}
        <span class="text-xs text-green-500">Live</span>
      {/if}
      <button
        on:click={loadAll}
        class="p-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
        disabled={loading}
      >
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </button>
    </div>
  </div>

  <!-- Summary Cards -->
  <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
    <!-- Gateway -->
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-2 text-slate-400 text-sm mb-2">
        <Globe class="w-4 h-4" />
        Default Gateway
      </div>
      <p class="text-lg font-semibold">{gateway?.gateway || "Not set"}</p>
      {#if gateway?.interface}
        <p class="text-xs text-slate-500">via {gateway.interface}</p>
      {/if}
    </div>

    <!-- DNS -->
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-2 text-slate-400 text-sm mb-2">
        <Server class="w-4 h-4" />
        DNS Servers
      </div>
      {#if dnsConfig?.nameservers?.length}
        <p class="text-lg font-semibold">{dnsConfig.nameservers[0]}</p>
        {#if dnsConfig.nameservers.length > 1}
          <p class="text-xs text-slate-500">+{dnsConfig.nameservers.length - 1} more</p>
        {/if}
      {:else}
        <p class="text-slate-500">Not configured</p>
      {/if}
    </div>

    <!-- Active Connections -->
    <div class="bg-slate-800 rounded-lg p-4">
      <div class="flex items-center gap-2 text-slate-400 text-sm mb-2">
        <Link2 class="w-4 h-4" />
        Active Connections
      </div>
      <p class="text-lg font-semibold">{connections.length}</p>
      <p class="text-xs text-slate-500">TCP/UDP sockets</p>
    </div>
  </div>

  <!-- Tabs -->
  <div class="flex gap-2 border-b border-slate-700 pb-2">
    <button
      class="px-4 py-2 rounded-t-lg transition-colors {activeTab === 'interfaces' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'}"
      on:click={() => (activeTab = "interfaces")}
    >
      <span class="flex items-center gap-2">
        <Cable class="w-4 h-4" />
        Interfaces
      </span>
    </button>
    <button
      class="px-4 py-2 rounded-t-lg transition-colors {activeTab === 'routes' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'}"
      on:click={() => (activeTab = "routes")}
    >
      <span class="flex items-center gap-2">
        <Router class="w-4 h-4" />
        Routes
      </span>
    </button>
    <button
      class="px-4 py-2 rounded-t-lg transition-colors {activeTab === 'connections' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'}"
      on:click={() => (activeTab = "connections")}
    >
      <span class="flex items-center gap-2">
        <Activity class="w-4 h-4" />
        Connections
      </span>
    </button>
  </div>

  <!-- Interfaces Tab -->
  {#if activeTab === "interfaces"}
    <div class="space-y-3">
      {#each interfaces as iface}
        {@const wsIface = getWsInterface(iface.name)}
        {@const ifaceIsUp = isUp(iface.state)}
        <div class="bg-slate-800 rounded-lg overflow-hidden">
          <!-- Interface Header -->
          <button
            class="w-full p-4 flex items-center justify-between hover:bg-slate-750 transition-colors"
            on:click={() => toggleExpand(iface.name)}
          >
            <div class="flex items-center gap-4">
              <div class="p-2 rounded-lg {ifaceIsUp ? 'bg-green-500/20' : 'bg-slate-700'}">
                {#if iface.type === "ether" || iface.type === "ethernet"}
                  <Cable class="w-5 h-5 {ifaceIsUp ? 'text-green-400' : 'text-slate-400'}" />
                {:else if iface.type === "wlan" || iface.name.startsWith("wlan")}
                  <Wifi class="w-5 h-5 {ifaceIsUp ? 'text-green-400' : 'text-slate-400'}" />
                {:else}
                  <Network class="w-5 h-5 {ifaceIsUp ? 'text-green-400' : 'text-slate-400'}" />
                {/if}
              </div>
              <div class="text-left">
                <div class="flex items-center gap-2">
                  <span class="font-semibold">{iface.name}</span>
                  <span class="px-2 py-0.5 text-xs rounded {ifaceIsUp ? 'bg-green-500/20 text-green-400' : 'bg-slate-600 text-slate-400'}">
                    {iface.state}
                  </span>
                </div>
                <p class="text-sm text-slate-400">
                  {iface.ip_addresses?.length ? iface.ip_addresses[0] : "No IP"}
                </p>
              </div>
            </div>

            <div class="flex items-center gap-6">
              <!-- Live Speed from WebSocket -->
              {#if wsIface && ifaceIsUp}
                <div class="flex items-center gap-4 text-sm">
                  <span class="flex items-center gap-1 text-green-400">
                    <ArrowDown class="w-3 h-3" />
                    {formatBytesPerSecond(wsIface.bytes_recv_rate)}
                  </span>
                  <span class="flex items-center gap-1 text-blue-400">
                    <ArrowUp class="w-3 h-3" />
                    {formatBytesPerSecond(wsIface.bytes_sent_rate)}
                  </span>
                </div>
              {/if}

              {#if expandedInterface === iface.name}
                <ChevronUp class="w-5 h-5 text-slate-400" />
              {:else}
                <ChevronDown class="w-5 h-5 text-slate-400" />
              {/if}
            </div>
          </button>

          <!-- Expanded Details -->
          {#if expandedInterface === iface.name}
            <div class="px-4 pb-4 border-t border-slate-700 pt-4">
              <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <!-- MAC -->
                <div>
                  <p class="text-xs text-slate-500 mb-1">MAC Address</p>
                  <p class="text-sm font-mono">{iface.mac || "N/A"}</p>
                </div>

                <!-- MTU -->
                <div>
                  <p class="text-xs text-slate-500 mb-1">MTU</p>
                  <p class="text-sm">{iface.mtu}</p>
                </div>

                <!-- Type -->
                <div>
                  <p class="text-xs text-slate-500 mb-1">Type</p>
                  <p class="text-sm">{iface.type}</p>
                </div>

                <!-- IP Addresses -->
                <div>
                  <p class="text-xs text-slate-500 mb-1">IP Addresses</p>
                  {#if iface.ip_addresses?.length}
                    {#each iface.ip_addresses as ip}
                      <p class="text-sm">{ip}</p>
                    {/each}
                  {:else}
                    <p class="text-sm text-slate-500">None</p>
                  {/if}
                </div>
              </div>

              <!-- Traffic Stats from WebSocket -->
              {#if wsIface}
                <div class="mt-4 pt-4 border-t border-slate-700">
                  <p class="text-xs text-slate-500 mb-3">Traffic Statistics (Live)</p>
                  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <p class="text-xs text-slate-500">RX Bytes</p>
                      <p class="text-sm text-green-400">{formatBytes(wsIface.bytes_recv)}</p>
                    </div>
                    <div>
                      <p class="text-xs text-slate-500">TX Bytes</p>
                      <p class="text-sm text-blue-400">{formatBytes(wsIface.bytes_sent)}</p>
                    </div>
                    <div>
                      <p class="text-xs text-slate-500">RX Rate</p>
                      <p class="text-sm text-green-400">{formatBytesPerSecond(wsIface.bytes_recv_rate)}</p>
                    </div>
                    <div>
                      <p class="text-xs text-slate-500">TX Rate</p>
                      <p class="text-sm text-blue-400">{formatBytesPerSecond(wsIface.bytes_sent_rate)}</p>
                    </div>
                  </div>
                </div>
              {/if}

              <!-- Actions -->
              <div class="mt-4 pt-4 border-t border-slate-700 flex gap-2">
                <button
                  on:click|stopPropagation={() => toggleInterface(iface.name, iface.state)}
                  class="flex items-center gap-2 px-3 py-1.5 rounded text-sm transition-colors
                    {ifaceIsUp
                      ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                      : 'bg-green-500/20 text-green-400 hover:bg-green-500/30'}"
                >
                  {#if ifaceIsUp}
                    <PowerOff class="w-4 h-4" />
                    Disable
                  {:else}
                    <Power class="w-4 h-4" />
                    Enable
                  {/if}
                </button>
              </div>
            </div>
          {/if}
        </div>
      {:else}
        <p class="text-slate-400 text-center py-8">No network interfaces found</p>
      {/each}
    </div>
  {/if}

  <!-- Routes Tab -->
  {#if activeTab === "routes"}
    <div class="bg-slate-800 rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-slate-700">
          <tr>
            <th class="text-left p-3 font-medium">Destination</th>
            <th class="text-left p-3 font-medium">Gateway</th>
            <th class="text-left p-3 font-medium">Interface</th>
            <th class="text-left p-3 font-medium">Protocol</th>
          </tr>
        </thead>
        <tbody>
          {#each routes as route}
            <tr class="border-t border-slate-700 hover:bg-slate-750">
              <td class="p-3 font-mono">{route.dst || "default"}</td>
              <td class="p-3 font-mono">{route.gateway || "-"}</td>
              <td class="p-3">{route.dev}</td>
              <td class="p-3 text-slate-400">{route.protocol || "-"}</td>
            </tr>
          {:else}
            <tr>
              <td colspan="4" class="p-8 text-center text-slate-400">No routes found</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <!-- Connections Tab -->
  {#if activeTab === "connections"}
    <div class="bg-slate-800 rounded-lg overflow-hidden">
      <div class="p-3 bg-slate-700 flex justify-between items-center">
        <span class="text-sm text-slate-300">{connections.length} active connections</span>
        <button on:click={loadConnections} class="text-sm text-blue-400 hover:text-blue-300">
          Refresh
        </button>
      </div>
      <div class="max-h-96 overflow-y-auto">
        <table class="w-full text-sm">
          <thead class="bg-slate-750 sticky top-0">
            <tr>
              <th class="text-left p-3 font-medium">Protocol</th>
              <th class="text-left p-3 font-medium">State</th>
              <th class="text-left p-3 font-medium">Local</th>
              <th class="text-left p-3 font-medium">Remote</th>
            </tr>
          </thead>
          <tbody>
            {#each connections as conn}
              <tr class="border-t border-slate-700 hover:bg-slate-750">
                <td class="p-3">
                  <span class="px-2 py-0.5 rounded text-xs {conn.protocol === 'tcp' ? 'bg-blue-500/20 text-blue-400' : 'bg-purple-500/20 text-purple-400'}">
                    {conn.protocol}
                  </span>
                </td>
                <td class="p-3">
                  <span class="text-xs {conn.state === 'ESTAB' ? 'text-green-400' : 'text-slate-400'}">
                    {conn.state}
                  </span>
                </td>
                <td class="p-3 font-mono text-xs">{conn.local}</td>
                <td class="p-3 font-mono text-xs">{conn.remote || "-"}</td>
              </tr>
            {:else}
              <tr>
                <td colspan="4" class="p-8 text-center text-slate-400">No active connections</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </div>
  {/if}

  <!-- DNS Details -->
  {#if dnsConfig && (dnsConfig.nameservers?.length > 1 || dnsConfig.search_domains?.length)}
    <div class="bg-slate-800 rounded-lg p-4">
      <h3 class="font-semibold mb-3 flex items-center gap-2">
        <Server class="w-4 h-4 text-slate-400" />
        DNS Configuration
      </h3>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <p class="text-xs text-slate-500 mb-2">Name Servers</p>
          {#each dnsConfig.nameservers as ns}
            <p class="text-sm font-mono">{ns}</p>
          {/each}
        </div>
        {#if dnsConfig.search_domains?.length}
          <div>
            <p class="text-xs text-slate-500 mb-2">Search Domains</p>
            {#each dnsConfig.search_domains as domain}
              <p class="text-sm font-mono">{domain}</p>
            {/each}
          </div>
        {/if}
      </div>
    </div>
  {/if}
</div>
