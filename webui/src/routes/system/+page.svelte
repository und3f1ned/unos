<script lang="ts">
  import { page } from "$app/stores";
  import { goto } from "$app/navigation";
  import { isAdmin } from "$lib/stores/auth";
  import { t } from "$lib/i18n";
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
    ChevronRight,
    ArrowUpCircle
  } from "lucide-svelte";

  // Sub-navigation for System page. Only the Security tab (own password,
  // 2FA and login history) works for regular accounts; the APIs behind the
  // other tabs are administrator-only. Reactive: the labels follow the language of the page.
  $: tabs = [
    { id: "general", icon: Settings, label: $t("system.tabs.general"), adminOnly: true },
    { id: "updates", icon: ArrowUpCircle, label: $t("system.tabs.updates"), adminOnly: true },
    { id: "security", icon: Shield, label: $t("system.tabs.security"), adminOnly: false },
    { id: "monitor", icon: Gauge, label: $t("system.tabs.monitor"), adminOnly: true },
    { id: "logs", icon: FileText, label: $t("system.tabs.logs"), adminOnly: true },
    { id: "health", icon: Heart, label: $t("system.tabs.health"), adminOnly: true },
    { id: "backup", icon: Database, label: $t("system.tabs.backup"), adminOnly: true },
    { id: "scheduler", icon: Calendar, label: $t("system.tabs.scheduler"), adminOnly: true },
    { id: "notifications", icon: Bell, label: $t("system.tabs.notifications"), adminOnly: true },
    { id: "network", icon: Network, label: $t("system.tabs.network"), adminOnly: true },
    { id: "power", icon: Power, label: $t("system.tabs.power"), adminOnly: true },
  ];

  $: visibleTabs = $isAdmin ? tabs : tabs.filter(t => !t.adminOnly);

  let activeTab = "general";

  // Follow the URL hash (links such as /system#notifications, /settings,
  // reloads, bookmarks) and never show a tab the user may not open (e.g.
  // /system#logs typed by a regular user). The work is done in a function so
  // that this statement depends only on the URL and the visible tabs. If it
  // read activeTab itself, every tab click would re-run it and switch straight
  // back to the tab named in the URL.
  $: syncTabFromHash($page.url.hash, visibleTabs);

  function syncTabFromHash(rawHash: string, available: typeof tabs) {
    const hash = rawHash.slice(1);
    if (hash && available.some(t => t.id === hash)) {
      activeTab = hash;
    } else if (!available.some(t => t.id === activeTab)) {
      activeTab = available[0].id;
    }
  }

  function setTab(id: string) {
    activeTab = id;
    // SvelteKit's goto, not history.replaceState: it updates $page.url and
    // keeps the router's state in the history entry, which Back/Forward need.
    goto(`#${id}`, { replaceState: true, noScroll: true, keepFocus: true });
  }
</script>

<div class="flex h-full -m-6">
  <!-- Sidebar tabs -->
  <div class="w-56 bg-slate-800 border-r border-slate-700 p-3 overflow-y-auto">
    <h2 class="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 px-2">{$isAdmin ? $t("system.heading.admin") : $t("system.heading.user")}</h2>
    <nav class="space-y-1">
      {#each visibleTabs as tab}
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
    {:else if activeTab === "updates"}
      {#await import("./tabs/UpdateTab.svelte") then { default: UpdateTab }}
        <UpdateTab />
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
