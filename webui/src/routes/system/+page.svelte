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
    ArrowUpCircle
  } from "lucide-svelte";

  // The System page's tabs (ADR-0428), in three groups: Settings, Monitoring, Maintenance; the
  // command palette lists them in this order (palette/choices.ts). Only the Security tab (own
  // password, 2FA and login history) works for regular accounts; the APIs behind the other tabs
  // are administrator-only. `form`: a tab of forms, which keeps to a readable width.
  // Reactive: the labels follow the language of the page.
  let tabs = $derived([
    { id: "general", group: "settings", icon: Settings, label: $t("system.tabs.general"), adminOnly: true, form: true },
    { id: "network", group: "settings", icon: Network, label: $t("system.tabs.network"), adminOnly: true, form: false },
    { id: "remote", group: "settings", icon: Globe, label: $t("remote.tab"), adminOnly: true, form: true },
    { id: "security", group: "settings", icon: Shield, label: $t("system.tabs.security"), adminOnly: false, form: true },
    { id: "monitor", group: "monitoring", icon: Gauge, label: $t("system.tabs.monitor"), adminOnly: true, form: false },
    { id: "logs", group: "monitoring", icon: FileText, label: $t("system.tabs.logs"), adminOnly: true, form: false },
    { id: "health", group: "monitoring", icon: Heart, label: $t("system.tabs.health"), adminOnly: true, form: false },
    { id: "notifications", group: "monitoring", icon: Bell, label: $t("system.tabs.notifications"), adminOnly: true, form: true },
    { id: "updates", group: "maintenance", icon: ArrowUpCircle, label: $t("system.tabs.updates"), adminOnly: true, form: true },
    { id: "backup", group: "maintenance", icon: Database, label: $t("system.tabs.backup"), adminOnly: true, form: true },
    { id: "scheduler", group: "maintenance", icon: Calendar, label: $t("system.tabs.scheduler"), adminOnly: true, form: false },
    { id: "power", group: "maintenance", icon: Power, label: $t("system.tabs.power"), adminOnly: true, form: true },
  ]);

  let visibleTabs = $derived($isAdmin ? tabs : tabs.filter(t => !t.adminOnly));

  const GROUPS = [
    { id: "settings", key: "system.group.settings" },
    { id: "monitoring", key: "system.group.monitoring" },
    { id: "maintenance", key: "system.group.maintenance" },
  ];
  // A regular user's one tab needs no group label.
  let groups = $derived(
    GROUPS.map((group) => ({ ...group, tabs: visibleTabs.filter((tab) => tab.group === group.id) }))
      .filter((group) => group.tabs.length > 0)
  );
  let labelled = $derived(visibleTabs.length > 1);

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

<!-- The page's h1, then from xl the tabs' groups in a 220 px column beside the tab; below xl
     three labelled rows of chips above it, so that the tab keeps the width (ADR-0428). The page
     scrolls as a whole. -->
<div class="page-container">
  <div class="page-header">
    <h1 id="system-title" class="page-title">{$isAdmin ? $t("system.heading.admin") : $t("system.heading.user")}</h1>
  </div>

  <div class="grid gap-6 items-start xl:grid-cols-[220px_minmax(0,1fr)]">
    <nav aria-labelledby="system-title" class="flex flex-col gap-2.5 min-w-0 xl:gap-[18px] xl:sticky xl:top-0">
      {#each groups as group (group.id)}
        <div class="min-w-0">
          {#if labelled}
            <p id="system-group-{group.id}" class="eyebrow mb-1.5 xl:mx-2.5">{$t(group.key)}</p>
          {/if}
          <ul
            class="flex flex-wrap gap-1.5 xl:flex-col xl:gap-0.5"
            aria-labelledby={labelled ? `system-group-${group.id}` : undefined}
          >
            {#each group.tabs as tab (tab.id)}
              {@const active = activeTab === tab.id}
              <li class="min-w-0">
                <Button variant="plain"
                  onclick={() => setTab(tab.id)}
                  aria-current={active ? "true" : undefined}
                  class="relative flex items-center gap-2 h-8 px-3 rounded border text-[13px] transition-colors xl:w-full xl:h-9 xl:px-2.5 xl:border-0 xl:text-sm {active
                    ? 'bg-brand/20 border-brand-ring text-content font-medium xl:before:absolute xl:before:left-0 xl:before:top-2 xl:before:bottom-2 xl:before:w-[3px] xl:before:rounded-r-[3px] xl:before:bg-brand-ring'
                    : 'bg-surface-elevated border-border text-content-secondary hover:bg-surface-overlay hover:text-content xl:bg-transparent'}"
                >
                  <tab.icon class="w-4 h-4 shrink-0 xl:w-[18px] xl:h-[18px] {active ? 'text-brand-ring' : 'text-content-muted'}" aria-hidden="true" />
                  <span class="truncate">{tab.label}</span>
                </Button>
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </nav>

    <!-- The open tab; a tab of forms keeps to a readable width. -->
    <div class="min-w-0 {visibleTabs.find((tab) => tab.id === activeTab)?.form ? 'content-narrow' : ''}">
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
</div>
