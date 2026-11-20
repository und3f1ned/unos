<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { onDestroy, onMount, untrack } from 'svelte';
	import { Download, ScrollText } from 'lucide-svelte';
	import { exportAuditLog, type AuditExportFormat, type AuditFilters as Filters } from '$lib/api/audit';
	import { getUsers } from '$lib/api/auth';
	import { Alert, Button, Card, EmptyState, Skeleton } from '$lib/components/ui';
	import { AuditFilters, AuditLog, AuditTimeline } from '$lib/components/audit';
	import { dayBound, textOf, today } from '$lib/components/audit/timeline';
	import { intlLocale, t } from '$lib/i18n';

	// The audit log as a timeline, newest first (ADR-0401). The server answers each time in UTC
	// with its offset: the page shows it in the browser's time zone, groups the entries by the
	// browser's days, and From and To are the browser's days, sent as their first and last instant.

	let search = $state('');
	let username = $state('');
	let action = $state('');
	let startDate = $state('');
	let endDate = $state('');

	const log = new AuditLog();
	let exporting = $state<AuditExportFormat | null>(null);
	let exportError = $state('');

	let mounted = $state(false);

	const filters: Filters = $derived({
		search: search.trim(),
		username: username.trim(),
		action,
		start_date: dayBound(startDate, false),
		end_date: dayBound(endDate, true)
	});
	const filtered = $derived(Object.values(filters).some(Boolean));
	// How many filters are set: From and To count once each, as fields.
	const activeFilters = $derived(Object.values(filters).filter(Boolean).length);
	// The NAS's users, which the User field suggests; a name of a deleted user can still be typed.
	let usernames = $state<string[]>([]);
	// A change of the filters shows the newest entries that match, from the first page; the first
	// load once the page is mounted. Only those make it run again: what schedule reads and writes
	// is its own business.
	$effect(() => {
		if (!mounted) return;
		const current = filters;
		untrack(() => log.schedule(current));
	});

	// The server's values, shown by the keys of their texts in the catalog (ADR-0287); a value
	// without a key shows as it comes, such as the action of an older entry that AuditAction no
	// longer has (ADR-0359). The actions of AuditAction (supervisor/src/audit/schemas.py),
	// whose English names are the words the search finds (ADR-0298), and the types of resource it
	// records: supervisor/tests/test_audit_log.py reads both maps from this file.
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
		iscsi_target_created: 'audit.action.iscsiTargetCreated',
		iscsi_target_changed: 'audit.action.iscsiTargetChanged',
		iscsi_target_deleted: 'audit.action.iscsiTargetDeleted',
		remote_access_enabled: 'audit.action.remoteAccessEnabled',
		remote_access_disabled: 'audit.action.remoteAccessDisabled',
		remote_access_changed: 'audit.action.remoteAccessChanged',
		remote_access_peer_added: 'audit.action.remoteAccessPeerAdded',
		remote_access_peer_revoked: 'audit.action.remoteAccessPeerRevoked',
		app_names_changed: 'audit.action.appNamesChanged',
		network_interface_created: 'audit.action.networkInterfaceCreated',
		network_interface_deleted: 'audit.action.networkInterfaceDeleted',
		network_change_kept: 'audit.action.networkChangeKept',
		other: 'audit.action.other'
	};
	// An info entry shows no severity.
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
		iscsi_target: 'audit.resource.iscsiTarget',
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

	const actionOptions = $derived([
		{ value: '', label: $t('audit.filter.allActions') },
		...log.actions
			.map((a) => ({ value: a.value, label: textOf(ACTION_NAMES, a.value, $t) }))
			.sort((a, b) => a.label.localeCompare(b.label, $intlLocale))
	]);
	// The export writes what the list shows: not while the list could not be read.
	const exportBlocked = $derived(log.error ? $t('audit.exportBlocked') : '');
	const countText = $derived(
		log.entries.length < log.total
			? $t('audit.showing', { shown: log.entries.length, n: log.total })
			: $t('audit.count', { n: log.total })
	);

	onMount(() => {
		mounted = true;
		void log.loadActions();
		// Only suggestions: a failure leaves the field as it was, free text.
		getUsers()
			.then((users) => (usernames = users.map((u) => u.username).sort((a, b) => a.localeCompare(b))))
			.catch(() => {});
	});

	onDestroy(() => log.dispose());

	function clearFilters() {
		search = '';
		username = '';
		action = '';
		startDate = '';
		endDate = '';
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
</script>

<div class="page-container">
	<div class="page-header">
		<h1 class="page-title">{$t('audit.title')}</h1>
		<div class="flex flex-wrap items-center gap-2">
			<Button
				variant="secondary"
				icon={Download}
				loading={exporting === 'csv'}
				disabled={exporting !== null || exportBlocked !== ''}
				title={exportBlocked || undefined}
				onclick={() => exportAs('csv')}>{$t('audit.exportCsv')}</Button
			>
			<Button
				variant="secondary"
				icon={Download}
				loading={exporting === 'json'}
				disabled={exporting !== null || exportBlocked !== ''}
				title={exportBlocked || undefined}
				onclick={() => exportAs('json')}>{$t('audit.exportJson')}</Button
			>
		</div>
	</div>

	{#if exportError}
		<Alert
			variant="error"
			title={$t('audit.exportError')}
			dismissible
			ondismiss={() => (exportError = '')}>{exportError}</Alert
		>
	{/if}

	<AuditFilters
		bind:search
		bind:username
		bind:action
		bind:startDate
		bind:endDate
		{actionOptions}
		actionsError={log.actionsError}
		count={log.loaded && !log.error ? countText : ''}
		{filtered}
		active={activeFilters}
		{usernames}
		onclear={clearFilters}
	/>

	{#if log.error}
		<Alert variant="error" title={$t('audit.loadError')}>
			<p>{log.error}</p>
			<Button variant="secondary" size="sm" class="mt-3" loading={log.loading} onclick={() => log.retry(filters)}
				>{$t('common.retry')}</Button
			>
		</Alert>
	{:else if !log.loaded}
		<!-- The first load (ADR-0430): a day of the timeline, its entries in their places. -->
		<div class="card" aria-busy="true">
			<Skeleton size="eyebrow" class="w-28 py-1.5 mb-2" />
			<div class="ml-1.5 border-l border-border">
				{#each [0, 1, 2, 3, 4] as entry (entry)}
					<div class="pb-5 pl-6 last:pb-1">
						<Skeleton class="w-1/2" />
						<Skeleton class="mt-1 w-3/4" />
					</div>
				{/each}
			</div>
		</div>
	{:else if log.entries.length === 0}
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
		<AuditTimeline
			entries={log.entries}
			loading={log.loading}
			more={log.entries.length < log.total}
			actionNames={ACTION_NAMES}
			resourceTypes={RESOURCE_TYPES}
			onshowmore={() => log.showMore(filters)}
		/>
	{/if}
</div>
