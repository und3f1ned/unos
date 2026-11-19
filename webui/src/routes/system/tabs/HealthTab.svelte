<script lang="ts">
  import { onMount } from "svelte";
  import {
    getDiskHealth,
    getDiskPredictions,
    replaceSoon,
    type DiskPrediction,
    type SmartInfo
  } from "$lib/api/health";
  import { Alert, Badge, Button } from "$lib/components/ui";
  import { SmartDataDialog } from "$lib/components/system/health";
  import {
    HEALTH_STATUS,
    capacityText,
    healthClass,
    healthIcon,
    orUnknown,
    predictionOf,
    textOf
  } from "$lib/components/system/health/texts";
  import { intlLocale, t } from "$lib/i18n";
  import { HardDrive, Thermometer, RefreshCw } from "lucide-svelte";

  let disks = $state<SmartInfo[]>([]);
  // The predictions that say replace soon (ADR-0235).
  let toReplace = $state<DiskPrediction[]>([]);
  let loading = $state(true);
  let error = $state("");
  let predictionsError = $state("");
  // The disk whose SMART data the dialog shows: the data of GET /health/disks,
  // so that opening a disk runs no smartctl.
  let openDisk = $state<SmartInfo | null>(null);

  onMount(() => loadHealth());

  function messageOf(e: unknown, fallback: string): string {
    return e instanceof Error && e.message ? e.message : fallback;
  }

  async function loadHealth() {
    loading = true;
    error = "";
    predictionsError = "";
    const [health, predictions] = await Promise.allSettled([getDiskHealth(), getDiskPredictions()]);
    if (health.status === "fulfilled") {
      disks = health.value.disks;
    } else {
      // For example 500 "Could not list the host's disks: ..." when lsblk
      // fails: say so instead of "No disks found", and drop the old list.
      disks = [];
      error = messageOf(health.reason, $t("health.loadError"));
    }
    if (predictions.status === "fulfilled") {
      toReplace = replaceSoon(predictions.value.predictions);
    } else {
      // Without the predictions no disk is marked: say so, so that a disk to
      // replace is not taken for one without a mark.
      toReplace = [];
      predictionsError = messageOf(predictions.reason, $t("health.predictionsError"));
    }
    loading = false;
  }

  // The predictions to replace soon that match no listed disk: smartctl could
  // not read the disk on this load (its serial shows as Unknown), or another
  // disk took its place. They are listed apart, as on the dashboard.
  let unmatched = $derived(toReplace.filter((prediction) => !disks.some((disk) => disk.serial === prediction.serial)));
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">{$t("health.title")}</h2>
    <Button variant="secondary" size="icon" onclick={loadHealth} disabled={loading}>
      <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      <span class="sr-only">{$t("health.refresh")}</span>
    </Button>
  </div>

  {#if loading}
    <p class="loading-text">{$t("common.loading")}</p>
  {:else if error}
    <Alert variant="error" title={$t("health.loadError")}>{error}</Alert>
  {:else if disks.length === 0}
    <p class="text-muted text-center py-8">{$t("health.noDisks")}</p>
  {:else}
    {#if predictionsError}
      <Alert variant="warning" title={$t("health.predictionsError")}>{predictionsError}</Alert>
    {/if}
    {#if unmatched.length > 0}
      <Alert variant="error" title={$t("health.replaceSoon")}>
        <p class="text-sm">{$t("health.unmatched")}</p>
        <ul class="mt-2 space-y-1 text-sm">
          {#each unmatched as prediction}
            <li>
              <span class="mono">{prediction.device}</span>
              ({$t("health.serialOf", { serial: prediction.serial })}): {prediction.risk_factors.join("; ")}
            </li>
          {/each}
        </ul>
      </Alert>
    {/if}
    <ul class="space-y-3" aria-label={$t("health.disks")}>
      {#each disks as disk}
        {@const prediction = predictionOf(disk, toReplace)}
        {@const Icon = healthIcon(disk.health_status)}
        <li class="card">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex items-center gap-3">
              <HardDrive class="w-8 h-8 icon-storage" />
              <div>
                <p class="font-semibold">{orUnknown($t, disk.model)}</p>
                <p class="text-sm text-secondary"><span class="mono">{disk.device}</span> · {capacityText($t, $intlLocale, disk.capacity)}</p>
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-4">
              {#if prediction}
                <Badge variant="error">{$t("health.replaceSoon")}</Badge>
              {/if}
              {#if disk.temperature !== null}
                <span class="flex items-center gap-1 text-sm">
                  <Thermometer class="w-4 h-4 icon-temperature" />
                  {$t("health.celsius", { value: disk.temperature })}
                </span>
              {/if}
              <span class="flex items-center gap-1 font-medium {healthClass(disk.health_status)}">
                <Icon class="w-5 h-5" />
                {textOf($t, HEALTH_STATUS, disk.health_status)}
              </span>
              <Button variant="secondary" size="sm" onclick={() => (openDisk = disk)}>
                {$t("health.smartData")}<span class="sr-only">{$t("health.smartDataOf", { device: disk.device })}</span>
              </Button>
            </div>
          </div>
          {#if prediction}
            <div class="mt-3 text-sm">
              <p class="text-error">{prediction.risk_factors.join("; ")}</p>
              <p class="text-secondary">{prediction.recommendation}</p>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<SmartDataDialog disk={openDisk} onclose={() => (openDisk = null)} />
