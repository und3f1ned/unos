<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import {
    getLogEntries,
    getLogSources,
    PRIORITY_LEVELS,
    TIME_RANGES,
    type LogEntry,
    type LogSource
  } from "$lib/api/logs";
  import { Button, Card, Badge, Alert, EmptyState, Select } from "$lib/components/ui";
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
    Globe,
    Calendar,
    X,
    ChevronDown,
    ChevronUp,
    Pause,
    Play
  } from "lucide-svelte";

  let entries: LogEntry[] = [];
  let sources: LogSource[] = [];
  let loading = true;
  let error = "";
  let search = "";
  let source = "journal";
  let priority = "";
  let since = "";
  let limitStr = "200";

  // Options for Select components
  const priorityOptions = [
    { value: "", label: "All priorities" },
    ...PRIORITY_LEVELS.map(p => ({ value: p.value, label: p.label }))
  ];

  const timeRangeOptions = TIME_RANGES.map(t => ({ value: t.value, label: t.label }));

  const limitOptions = [
    { value: "50", label: "50" },
    { value: "100", label: "100" },
    { value: "200", label: "200" },
    { value: "500", label: "500" },
    { value: "1000", label: "1000" }
  ];

  $: limit = parseInt(limitStr, 10);
  let autoRefresh = false;
  let refreshInterval: ReturnType<typeof setInterval> | null = null;
  let showFilters = true;

  // Source icons mapping
  const sourceIcons: Record<string, typeof FileText> = {
    journal: Server,
    dmesg: Terminal,
    docker: Box,
    samba: FileText,
    auth: Shield,
    supervisor: Server,
    caddy: Globe,
    cron: Calendar
  };

  onMount(async () => {
    await loadSources();
    await loadLogs();
  });

  onDestroy(() => {
    if (refreshInterval) {
      clearInterval(refreshInterval);
    }
  });

  async function loadSources() {
    try {
      const result = await getLogSources();
      sources = result.sources.filter(s => s.available);
    } catch (e) {
      console.error("Failed to load log sources:", e);
    }
  }

  async function loadLogs() {
    loading = true;
    error = "";
    try {
      const result = await getLogEntries({
        source,
        limit,
        priority: priority || undefined,
        search: search || undefined,
        since: since || undefined
      });
      entries = result.entries;
    } catch (e) {
      console.error("Failed to load logs:", e);
      error = e instanceof Error ? e.message : "Failed to load logs";
    }
    loading = false;
  }

  function handleSourceChange() {
    loadLogs();
  }

  function handleFilterChange() {
    loadLogs();
  }

  function handleSearch() {
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

  function toggleAutoRefresh() {
    autoRefresh = !autoRefresh;
    if (autoRefresh) {
      refreshInterval = setInterval(() => {
        loadLogs();
      }, 5000);
    } else if (refreshInterval) {
      clearInterval(refreshInterval);
      refreshInterval = null;
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

  function formatTimestamp(ts: string): string {
    if (!ts) return "";
    // Handle ISO format
    if (ts.includes("T")) {
      return ts.replace("T", " ");
    }
    return ts;
  }

  function highlightSearch(text: string): string {
    if (!search) return text;
    const regex = new RegExp(`(${search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    return text.replace(regex, '<mark class="bg-yellow-500/30 text-yellow-200">$1</mark>');
  }

  $: hasActiveFilters = priority || since || search;
  $: currentSource = sources.find(s => s.name === source);
</script>

<div class="space-y-4">
  <!-- Header -->
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-3">
      <h2 class="text-xl font-bold flex items-center gap-2">
        <FileText class="w-5 h-5 text-info" />
        System Logs
      </h2>
      {#if currentSource}
        <span class="text-sm text-secondary">
          {currentSource.description}
        </span>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      <Button
        variant={autoRefresh ? "primary" : "secondary"}
        size="sm"
        on:click={toggleAutoRefresh}
        title={autoRefresh ? "Stop auto-refresh" : "Start auto-refresh (5s)"}
      >
        {#if autoRefresh}
          <Pause class="w-4 h-4" />
        {:else}
          <Play class="w-4 h-4" />
        {/if}
        <span class="ml-1">{autoRefresh ? "Auto" : "Auto"}</span>
      </Button>
      <Button
        variant="secondary"
        size="sm"
        on:click={() => showFilters = !showFilters}
      >
        <Filter class="w-4 h-4" />
        <span class="ml-1">Filters</span>
        {#if showFilters}
          <ChevronUp class="w-3 h-3 ml-1" />
        {:else}
          <ChevronDown class="w-3 h-3 ml-1" />
        {/if}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        on:click={loadLogs}
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
      <button
        class="px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors
          {source === s.name
            ? 'bg-blue-600 text-white'
            : 'bg-surface-overlay text-secondary hover:text-content hover:bg-surface-muted'}"
        on:click={() => { source = s.name; handleSourceChange(); }}
      >
        <Icon class="w-4 h-4" />
        {s.label}
      </button>
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
              on:keydown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="Search in logs... (press Enter)"
              class="w-full pl-10 pr-10 py-2 bg-surface-overlay border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {#if search}
              <button
                class="absolute right-3 top-1/2 -translate-y-1/2 text-secondary hover:text-content"
                on:click={clearSearch}
              >
                <X class="w-4 h-4" />
              </button>
            {/if}
          </div>
          <Button variant="primary" size="sm" on:click={handleSearch}>
            Search
          </Button>
        </div>

        <!-- Filter Row -->
        <div class="flex flex-wrap items-end gap-4">
          <!-- Priority Filter -->
          <Select
            class="flex-1 min-w-[150px]"
            label="Priority Level"
            bind:value={priority}
            options={priorityOptions}
            placeholder="All priorities"
            on:change={handleFilterChange}
          />

          <!-- Time Range Filter -->
          <Select
            class="flex-1 min-w-[150px]"
            label="Time Range"
            bind:value={since}
            options={timeRangeOptions}
            placeholder="All time"
            on:change={handleFilterChange}
          />

          <!-- Limit -->
          <Select
            class="w-[120px]"
            label="Limit"
            bind:value={limitStr}
            options={limitOptions}
            on:change={handleFilterChange}
          />

          <!-- Clear Filters -->
          {#if hasActiveFilters}
            <Button variant="ghost" size="sm" on:click={clearFilters}>
              <X class="w-4 h-4 mr-1" />
              Clear filters
            </Button>
          {/if}
        </div>

        <!-- Active Filters Badges -->
        {#if hasActiveFilters}
          <div class="flex flex-wrap gap-2 pt-2 border-t border-border">
            <span class="text-sm text-secondary">Active filters:</span>
            {#if priority}
              <Badge variant="info">
                Priority: {PRIORITY_LEVELS.find(p => p.value === priority)?.label || priority}
              </Badge>
            {/if}
            {#if since}
              <Badge variant="info">
                Time: {TIME_RANGES.find(t => t.value === since)?.label || since}
              </Badge>
            {/if}
            {#if search}
              <Badge variant="info">
                Search: "{search}"
              </Badge>
            {/if}
          </div>
        {/if}
      </div>
    </Card>
  {/if}

  <!-- Error Alert -->
  {#if error}
    <Alert variant="error" title="Error loading logs">
      {error}
    </Alert>
  {/if}

  <!-- Logs Display -->
  <Card>
    <div class="flex items-center justify-between mb-3">
      <span class="text-sm text-secondary">
        {#if loading}
          Loading...
        {:else}
          Showing {entries.length} log entries
        {/if}
      </span>
      {#if autoRefresh}
        <span class="text-xs text-success flex items-center gap-1">
          <span class="w-2 h-2 bg-success rounded-full animate-pulse"></span>
          Auto-refreshing every 5s
        </span>
      {/if}
    </div>

    <div class="bg-surface-muted rounded-lg overflow-hidden">
      <div class="max-h-[600px] overflow-y-auto scrollbar-thin">
        {#if loading && entries.length === 0}
          <div class="flex items-center justify-center py-12">
            <RefreshCw class="w-6 h-6 animate-spin text-secondary" />
            <span class="ml-2 text-secondary">Loading logs...</span>
          </div>
        {:else if entries.length === 0}
          <EmptyState
            icon={FileText}
            title="No logs found"
            description={hasActiveFilters
              ? "Try adjusting your filters or search terms"
              : "No log entries available for this source"}
          />
        {:else}
          <table class="w-full text-sm">
            <thead class="sticky top-0 bg-surface-overlay border-b border-border">
              <tr>
                <th class="px-3 py-2 text-left text-secondary font-medium w-[160px]">Time</th>
                <th class="px-3 py-2 text-left text-secondary font-medium w-[80px]">Level</th>
                <th class="px-3 py-2 text-left text-secondary font-medium w-[120px]">Source</th>
                <th class="px-3 py-2 text-left text-secondary font-medium">Message</th>
              </tr>
            </thead>
            <tbody class="font-mono text-xs">
              {#each entries as entry, i}
                <tr class="border-b border-border/50 hover:bg-surface-overlay/50 transition-colors">
                  <td class="px-3 py-2 text-secondary whitespace-nowrap">
                    {formatTimestamp(entry.timestamp)}
                  </td>
                  <td class="px-3 py-2">
                    <Badge variant={getPriorityVariant(entry.priority)} class="text-xs">
                      {entry.priority}
                    </Badge>
                  </td>
                  <td class="px-3 py-2 text-secondary">
                    {#if entry.unit}
                      <span class="truncate block max-w-[120px]" title={entry.unit}>
                        {entry.unit}
                      </span>
                    {:else}
                      <span class="text-muted">-</span>
                    {/if}
                  </td>
                  <td class="px-3 py-2 text-content">
                    <span class="break-all">
                      {#if search}
                        {@html highlightSearch(entry.message)}
                      {:else}
                        {entry.message}
                      {/if}
                    </span>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    </div>
  </Card>
</div>
