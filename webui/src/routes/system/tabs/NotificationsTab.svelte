<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { onMount } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { Plus, RefreshCw } from 'lucide-svelte';
	import { Alert, Button, Tabs, confirm, toast } from '$lib/components/ui';
	import {
		ChannelDialog,
		ChannelForm,
		ChannelsView,
		HistoryFilter,
		HistoryView
	} from '$lib/components/system/notifications';
	import { kindName, nameOf } from '$lib/components/system/notifications/channels';
	import { t } from '$lib/i18n';
	import {
		createConfig,
		deleteConfig,
		getConfigs,
		getLogs,
		testNotification,
		updateConfig,
		type ChannelKind,
		type NotificationConfig,
		type NotificationLog
	} from '$lib/api/notifications';

	// Channels of five kinds (ADR-0326), each of which receives every alert. A test that the server
	// delivered says so in a toast, one that it did not says why next to its channel, and the history lists every
	// notification sent, newest first. A channel's events are not offered: the server sends every
	// alert to every enabled channel whatever they name.

	type View = 'channels' | 'history';

	let logs = $state<NotificationLog[]>([]);
	let configs = $state<NotificationConfig[]>([]);
	// The channels whose on/off the server has not answered yet: each shows its new state, and takes
	// no second change until the answer.
	const toggling = new SvelteSet<string>();
	let loading = $state(true);
	let loadError = $state('');
	let activeView = $state<View>('channels');

	// Why each channel's last test was not sent, by channel id: shown under the channel.
	let results = $state<Record<string, string>>({});
	let testing = $state<string | null>(null);
	let actionError = $state('');

	// The dialog that adds or edits a channel.
	const form = new ChannelForm();

	const filter = new HistoryFilter();

	onMount(() => loadData());

	async function loadData() {
		loading = true;
		try {
			[logs, configs] = await Promise.all([getLogs(), getConfigs()]);
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	async function refreshLogs() {
		try {
			logs = await getLogs();
		} catch (e) {
			loadError = messageOf(e);
		}
	}

	/** The name a new channel of `kind` gets until it is given one. */
	function defaultName(kind: ChannelKind): string {
		return $t('notifications.form.defaultName', { kind: kindName($t, kind) });
	}

	function openCreate() {
		form.openCreate('email', defaultName('email'));
	}

	async function handleSave() {
		if (form.saving || !form.complete) return;
		form.saving = true;
		form.saveError = '';
		try {
			const editing = form.editing;
			if (editing?.id) {
				await updateConfig(editing.id, form.config());
				// A test of the old settings says nothing about the new ones.
				dismissResult(editing.id);
			} else {
				await createConfig(form.config());
			}
			form.saving = false;
			form.close();
			await loadData();
		} catch (e) {
			form.saveError = messageOf(e);
		} finally {
			form.saving = false;
		}
	}

	async function handleTest(config: NotificationConfig) {
		const id = config.id;
		if (!id || testing) return;
		testing = id;
		dismissResult(id);
		try {
			await testNotification(id);
			toast.success($t('notifications.test.sentTo', { name: nameOf($t, config) }));
		} catch (e) {
			results = { ...results, [id]: messageOf(e) };
		} finally {
			testing = null;
		}
		// The test is the newest entry of the history now.
		await refreshLogs();
	}

	function dismissResult(id: string) {
		const others = { ...results };
		delete others[id];
		results = others;
	}

	async function toggleEnabled(config: NotificationConfig) {
		const id = config.id;
		if (!id || toggling.has(id)) return;
		// The new state at once; the stored one comes back with the reload if the server refused it.
		const stored = { ...config };
		config.enabled = !stored.enabled;
		toggling.add(id);
		try {
			await updateConfig(id, { ...stored, enabled: !stored.enabled });
			actionError = '';
		} catch (e) {
			config.enabled = stored.enabled;
			actionError = messageOf(e);
		} finally {
			toggling.delete(id);
		}
		await loadData();
	}

	// Delete asks first; why the server refused it is a toast.
	async function deleteChannel(config: NotificationConfig) {
		const id = config.id;
		if (!id) return;
		const name = nameOf($t, config);
		const yes = await confirm({
			title: $t('notifications.delete.title', { name }),
			message: $t('notifications.delete.text', { name }),
			confirmLabel: $t('notifications.delete.confirm')
		});
		if (!yes) return;
		try {
			await deleteConfig(id);
		} catch (e) {
			toast.error(messageOf(e), { title: $t('notifications.delete.error') });
			return;
		}
		dismissResult(id);
		await loadData();
	}
</script>

<!-- A tab of System's Settings: the channels the NAS notifies through, and what it sent (ADR-0431). -->
<div class="space-y-6">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div class="min-w-0">
			<h2 class="text-xl font-semibold">{$t('system.tabs.notifications')}</h2>
			<p class="page-subtitle">{$t('notifications.subtitle')}</p>
		</div>
		<Button variant="primary" icon={Plus} onclick={openCreate}>{$t('notifications.addChannel')}</Button>
	</div>

	<div class="flex items-center gap-2">
		<Tabs
			tabs={[
				{ id: 'channels', label: $t('notifications.views.channels') },
				{ id: 'history', label: $t('notifications.views.history') }
			]}
			active={activeView}
			label={$t('system.tabs.notifications')}
			tabId={(id) => `notifications-tab-${id}`}
			panelId={(id) => `notifications-panel-${id}`}
			onselect={(id) => (activeView = id)}
			class="flex-1 min-w-0"
		/>
		<Button variant="ghost" size="icon-sm" icon={RefreshCw} {loading} title={$t('common.refresh')} onclick={loadData}>
			<span class="sr-only">{$t('common.refresh')}</span>
		</Button>
	</div>

	{#if loadError}
		<div role="alert">
			<Alert variant="error" title={$t('notifications.loadError')}>{loadError}</Alert>
		</div>
	{/if}
	{#if actionError}
		<div role="alert">
			<Alert variant="error" dismissible ondismiss={() => (actionError = '')}>{actionError}</Alert>
		</div>
	{/if}

	<div role="tabpanel" id="notifications-panel-{activeView}" aria-labelledby="notifications-tab-{activeView}">
		{#if activeView === 'channels'}
			<ChannelsView
				{configs}
				{loading}
				{results}
				{testing}
				{toggling}
				ontest={handleTest}
				ontoggle={toggleEnabled}
				onedit={(config) => form.openEdit(config)}
				ondelete={deleteChannel}
				ondismissresult={dismissResult}
			/>
		{:else}
			<HistoryView {logs} {loading} {filter} />
		{/if}
	</div>
</div>

<ChannelDialog {form} {defaultName} onsave={handleSave} />
