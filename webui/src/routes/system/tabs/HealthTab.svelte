<script lang="ts">
  import { onMount } from "svelte";
  import {
    getDiskHealth,
    getDiskPredictions,
    replaceSoon,
    type DiskPrediction,
    type SmartAttribute,
    type SmartInfo
  } from "$lib/api/health";
  import { Alert, Badge, Button, Modal } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { HardDrive, Thermometer, Check, X, AlertTriangle, RefreshCw } from "lucide-svelte";

  let disks: SmartInfo[] = [];
  // The predictions that say replace soon (ADR-0235).
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

  // By smartctl's serial, which the SMART monitor records too: a disk's path
  // can change (ADR-0235).
  function predictionOf(disk: SmartInfo, predictions: DiskPrediction[]): DiskPrediction | undefined {
    return predictions.find((prediction) => prediction.serial === disk.serial);
  }

  // The predictions to replace soon that match no listed disk: smartctl could
  // not read the disk on this load (its serial shows as Unknown), or another
  // disk took its place. They are listed apart, as on the dashboard.
  $: unmatched = toReplace.filter((prediction) => !disks.some((disk) => disk.serial === prediction.serial));

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

  // The server's values, shown by the keys of their texts in the catalog (ADR-0287).
  // "critical": the value is at or below the attribute's failure threshold;
  // "warning": within 10 of it (parse_smart_data in supervisor/src/health/router.py).
  const ATTRIBUTE_STATUS: Record<SmartAttribute["status"], { variant: BadgeVariant; key: string }> = {
    ok: { variant: "success", key: "health.attribute.ok" },
    warning: { variant: "warning", key: "health.attribute.warning" },
    critical: { variant: "error", key: "health.attribute.critical" }
  };
  const HEALTH_STATUS: Record<string, string> = {
    PASSED: "health.status.passed",
    FAILED: "health.status.failed",
    UNKNOWN: "health.status.unknown"
  };
  // The numbers of an NVMe disk's SMART/Health Information log, by smartctl's names.
  const NVME_FIELDS: Record<string, string> = {
    critical_warning: "health.nvme.criticalWarning",
    temperature: "health.nvme.temperature",
    available_spare: "health.nvme.availableSpare",
    available_spare_threshold: "health.nvme.availableSpareThreshold",
    percentage_used: "health.nvme.percentageUsed",
    data_units_read: "health.nvme.dataUnitsRead",
    data_units_written: "health.nvme.dataUnitsWritten",
    host_reads: "health.nvme.hostReads",
    host_writes: "health.nvme.hostWrites",
    controller_busy_time: "health.nvme.controllerBusyTime",
    power_cycles: "health.nvme.powerCycles",
    power_on_hours: "health.nvme.powerOnHours",
    unsafe_shutdowns: "health.nvme.unsafeShutdowns",
    media_errors: "health.nvme.mediaErrors",
    num_err_log_entries: "health.nvme.errorLogEntries",
    warning_temp_time: "health.nvme.warningTempTime",
    critical_comp_time: "health.nvme.criticalCompTime"
  };

  // The helpers below that give texts are reactive, so that an open dialog follows a change of
  // the language.

  /** The text of `value` by its key in `keys`, in the language of the page; else `value`. */
  $: textOf = (keys: Record<string, string>, value: string): string =>
    Object.prototype.hasOwnProperty.call(keys, value) ? $t(keys[value]) : value;

  $: attributeStatus = (status: string): { variant: BadgeVariant; label: string } => {
    const known = ATTRIBUTE_STATUS[status as SmartAttribute["status"]];
    return known ? { variant: known.variant, label: $t(known.key) } : { variant: "neutral", label: status };
  };

  /** smartctl's "available_spare_threshold" as "Available spare threshold". */
  function nvmeLabel(key: string): string {
    const words = key.replace(/_/g, " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }

  $: nvmeField = (key: string): string =>
    Object.prototype.hasOwnProperty.call(NVME_FIELDS, key) ? $t(NVME_FIELDS[key]) : nvmeLabel(key);

  // The server's word for what neither smartctl nor lsblk reported (_disk_health of
  // supervisor/src/health/router.py).
  $: orUnknown = (value: string): string => (value === "Unknown" ? $t("common.unknown") : value);

  // A disk's capacity as the server writes it, "3.64 TB" or "931.51 GB" (_format_capacity of
  // supervisor/src/health/router.py), by the unit: keys of the catalog.
  const CAPACITY_UNITS: Record<string, string> = {
    GB: "health.size.gb",
    TB: "health.size.tb"
  };

  /** A capacity with its number in the format of the page and the unit of the catalog; another
   *  text as it comes. */
  $: capacityText = (capacity: string): string => {
    const [number, unit, ...rest] = capacity.split(" ");
    const known = Object.prototype.hasOwnProperty.call(CAPACITY_UNITS, unit);
    if (rest.length > 0 || !known || !/^\d+(\.\d+)?$/.test(number)) return orUnknown(capacity);
    const decimals = number.split(".")[1]?.length ?? 0;
    const size = Number(number).toLocaleString($intlLocale, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
      useGrouping: false
    });
    return $t(CAPACITY_UNITS[unit], { size });
  };

  $: orNotReported = (value: number | null, format: (value: number) => string = String): string =>
    value === null ? $t("health.notReported") : format(value);

  /** 51775 power-on hours as 5 years, 332 days; 1200 as 50 days, 0 hours. */
  $: powerOnTime = (hours: number): string => {
    const days = Math.floor(hours / 24);
    const years = Math.floor(days / 365);
    return years > 0
      ? `${$t("health.years", { n: years })}, ${$t("health.days", { n: days % 365 })}`
      : `${$t("health.days", { n: days })}, ${$t("health.hours", { n: hours % 24 })}`;
  };

  $: lastSelfTest = (disk: SmartInfo): string => {
    if (!disk.self_test_status && !disk.last_test_result) return $t("health.noSelfTest");
    return [disk.self_test_status, disk.last_test_result]
      .filter((part): part is string => Boolean(part))
      .map(orUnknown)
      .join(": ");
  };
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">{$t("health.title")}</h2>
    <Button variant="secondary" size="icon" on:click={loadHealth} disabled={loading}>
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
        <li class="card">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex items-center gap-3">
              <HardDrive class="w-8 h-8 icon-storage" />
              <div>
                <p class="font-semibold">{orUnknown(disk.model)}</p>
                <p class="text-sm text-secondary"><span class="mono">{disk.device}</span> · {capacityText(disk.capacity)}</p>
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
                <svelte:component this={healthIcon(disk.health_status)} class="w-5 h-5" />
                {textOf(HEALTH_STATUS, disk.health_status)}
              </span>
              <Button variant="secondary" size="sm" on:click={() => (openDisk = disk)}>
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

<Modal
  open={openDisk !== null}
  title={openDisk ? `${orUnknown(openDisk.model)} (${openDisk.device})` : ""}
  size="xl"
  on:close={() => (openDisk = null)}
>
  {#if openDisk}
    <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
      <div>
        <dt class="text-secondary">{$t("health.overall")}</dt>
        <dd class="font-medium {healthClass(openDisk.health_status)}">{textOf(HEALTH_STATUS, openDisk.health_status)}</dd>
      </div>
      <div>
        <dt class="text-secondary">{$t("health.temperature")}</dt>
        <dd>{orNotReported(openDisk.temperature, (celsius) => $t("health.celsius", { value: celsius }))}</dd>
      </div>
      <div>
        <dt class="text-secondary">{$t("health.powerOnHours")}</dt>
        <dd>{orNotReported(openDisk.power_on_hours, (hours) => `${hours} (${powerOnTime(hours)})`)}</dd>
      </div>
      <div>
        <dt class="text-secondary">{$t("health.powerCycles")}</dt>
        <dd>{orNotReported(openDisk.power_cycle_count)}</dd>
      </div>
      <div>
        <dt class="text-secondary">{$t("health.serial")}</dt>
        <dd class="mono">{orUnknown(openDisk.serial)}</dd>
      </div>
      <div>
        <dt class="text-secondary">{$t("health.firmware")}</dt>
        <dd class="mono">{orUnknown(openDisk.firmware)}</dd>
      </div>
      <div>
        <dt class="text-secondary">{$t("health.capacity")}</dt>
        <dd>{capacityText(openDisk.capacity)}</dd>
      </div>
      <div>
        <dt class="text-secondary">{$t("health.lastSelfTest")}</dt>
        <dd>{lastSelfTest(openDisk)}</dd>
      </div>
    </dl>

    {#if openDisk.attributes.length > 0}
      <div class="table-frame mt-6">
        <table class="table">
          <caption class="sr-only">{$t("health.attributes")}</caption>
          <thead class="table-header">
            <tr>
              <th>{$t("health.attribute.id")}</th>
              <th>{$t("health.attribute.name")}</th>
              <th>{$t("health.attribute.value")}</th>
              <th>{$t("health.attribute.worst")}</th>
              <th>{$t("health.attribute.threshold")}</th>
              <th>{$t("health.attribute.raw")}</th>
              <th>{$t("health.attribute.status")}</th>
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
      <div class="table-frame mt-6">
        <table class="table">
          <caption class="sr-only">{$t("health.nvmeLog")}</caption>
          <thead class="table-header">
            <tr>
              <th>{$t("health.nvmeField")}</th>
              <th>{$t("health.attribute.value")}</th>
            </tr>
          </thead>
          <tbody>
            {#each Object.entries(openDisk.nvme_health) as [key, value]}
              <tr class="table-row">
                <td>{nvmeField(key)}</td>
                <td class="mono">{value}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}
      <p class="text-secondary text-sm mt-6">{$t("health.noAttributes")}</p>
    {/if}
  {/if}
</Modal>
