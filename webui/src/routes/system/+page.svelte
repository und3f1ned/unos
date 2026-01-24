<script lang="ts">
  import { page } from "$app/stores";
  import {
    Settings,
    Gauge,
    FileText,
    Heart,
    Database,
    Power,
    Calendar,
    Bell,
    Network,
    Shield,
    ChevronRight
  } from "lucide-svelte";

  // Sub-navigation for System page
  const tabs = [
    { id: "general", icon: Settings, label: "General" },
    { id: "security", icon: Shield, label: "Security" },
    { id: "monitor", icon: Gauge, label: "Resource Monitor" },
    { id: "logs", icon: FileText, label: "System Logs" },
    { id: "health", icon: Heart, label: "Disk Health" },
    { id: "backup", icon: Database, label: "Backup & Restore" },
    { id: "scheduler", icon: Calendar, label: "Scheduler" },
    { id: "notifications", icon: Bell, label: "Notifications" },
    { id: "network", icon: Network, label: "Network" },
    { id: "power", icon: Power, label: "Power Management" },
  ];

  let activeTab = "general";

  // Reactively update activeTab when URL hash changes
  $: {
    const hash = $page.url.hash.slice(1);
    if (hash && tabs.some(t => t.id === hash)) {
      activeTab = hash;
    }
  }

  function setTab(id: string) {
    activeTab = id;
    history.replaceState(null, "", "#" + id);
  }
</script>

<div class="flex h-full -m-6">
  <!-- Sidebar tabs -->
  <div class="w-56 bg-slate-800 border-r border-slate-700 p-3 overflow-y-auto">
    <h2 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 px-2">System</h2>
    <nav class="space-y-1">
      {#each tabs as tab}
        <button
          on:click={() => setTab(tab.id)}
          class="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors {activeTab === tab.id ? 'bg-blue-600 text-white' : 'text-slate-300 hover:bg-slate-700'}"
        >
          <svelte:component this={tab.icon} class="w-4 h-4" />
          <span>{tab.label}</span>
          {#if activeTab === tab.id}
            <ChevronRight class="w-4 h-4 ml-auto" />
          {/if}
        </button>
      {/each}
    </nav>
  </div>

  <!-- Content area -->
  <div class="flex-1 overflow-auto p-6">
    {#if activeTab === "general"}
      {#await import("./tabs/GeneralTab.svelte") then { default: GeneralTab }}
        <GeneralTab />
      {/await}
    {:else if activeTab === "security"}
      {#await import("./tabs/SecurityTab.svelte") then { default: SecurityTab }}
        <SecurityTab />
      {/await}
    {:else if activeTab === "monitor"}
      {#await import("./tabs/MonitorTab.svelte") then { default: MonitorTab }}
        <MonitorTab />
      {/await}
    {:else if activeTab === "logs"}
      {#await import("./tabs/LogsTab.svelte") then { default: LogsTab }}
        <LogsTab />
      {/await}
    {:else if activeTab === "health"}
      {#await import("./tabs/HealthTab.svelte") then { default: HealthTab }}
        <HealthTab />
      {/await}
    {:else if activeTab === "backup"}
      {#await import("./tabs/BackupTab.svelte") then { default: BackupTab }}
        <BackupTab />
      {/await}
    {:else if activeTab === "scheduler"}
      {#await import("./tabs/SchedulerTab.svelte") then { default: SchedulerTab }}
        <SchedulerTab />
      {/await}
    {:else if activeTab === "notifications"}
      {#await import("./tabs/NotificationsTab.svelte") then { default: NotificationsTab }}
        <NotificationsTab />
      {/await}
    {:else if activeTab === "network"}
      {#await import("./tabs/NetworkTab.svelte") then { default: NetworkTab }}
        <NetworkTab />
      {/await}
    {:else if activeTab === "power"}
      {#await import("./tabs/PowerTab.svelte") then { default: PowerTab }}
        <PowerTab />
      {/await}
    {/if}
  </div>
</div>
