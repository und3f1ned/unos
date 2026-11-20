<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import {
    getLogEntries,
    getLogSources,
    streamLogEntries,
    type JournalStream,
    type LogEntry,
    type LogQueryParams,
    type LogSource
  } from "$lib/api/logs";
  import { Button } from "$lib/components/ui";
  import LogEntries from "$lib/components/system/journal/LogEntries.svelte";
  import LogFilters from "$lib/components/system/journal/LogFilters.svelte";
  import { textOf } from "$lib/components/system/journal/logs";
  import { t } from "$lib/i18n";
  import {
    FileText,
    RefreshCw,
    Filter,
    Terminal,
    Server,
    Shield,
    Box,
    Calendar,
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

  let hasActiveFilters = $derived(!!(priority || since || search));
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
          {textOf($t, SOURCE_DESCRIPTIONS, currentSource.name, currentSource.description)}
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
        {textOf($t, SOURCE_LABELS, s.name, s.label)}
      </Button>
    {/each}
  </div>

  <!-- Filters Panel -->
  {#if showFilters}
    <LogFilters
      bind:search
      bind:priority
      bind:since
      bind:limitStr
      {loading}
      {hasActiveFilters}
      onsearch={handleSearch}
      onclearsearch={clearSearch}
      onfilterchange={handleFilterChange}
      onclear={clearFilters}
    />
  {/if}

  <!-- Error Alert, instead of the list: no entries belong to the request that failed -->
  <LogEntries {entries} {loading} {error} {search} {hasActiveFilters} {autoRefresh} {autoStopped} />
</div>
