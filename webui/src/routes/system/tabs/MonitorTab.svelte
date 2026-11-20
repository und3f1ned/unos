<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { onMount, untrack } from "svelte";
  import { get } from "svelte/store";
  import { Cpu, MemoryStick, Network, HardDrive, Activity, Wifi, WifiOff } from "lucide-svelte";
  import { Alert, Badge, Button, Card, Skeleton } from "$lib/components/ui";
  import { stats, wsConnected, wsForbidden, wsSettled, type WebSocketStats } from "$lib/stores/stats";
  import { getMonitorHistory } from "$lib/api/monitor";
  import {
    cpuOf,
    memoryOf,
    path,
    peak,
    readOf,
    recvOf,
    sentOf,
    writeOf,
    type Point,
    type Series
  } from "$lib/components/system/monitor/chart";
  import { formatNumber, scaleBytes } from "$lib/format";
  import { intlLocale, t } from "$lib/i18n";

  // System > Resource Monitor (ADR-0399): the history that the supervisor keeps for an hour, read
  // once when the tab opens, and each stats message of /ws/v1/events added to it as it arrives. No
  // timer asks the API again (ADR-0365).

  const KEPT_MS = 3600 * 1000;
  const RANGES = [
    { seconds: 300, key: "monitor.range.5m", span: "monitor.range.5m.span" },
    { seconds: 900, key: "monitor.range.15m", span: "monitor.range.15m.span" },
    { seconds: 3600, key: "monitor.range.1h", span: "monitor.range.1h.span" }
  ];
  // Raw: the buttons compare it with the option itself (a proxy would be another object).
  let range = $state.raw(RANGES[0]);

  // Replaced whole, never changed in place: up to an hour of points need no deep proxy.
  let history = $state.raw<Point[]>([]);
  let live = $state.raw<Point[]>([]);
  let historyError = $state("");

  onMount(async () => {
    const opened = Date.now();
    try {
      const answer = await getMonitorHistory();
      history = answer.points.map((point) => ({
        at: opened - point.age_seconds * 1000,
        cpu: point.cpu_percent,
        memory: point.memory_percent,
        netRecv: point.net_recv_rate,
        netSent: point.net_sent_rate,
        diskRead: point.disk_read_rate,
        diskWrite: point.disk_write_rate
      }));
    } catch (error) {
      historyError = messageOf(error);
    }
  });

  // The message that the store already held when the tab opened: its time is not known, and the
  // socket may have closed since. The points start with the next one.
  const before = get(stats);

  // A message whose rates and CPU shares are of no time (the first sample after a gap) is no point
  // of the history: the cards show it, the charts do not.
  function addLive(message: WebSocketStats | null) {
    if (!message?.cpu || message === before || !(message.sample_seconds > 0)) return;
    const now = Date.now();
    live = [
      ...live.filter((point) => point.at >= now - KEPT_MS),
      {
        at: now,
        cpu: message.cpu.percent,
        memory: message.memory.percent,
        netRecv: message.network.bytes_recv_rate,
        netSent: message.network.bytes_sent_rate,
        diskRead: message.disk_io.read_rate,
        diskWrite: message.disk_io.write_rate
      }
    ];
  }

  let currentStats = $derived($stats);
  // Each message of the store, before the page is drawn, as the statement of Svelte 4 did.
  $effect.pre(() => {
    const message = $stats;
    untrack(() => addLive(message));
  });
  // The history up to the first message that arrived since the tab opened, then the messages.
  let points = $derived([...history.filter((point) => live.length === 0 || point.at < live[0].at), ...live]);
  // The range ends at the newest point: what the charts show changes only with a new message.
  let end = $derived(points.length > 0 ? points[points.length - 1].at : 0);
  let start = $derived(end - range.seconds * 1000);
  let shown = $derived(points.filter((point) => point.at >= start));

  let cpuPeak = $derived(peak(shown, cpuOf));
  let memoryPeak = $derived(peak(shown, memoryOf));
  let recvPeak = $derived(peak(shown, recvOf));
  let sentPeak = $derived(peak(shown, sentOf));
  let readPeak = $derived(peak(shown, readOf));
  let writePeak = $derived(peak(shown, writeOf));
  // The rates' charts scale to the peak of the range; percentages to 100.
  let networkMax = $derived(Math.max(recvPeak, sentPeak));
  let diskMax = $derived(Math.max(readPeak, writePeak));

  // The units of a size and of a rate, one for each power of 1024: keys of the catalog.
  const SIZE_UNITS = ["monitor.size.b", "monitor.size.kb", "monitor.size.mb", "monitor.size.gb", "monitor.size.tb"];
  const RATE_UNITS = ["monitor.rate.b", "monitor.rate.kb", "monitor.rate.mb", "monitor.rate.gb"];

  // The numbers in the format of the page and the units of the catalog (webui/AGENTS.md, Data):
  // a size or a rate in the largest unit that keeps it at 1 or more, with up to two decimals; a
  // percentage with one decimal.
  function amount(bytes: number, units: string[]): string {
    const { value, power } = scaleBytes(bytes, units.length);
    return $t(units[power], { value: formatNumber(value, $intlLocale, 2) });
  }
  function percent(value: number): string {
    return $t("monitor.percent", {
      value: value.toLocaleString($intlLocale, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
        useGrouping: false
      })
    });
  }
  function chartName(metric: string): string {
    return $t("monitor.chart", { metric: $t(metric), span: $t(range.span) });
  }

  function percentClass(p: number): string {
    return p < 70 ? "text-success" : p < 90 ? "text-warning" : "text-error";
  }

  function percentFill(p: number): string {
    return p < 70 ? "bg-status-success" : p < 90 ? "bg-status-warning" : "bg-status-error";
  }
</script>

<!-- A chart of the range: one line for each series, scaled from 0 to max. -->
{#snippet chart(metric: string, svgClass: string, max: number, lines: { cls?: string; value: Series }[])}
  <svg viewBox="0 0 100 40" preserveAspectRatio="none" class={svgClass} role="img" aria-label={chartName(metric)}>
    {#each lines as line}
      <path class={line.cls} d={path(shown, line.value, max, start, range.seconds * 1000)} fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
    {/each}
  </svg>
{/snippet}

<div class="space-y-6">
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 class="text-xl font-semibold">{$t("monitor.title")}</h2>
    <div class="flex flex-wrap items-center gap-3">
      <!-- The ranges as a segmented control (ADR-0428). -->
      <div class="segmented" role="group" aria-label={$t("monitor.range")}>
        {#each RANGES as option (option.seconds)}
          <Button
            variant="plain"
            class="tab-pill {range === option ? 'tab-pill-active' : ''}"
            pressed={range === option}
            onclick={() => (range = option)}
          >
            {$t(option.key)}
          </Button>
        {/each}
      </div>
      <Badge variant={$wsConnected ? "success" : $wsSettled ? "error" : "neutral"} class="flex items-center gap-1">
        {#if $wsConnected}
          <Wifi class="w-4 h-4" />
          {$t("monitor.live")}
        {:else if !$wsSettled}
          <!-- The first attempt has not ended: connecting, not disconnected (ADR-0430). -->
          <Wifi class="w-4 h-4" />
          {$t("monitor.connecting")}
        {:else}
          <WifiOff class="w-4 h-4" />
          {$t("monitor.disconnected")}
        {/if}
      </Badge>
    </div>
  </div>

  {#if historyError}
    <Alert variant="warning" title={$t("monitor.historyError")}><p>{historyError}</p></Alert>
  {/if}

  {#if !currentStats?.cpu && !$wsForbidden && ($wsConnected || !$wsSettled)}
    <!-- The first stats have not come (ADR-0430): the four cards and the charts in their places. -->
    <div class="space-y-4" aria-busy="true">
      <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {#each [0, 1, 2, 3] as tile (tile)}
          <Skeleton shape="tile" />
        {/each}
      </div>
      <Skeleton shape="block" class="h-64 w-full rounded-card" />
    </div>
  {:else if !currentStats?.cpu}
    <div class="flex items-center justify-center py-12">
      <p class="text-muted">{$t("monitor.waiting")}</p>
    </div>
  {:else}
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      <Card>
        <div class="stat-card-header">
          <Cpu class="w-5 h-5 icon-cpu" />
          <h3 class="stat-card-label">{$t("monitor.cpu")}</h3>
        </div>
        <p class="stat-card-value {percentClass(currentStats.cpu.percent)}">{percent(currentStats.cpu.percent)}</p>
        {@render chart("monitor.cpu", "w-full h-16 mt-2 icon-cpu", 100, [{ value: cpuOf }])}
        <p class="stat-card-sublabel">{$t("monitor.peak", { value: percent(cpuPeak) })}</p>
      </Card>

      <Card>
        <div class="stat-card-header">
          <MemoryStick class="w-5 h-5 icon-memory" />
          <h3 class="stat-card-label">{$t("monitor.memory")}</h3>
        </div>
        <p class="stat-card-value {percentClass(currentStats.memory.percent)}">{percent(currentStats.memory.percent)}</p>
        <p class="stat-card-sublabel">{amount(currentStats.memory.used, SIZE_UNITS)} / {amount(currentStats.memory.total, SIZE_UNITS)}</p>
        {@render chart("monitor.memory", "w-full h-16 mt-2 icon-memory", 100, [{ value: memoryOf }])}
        <p class="stat-card-sublabel">{$t("monitor.peak", { value: percent(memoryPeak) })}</p>
      </Card>

      <Card>
        <div class="stat-card-header">
          <Network class="w-5 h-5 icon-network" />
          <h3 class="stat-card-label">{$t("monitor.network")}</h3>
        </div>
        <p class="text-sm">↓ <span class="text-success font-semibold">{amount(currentStats.network.bytes_recv_rate, RATE_UNITS)}</span></p>
        <p class="text-sm">↑ <span class="icon-temperature font-semibold">{amount(currentStats.network.bytes_sent_rate, RATE_UNITS)}</span></p>
        {@render chart("monitor.network", "w-full h-16 mt-2", networkMax, [
          { cls: "text-success", value: recvOf },
          { cls: "icon-temperature", value: sentOf }
        ])}
        <p class="stat-card-sublabel">{$t("monitor.peak.network", { recv: amount(recvPeak, RATE_UNITS), sent: amount(sentPeak, RATE_UNITS) })}</p>
      </Card>

      <Card>
        <div class="stat-card-header">
          <HardDrive class="w-5 h-5 icon-storage" />
          <h3 class="stat-card-label">{$t("monitor.diskIo")}</h3>
        </div>
        <p class="text-sm">{$t("monitor.read")} <span class="text-success font-semibold">{amount(currentStats.disk_io.read_rate, RATE_UNITS)}</span></p>
        <p class="text-sm">{$t("monitor.write")} <span class="text-error font-semibold">{amount(currentStats.disk_io.write_rate, RATE_UNITS)}</span></p>
        {@render chart("monitor.diskIo", "w-full h-16 mt-2", diskMax, [
          { cls: "text-success", value: readOf },
          { cls: "text-error", value: writeOf }
        ])}
        <p class="stat-card-sublabel">{$t("monitor.peak.disk", { read: amount(readPeak, RATE_UNITS), write: amount(writePeak, RATE_UNITS) })}</p>
      </Card>
    </div>

    {#if currentStats.cpu.per_core.length > 1}
      <Card>
        <h3 class="card-title mb-3">{$t("monitor.cores")}</h3>
        <div class="grid grid-cols-8 gap-2">
          {#each currentStats.cpu.per_core as core, i}
            <div class="text-center">
              <div class="h-12 bg-surface-overlay rounded relative overflow-hidden">
                <div class="absolute bottom-0 left-0 right-0 {percentFill(core)}" style="height: {core}%"></div>
              </div>
              <p class="text-xs mt-1 text-muted">{i}</p>
            </div>
          {/each}
        </div>
      </Card>
    {/if}

    <Card>
      <h3 class="card-title mb-1 flex items-center gap-2">
        <Activity class="w-4 h-4" />
        {$t("monitor.processes")}
      </h3>
      <p class="text-sm text-muted mb-3">{$t("monitor.processes.hint")}</p>
      <div class="table-frame">
        <table class="table">
          <thead class="table-header">
            <tr>
              <th>{$t("monitor.process.name")}</th>
              <th class="text-right">{$t("monitor.process.pid")}</th>
              <th>{$t("monitor.process.user")}</th>
              <th class="text-right">{$t("monitor.process.cpu")}</th>
              <th class="text-right">{$t("monitor.process.memory")}</th>
            </tr>
          </thead>
          <tbody>
            {#each currentStats.processes as proc (proc.pid)}
              <tr class="table-row">
                <td class="mono break-all">{proc.name}</td>
                <td class="text-right mono">{proc.pid}</td>
                <td class="text-secondary break-all">{proc.username}</td>
                <td class="text-right {percentClass(proc.cpu_percent)}">{percent(proc.cpu_percent)}</td>
                <td class="text-right whitespace-nowrap">{amount(proc.memory_bytes, SIZE_UNITS)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </Card>
  {/if}
</div>
