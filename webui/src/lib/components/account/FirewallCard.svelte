<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import {
		createFirewallRule,
		deleteFirewallRule,
		disableFirewall,
		enableFirewall,
		getFirewallStatus,
		keepFirewallChange,
		resetFirewall,
		type FirewallAction,
		type FirewallProtocol,
		type FirewallRule,
		type FirewallStatus
	} from '$lib/api/security';
	import { Alert, Badge, Button, Card, Input, Modal, Select } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { Flame, Plus, RefreshCw, ShieldCheck, ShieldX, Trash2 } from 'lucide-svelte';

	// System > Security, administrators only: the host's ufw (ADR-0416). Turning it on adds UNOS's
	// rules for the local networks first, and a change made while it is on is undone by the host
	// after revert_seconds unless the page keeps it: the card counts down and offers Keep, and
	// reads the firewall again once the time has run out.

	// The local networks of SMB's hosts allow (ADR-0095) that UNOS's rules let in, as the server
	// writes them (LOCAL_NETWORKS of supervisor/src/security/firewall.py).
	// i18n-ignore: network addresses, the same in every language
	const LOCAL_NETWORKS = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16', 'fc00::/7', 'fe80::/10'];
	// Seconds the host's revert may take after its time before the card reads the firewall again,
	// and how often it reads again while the host still undoes the change.
	const REVERT_GRACE_MS = 5000;
	const REVERT_CHECKS = 3;

	let status: FirewallStatus | null = null;
	let loading = false;
	let loadError = '';
	let busy = false;
	let error = '';
	let success = '';
	let notice = '';
	let showDetails = false;
	let enabling = false;
	let showEnable = false;
	let showAdd = false;
	let addError = '';
	let newRule: { action: FirewallAction; protocol: FirewallProtocol; port: string; source: string; comment: string } =
		emptyRule();

	// The countdown of a change that waits to be kept: when the host undoes it, in the page's clock.
	let deadline: number | null = null;
	let now = Date.now();
	let ticker: ReturnType<typeof setInterval> | null = null;
	let check: ReturnType<typeof setTimeout> | null = null;
	let checksLeft = 0;
	let keepButton: HTMLButtonElement | null = null;

	$: revertSeconds = status?.revert_seconds ?? 120;
	$: secondsLeft = deadline === null ? null : Math.max(0, Math.ceil((deadline - now) / 1000));

	// The labels of the values that the server answers as words (FirewallRule and FirewallStatus of
	// $lib/api/security): keys of the catalog, by value.
	const RULE_ACTIONS: Record<string, string> = {
		allow: 'firewall.allow',
		deny: 'firewall.deny',
		reject: 'firewall.reject',
		limit: 'firewall.limit'
	};
	const RULE_DIRECTIONS: Record<string, string> = { in: 'firewall.in', out: 'firewall.out' };
	const RULE_PROTOCOLS: Record<string, string> = {
		tcp: 'firewall.tcp',
		udp: 'firewall.udp',
		any: 'firewall.anyProtocol'
	};
	const POLICIES: Record<string, string> = {
		allow: 'firewall.policyAllow',
		deny: 'firewall.policyDeny',
		reject: 'firewall.policyReject'
	};

	/** The label of `value` in the language of the page; a value without one as it comes. */
	$: labelOf = (labels: Record<string, string>, value: string): string =>
		Object.prototype.hasOwnProperty.call(labels, value) ? $t(labels[value]) : value;

	/** A rule's direction, protocol, port and addresses: "IN | TCP | Port: 22 | From: 192.168.1.0/24". */
	$: ruleLine = (rule: FirewallRule): string => {
		const parts = [
			labelOf(RULE_DIRECTIONS, rule.direction),
			labelOf(RULE_PROTOCOLS, rule.protocol),
			$t('firewall.port', { port: rule.port || $t('firewall.any') })
		];
		if (rule.source) parts.push($t('firewall.from', { source: rule.source }));
		if (rule.destination) parts.push($t('firewall.to', { destination: rule.destination }));
		if (rule.interface) parts.push($t('firewall.on', { interface: rule.interface }));
		// i18n-ignore: a separator
		return parts.join(' | ');
	};

	$: actionOptions = [
		{ value: 'allow', label: $t('firewall.allow') },
		{ value: 'deny', label: $t('firewall.deny') },
		{ value: 'reject', label: $t('firewall.reject') }
	];
	$: protocolOptions = [
		{ value: 'tcp', label: $t('firewall.tcp') },
		{ value: 'udp', label: $t('firewall.udp') },
		{ value: 'any', label: $t('firewall.any') }
	];

	onMount(() => {
		void load();
	});

	onDestroy(() => {
		if (ticker) clearInterval(ticker);
		if (check) clearTimeout(check);
	});

	function emptyRule(): typeof newRule {
		return { action: 'allow', protocol: 'tcp', port: '', source: '', comment: '' };
	}

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

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
		success = '';
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

	async function turnOn() {
		showEnable = false;
		enabling = true;
		await act(async () => {
			const result = await enableFirewall();
			success = result.changed ? $t('firewall.enabledNotice') : $t('firewall.alreadyOn');
			if (result.revert_in !== null) startCountdown(result.revert_in);
		}, 'firewall.toggleFailed');
		enabling = false;
		await focusKeep();
	}

	async function turnOff() {
		await act(async () => {
			await disableFirewall();
			success = $t('firewall.disabledNotice');
		}, 'firewall.toggleFailed');
	}

	async function keep() {
		await act(async () => {
			await keepFirewallChange();
			stopCountdown();
			success = $t('firewall.kept');
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
		success = '';
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
			success = $t('firewall.added');
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
		if (!confirm($t('firewall.confirmDelete'))) return;
		await act(async () => {
			const result = await deleteFirewallRule(rule.id);
			success = $t('firewall.deleted');
			if (result.revert_in !== null) startCountdown(result.revert_in);
		}, 'firewall.deleteFailed');
		await focusKeep();
	}

	async function reset() {
		if (!confirm($t('firewall.confirmReset'))) return;
		await act(async () => {
			await resetFirewall();
			stopCountdown();
			success = $t('firewall.resetDone');
		}, 'firewall.resetFailed');
	}

	function openAdd() {
		addError = '';
		showAdd = true;
	}
</script>

<Card>
	<div class="flex flex-wrap items-center justify-between gap-2 mb-4">
		<h3 class="text-lg font-semibold flex items-center gap-2 min-w-0">
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
			{/if}
			<Button variant="primary" aria-expanded={showDetails} on:click={() => (showDetails = !showDetails)}
				>{$t('firewall.configure')}</Button
			>
		</div>
	</div>

	{#if success}
		<Alert variant="success" class="mb-4">{success}</Alert>
	{/if}
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
			<Button variant="secondary" size="sm" class="mt-3" {loading} on:click={load}>{$t('common.retry')}</Button>
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
					on:click={keep}>{$t('firewall.keep')}</Button
				>
			</div>
		</Alert>
	{/if}

	<p class="text-sm text-content-secondary">{$t('firewall.description')}</p>

	{#if status && !status.installed}
		<Alert variant="info" class="mt-4">{$t('firewall.missing')}</Alert>
	{/if}

	{#if showDetails && status?.installed}
		<div class="mt-4 space-y-4">
			<div class="flex flex-wrap items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
				<div class="min-w-0">
					<p class="font-medium">{$t('firewall.status')}</p>
					<p class="text-sm text-content-secondary">
						{$t('firewall.defaults', {
							incoming: labelOf(POLICIES, status.default_incoming),
							outgoing: labelOf(POLICIES, status.default_outgoing)
						})}
					</p>
				</div>
				<div class="flex flex-wrap gap-2">
					{#if status.enabled}
						<Button variant="danger" disabled={busy} on:click={turnOff}>{$t('firewall.disable')}</Button>
					{:else}
						<Button variant="success" disabled={busy} on:click={() => (showEnable = true)}>{$t('firewall.enable')}</Button>
					{/if}
					<Button variant="secondary" disabled={busy} on:click={reset}>
						<RefreshCw class="w-4 h-4" />
						{$t('firewall.reset')}
					</Button>
				</div>
			</div>

			<p class="text-sm text-content-secondary">{$t('firewall.unosHint')} {$t('firewall.docker')}</p>

			<div class="flex flex-wrap justify-between items-center gap-2">
				<h4 class="font-medium">{$t('firewall.rules')}</h4>
				<Button variant="primary" size="sm" disabled={busy} on:click={openAdd}>
					<Plus class="w-4 h-4" />
					{$t('firewall.addRule')}
				</Button>
			</div>

			{#if status.rules.length === 0}
				<p class="text-sm text-content-secondary text-center py-4">{$t('firewall.noRules')}</p>
			{:else}
				<ul class="space-y-2" aria-label={$t('firewall.rules')}>
					{#each status.rules as rule (rule.id)}
						<li>
							<div class="flex items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
								<div class="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
									<span
										class="px-2 py-1 rounded text-xs font-medium uppercase {rule.action === 'allow'
											? 'bg-status-success/20 text-status-success'
											: rule.action === 'limit'
												? 'bg-status-warning/20 text-status-warning'
												: 'bg-status-error/20 text-status-error'}"
									>
										{labelOf(RULE_ACTIONS, rule.action)}
									</span>
									<div class="min-w-0 break-words">
										<p class="font-medium">
											{rule.comment ?? rule.app ?? $t('firewall.ruleNumber', { n: rule.number })}
										</p>
										<p class="text-sm text-content-secondary">{ruleLine(rule)}</p>
									</div>
									{#if rule.family === 'ipv6'}
										<Badge variant="info">{$t('firewall.ipv6')}</Badge>
									{/if}
									{#if rule.route}
										<Badge variant="neutral">{$t('firewall.route')}</Badge>
									{/if}
									{#if rule.unos}
										<Badge variant="purple">{$t('firewall.unos')}</Badge>
									{/if}
								</div>
								{#if !rule.unos}
									<Button
										variant="ghost"
										size="icon"
										class="text-status-error hover:text-status-error"
										disabled={busy}
										aria-label={$t('firewall.deleteRule', { n: rule.number })}
										on:click={() => deleteRule(rule)}
									>
										<Trash2 class="w-4 h-4" />
									</Button>
								{/if}
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{/if}
</Card>

<Modal open={showEnable} title={$t('firewall.enableTitle')} on:close={() => (showEnable = false)}>
	<div class="space-y-3 text-sm break-words">
		<p>{$t('firewall.enableRules', { networks: LOCAL_NETWORKS.join(', ') })}</p>
		<p>{$t('firewall.enableRevert', { n: revertSeconds })}</p>
		{#if status?.client_local === false}
			<Alert variant="warning">{$t('firewall.notLocal')}</Alert>
		{/if}
		<p>{$t('firewall.enableOthers')}</p>
		<p>{$t('firewall.docker')}</p>
	</div>
	<svelte:fragment slot="footer">
		<Button variant="secondary" on:click={() => (showEnable = false)}>{$t('common.cancel')}</Button>
		<Button variant="primary" disabled={busy} on:click={turnOn}>{$t('firewall.turnOn')}</Button>
	</svelte:fragment>
</Modal>

<Modal open={showAdd} title={$t('firewall.addTitle')} closable={!busy} on:close={() => (showAdd = false)}>
	<form id="firewall-add-rule" class="space-y-4" on:submit|preventDefault={addRule}>
		<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
			<Select label={$t('firewall.action')} options={actionOptions} bind:value={newRule.action} />
			<Select label={$t('firewall.protocol')} options={protocolOptions} bind:value={newRule.protocol} />
		</div>
		<Input
			label={$t('firewall.portLabel')}
			bind:value={newRule.port}
			placeholder={$t('firewall.portPlaceholder')}
		/>
		<Input label={$t('firewall.source')} bind:value={newRule.source} placeholder={$t('firewall.sourcePlaceholder')} />
		<Input
			label={$t('firewall.comment')}
			bind:value={newRule.comment}
			placeholder={$t('firewall.commentPlaceholder')}
			maxlength={64}
		/>
		<p class="text-sm text-content-secondary">{$t('firewall.addHint', { n: revertSeconds })}</p>
		{#if addError}
			<Alert variant="error">{addError}</Alert>
		{/if}
	</form>
	<svelte:fragment slot="footer">
		<Button variant="secondary" type="button" disabled={busy} on:click={() => (showAdd = false)}>{$t('common.cancel')}</Button>
		<Button variant="primary" type="submit" form="firewall-add-rule" disabled={busy}>
			{busy ? $t('firewall.adding') : $t('firewall.addRule')}
		</Button>
	</svelte:fragment>
</Modal>
