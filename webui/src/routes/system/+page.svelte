<script lang="ts">
  import { untrack } from "svelte";
  import { Button } from '$lib/components/ui';
  import { page } from "$app/state";
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
    Globe,
    ChevronRight,
    ArrowUpCircle
  } from "lucide-svelte";

  // Sub-navigation for System page. Only the Security tab (own password,
  // 2FA and login history) works for regular accounts; the APIs behind the
  // other tabs are administrator-only. Reactive: the labels follow the language of the page.
  let tabs = $derived([
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
    { id: "remote", icon: Globe, label: $t("remote.tab"), adminOnly: true },
    { id: "power", icon: Power, label: $t("system.tabs.power"), adminOnly: true },
  ]);

  let visibleTabs = $derived($isAdmin ? tabs : tabs.filter(t => !t.adminOnly));

  let activeTab = $state("general");

  // Follow the URL hash (links such as /system#notifications, /settings,
  // reloads, bookmarks) and never show a tab the user may not open (e.g.
  // /system#logs typed by a regular user). It runs before the page is drawn, also the first
  // time, and depends only on the URL and the visible tabs: syncTabFromHash runs untracked. If it
  // depended on activeTab, every tab click would re-run it and switch straight back to the tab
  // named in the URL.
  $effect.pre(() => {
    const hash = page.url.hash;
    const available = visibleTabs;
    untrack(() => syncTabFromHash(hash, available));
  });

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
    // SvelteKit's goto, not history.replaceState: it updates page.url and
    // keeps the router's state in the history entry, which Back/Forward need.
    goto(`#${id}`, { replaceState: true, noScroll: true, keepFocus: true });
  }
</script>

<!-- From md the tabs are a sidebar beside the tab, which scrolls on its own; below md they wrap
     above it, and the page scrolls as a whole (ADR-0302). -->
<div class="flex flex-col md:flex-row md:h-full -m-4 md:-m-6">
  <!-- Sidebar tabs -->
  <div class="md:w-56 md:shrink-0 bg-surface-elevated border-b md:border-b-0 md:border-r border-border p-3 md:overflow-y-auto">
    <h2 class="text-xs font-semibold text-content-muted uppercase tracking-wider mb-3 px-2">{$isAdmin ? $t("system.heading.admin") : $t("system.heading.user")}</h2>
    <nav class="flex flex-wrap gap-1 md:block md:space-y-1">
      {#each visibleTabs as tab}
        <Button variant="plain"
          onclick={() => setTab(tab.id)}
          class="md:w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors {activeTab === tab.id ? 'bg-brand text-content-on-brand' : 'text-content-soft hover:bg-surface-overlay'}"
        >
          <tab.icon class="w-4 h-4" />
          <span>{tab.label}</span>
          {#if activeTab === tab.id}
            <ChevronRight class="w-4 h-4 ml-auto hidden md:block" />
          {/if}
        </Button>
      {/each}
    </nav>
  </div>

  <!-- Content area -->
  <div class="min-w-0 md:flex-1 md:overflow-auto p-4 md:p-6">
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
    {:else if activeTab === "remote"}
      {#await import("./tabs/RemoteAccessTab.svelte") then { default: RemoteAccessTab }}
        <RemoteAccessTab />
      {/await}
    {:else if activeTab === "power"}
      {#await import("./tabs/PowerTab.svelte") then { default: PowerTab }}
        <PowerTab />
      {/await}
    {/if}
  </div>
</div>
