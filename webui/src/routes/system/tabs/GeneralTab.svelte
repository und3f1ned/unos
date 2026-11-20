<script lang="ts">
  import { onMount } from "svelte";
  import { getSystemInfo, type SystemInfo } from "$lib/api/system";
  import { getCurrentVersion, type CurrentVersion } from "$lib/api/updates";
  import { user } from "$lib/stores/auth";
  import { Badge, Button, Card, Skeleton } from "$lib/components/ui";
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

<!-- A fact as label over value (.kv, ADR-0428). -->
{#snippet info(label: string, value: string, valueClass: string)}
  <div>
    <span class="kv-label">{label}</span>
    <span class="kv-value {valueClass}">{value}</span>
  </div>
{/snippet}

<!-- A card's title as it will be, and its facts. -->
{#snippet pendingCard(facts: number, button: boolean)}
  <div class="card">
    <Skeleton size="base" class="w-40 mb-4" />
    <div class="kv">
      {#each Array.from({ length: facts }, (_, i) => i) as fact (fact)}
        <div>
          <Skeleton size="xs" class="w-16" />
          <Skeleton class="w-28" />
        </div>
      {/each}
    </div>
    {#if button}
      <Skeleton shape="block" class="mt-3 h-8 w-36" />
    {/if}
  </div>
{/snippet}

<div class="space-y-4">
  <h2 class="text-xl font-semibold">{$t("general.title")}</h2>

  {#if loading || host.loading}
  <!-- The first load (ADR-0430): the four cards in their places, swapped for them at once. -->
  <div class="space-y-4" aria-busy="true">
    {@render pendingCard(5, false)}
    <div class="card">
      <Skeleton size="base" class="w-40 mb-4" />
      <div class="space-y-4">
        {#each [0, 1] as field (field)}
          <div>
            <Skeleton size="xs" class="w-24 mb-1" />
            <Skeleton shape="block" class="h-9 w-full" />
            <Skeleton size="xs" class="mt-1 w-2/3" />
          </div>
        {/each}
      </div>
    </div>
    {@render pendingCard(1, true)}
    {@render pendingCard(2, true)}
  </div>
  {:else}
  <!-- System Info -->
  <Card>
    <h3 class="card-title mb-4">
      <Server class="w-5 h-5 text-content-secondary" aria-hidden="true" />
      {$t("general.info.title")}
    </h3>

    {#if systemInfo}
      <div class="kv">
        {@render info($t("general.info.hostname"), systemInfo.hostname, "font-medium break-words")}
        {@render info($t("general.info.os"), systemInfo.os, "")}
        {@render info($t("general.info.kernel"), systemInfo.kernel, "font-mono text-xs")}
        {@render info($t("general.info.architecture"), systemInfo.architecture, "")}
        {@render info($t("general.info.uptime"), formatUptime(systemInfo.boot_time), "tabular")}
      </div>
    {:else}
      <p class="text-content-muted">{$t("general.info.loadFailed")}</p>
    {/if}
  </Card>

  <HostSettingsCard {host} onrenamed={renamed} />

  <!-- UNOS Version: the version the supervisor image was built with (ADR-0234) -->
  <Card>
    <h3 class="card-title mb-4">
      <Package class="w-5 h-5 text-content-secondary" aria-hidden="true" />
      {$t("general.version.title")}
    </h3>

    {#if currentVersion}
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
  <Card>
    <h3 class="card-title mb-4">
      <User class="w-5 h-5 text-content-secondary" aria-hidden="true" />
      {$t("general.user.title")}
    </h3>

    {#if $user}
      <div class="space-y-3">
        <div class="kv">
          <div>
            <span class="kv-label">{$t("general.user.username")}</span>
            <span class="kv-value font-medium">{$user.username}</span>
          </div>
          <div>
            <span class="kv-label">{$t("general.user.role")}</span>
            <span class="kv-value">
              {#if $user.is_admin}
                <Badge variant="info">{$t("general.user.admin")}</Badge>
              {:else}
                <Badge variant="neutral">{$t("general.user.user")}</Badge>
              {/if}
            </span>
          </div>
        </div>
        <Button href="/users" variant="secondary" size="sm">{$t("general.user.manage")}</Button>
      </div>
    {:else}
      <p class="text-content-muted">{$t("general.user.signedOut")}</p>
    {/if}
  </Card>
  {/if}
</div>
