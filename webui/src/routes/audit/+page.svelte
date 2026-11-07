<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { Download, ScrollText, X } from 'lucide-svelte';
	import {
		actionLabel,
		exportAuditLog,
		getAuditActions,
		getAuditCount,
		getAuditLogs,
		type AuditAction,
		type AuditEntry,
		type AuditExportFormat,
		type AuditFilters,
		type AuditSeverity
	} from '$lib/api/audit';
	import { Alert, Badge, Button, Card, EmptyState, Input, Select } from '$lib/components/ui';

	// The audit log as a timeline, newest first (ADR-0233). Times are the server's local time
	// as the entries carry it, without a zone: the page shows them as they are, and the date
	// filters are the server's days too.

	// Entries per "Show more", and how long typing may pause before the list follows it.
	const PAGE_SIZE = 100;
	const TYPING_DELAY_MS = 300;
	const SEARCH_MAX_CHARS = 256; // MAX_SEARCH_CHARS of supervisor/src/audit/router.py

	let search = '';
	let username = '';
	let action = '';
	let startDate = '';
	let endDate = '';

	let actions: AuditAction[] = [];
	let actionsError = '';
	let entries: AuditEntry[] = [];
	let total = 0;
	let limit = PAGE_SIZE;
	// loaded: an answer has arrived; loading: a request is out.
	let loaded = false;
	let loading = false;
	let error = '';
	let exporting: AuditExportFormat | null = null;
	let exportError = '';

	let mounted = false;
	let timer: ReturnType<typeof setTimeout> | undefined;
	// The filters of the last scheduled load: spaces added around a value change nothing.
	let scheduled = '';
	// Each load gets a number: an answer that is not the newest request's is dropped.
	let latest = 0;

	let filters: AuditFilters;
	$: filters = {
		search: search.trim(),
		username: username.trim(),
		action,
		start_date: startDate,
		end_date: endDate
	};
	$: filtered = Object.values(filters).some(Boolean);
	// A change of the filters shows the newest entries that match, from the first page.
	$: if (mounted) schedule(filters);

	$: actionOptions = [
		{ value: '', label: 'All actions' },
		...actions
			.map((a) => ({ value: a.value, label: actionLabel(a.value) }))
			.sort((a, b) => a.label.localeCompare(b.label))
	];
	$: days = byDay(entries);
	$: countText =
		entries.length < total
			? `Showing ${entries.length} of ${total} entries`
			: `${total} ${total === 1 ? 'entry' : 'entries'}`;

	onMount(() => {
		mounted = true;
		void loadActions();
	});

	onDestroy(() => clearTimeout(timer));

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	function schedule(current: AuditFilters) {
		const key = JSON.stringify(current);
		if (key === scheduled) return;
		scheduled = key;
		clearTimeout(timer);
		timer = setTimeout(
			() => {
				limit = PAGE_SIZE;
				void load(current, limit);
			},
			loaded ? TYPING_DELAY_MS : 0
		);
	}

	async function load(current: AuditFilters, size: number) {
		const request = ++latest;
		loading = true;
		try {
			const [page, count] = await Promise.all([getAuditLogs(current, size), getAuditCount(current)]);
			if (request !== latest) return;
			entries = page;
			total = count;
			error = '';
		} catch (e) {
			if (request !== latest) return;
			// What was shown matched other filters.
			entries = [];
			total = 0;
			error = messageOf(e);
		} finally {
			if (request === latest) {
				loading = false;
				loaded = true;
			}
		}
	}

	async function loadActions() {
		try {
			actions = await getAuditActions();
			actionsError = '';
		} catch (e) {
			actionsError = `Could not load the actions: ${messageOf(e)}`;
		}
	}

	// From the newest entry with a larger limit rather than the next offset: entries written
	// since the last load would shift the pages and show an entry twice.
	function showMore() {
		limit += PAGE_SIZE;
		void load(filters, limit);
	}

	function retry() {
		void load(filters, limit);
	}

	function clearFilters() {
		search = '';
		username = '';
		action = '';
		startDate = '';
		endDate = '';
	}

	/** The browser's date today as YYYYMMDD, for the file name of an export. */
	function today(): string {
		const now = new Date();
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
	}

	async function exportAs(format: AuditExportFormat) {
		exporting = format;
		exportError = '';
		try {
			// The name the server gives the file (Content-Disposition), with the browser's date.
			await exportAuditLog(filters, format, `audit_log_${today()}.${format}`);
		} catch (e) {
			exportError = messageOf(e);
		} finally {
			exporting = null;
		}
	}

	interface Day {
		day: string;
		entries: AuditEntry[];
	}

	/** The entries by the day of their time, in their order (newest first). */
	function byDay(list: AuditEntry[]): Day[] {
		const groups: Day[] = [];
		for (const entry of list) {
			const day = entry.timestamp.slice(0, 10);
			const last = groups[groups.length - 1];
			if (last && last.day === day) last.entries.push(entry);
			else groups.push({ day, entries: [entry] });
		}
		return groups;
	}

	function dayLabel(day: string): string {
		const [year, month, date] = day.split('-').map(Number);
		const moment = new Date(year, month - 1, date);
		if (Number.isNaN(moment.getTime())) return day;
		return moment.toLocaleDateString(undefined, {
			weekday: 'long',
			year: 'numeric',
			month: 'long',
			day: 'numeric'
		});
	}

	const SEVERITY: Record<AuditSeverity, { label: string; variant: 'info' | 'warning' | 'error' }> = {
		info: { label: 'Info', variant: 'info' },
		warning: { label: 'Warning', variant: 'warning' },
		critical: { label: 'Critical', variant: 'error' }
	};

	function markerClass(entry: AuditEntry): string {
		if (!entry.success || entry.severity === 'critical') return 'bg-status-error';
		if (entry.severity === 'warning') return 'bg-status-warning';
		return 'bg-status-info';
	}

	function resourceOf(entry: AuditEntry): string {
		return [entry.resource_type, entry.resource_id].filter(Boolean).join(' ');
	}
</script>

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">Audit Log</h1>
		<div class="flex items-center gap-2">
			<Button
				variant="secondary"
				icon={Download}
				loading={exporting === 'csv'}
				disabled={exporting !== null}
				on:click={() => exportAs('csv')}>Export CSV</Button
			>
			<Button
				variant="secondary"
				icon={Download}
				loading={exporting === 'json'}
				disabled={exporting !== null}
				on:click={() => exportAs('json')}>Export JSON</Button
			>
		</div>
	</div>

	{#if exportError}
		<Alert
			variant="error"
			title="Could not export the audit log"
			dismissible
			on:dismiss={() => (exportError = '')}>{exportError}</Alert
		>
	{/if}

	<Card>
		<div role="search" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
			<Input
				id="audit-search"
				type="search"
				label="Search"
				bind:value={search}
				placeholder="User, address, resource or details"
				maxlength={SEARCH_MAX_CHARS}
				class="sm:col-span-2"
			/>
			<Input
				id="audit-user"
				label="User"
				bind:value={username}
				placeholder="The exact name"
				autocomplete="off"
			/>
			<Select
				id="audit-action"
				label="Action"
				bind:value={action}
				options={actionOptions}
				error={actionsError}
			/>
			<Input id="audit-from" type="date" label="From" bind:value={startDate} />
			<Input id="audit-to" type="date" label="To" bind:value={endDate} />
		</div>
		<div class="mt-3 flex min-h-8 items-center justify-between gap-2">
			<p class="text-sm text-content-secondary" role="status">{loaded && !error ? countText : ''}</p>
			{#if filtered}
				<Button variant="ghost" size="sm" icon={X} on:click={clearFilters}>Clear filters</Button>
			{/if}
		</div>
	</Card>

	{#if error}
		<Alert variant="error" title="Could not load the audit log">
			<p>{error}</p>
			<Button variant="secondary" size="sm" class="mt-3" loading={loading} on:click={retry}
				>Retry</Button
			>
		</Alert>
	{:else if !loaded}
		<p class="loading-text">Loading...</p>
	{:else if entries.length === 0}
		<Card>
			{#if filtered}
				<EmptyState
					icon={ScrollText}
					title="No entries match the filters"
					description="Change or clear the filters to see more entries."
				/>
			{:else}
				<EmptyState
					icon={ScrollText}
					title="No audit entries yet"
					description="Sign-ins and changes by administrators appear here."
				/>
			{/if}
		</Card>
	{:else}
		<Card>
			<section
				aria-label="Timeline"
				aria-busy={loading}
				class="space-y-6 transition-opacity"
				class:opacity-60={loading}
			>
				{#each days as group}
					<div>
						<h2 class="section-title mb-3">
							<time datetime={group.day}>{dayLabel(group.day)}</time>
						</h2>
						<ol class="relative ml-1.5 border-l border-border">
							{#each group.entries as entry}
								<li class="relative pb-5 pl-6 last:pb-1">
									<span
										class="absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full {markerClass(entry)}"
										aria-hidden="true"
									></span>
									<div class="flex flex-wrap items-center gap-2">
										<time datetime={entry.timestamp.slice(0, 19)} class="mono text-content-muted"
											>{entry.timestamp.slice(11, 19)}</time
										>
										<span class="font-medium text-content">{actionLabel(entry.action)}</span>
										{#if !entry.success}
											<Badge variant="error">Failed</Badge>
										{/if}
										{#if entry.severity !== 'info'}
											<Badge variant={SEVERITY[entry.severity]?.variant ?? 'neutral'}
												>{SEVERITY[entry.severity]?.label ?? entry.severity}</Badge
											>
										{/if}
									</div>
									{#if entry.details}
										<p class="mt-1 text-sm text-content">{entry.details}</p>
									{/if}
									<dl class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
										{#if entry.username}
											<div class="flex gap-1">
												<dt class="text-content-muted">User</dt>
												<dd class="text-content-secondary">{entry.username}</dd>
											</div>
										{/if}
										{#if entry.ip_address}
											<div class="flex gap-1">
												<dt class="text-content-muted">Address</dt>
												<dd class="text-content-secondary">{entry.ip_address}</dd>
											</div>
										{/if}
										{#if resourceOf(entry)}
											<div class="flex gap-1">
												<dt class="text-content-muted">Resource</dt>
												<dd class="text-content-secondary">{resourceOf(entry)}</dd>
											</div>
										{/if}
										{#if entry.user_agent}
											<div class="flex min-w-0 gap-1">
												<dt class="text-content-muted">Browser</dt>
												<dd class="break-all text-content-secondary">{entry.user_agent}</dd>
											</div>
										{/if}
									</dl>
								</li>
							{/each}
						</ol>
					</div>
				{/each}
			</section>
			{#if entries.length < total}
				<div class="mt-4 flex justify-center">
					<Button variant="secondary" loading={loading} on:click={showMore}>Show more</Button>
				</div>
			{/if}
		</Card>
	{/if}
</div>
