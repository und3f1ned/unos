<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { onDestroy, onMount, tick } from 'svelte';
	import {
		createFirewallRule,
		deleteFirewallRule,
		disableFirewall,
		enableFirewall,
		getFirewallStatus,
		keepFirewallChange,
		resetFirewall,
		type FirewallRule,
		type FirewallStatus
	} from '$lib/api/security';
	import { Alert, Badge, Button, Card, CardAction, Skeleton, confirm, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { Flame, ShieldCheck, ShieldX } from 'lucide-svelte';
	import FirewallAddRuleDialog from './FirewallAddRuleDialog.svelte';
	import FirewallDetails from './FirewallDetails.svelte';
		import { emptyRule, type NewRule } from './firewall';

	// System > Security, administrators only: the host's ufw (ADR-0416). Turning it on adds UNOS's
	// rules for the local networks first, and a change made while it is on is undone by the host
	// after revert_seconds unless the page keeps it: the card counts down and offers Keep, and
	// reads the firewall again once the time has run out.

	// The local networks of SMB's hosts allow (ADR-0095) that UNOS's rules let in, as the server
	// writes them (LOCAL_NETWORKS of supervisor/src/security/firewall.py, which
	// supervisor/tests/test_firewall.py compares with this line).
	// i18n-ignore: network addresses, the same in every language
	const LOCAL_NETWORKS = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16', 'fc00::/7', 'fe80::/10'];
	// Seconds the host's revert may take after its time before the card reads the firewall again,
	// and how often it reads again while the host still undoes the change.
	const REVERT_GRACE_MS = 5000;
	const REVERT_CHECKS = 3;

	let status = $state<FirewallStatus | null>(null);
	let loading = $state(false);
	let loadError = $state('');
	let busy = $state(false);
	let error = $state('');
	let notice = $state('');
	let showDetails = $state(false);
	let enabling = $state(false);
	let showAdd = $state(false);
	let addError = $state('');
	let newRule = $state<NewRule>(emptyRule());

	// The countdown of a change that waits to be kept: when the host undoes it, in the page's clock.
	let deadline = $state<number | null>(null);
	let now = $state(Date.now());
	let ticker: ReturnType<typeof setInterval> | null = null;
	let check: ReturnType<typeof setTimeout> | null = null;
	let checksLeft = 0;
	let keepButton = $state<HTMLButtonElement | null>(null);

	const revertSeconds = $derived(status?.revert_seconds ?? 120);
	const secondsLeft = $derived(deadline === null ? null : Math.max(0, Math.ceil((deadline - now) / 1000)));

	onMount(() => {
		void load();
	});

	onDestroy(() => {
		if (ticker) clearInterval(ticker);
		if (check) clearTimeout(check);
	});

	async function load() {
		loading = true;
		try {
			status = await getFirewallStatus();
			loadError = '';
			follow(status.revert_in);
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	// A change that the host still counts down: a running countdown keeps its own deadline, and one
	// found after a reload starts one.
	function follow(revertIn: number | null) {
		if (revertIn === null) {
			if (!check) deadline = null;
		} else if (deadline === null) {
			deadline = Date.now() + revertIn * 1000;
		}
		syncTicker();
	}

	function startCountdown(revertIn: number) {
		notice = '';
		deadline = Date.now() + revertIn * 1000;
		syncTicker();
	}

	// After a change that waits to be kept, the focus goes to Keep, once the button is enabled again
	// and the dialog that asked has closed.
	async function focusKeep() {
		if (deadline === null) return;
		await tick();
		keepButton?.focus();
	}

	function syncTicker() {
		now = Date.now();
		if (deadline !== null && !ticker) {
			// polling-ignore: a countdown; when the time to keep a change has run out, the firewall is read
			// once to see what the host's revert left, and at most twice more while the host still undoes
			// it (ADR-0365).
			ticker = setInterval(onTick, 1000);
		} else if (deadline === null && ticker) {
			clearInterval(ticker);
			ticker = null;
		}
	}

	function onTick() {
		now = Date.now();
		if (deadline !== null && deadline <= now && !check) {
			checksLeft = REVERT_CHECKS;
			check = setTimeout(checkRevert, REVERT_GRACE_MS);
		}
	}

	async function checkRevert() {
		check = null;
		deadline = null;
		syncTicker();
		try {
			status = await getFirewallStatus();
		} catch (e) {
			error = messageOf(e);
			return;
		}
		checksLeft -= 1;
		if (status.reverting && checksLeft > 0) {
			// The host still undoes it: one more look a few seconds later.
			notice = $t('firewall.reverting');
			check = setTimeout(checkRevert, REVERT_GRACE_MS);
		} else if (status.revert_failed) error = $t('firewall.revertFailed');
		else if (status.revert_in === null && !status.reverting) notice = $t('firewall.undone');
		else if (status.revert_in !== null && status.revert_in > 0) startCountdown(status.revert_in);
		else error = $t('firewall.notYet');
	}

	function stopCountdown() {
		deadline = null;
		if (check) clearTimeout(check);
		check = null;
		syncTicker();
	}

	/** Run a change; then read the firewall again. */
	async function act(change: () => Promise<void>, failed: string) {
		busy = true;
		error = '';
		notice = '';
		try {
			await change();
		} catch (e) {
			error = messageOf(e) || $t(failed);
		} finally {
			busy = false;
		}
		await load();
	}

	/**
	 * Turn the firewall on once confirmed (ADR-0416): what UNOS lets in first, that the host undoes
	 * the change unless it is kept, and a warning for a browser outside the local networks.
	 */
	async function turnOn() {
		const yes = await confirm({
			title: $t('firewall.enableTitle'),
			message: $t('firewall.enableRules', { networks: LOCAL_NETWORKS.join(', ') }),
			// This browser reaches the NAS from outside the local networks: a warning to weigh first.
			warning: status?.client_local === false ? $t('firewall.notLocal') : undefined,
			consequences: [$t('firewall.enableRevert', { n: revertSeconds }), $t('firewall.enableOthers'), $t('firewall.docker')],
			confirmLabel: $t('firewall.turnOn'),
			tone: 'warning'
		});
		if (!yes) return;
		enabling = true;
		await act(async () => {
			const result = await enableFirewall();
			toast.success(result.changed ? $t('firewall.enabledNotice') : $t('firewall.alreadyOn'));
			if (result.revert_in !== null) startCountdown(result.revert_in);
		}, 'firewall.toggleFailed');
		enabling = false;
		await focusKeep();
	}

	async function turnOff() {
		await act(async () => {
			await disableFirewall();
			toast.success($t('firewall.disabledNotice'));
		}, 'firewall.toggleFailed');
	}

	async function keep() {
		await act(async () => {
			await keepFirewallChange();
			stopCountdown();
			toast.success($t('firewall.kept'));
		}, 'firewall.keepFailed');
	}

	async function addRule() {
		if (!newRule.port.trim()) {
			addError = $t('firewall.portRequired');
			return;
		}
		busy = true;
		addError = '';
		error = '';
		notice = '';
		try {
			const result = await createFirewallRule({
				action: newRule.action,
				protocol: newRule.protocol,
				port: newRule.port.trim(),
				source: newRule.source.trim() || null,
				comment: newRule.comment.trim() || null
			});
			showAdd = false;
			newRule = emptyRule();
			toast.success($t('firewall.added'));
			if (result.revert_in !== null) startCountdown(result.revert_in);
		} catch (e) {
			addError = messageOf(e) || $t('firewall.addFailed');
		} finally {
			busy = false;
		}
		await load();
		await focusKeep();
	}

	async function deleteRule(rule: FirewallRule) {
		const yes = await confirm({ title: $t('firewall.confirmDelete'), confirmLabel: $t('firewall.deleteAction') });
		if (!yes) return;
		await act(async () => {
			const result = await deleteFirewallRule(rule.id);
			toast.success($t('firewall.deleted'));
			if (result.revert_in !== null) startCountdown(result.revert_in);
		}, 'firewall.deleteFailed');
		await focusKeep();
	}

	async function reset() {
		const yes = await confirm({
			title: $t('firewall.resetTitle'),
			message: $t('firewall.confirmReset'),
			confirmLabel: $t('firewall.resetAction')
		});
		if (!yes) return;
		await act(async () => {
			await resetFirewall();
			stopCountdown();
			toast.success($t('firewall.resetDone'));
		}, 'firewall.resetFailed');
	}

	function openAdd() {
		addError = '';
		showAdd = true;
	}
</script>

<Card>
	<div class="flex flex-wrap items-center justify-between gap-2 mb-4">
		<h3 class="card-title min-w-0">
			<Flame class="w-5 h-5 shrink-0" />
			{$t('firewall.title')}
		</h3>
		<div class="flex flex-wrap items-center gap-2">
			{#if status && !status.installed}
				<Badge variant="neutral">{$t('firewall.notInstalled')}</Badge>
			{:else if status?.enabled}
				<Badge variant="success"><ShieldCheck class="w-4 h-4 inline" /> {$t('firewall.active')}</Badge>
			{:else if status}
				<Badge variant="warning"><ShieldX class="w-4 h-4 inline" /> {$t('firewall.inactive')}</Badge>
			{:else if !loadError}
				<!-- The badge's place until the status comes (ADR-0430). -->
				<span aria-busy="true"><Skeleton shape="block" class="h-6 w-24 rounded-full" /></span>
			{/if}
			<!-- A card's action opens it here: a small secondary button, no primary (ADR-0432). -->
			<CardAction aria-expanded={showDetails} onclick={() => (showDetails = !showDetails)}>{$t('firewall.configure')}</CardAction>
		</div>
	</div>

	{#if notice}
		<Alert variant="info" class="mb-4">{notice}</Alert>
	{/if}
	{#if error}
		<Alert variant="error" class="mb-4">{error}</Alert>
	{/if}
	{#if enabling}
		<Alert variant="info" class="mb-4">{$t('firewall.working')}</Alert>
	{/if}
	{#if status?.revert_failed && error !== $t('firewall.revertFailed')}
		<Alert variant="error" class="mb-4">{$t('firewall.revertFailed')}</Alert>
	{/if}
	{#if loadError}
		<Alert variant="error" title={$t('firewall.loadFailed')} class="mb-4">
			<p class="break-words">{loadError}</p>
			<Button variant="secondary" size="sm" class="mt-3" {loading} onclick={load}>{$t('common.retry')}</Button>
		</Alert>
	{/if}

	{#if secondsLeft !== null}
		<Alert variant="warning" class="mb-4">
			<p id="firewall-countdown">{$t('firewall.waiting', { n: secondsLeft })}</p>
			<div class="mt-3">
				<Button
					variant="primary"
					size="sm"
					disabled={busy}
					aria-describedby="firewall-countdown"
					bind:el={keepButton}
					onclick={keep}>{$t('firewall.keep')}</Button
				>
			</div>
		</Alert>
	{/if}

	<p class="text-sm text-content-secondary">{$t('firewall.description')}</p>

	{#if status && !status.installed}
		<Alert variant="info" class="mt-4">{$t('firewall.missing')}</Alert>
	{/if}

	{#if showDetails && status?.installed}
		<FirewallDetails
			{status}
			{busy}
			onenable={turnOn}
			ondisable={turnOff}
			onreset={reset}
			onadd={openAdd}
			ondeleterule={deleteRule}
		/>
	{/if}
</Card>

<FirewallAddRuleDialog
	open={showAdd}
	bind:rule={newRule}
	{revertSeconds}
	{busy}
	error={addError}
	onadd={addRule}
	onclose={() => (showAdd = false)}
/>
