<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import {
		getLogEntries,
		getLogSources,
		streamLogEntries,
		type JournalStream,
		type LogEntry,
		type LogQueryParams,
		type LogSource
	} from '$lib/api/logs';
	import { Button, Switch, Segmented } from '$lib/components/ui';
	import LogEntries from '$lib/components/system/journal/LogEntries.svelte';
	import LogFilters from '$lib/components/system/journal/LogFilters.svelte';
	import { textOf } from '$lib/components/system/journal/logs';
	import { t } from '$lib/i18n';
	import { RefreshCw } from 'lucide-svelte';

	// Replaced whole, never changed in place: up to 1000 entries need no deep proxy.
	let entries = $state.raw<LogEntry[]>([]);
	let sources = $state<LogSource[]>([]);
	let loading = $state(true);
	let error = $state('');
	let search = $state('');
	let source = $state('journal');
	let priority = $state('');
	let since = $state('');
	let limitStr = $state('200');

	// The server's values, shown by the keys of their texts in the catalog (ADR-0287). The sources
	// of GET /logs/sources by name: one without keys shows the server's label and description.
	const SOURCE_LABELS: Record<string, string> = {
		journal: 'logs.source.journal',
		dmesg: 'logs.source.dmesg',
		docker: 'logs.source.docker',
		samba: 'logs.source.samba',
		auth: 'logs.source.auth',
		cron: 'logs.source.cron'
	};
	const SOURCE_DESCRIPTIONS: Record<string, string> = {
		journal: 'logs.source.journalDescription',
		dmesg: 'logs.source.dmesgDescription',
		docker: 'logs.source.dockerDescription',
		samba: 'logs.source.sambaDescription',
		auth: 'logs.source.authDescription',
		cron: 'logs.source.cronDescription'
	};

	// Auto: the entries come from the journal socket, the newest ones of the filters and then each
	// new one as the journal gets it (ADR-0366); nothing asks GET /entries again.
	let autoRefresh = $state(false);
	let stream: JournalStream | null = null;
	// Why Auto stopped by itself: the connection was lost, or the server refused it (4403).
	let autoStopped = $state<'' | 'lost' | 'forbidden'>('');
	// The number of the newest request: the answer of an older one (a source picked
	// before, an earlier refresh) arrives too late to be shown.
	let latestRequest = 0;

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
			console.error('Failed to load log sources:', e);
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
		error = '';
		autoStopped = '';
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
			console.error('Failed to load logs:', e);
			// The entries of the request before belong to another source or other filters.
			entries = [];
			error = e instanceof Error ? e.message : $t('logs.loadFailed');
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
		search = '';
		loadLogs();
	}

	function clearFilters() {
		priority = '';
		since = '';
		search = '';
		limitStr = '200';
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
				if (!error) autoStopped = reason === 'forbidden' ? 'forbidden' : 'lost';
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
	let currentSource = $derived(sources.find((s) => s.name === source));
</script>

<!-- One look of tabs for the sources, Follow as an on/off, the filters always shown, and one
     refresh. -->
<div class="space-y-4">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div class="min-w-0">
			<h2 class="text-xl font-semibold">{$t('logs.title')}</h2>
			{#if currentSource}
				<p class="page-subtitle">{textOf($t, SOURCE_DESCRIPTIONS, currentSource.name, currentSource.description)}</p>
			{/if}
		</div>
		<div class="flex flex-wrap items-center gap-3">
			<!-- A Switch (ADR-0432): follow the journal as it gets new entries. -->
			<label
				class="flex cursor-pointer items-center gap-2 text-sm text-content-secondary"
				title={autoRefresh ? $t('logs.autoStop') : $t('logs.autoStart')}
			>
				<Switch checked={autoRefresh} onchange={toggleAutoRefresh} />
				{$t('logs.auto')}
			</label>
			<Button variant="ghost" size="icon" icon={RefreshCw} {loading} title={$t('common.refresh')} onclick={loadLogs}>
				<span class="sr-only">{$t('common.refresh')}</span>
			</Button>
		</div>
	</div>

	<!-- The journal or a log file to read: a filter of the entries, a Segmented (ADR-0432). -->
	{#if sources.length > 0}
		<Segmented
			options={sources.map((s) => ({ value: s.name, label: textOf($t, SOURCE_LABELS, s.name, s.label) }))}
			value={source}
			label={$t('logs.sourceLabel')}
			onchange={(name) => {
				source = name;
				handleSourceChange();
			}}
			class="flex-wrap"
		/>
	{/if}

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

	<!-- Error Alert, instead of the list: no entries belong to the request that failed -->
	<LogEntries {entries} {loading} {error} {search} {hasActiveFilters} {autoRefresh} {autoStopped} />
</div>
