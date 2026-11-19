<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { Download, ScrollText, X } from 'lucide-svelte';
	import {
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
	import { intlLocale, t } from '$lib/i18n';
	import { apiTime, formatTimeOfDay, localDay } from '$lib/time';

	// The audit log as a timeline, newest first (ADR-0401). The server answers each time in UTC
	// with its offset: the page shows it in the browser's time zone, groups the entries by the
	// browser's days, and From and To are the browser's days, sent as their first and last instant.

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
		start_date: dayBound(startDate, false),
		end_date: dayBound(endDate, true)
	};
	$: filtered = Object.values(filters).some(Boolean);
	// A change of the filters shows the newest entries that match, from the first page.
	$: if (mounted) schedule(filters);

	// The server's values, shown by the keys of their texts in the catalog (ADR-0287); a value
	// without a key shows as it comes, such as the action of an older entry that AuditAction no
	// longer has (ADR-0359). The actions of AuditAction (supervisor/src/audit/schemas.py),
	// whose English names are the words the search finds (ADR-0298,
	// supervisor/tests/test_audit_log.py), the severities and the types of resource it records.
	const ACTION_NAMES: Record<string, string> = {
		login_success: 'audit.action.loginSuccess',
		login_failed: 'audit.action.loginFailed',
		logout: 'audit.action.logout',
		password_change: 'audit.action.passwordChange',
		totp_enabled: 'audit.action.totpEnabled',
		totp_disabled: 'audit.action.totpDisabled',
		dashboard_layout_changed: 'audit.action.dashboardLayoutChanged',
		theme_changed: 'audit.action.themeChanged',
		security_key_added: 'audit.action.securityKeyAdded',
		security_key_removed: 'audit.action.securityKeyRemoved',
		user_created: 'audit.action.userCreated',
		user_updated: 'audit.action.userUpdated',
		user_deleted: 'audit.action.userDeleted',
		pool_created: 'audit.action.poolCreated',
		pool_deleted: 'audit.action.poolDeleted',
		pool_mounted: 'audit.action.poolMounted',
		pool_unmounted: 'audit.action.poolUnmounted',
		pool_imported: 'audit.action.poolImported',
		volume_created: 'audit.action.volumeCreated',
		volume_deleted: 'audit.action.volumeDeleted',
		snapshot_created: 'audit.action.snapshotCreated',
		snapshot_deleted: 'audit.action.snapshotDeleted',
		snapshot_restored: 'audit.action.snapshotRestored',
		balance_started: 'audit.action.balanceStarted',
		balance_paused: 'audit.action.balancePaused',
		balance_resumed: 'audit.action.balanceResumed',
		balance_cancelled: 'audit.action.balanceCancelled',
		pool_disk_added: 'audit.action.poolDiskAdded',
		pool_disk_removal_started: 'audit.action.poolDiskRemovalStarted',
		share_created: 'audit.action.shareCreated',
		share_updated: 'audit.action.shareUpdated',
		share_deleted: 'audit.action.shareDeleted',
		share_permissions_repaired: 'audit.action.sharePermissionsRepaired',
		group_created: 'audit.action.groupCreated',
		group_updated: 'audit.action.groupUpdated',
		group_deleted: 'audit.action.groupDeleted',
		smb_account_updated: 'audit.action.smbAccountUpdated',
		smb_shares_imported: 'audit.action.smbSharesImported',
		smb_reconciled: 'audit.action.smbReconciled',
		smb_restored: 'audit.action.smbRestored',
		smb_interfaces_changed: 'audit.action.smbInterfacesChanged',
		smb_restarted: 'audit.action.smbRestarted',
		nfs_export_created: 'audit.action.nfsExportCreated',
		nfs_export_deleted: 'audit.action.nfsExportDeleted',
		container_started: 'audit.action.containerStarted',
		container_stopped: 'audit.action.containerStopped',
		container_removed: 'audit.action.containerRemoved',
		container_created: 'audit.action.containerCreated',
		image_pulled: 'audit.action.imagePulled',
		image_removed: 'audit.action.imageRemoved',
		app_installed: 'audit.action.appInstalled',
		app_uninstalled: 'audit.action.appUninstalled',
		app_limits_changed: 'audit.action.appLimitsChanged',
		app_updated: 'audit.action.appUpdated',
		app_rolled_back: 'audit.action.appRolledBack',
		app_earlier_version_removed: 'audit.action.appEarlierVersionRemoved',
		backup_created: 'audit.action.backupCreated',
		backup_restored: 'audit.action.backupRestored',
		backup_deleted: 'audit.action.backupDeleted',
		system_reboot: 'audit.action.systemReboot',
		system_shutdown: 'audit.action.systemShutdown',
		system_suspend: 'audit.action.systemSuspend',
		settings_changed: 'audit.action.settingsChanged',
		task_created: 'audit.action.taskCreated',
		task_updated: 'audit.action.taskUpdated',
		task_deleted: 'audit.action.taskDeleted',
		task_run: 'audit.action.taskRun',
		network_config_changed: 'audit.action.networkConfigChanged',
		firewall_enabled: 'audit.action.firewallEnabled',
		firewall_disabled: 'audit.action.firewallDisabled',
		firewall_rule_added: 'audit.action.firewallRuleAdded',
		firewall_rule_deleted: 'audit.action.firewallRuleDeleted',
		firewall_reset: 'audit.action.firewallReset',
		login_history_cleared: 'audit.action.loginHistoryCleared',
		certificate_uploaded: 'audit.action.certificateUploaded',
		certificate_generated: 'audit.action.certificateGenerated',
		certificate_deleted: 'audit.action.certificateDeleted',
		certificate_served: 'audit.action.certificateServed',
		firewall_change_kept: 'audit.action.firewallChangeKept',
		remote_access_enabled: 'audit.action.remoteAccessEnabled',
		remote_access_disabled: 'audit.action.remoteAccessDisabled',
		remote_access_changed: 'audit.action.remoteAccessChanged',
		remote_access_peer_added: 'audit.action.remoteAccessPeerAdded',
		remote_access_peer_revoked: 'audit.action.remoteAccessPeerRevoked',
		app_names_changed: 'audit.action.appNamesChanged',
		other: 'audit.action.other'
	};
	// An info entry shows no severity.
	const SEVERITIES: Record<Exclude<AuditSeverity, 'info'>, { key: string; variant: 'warning' | 'error' }> = {
		warning: { key: 'audit.severity.warning', variant: 'warning' },
		critical: { key: 'audit.severity.critical', variant: 'error' }
	};
	const RESOURCE_TYPES: Record<string, string> = {
		user: 'audit.resource.user',
		pool: 'audit.resource.pool',
		disk: 'audit.resource.disk',
		volume: 'audit.resource.volume',
		snapshot: 'audit.resource.snapshot',
		share: 'audit.resource.share',
		smb: 'audit.resource.smb',
		group: 'audit.resource.group',
		smb_account: 'audit.resource.smbAccount',
		nfs_export: 'audit.resource.nfsExport',
		api_token: 'audit.resource.apiToken',
		recovery_codes: 'audit.resource.recoveryCodes',
		security_key: 'audit.resource.securityKey',
		container: 'audit.resource.container',
		app: 'audit.resource.app',
		dashboard_layout: 'audit.resource.dashboardLayout',
		theme: 'audit.resource.theme',
		audit_log: 'audit.resource.auditLog',
		backup: 'audit.resource.backup',
		host: 'audit.resource.host',
		network_interface: 'audit.resource.networkInterface',
		task: 'audit.resource.task',
		two_factor_requirement: 'audit.resource.twoFactorRequirement',
		firewall: 'audit.resource.firewall',
		firewall_rule: 'audit.resource.firewallRule',
		login_history: 'audit.resource.loginHistory',
		image: 'audit.resource.image',
		power: 'audit.resource.power',
		ups: 'audit.resource.ups',
		notification_channel: 'audit.resource.notificationChannel',
		offsite: 'audit.resource.offsite',
		certificate: 'audit.resource.certificate',
		remote_access: 'audit.resource.remoteAccess',
		remote_peer: 'audit.resource.remotePeer',
		app_name: 'audit.resource.appName'
	};

	/** The text of `value` by its key in `keys`, in the language of the page; else `value`. */
	$: textOf = (keys: Record<string, string>, value: string): string =>
		Object.prototype.hasOwnProperty.call(keys, value) ? $t(keys[value]) : value;

	$: actionOptions = [
		{ value: '', label: $t('audit.filter.allActions') },
		...actions
			.map((a) => ({ value: a.value, label: textOf(ACTION_NAMES, a.value) }))
			.sort((a, b) => a.label.localeCompare(b.label, $intlLocale))
	];
	$: days = byDay(entries);
	$: countText =
		entries.length < total
			? $t('audit.showing', { shown: entries.length, n: total })
			: $t('audit.count', { n: total });

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
			actionsError = messageOf(e);
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

	/**
	 * The bound of the browser's day `day` (YYYY-MM-DD, as a date field holds it) for the server:
	 * its first instant, or (`last`) its last millisecond, which the server takes as the whole
	 * millisecond. In UTC with its offset; '' for no day.
	 */
	function dayBound(day: string, last: boolean): string {
		const [year, month, date] = day.split('-').map(Number);
		if (!year || !month || !date) return '';
		const moment = last ? new Date(year, month - 1, date + 1).getTime() - 1 : new Date(year, month - 1, date).getTime();
		return Number.isNaN(moment) ? '' : new Date(moment).toISOString();
	}

	interface Day {
		day: string;
		entries: AuditEntry[];
	}

	/** The entries by the browser's day of their time, in their order (newest first). */
	function byDay(list: AuditEntry[]): Day[] {
		const groups: Day[] = [];
		for (const entry of list) {
			const day = localDay(entry.timestamp) ?? entry.timestamp;
			const last = groups[groups.length - 1];
			if (last && last.day === day) last.entries.push(entry);
			else groups.push({ day, entries: [entry] });
		}
		return groups;
	}

	// In the language of the page; reactive, so that the timeline follows a change of the language.
	$: dayLabel = (day: string): string => {
		const [year, month, date] = day.split('-').map(Number);
		const moment = new Date(year, month - 1, date);
		if (Number.isNaN(moment.getTime())) return day;
		return moment.toLocaleDateString($intlLocale, {
			weekday: 'long',
			year: 'numeric',
			month: 'long',
			day: 'numeric'
		});
	};

	function markerClass(entry: AuditEntry): string {
		if (!entry.success || entry.severity === 'critical') return 'bg-status-error';
		if (entry.severity === 'warning') return 'bg-status-warning';
		return 'bg-status-info';
	}

	$: resourceOf = (entry: AuditEntry): string =>
		[entry.resource_type && textOf(RESOURCE_TYPES, entry.resource_type), entry.resource_id]
			.filter(Boolean)
			.join(' ');
</script>

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">{$t('audit.title')}</h1>
		<div class="flex flex-wrap items-center gap-2">
			<Button
				variant="secondary"
				icon={Download}
				loading={exporting === 'csv'}
				disabled={exporting !== null}
				on:click={() => exportAs('csv')}>{$t('audit.exportCsv')}</Button
			>
			<Button
				variant="secondary"
				icon={Download}
				loading={exporting === 'json'}
				disabled={exporting !== null}
				on:click={() => exportAs('json')}>{$t('audit.exportJson')}</Button
			>
		</div>
	</div>

	{#if exportError}
		<Alert
			variant="error"
			title={$t('audit.exportError')}
			dismissible
			on:dismiss={() => (exportError = '')}>{exportError}</Alert
		>
	{/if}

	<Card>
		<div role="search" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
			<Input
				id="audit-search"
				type="search"
				label={$t('audit.filter.search')}
				bind:value={search}
				placeholder={$t('audit.filter.searchPlaceholder')}
				maxlength={SEARCH_MAX_CHARS}
				class="sm:col-span-2"
			/>
			<Input
				id="audit-user"
				label={$t('audit.filter.user')}
				bind:value={username}
				placeholder={$t('audit.filter.userPlaceholder')}
				autocomplete="off"
			/>
			<Select
				id="audit-action"
				label={$t('audit.filter.action')}
				bind:value={action}
				options={actionOptions}
				error={actionsError && $t('audit.filter.actionsError', { message: actionsError })}
			/>
			<Input id="audit-from" type="date" label={$t('audit.filter.from')} bind:value={startDate} />
			<Input id="audit-to" type="date" label={$t('audit.filter.to')} bind:value={endDate} />
		</div>
		<div class="mt-3 flex min-h-8 items-center justify-between gap-2">
			<p class="text-sm text-content-secondary" role="status">{loaded && !error ? countText : ''}</p>
			{#if filtered}
				<Button variant="ghost" size="sm" icon={X} on:click={clearFilters}>{$t('audit.filter.clear')}</Button>
			{/if}
		</div>
	</Card>

	{#if error}
		<Alert variant="error" title={$t('audit.loadError')}>
			<p>{error}</p>
			<Button variant="secondary" size="sm" class="mt-3" loading={loading} on:click={retry}
				>{$t('common.retry')}</Button
			>
		</Alert>
	{:else if !loaded}
		<p class="loading-text">{$t('common.loading')}</p>
	{:else if entries.length === 0}
		<Card>
			{#if filtered}
				<EmptyState
					icon={ScrollText}
					title={$t('audit.noMatches')}
					description={filters.search ? $t('audit.noSearchMatchesHint') : $t('audit.noMatchesHint')}
				/>
			{:else}
				<EmptyState icon={ScrollText} title={$t('audit.empty')} description={$t('audit.emptyHint')} />
			{/if}
		</Card>
	{:else}
		<Card>
			<section
				aria-label={$t('audit.timeline')}
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
										<time datetime={apiTime(entry.timestamp)?.toISOString() ?? entry.timestamp} class="mono text-content-muted"
											>{formatTimeOfDay(entry.timestamp, $intlLocale)}</time
										>
										<span class="font-medium text-content">{textOf(ACTION_NAMES, entry.action)}</span>
										{#if !entry.success}
											<Badge variant="error">{$t('audit.failed')}</Badge>
										{/if}
										{#if entry.severity !== 'info'}
											{@const severity = SEVERITIES[entry.severity]}
											<Badge variant={severity?.variant ?? 'neutral'}
												>{severity ? $t(severity.key) : entry.severity}</Badge
											>
										{/if}
										{#if entry.chain === 'broken'}
											<Badge variant="error">{$t('audit.chain.broken')}</Badge>
										{/if}
									</div>
									{#if entry.chain === 'broken'}
										<p class="mt-1 text-sm text-status-error">{$t('audit.chain.brokenHint')}</p>
									{/if}
									<!-- A token's name of 64 characters, a name tried at sign-in of 128 and an
									     IPv6 address break within the entry on a phone (ADR-0302). -->
									{#if entry.details}
										<p class="mt-1 break-words text-sm text-content">{entry.details}</p>
									{/if}
									<dl class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
										{#if entry.username}
											<div class="flex gap-1">
												<dt class="text-content-muted">{$t('audit.field.user')}</dt>
												<dd class="break-all text-content-secondary">{entry.username}</dd>
											</div>
										{/if}
										{#if entry.ip_address}
											<div class="flex gap-1">
												<dt class="text-content-muted">{$t('audit.field.address')}</dt>
												<dd class="break-all text-content-secondary">{entry.ip_address}</dd>
											</div>
										{/if}
										{#if resourceOf(entry)}
											<div class="flex min-w-0 gap-1">
												<dt class="text-content-muted">{$t('audit.field.resource')}</dt>
												<dd class="break-all text-content-secondary">{resourceOf(entry)}</dd>
											</div>
										{/if}
										{#if entry.user_agent}
											<div class="flex min-w-0 gap-1">
												<dt class="text-content-muted">{$t('audit.field.browser')}</dt>
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
					<Button variant="secondary" loading={loading} on:click={showMore}>{$t('audit.showMore')}</Button>
				</div>
			{/if}
		</Card>
	{/if}
</div>
