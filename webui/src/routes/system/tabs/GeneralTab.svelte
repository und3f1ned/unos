<script lang="ts">
  import { onMount } from "svelte";
  import { getSystemInfo, type SystemInfo } from "$lib/api/system";
  import { getCurrentVersion, type CurrentVersion } from "$lib/api/updates";
  import { user } from "$lib/stores/auth";
  import { Badge, Button, Card } from "$lib/components/ui";
  import { HostSettings, HostSettingsCard } from "$lib/components/system/general";
  import { t } from "$lib/i18n";
  import { apiTime } from "$lib/time";
  import { Server, User, Package } from "lucide-svelte";

  let systemInfo = $state<SystemInfo | null>(null);
  let currentVersion = $state<CurrentVersion | null>(null);
  let loading = $state(true);

  // The name and time zone of the host (ADR-0331).
  const host = new HostSettings();

  function renamed(hostname: string) {
    if (systemInfo) systemInfo = { ...systemInfo, hostname };
  }

  onMount(async () => {
    host.load();
    try {
      const [info, version] = await Promise.all([
        getSystemInfo(),
        getCurrentVersion(),
      ]);
      systemInfo = info;
      currentVersion = version;
    } catch (e) {
      console.error("Failed to load system info:", e);
    } finally {
      loading = false;
    }
  });

  // Reactive, so that the uptime follows a change of the language.
  let formatUptime = $derived((bootTime: string): string => {
    const booted = apiTime(bootTime);
    if (!booted) return bootTime;
    const seconds = Math.floor((Date.now() - booted.getTime()) / 1000);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) return $t("general.uptime.days", { days, hours, minutes });
    if (hours > 0) return $t("general.uptime.hours", { hours, minutes });
    return $t("general.uptime.minutes", { minutes });
  });

  // A version in the language of the page: the server answers "unknown" when UNOS was built
  // without one (get_current_version of supervisor/src/updates/updater.py).
  let versionLabel = $derived((version: string): string => (version === "unknown" ? $t("general.version.unknown") : version));
</script>

{#snippet info(label: string, value: string, valueClass: string)}
  <div>
    <span class="text-content-secondary">{label}</span>
    <span class={valueClass}>{value}</span>
  </div>
{/snippet}

<div class="space-y-6">
  <h2 class="text-xl font-semibold">{$t("general.title")}</h2>

  <!-- System Info -->
  <div class="bg-surface-elevated rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Server class="w-5 h-5" />
      {$t("general.info.title")}
    </h3>

    {#if loading}
      <p class="text-content-muted">{$t("common.loading")}</p>
    {:else if systemInfo}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
        {@render info($t("general.info.hostname"), systemInfo.hostname, "ml-2 font-medium break-words")}
        {@render info($t("general.info.os"), systemInfo.os, "ml-2")}
        {@render info($t("general.info.kernel"), systemInfo.kernel, "ml-2 font-mono text-xs")}
        {@render info($t("general.info.architecture"), systemInfo.architecture, "ml-2")}
        {@render info($t("general.info.uptime"), formatUptime(systemInfo.boot_time), "ml-2")}
      </div>
    {:else}
      <p class="text-content-muted">{$t("general.info.loadFailed")}</p>
    {/if}
  </div>

  <HostSettingsCard {host} onrenamed={renamed} />

  <!-- UNOS Version: the version the supervisor image was built with (ADR-0234) -->
  <Card>
    <h3 class="section-title mb-4">
      <Package class="w-5 h-5" />
      {$t("general.version.title")}
    </h3>

    {#if loading}
      <p class="text-sm text-muted">{$t("common.loading")}</p>
    {:else if currentVersion}
      <p class="text-sm flex flex-wrap items-center gap-2">
        <span class="text-secondary">{$t("general.version.label")}</span>
        <span class="mono">{versionLabel(currentVersion.version)}</span>
        {#if currentVersion.is_development}
          <Badge variant="warning">{$t("general.version.dev")}</Badge>
        {/if}
      </p>
      <Button href="#updates" variant="secondary" size="sm" class="mt-3">{$t("general.version.check")}</Button>
    {:else}
      <p class="text-sm text-muted">{$t("general.version.unavailable")}</p>
    {/if}
  </Card>

  <!-- User Account -->
  <div class="bg-surface-elevated rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <User class="w-5 h-5" />
      {$t("general.user.title")}
    </h3>

    {#if $user}
      <div class="space-y-3">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div>
            <span class="text-content-secondary">{$t("general.user.username")}</span>
            <span class="ml-2 font-medium">{$user.username}</span>
          </div>
          <div>
            <span class="text-content-secondary">{$t("general.user.role")}</span>
            <span class="ml-2">
              {#if $user.is_admin}
                <span class="px-2 py-0.5 bg-status-info/20 text-status-info rounded text-xs">{$t("general.user.admin")}</span>
              {:else}
                <span class="px-2 py-0.5 bg-status-neutral/20 text-content-secondary rounded text-xs">{$t("general.user.user")}</span>
              {/if}
            </span>
          </div>
        </div>
        <a
          href="/users"
          class="inline-block px-4 py-2 bg-surface-overlay hover:bg-surface-hover rounded-lg transition-colors text-sm"
        >
          {$t("general.user.manage")}
        </a>
      </div>
    {:else}
      <p class="text-content-muted">{$t("general.user.signedOut")}</p>
    {/if}
  </div>
</div>
