<script lang="ts">
  import { onMount } from "svelte";
  import {
    formatPowerOnHours,
    getDiskHealth,
    getDiskPredictions,
    replaceSoon,
    type DiskPrediction,
    type SmartAttribute,
    type SmartInfo
  } from "$lib/api/health";
  import { Alert, Badge, Button, Modal } from "$lib/components/ui";
  import { HardDrive, Thermometer, Check, X, AlertTriangle, RefreshCw } from "lucide-svelte";

  let disks: SmartInfo[] = [];
  // The predictions that say replace soon (ADR-0232).
  let toReplace: DiskPrediction[] = [];
  let loading = true;
  let error = "";
  let predictionsError = "";
  // The disk whose SMART data the dialog shows: the data of GET /health/disks,
  // so that opening a disk runs no smartctl.
  let openDisk: SmartInfo | null = null;

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
      error = messageOf(health.reason, "Could not load disk health");
    }
    if (predictions.status === "fulfilled") {
      toReplace = replaceSoon(predictions.value.predictions);
    } else {
      // Without the predictions no disk is marked: say so, so that a disk to
      // replace is not taken for one without a mark.
      toReplace = [];
      predictionsError = messageOf(predictions.reason, "Could not load the failure predictions");
    }
    loading = false;
  }

  // By smartctl's serial, which the SMART monitor records too: a disk's path
  // can change (ADR-0232).
  function predictionOf(disk: SmartInfo, predictions: DiskPrediction[]): DiskPrediction | undefined {
    return predictions.find((prediction) => prediction.serial === disk.serial);
  }

  function healthIcon(status: string) {
    if (status === "PASSED") return Check;
    if (status === "FAILED") return X;
    return AlertTriangle;
  }

  function healthClass(status: string): string {
    if (status === "PASSED") return "text-success";
    if (status === "FAILED") return "text-error";
    return "text-warning";
  }

  type BadgeVariant = "success" | "warning" | "error" | "neutral";

  // "critical": the value is at or below the attribute's failure threshold;
  // "warning": within 10 of it (parse_smart_data in supervisor/src/health/router.py).
  const ATTRIBUTE_STATUS: Record<SmartAttribute["status"], { variant: BadgeVariant; label: string }> = {
    ok: { variant: "success", label: "OK" },
    warning: { variant: "warning", label: "Warning" },
    critical: { variant: "error", label: "Failing" }
  };

  function attributeStatus(status: string): { variant: BadgeVariant; label: string } {
    return ATTRIBUTE_STATUS[status as SmartAttribute["status"]] ?? { variant: "neutral", label: status };
  }

  /** smartctl's "available_spare_threshold" as "Available spare threshold". */
  function nvmeLabel(key: string): string {
    const words = key.replace(/_/g, " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }

  function orNotReported(value: number | null, format: (value: number) => string = String): string {
    return value === null ? "Not reported" : format(value);
  }

  function lastSelfTest(disk: SmartInfo): string {
    if (!disk.self_test_status && !disk.last_test_result) return "None recorded";
    return [disk.self_test_status, disk.last_test_result].filter(Boolean).join(": ");
  }
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Disk Health</h2>
    <Button variant="secondary" size="icon" on:click={loadHealth} disabled={loading}>
      <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      <span class="sr-only">Refresh disk health</span>
    </Button>
  </div>

  {#if loading}
    <p class="loading-text">Loading...</p>
  {:else if error}
    <Alert variant="error" title="Could not load disk health">{error}</Alert>
  {:else if disks.length === 0}
    <p class="text-muted text-center py-8">No disks found</p>
  {:else}
    {#if predictionsError}
      <Alert variant="warning" title="Could not load the failure predictions">{predictionsError}</Alert>
    {/if}
    <ul class="space-y-3" aria-label="Disks">
      {#each disks as disk}
        {@const prediction = predictionOf(disk, toReplace)}
        <li class="card">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex items-center gap-3">
              <HardDrive class="w-8 h-8 icon-storage" />
              <div>
                <p class="font-semibold">{disk.model}</p>
                <p class="text-sm text-secondary"><span class="mono">{disk.device}</span> · {disk.capacity}</p>
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-4">
              {#if prediction}
                <Badge variant="error">Replace soon</Badge>
              {/if}
              {#if disk.temperature !== null}
                <span class="flex items-center gap-1 text-sm">
                  <Thermometer class="w-4 h-4 icon-temperature" />
                  {disk.temperature} °C
                </span>
              {/if}
              <span class="flex items-center gap-1 font-medium {healthClass(disk.health_status)}">
                <svelte:component this={healthIcon(disk.health_status)} class="w-5 h-5" />
                {disk.health_status}
              </span>
              <Button variant="secondary" size="sm" on:click={() => (openDisk = disk)}>
                SMART data<span class="sr-only"> of {disk.device}</span>
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

<Modal
  open={openDisk !== null}
  title={openDisk ? `${openDisk.model} (${openDisk.device})` : ""}
  size="xl"
  on:close={() => (openDisk = null)}
>
  {#if openDisk}
    <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
      <div>
        <dt class="text-secondary">Overall health</dt>
        <dd class="font-medium {healthClass(openDisk.health_status)}">{openDisk.health_status}</dd>
      </div>
      <div>
        <dt class="text-secondary">Temperature</dt>
        <dd>{orNotReported(openDisk.temperature, (celsius) => `${celsius} °C`)}</dd>
      </div>
      <div>
        <dt class="text-secondary">Power-on hours</dt>
        <dd>{orNotReported(openDisk.power_on_hours, (hours) => `${hours} (${formatPowerOnHours(hours)})`)}</dd>
      </div>
      <div>
        <dt class="text-secondary">Power cycles</dt>
        <dd>{orNotReported(openDisk.power_cycle_count)}</dd>
      </div>
      <div>
        <dt class="text-secondary">Serial</dt>
        <dd class="mono">{openDisk.serial}</dd>
      </div>
      <div>
        <dt class="text-secondary">Firmware</dt>
        <dd class="mono">{openDisk.firmware}</dd>
      </div>
      <div>
        <dt class="text-secondary">Capacity</dt>
        <dd>{openDisk.capacity}</dd>
      </div>
      <div>
        <dt class="text-secondary">Last self-test</dt>
        <dd>{lastSelfTest(openDisk)}</dd>
      </div>
    </dl>

    {#if openDisk.attributes.length > 0}
      <div class="overflow-x-auto mt-6">
        <table class="table">
          <caption class="sr-only">SMART attributes</caption>
          <thead class="table-header">
            <tr>
              <th>ID</th>
              <th>Attribute</th>
              <th>Value</th>
              <th>Worst</th>
              <th>Threshold</th>
              <th>Raw value</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {#each openDisk.attributes as attribute}
              {@const status = attributeStatus(attribute.status)}
              <tr class="table-row">
                <td class="mono">{attribute.id}</td>
                <td>{attribute.name}</td>
                <td>{attribute.value}</td>
                <td>{attribute.worst}</td>
                <td>{attribute.thresh}</td>
                <td class="mono">{attribute.raw_value}</td>
                <td><Badge variant={status.variant}>{status.label}</Badge></td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else if openDisk.nvme_health}
      <div class="overflow-x-auto mt-6">
        <table class="table">
          <caption class="sr-only">NVMe SMART/Health Information log</caption>
          <thead class="table-header">
            <tr>
              <th>Health information</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {#each Object.entries(openDisk.nvme_health) as [key, value]}
              <tr class="table-row">
                <td>{nvmeLabel(key)}</td>
                <td class="mono">{value}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p class="text-secondary text-sm mt-6">smartctl reported no SMART attributes for this disk.</p>
    {/if}
  {/if}
</Modal>
