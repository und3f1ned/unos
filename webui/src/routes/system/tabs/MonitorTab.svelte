<script lang="ts">
  import { onMount } from "svelte";
  import { get } from "svelte/store";
  import { Cpu, MemoryStick, Network, HardDrive, Activity, Wifi, WifiOff } from "lucide-svelte";
  import { Alert, Badge, Button, Card } from "$lib/components/ui";
  import { stats, wsConnected, type WebSocketStats } from "$lib/stores/stats";
  import { getMonitorHistory } from "$lib/api/monitor";
  import { intlLocale, t } from "$lib/i18n";

  // System > Resource Monitor (ADR-0398): the history that the supervisor keeps for an hour, read
  // once when the tab opens, and each stats message of /ws/v1/events added to it as it arrives. No
  // timer asks the API again (ADR-0365).

  interface Point {
    // The browser's time of the point, in milliseconds.
    at: number;
    cpu: number;
    memory: number;
    netRecv: number;
    netSent: number;
    diskRead: number;
    diskWrite: number;
  }

  const KEPT_MS = 3600 * 1000;
  // Two points further apart than this have no data between them: the chart's line breaks there.
  const GAP_MS = 30 * 1000;
  const RANGES = [
    { seconds: 300, key: "monitor.range.5m", span: "monitor.range.5m.span" },
    { seconds: 900, key: "monitor.range.15m", span: "monitor.range.15m.span" },
    { seconds: 3600, key: "monitor.range.1h", span: "monitor.range.1h.span" }
  ];
  let range = RANGES[0];

  let history: Point[] = [];
  let live: Point[] = [];
  let historyError = "";

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
      historyError = error instanceof Error ? error.message : String(error);
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

  $: currentStats = $stats;
  $: addLive($stats);
  // The history up to the first message that arrived since the tab opened, then the messages.
  $: points = [...history.filter((point) => live.length === 0 || point.at < live[0].at), ...live];
  // The range ends at the newest point: what the charts show changes only with a new message.
  $: end = points.length > 0 ? points[points.length - 1].at : 0;
  $: start = end - range.seconds * 1000;
  $: shown = points.filter((point) => point.at >= start);

  function peak(series: Point[], value: (point: Point) => number): number {
    return series.reduce((highest, point) => Math.max(highest, value(point)), 0);
  }

  // The commands of an SVG path over a chart of 100 x 40, from start to end, with values from 0 to
  // max; a gap breaks the line.
  function path(series: Point[], value: (point: Point) => number, max: number, from: number): string {
    const span = range.seconds * 1000;
    let previous: number | null = null;
    const commands: string[] = [];
    for (const point of series) {
      const x = (((point.at - from) / span) * 100).toFixed(2);
      const y = (40 - (Math.min(value(point), max) / Math.max(max, 1e-9)) * 40).toFixed(2);
      const move = previous === null || point.at - previous > GAP_MS;
      // i18n-ignore: the commands of an SVG path
      commands.push((move ? "M " : "L ") + x + " " + y);
      previous = point.at;
    }
    return commands.join(" ");
  }

  const cpuOf = (point: Point) => point.cpu;
  const memoryOf = (point: Point) => point.memory;
  const recvOf = (point: Point) => point.netRecv;
  const sentOf = (point: Point) => point.netSent;
  const readOf = (point: Point) => point.diskRead;
  const writeOf = (point: Point) => point.diskWrite;

  $: cpuPeak = peak(shown, cpuOf);
  $: memoryPeak = peak(shown, memoryOf);
  $: recvPeak = peak(shown, recvOf);
  $: sentPeak = peak(shown, sentOf);
  $: readPeak = peak(shown, readOf);
  $: writePeak = peak(shown, writeOf);
  // The rates' charts scale to the peak of the range; percentages to 100.
  $: networkMax = Math.max(recvPeak, sentPeak);
  $: diskMax = Math.max(readPeak, writePeak);

  // The units of a size and of a rate, one for each power of 1024: keys of the catalog.
  const SIZE_UNITS = ["monitor.size.b", "monitor.size.kb", "monitor.size.mb", "monitor.size.gb", "monitor.size.tb"];
  const RATE_UNITS = ["monitor.rate.b", "monitor.rate.kb", "monitor.rate.mb", "monitor.rate.gb"];

  // The numbers in the format of the page and the units of the catalog (webui/AGENTS.md, Data):
  // a size or a rate in the largest unit that keeps it at 1 or more, with up to two decimals; a
  // percentage with one decimal.
  $: amount = (bytes: number, units: string[]): string => {
    const power = bytes >= 1 ? Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1) : 0;
    const value = Number((bytes / 1024 ** power).toFixed(2));
    return $t(units[power], {
      value: value.toLocaleString($intlLocale, { maximumFractionDigits: 2, useGrouping: false })
    });
  };
  $: percent = (value: number): string =>
    $t("monitor.percent", {
      value: value.toLocaleString($intlLocale, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
        useGrouping: false
      })
    });
  $: chartName = (metric: string): string => $t("monitor.chart", { metric: $t(metric), span: $t(range.span) });

  function percentClass(p: number): string {
    return p < 70 ? "text-success" : p < 90 ? "text-warning" : "text-error";
  }

  function percentFill(p: number): string {
    return p < 70 ? "bg-status-success" : p < 90 ? "bg-status-warning" : "bg-status-error";
  }
</script>

<div class="space-y-6">
  <div class="flex flex-wrap items-center justify-between gap-3">
    <h2 class="text-xl font-bold">{$t("monitor.title")}</h2>
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex flex-wrap gap-1" role="group" aria-label={$t("monitor.range")}>
        {#each RANGES as option (option.seconds)}
          <Button
            size="sm"
            variant={range === option ? "primary" : "secondary"}
            pressed={range === option}
            on:click={() => (range = option)}
          >
            {$t(option.key)}
          </Button>
        {/each}
      </div>
      <Badge variant={$wsConnected ? "success" : "error"} class="flex items-center gap-1">
        {#if $wsConnected}
          <Wifi class="w-4 h-4" />
          {$t("monitor.live")}
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

  {#if !currentStats?.cpu}
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
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" class="w-full h-16 mt-2 icon-cpu" role="img" aria-label={chartName("monitor.cpu")}>
          <path d={path(shown, cpuOf, 100, start)} fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
        </svg>
        <p class="stat-card-sublabel">{$t("monitor.peak", { value: percent(cpuPeak) })}</p>
      </Card>

      <Card>
        <div class="stat-card-header">
          <MemoryStick class="w-5 h-5 icon-memory" />
          <h3 class="stat-card-label">{$t("monitor.memory")}</h3>
        </div>
        <p class="stat-card-value {percentClass(currentStats.memory.percent)}">{percent(currentStats.memory.percent)}</p>
        <p class="stat-card-sublabel">{amount(currentStats.memory.used, SIZE_UNITS)} / {amount(currentStats.memory.total, SIZE_UNITS)}</p>
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" class="w-full h-16 mt-2 icon-memory" role="img" aria-label={chartName("monitor.memory")}>
          <path d={path(shown, memoryOf, 100, start)} fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
        </svg>
        <p class="stat-card-sublabel">{$t("monitor.peak", { value: percent(memoryPeak) })}</p>
      </Card>

      <Card>
        <div class="stat-card-header">
          <Network class="w-5 h-5 icon-network" />
          <h3 class="stat-card-label">{$t("monitor.network")}</h3>
        </div>
        <p class="text-sm">↓ <span class="text-success font-semibold">{amount(currentStats.network.bytes_recv_rate, RATE_UNITS)}</span></p>
        <p class="text-sm">↑ <span class="icon-temperature font-semibold">{amount(currentStats.network.bytes_sent_rate, RATE_UNITS)}</span></p>
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" class="w-full h-16 mt-2" role="img" aria-label={chartName("monitor.network")}>
          <path class="text-success" d={path(shown, recvOf, networkMax, start)} fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
          <path class="icon-temperature" d={path(shown, sentOf, networkMax, start)} fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
        </svg>
        <p class="stat-card-sublabel">{$t("monitor.peak.network", { recv: amount(recvPeak, RATE_UNITS), sent: amount(sentPeak, RATE_UNITS) })}</p>
      </Card>

      <Card>
        <div class="stat-card-header">
          <HardDrive class="w-5 h-5 icon-storage" />
          <h3 class="stat-card-label">{$t("monitor.diskIo")}</h3>
        </div>
        <p class="text-sm">{$t("monitor.read")} <span class="text-success font-semibold">{amount(currentStats.disk_io.read_rate, RATE_UNITS)}</span></p>
        <p class="text-sm">{$t("monitor.write")} <span class="text-error font-semibold">{amount(currentStats.disk_io.write_rate, RATE_UNITS)}</span></p>
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" class="w-full h-16 mt-2" role="img" aria-label={chartName("monitor.diskIo")}>
          <path class="text-success" d={path(shown, readOf, diskMax, start)} fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
          <path class="text-error" d={path(shown, writeOf, diskMax, start)} fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
        </svg>
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
