<script lang="ts">
  import { onMount } from "svelte";
  import { getSystemInfo, type SystemInfo } from "$lib/api/system";
  import { Card } from "$lib/components/ui";
  import { HostSettings, HostSettingsCard } from "$lib/components/system/general";
  import { t } from "$lib/i18n";
  import { apiTime } from "$lib/time";
  import { Server } from "lucide-svelte";

  // System > General (ADR-0430): the facts of the host and its name and time zone. The host name is
  // shown by its field alone, the version by Updates, the signed-in account by the sidebar.
  let systemInfo = $state<SystemInfo | null>(null);
  let loading = $state(true);

  // The name and time zone of the host (ADR-0331).
  const host = new HostSettings();

  onMount(async () => {
    host.load();
    try {
      systemInfo = await getSystemInfo();
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
</script>

<!-- A fact as label over value (.kv, ADR-0428). -->
{#snippet info(label: string, value: string, valueClass: string)}
  <div>
    <span class="kv-label">{label}</span>
    <span class="kv-value {valueClass}">{value}</span>
  </div>
{/snippet}

<div class="space-y-4">
  <h2 class="text-xl font-semibold">{$t("general.title")}</h2>

  <!-- System Info -->
  <Card>
    <h3 class="card-title mb-4">
      <Server class="w-5 h-5 text-content-secondary" aria-hidden="true" />
      {$t("general.info.title")}
    </h3>

    {#if loading}
      <p class="text-content-muted">{$t("common.loading")}</p>
    {:else if systemInfo}
      <div class="kv">
        {@render info($t("general.info.os"), systemInfo.os, "")}
        {@render info($t("general.info.kernel"), systemInfo.kernel, "font-mono text-xs")}
        {@render info($t("general.info.architecture"), systemInfo.architecture, "")}
        {@render info($t("general.info.uptime"), formatUptime(systemInfo.boot_time), "tabular")}
      </div>
    {:else}
      <p class="text-content-muted">{$t("general.info.loadFailed")}</p>
    {/if}
  </Card>

  <HostSettingsCard {host} />
</div>
