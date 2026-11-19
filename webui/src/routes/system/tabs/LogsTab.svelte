<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import {
    getLogEntries,
    getLogSources,
    streamLogEntries,
    type JournalStream,
    type LogEntry,
    type LogPriority,
    type LogQueryParams,
    type LogSource
  } from "$lib/api/logs";
  import { Button, Card, Badge, Alert, EmptyState, Select } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { apiTime } from "$lib/time";
  import {
    FileText,
    Search,
    RefreshCw,
    Filter,
    Clock,
    AlertTriangle,
    Terminal,
    Server,
    Shield,
    Box,
    Calendar,
    X,
    ChevronDown,
    ChevronUp,
    Pause,
    Play
  } from "lucide-svelte";

  // Replaced whole, never changed in place: up to 1000 entries need no deep proxy.
  let entries = $state.raw<LogEntry[]>([]);
  let sources = $state<LogSource[]>([]);
  let loading = $state(true);
  let error = $state("");
  let search = $state("");
  let source = $state("journal");
  let priority = $state("");
  let since = $state("");
  let limitStr = $state("200");

  // The server's values, shown by the keys of their texts in the catalog (ADR-0287). The sources
  // of GET /logs/sources by name: one without keys shows the server's label and description.
  const SOURCE_LABELS: Record<string, string> = {
    journal: "logs.source.journal",
    dmesg: "logs.source.dmesg",
    docker: "logs.source.docker",
    samba: "logs.source.samba",
    auth: "logs.source.auth",
    cron: "logs.source.cron"
  };
  const SOURCE_DESCRIPTIONS: Record<string, string> = {
    journal: "logs.source.journalDescription",
    dmesg: "logs.source.dmesgDescription",
    docker: "logs.source.dockerDescription",
    samba: "logs.source.sambaDescription",
    auth: "logs.source.authDescription",
    cron: "logs.source.cronDescription"
  };
  // The journal's levels, the most severe first: the filter lists a level and the more severe
  // ones. LEVELS names an entry's level in the list.
  const PRIORITIES: Record<LogPriority, string> = {
    emerg: "logs.priority.emerg",
    alert: "logs.priority.alert",
    crit: "logs.priority.crit",
    err: "logs.priority.err",
    warning: "logs.priority.warning",
    notice: "logs.priority.notice",
    info: "logs.priority.info",
    debug: "logs.priority.debug"
  };
  const LEVELS: Record<LogPriority, string> = {
    emerg: "logs.level.emerg",
    alert: "logs.level.alert",
    crit: "logs.level.crit",
    err: "logs.level.err",
    warning: "logs.level.warning",
    notice: "logs.level.notice",
    info: "logs.level.info",
    debug: "logs.level.debug"
  };
  // The time ranges of the filter by the since they send: journalctl's --since, which the server
  // takes as <N> minutes ago, <N> hours ago or <N> days ago; "" reads the whole journal.
  const TIME_RANGES: Record<string, string> = {
    "": "logs.time.all",
    "5 minutes ago": "logs.time.last5Minutes",
    "15 minutes ago": "logs.time.last15Minutes",
    "1 hour ago": "logs.time.lastHour",
    "6 hours ago": "logs.time.last6Hours",
    "24 hours ago": "logs.time.last24Hours",
    "7 days ago": "logs.time.last7Days"
  };

  /** The text of `value` by its key in `keys`, in the language of the page; else `fallback`. */
  function textOf(keys: Record<string, string>, value: string, fallback: string = value): string {
    return Object.prototype.hasOwnProperty.call(keys, value) ? $t(keys[value]) : fallback;
  }

  // Options for Select components, in the language of the page.
  let priorityOptions = $derived([
    { value: "", label: $t("logs.filter.allPriorities") },
    ...Object.entries(PRIORITIES).map(([value, key]) => ({ value, label: $t(key) }))
  ]);

  let timeRangeOptions = $derived(Object.entries(TIME_RANGES).map(([value, key]) => ({ value, label: $t(key) })));

  const limitOptions = [
    { value: "50", label: "50" },
    { value: "100", label: "100" },
    { value: "200", label: "200" },
    { value: "500", label: "500" },
    { value: "1000", label: "1000" }
  ];

  // Auto: the entries come from the journal socket, the newest ones of the filters and then each
  // new one as the journal gets it (ADR-0366); nothing asks GET /entries again.
  let autoRefresh = $state(false);
  let stream: JournalStream | null = null;
  // Why Auto stopped by itself: the connection was lost, or the server refused it (4403).
  let autoStopped = $state<"" | "lost" | "forbidden">("");
  let showFilters = $state(true);
  // The number of the newest request: the answer of an older one (a source picked
  // before, an earlier refresh) arrives too late to be shown.
  let latestRequest = 0;

  // Source icons mapping
  const sourceIcons: Record<string, typeof FileText> = {
    journal: Server,
    dmesg: Terminal,
    docker: Box,
    samba: FileText,
    auth: Shield,
    cron: Calendar
  };

  onMount(async () => {
    await loadSources();
    await loadLogs();
  });

  onDestroy(() => {
    stream?.close();
  });

  async function loadSources() {
    try {
      const result = await getLogSources();
      sources = result.sources;
    } catch (e) {
      console.error("Failed to load log sources:", e);
    }
  }

  // The filters of a request, read from the fields when it is sent, also right after a handler
  // changed one of them.
  function query(): LogQueryParams {
    return {
      source,
      limit: Number(limitStr),
      priority: priority || undefined,
      search: search || undefined,
      since: since || undefined
    };
  }

  // The entries as the server lists them: the newest first (ADR-0366); with Auto, from the
  // journal socket.
  async function loadLogs() {
    const request = ++latestRequest;
    loading = true;
    error = "";
    autoStopped = "";
    if (autoRefresh) {
      follow();
      return;
    }
    try {
      const result = await getLogEntries(query());
      if (request !== latestRequest) return;
      entries = result.entries;
    } catch (e) {
      if (request !== latestRequest) return;
      console.error("Failed to load logs:", e);
      // The entries of the request before belong to another source or other filters.
      entries = [];
      error = e instanceof Error ? e.message : $t("logs.loadFailed");
    }
    loading = false;
  }

  function handleSourceChange() {
    loadLogs();
  }

  function handleFilterChange() {
    loadLogs();
  }

  // A search waits for the answer that is still coming: asking again would only
  // add another journalctl on the host behind it (ADR-0258).
  function handleSearch() {
    if (loading) return;
    loadLogs();
  }

  function clearSearch() {
    search = "";
    loadLogs();
  }

  function clearFilters() {
    priority = "";
    since = "";
    search = "";
    limitStr = "200";
    loadLogs();
  }

  /** Open the journal socket for the filters of now, in place of the one before. */
  function follow() {
    stream?.close();
    const limit = Number(limitStr);
    stream = streamLogEntries(query(), {
      onEntries(list) {
        entries = list;
        loading = false;
      },
      onEntry(entry) {
        // The newest first, and no more than the limit.
        entries = [entry, ...entries].slice(0, limit);
      },
      onError(message) {
        // As an error of GET /entries: no entries belong to the request that failed.
        entries = [];
        error = message;
      },
      onEnd(reason) {
        stream = null;
        autoRefresh = false;
        loading = false;
        if (!error) autoStopped = reason === "forbidden" ? "forbidden" : "lost";
      }
    });
  }

  function toggleAutoRefresh() {
    autoRefresh = !autoRefresh;
    if (autoRefresh) {
      loadLogs();
    } else {
      // The entries stay as they are.
      stream?.close();
      stream = null;
      loading = false;
    }
  }

  function getPriorityVariant(p: string): "error" | "warning" | "success" | "info" | "neutral" {
    switch (p) {
      case "emerg":
      case "alert":
      case "crit":
      case "err":
        return "error";
      case "warning":
        return "warning";
      case "notice":
        return "info";
      case "info":
        return "neutral";
      case "debug":
        return "neutral";
      default:
        return "neutral";
    }
  }

  // The server gives UTC times with their offset; the browser shows them in its own time zone,
  // in the language of the page, as toLocaleString writes them. One formatter per language for
  // every row (reactive, so that the list follows a change of the language).
  let dateFormat = $derived(
    new Intl.DateTimeFormat($intlLocale, {
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric"
    })
  );
  function formatTimestamp(ts: string): string {
    if (!ts) return "";
    const date = apiTime(ts);
    return date ? dateFormat.format(date) : ts;
  }

  // Split a log message around case-insensitive matches of the search term.
  // The parts are rendered as text, never as HTML: log lines are attacker-
  // controllable (usernames, request paths, container output).
  function splitByMatch(text: string, term: string): { text: string; match: boolean }[] {
    if (!term) return [{ text, match: false }];
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // With one capture group, split() puts the matches at the odd indexes.
    return text
      .split(new RegExp(`(${escaped})`, "gi"))
      .map((part, i) => ({ text: part, match: i % 2 === 1 }))
      .filter((part) => part.text !== "");
  }

  let hasActiveFilters = $derived(priority || since || search);
  let currentSource = $derived(sources.find(s => s.name === source));
</script>

<div class="space-y-4">
  <!-- Header -->
  <div class="flex flex-wrap items-center justify-between gap-3">
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
      <h2 class="text-xl font-bold flex items-center gap-2">
        <FileText class="w-5 h-5 text-info" />
        {$t("logs.title")}
      </h2>
      {#if currentSource}
        <span class="text-sm text-secondary">
          {textOf(SOURCE_DESCRIPTIONS, currentSource.name, currentSource.description)}
        </span>
      {/if}
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <Button
        variant={autoRefresh ? "primary" : "secondary"}
        size="sm"
        onclick={toggleAutoRefresh}
        title={autoRefresh ? $t("logs.autoStop") : $t("logs.autoStart")}
      >
        {#if autoRefresh}
          <Pause class="w-4 h-4" />
        {:else}
          <Play class="w-4 h-4" />
        {/if}
        <span class="ml-1">{$t("logs.auto")}</span>
      </Button>
      <Button
        variant="secondary"
        size="sm"
        onclick={() => showFilters = !showFilters}
      >
        <Filter class="w-4 h-4" />
        <span class="ml-1">{$t("logs.filters")}</span>
        {#if showFilters}
          <ChevronUp class="w-3 h-3 ml-1" />
        {:else}
          <ChevronDown class="w-3 h-3 ml-1" />
        {/if}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        onclick={loadLogs}
        disabled={loading}
      >
        <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      </Button>
    </div>
  </div>

  <!-- Source Tabs -->
  <div class="flex flex-wrap gap-2">
    {#each sources as s}
      {@const Icon = sourceIcons[s.name] || FileText}
      <Button variant="plain"
        class="px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors
          {source === s.name
            ? 'bg-brand text-content-on-brand'
            : 'bg-surface-overlay text-secondary hover:text-content hover:bg-surface-muted'}"
        onclick={() => { source = s.name; handleSourceChange(); }}
      >
        <Icon class="w-4 h-4" />
        {textOf(SOURCE_LABELS, s.name, s.label)}
      </Button>
    {/each}
  </div>

  <!-- Filters Panel -->
  {#if showFilters}
    <Card>
      <div class="space-y-4">
        <!-- Search -->
        <div class="flex gap-2">
          <div class="relative flex-1">
            <Search class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-secondary" />
            <input
              type="text"
              bind:value={search}
              onkeydown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder={$t("logs.searchPlaceholder")}
              class="form-input pl-10 pr-10 text-sm"
            />
            {#if search}
              <Button variant="plain" class="absolute right-3 top-1/2 -translate-y-1/2 text-secondary hover:text-content"
                onclick={clearSearch}
              >
                <X class="w-4 h-4" />
              </Button>
            {/if}
          </div>
          <Button variant="primary" size="sm" onclick={handleSearch} disabled={loading}>
            {$t("logs.search")}
          </Button>
        </div>

        <!-- Filter Row -->
        <div class="flex flex-wrap items-end gap-4">
          <!-- Priority Filter -->
          <Select
            class="flex-1 min-w-[150px]"
            label={$t("logs.filter.priority")}
            bind:value={priority}
            options={priorityOptions}
            placeholder={$t("logs.filter.allPriorities")}
            onchange={handleFilterChange}
          />

          <!-- Time Range Filter -->
          <Select
            class="flex-1 min-w-[150px]"
            label={$t("logs.filter.timeRange")}
            bind:value={since}
            options={timeRangeOptions}
            placeholder={$t("logs.time.all")}
            onchange={handleFilterChange}
          />

          <!-- Limit -->
          <Select
            class="w-[120px]"
            label={$t("logs.filter.limit")}
            bind:value={limitStr}
            options={limitOptions}
            onchange={handleFilterChange}
          />

          <!-- Clear Filters -->
          {#if hasActiveFilters}
            <Button variant="ghost" size="sm" onclick={clearFilters}>
              <X class="w-4 h-4 mr-1" />
              {$t("logs.filter.clear")}
            </Button>
          {/if}
        </div>

        <!-- Active Filters Badges -->
        {#if hasActiveFilters}
          <div class="flex flex-wrap gap-2 pt-2 border-t border-border">
            <span class="text-sm text-secondary">{$t("logs.filter.active")}</span>
            {#if priority}
              <Badge variant="info">
                {$t("logs.filter.priorityBadge", { level: textOf(PRIORITIES, priority) })}
              </Badge>
            {/if}
            {#if since}
              <Badge variant="info">
                {$t("logs.filter.timeBadge", { range: textOf(TIME_RANGES, since) })}
              </Badge>
            {/if}
            {#if search}
              <Badge variant="info">
                {$t("logs.filter.searchBadge", { text: search })}
              </Badge>
            {/if}
          </div>
        {/if}
      </div>
    </Card>
  {/if}

  <!-- Error Alert, instead of the list: no entries belong to the request that failed -->
  {#if error}
    <Alert variant="error" title={$t("logs.loadError")}>
      {error}
    </Alert>
  {:else}
    <!-- Logs Display -->
    <Card>
      <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
        <span class="text-sm text-secondary">
          {#if loading}
            {$t("common.loading")}
          {:else}
            {$t("logs.count", { n: entries.length })}
          {/if}
        </span>
        {#if autoRefresh}
          <span class="text-xs text-success flex items-center gap-1">
            <span class="w-2 h-2 bg-success rounded-full animate-pulse"></span>
            {$t("logs.following")}
          </span>
        {:else if autoStopped}
          <span class="text-xs text-warning">
            {$t(autoStopped === "forbidden" ? "logs.autoForbidden" : "logs.autoLost")}
          </span>
        {/if}
      </div>

      <div class="bg-surface-muted rounded-lg overflow-hidden">
        <div class="max-h-[600px] overflow-y-auto scrollbar-thin">
          {#if loading && entries.length === 0}
            <div class="flex items-center justify-center py-12">
              <RefreshCw class="w-6 h-6 animate-spin text-secondary" />
              <span class="ml-2 text-secondary">{$t("logs.loadingEntries")}</span>
            </div>
          {:else if entries.length === 0}
            <EmptyState
              icon={FileText}
              title={$t("logs.empty")}
              description={hasActiveFilters ? $t("logs.emptyFiltered") : $t("logs.emptySource")}
            />
          {:else}
            <!-- Below sm each entry stacks: its time, level and program on a line, the message
                 below them at the full width (ADR-0302). -->
            <table class="w-full text-sm max-sm:block">
              <thead class="sticky top-0 bg-surface-overlay border-b border-border max-sm:hidden">
                <tr>
                  <th class="px-3 py-2 text-left text-secondary font-medium w-[160px]">{$t("logs.column.time")}</th>
                  <th class="px-3 py-2 text-left text-secondary font-medium w-[80px]">{$t("logs.column.level")}</th>
                  <th class="px-3 py-2 text-left text-secondary font-medium w-[120px]">{$t("logs.column.source")}</th>
                  <th class="px-3 py-2 text-left text-secondary font-medium">{$t("logs.column.message")}</th>
                </tr>
              </thead>
              <tbody class="font-mono text-xs max-sm:block">
                {#each entries as entry, i}
                  <tr
                    class="border-b border-border/50 hover:bg-surface-overlay/50 transition-colors max-sm:flex max-sm:flex-wrap max-sm:items-center max-sm:gap-x-3 max-sm:px-3 max-sm:py-2"
                  >
                    <td class="px-3 py-2 text-secondary whitespace-nowrap max-sm:p-0">
                      {formatTimestamp(entry.timestamp)}
                    </td>
                    <td class="px-3 py-2 max-sm:p-0">
                      <Badge variant={getPriorityVariant(entry.priority)} class="text-xs">
                        {textOf(LEVELS, entry.priority)}
                      </Badge>
                    </td>
                    <td class="px-3 py-2 text-secondary max-sm:p-0 max-sm:min-w-0">
                      {#if entry.unit}
                        <span class="truncate block max-w-[120px]" title={entry.unit}>
                          {entry.unit}
                        </span>
                      {:else}
                        <span class="text-muted">-</span>
                      {/if}
                    </td>
                    <td class="px-3 py-2 text-content max-sm:w-full max-sm:p-0 max-sm:pt-1">
                      <span class="break-all"
                        >{#each splitByMatch(entry.message ?? "", search) as part}{#if part.match}<mark
                              class="bg-status-warning/20 text-status-warning rounded-sm">{part.text}</mark
                            >{:else}{part.text}{/if}{/each}</span
                      >
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      </div>
    </Card>
  {/if}
</div>
