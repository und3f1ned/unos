<script lang="ts">
	import { messageOf } from '$lib/errors';
	import { onMount } from 'svelte';
	import {
		DEFAULT_GRACE_DAYS,
		GRACE_DAYS_CHOICES,
		disableTwoFactorRequirement,
		enableTwoFactorRequirement,
		getTwoFactorRequirement,
		type GraceDays,
		type TwoFactorRequirement
	} from '$lib/api/security';
	import { Alert, Badge, Button, Card, Input, Select } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { UsersRound } from 'lucide-svelte';

	// System > Security, administrators only: every account must have two-factor authentication,
	// after a grace period. Turning it on or off takes a code of the administrator's own
	// authenticator app, so an administrator without two-factor authentication is told to turn it on
	// first.

	type Props = {
		/** Whether the administrator's own account has two-factor authentication. */
		ownTwoFactor: boolean;
	};

	let { ownTwoFactor }: Props = $props();

	let requirement = $state<TwoFactorRequirement | null>(null);
	let loading = $state(false);
	let loadError = $state('');
	let grace = $state(String(DEFAULT_GRACE_DAYS));
	let code = $state('');
	let busy = $state(false);
	let error = $state('');
	let success = $state('');

	const graceOptions = $derived(
		GRACE_DAYS_CHOICES.map((n) => ({
			value: String(n),
			label: n === 0 ? $t('twofactor.requirement.graceNone') : $t('twofactor.requirement.graceDays', { n })
		}))
	);
	const since = $derived(formatDateTime(requirement?.enabled_at, $intlLocale));

	onMount(() => {
		void load();
	});

	async function load() {
		loading = true;
		try {
			requirement = await getTwoFactorRequirement();
			loadError = '';
		} catch (e) {
			loadError = messageOf(e);
		} finally {
			loading = false;
		}
	}

	async function submit() {
		if (!requirement || busy) return;
		busy = true;
		error = '';
		success = '';
		try {
			if (requirement.enabled) {
				requirement = await disableTwoFactorRequirement(code);
				success = $t('twofactor.requirement.turnedOff');
			} else {
				requirement = await enableTwoFactorRequirement(Number(grace) as GraceDays, code);
				success = $t('twofactor.requirement.turnedOn');
			}
			code = '';
		} catch (e) {
			error = messageOf(e);
		} finally {
			busy = false;
		}
	}
</script>

<Card>
	<div class="flex flex-wrap items-center justify-between gap-2 mb-4">
		<h3 class="card-title min-w-0">
			<UsersRound class="w-5 h-5 shrink-0" />
			{$t('twofactor.requirement.title')}
		</h3>
		{#if requirement}
			<Badge variant={requirement.enabled ? 'success' : 'neutral'}
				>{requirement.enabled ? $t('twofactor.requirement.on') : $t('twofactor.requirement.off')}</Badge
			>
		{/if}
	</div>

	{#if success}
		<Alert variant="success" class="mb-4">{success}</Alert>
	{/if}

	{#if loadError}
		<Alert variant="error" title={$t('twofactor.requirement.loadError')} class="mb-4">
			<p>{loadError}</p>
			<Button variant="secondary" size="sm" class="mt-3" {loading} onclick={load}>{$t('common.retry')}</Button>
		</Alert>
	{/if}

	{#if requirement}
		<div class="space-y-2 text-sm min-w-0 break-words">
			{#if requirement.enabled && !requirement.enabled_at}
				<!-- A restored database whose start cannot be read: the requirement holds without a
				     grace period (src/auth/requirement.py). -->
				<p>{$t('twofactor.requirement.sinceUnknown')}</p>
			{:else if requirement.enabled}
				<!-- The name the administrator had then, as the audit log keeps it. -->
				<p>{$t('twofactor.requirement.since', { date: since, name: requirement.enabled_by ?? '' })}</p>
				<p>
					{requirement.grace_days
						? $t('twofactor.requirement.grace', { n: requirement.grace_days })
						: $t('twofactor.requirement.noGrace')}
				</p>
			{:else}
				<p>{$t('twofactor.requirement.offText')}</p>
			{/if}
			<p class="text-content-secondary">{$t('twofactor.requirement.hint')}</p>
		</div>

		{#if !ownTwoFactor}
			<Alert variant="info" class="mt-4">{$t('twofactor.requirement.ownFirst')}</Alert>
		{:else}
			<form
				class="mt-4 flex flex-wrap items-end gap-3"
				onsubmit={(event) => {
					event.preventDefault();
					submit();
				}}
			>
				{#if !requirement.enabled}
					<Select
						id="two-factor-grace"
						label={$t('twofactor.requirement.graceLabel')}
						bind:value={grace}
						options={graceOptions}
						class="w-56 max-w-full"
					/>
				{/if}
				<Input
					id="two-factor-requirement-code"
					label={$t('twofactor.requirement.codeLabel')}
					bind:value={code}
					autocomplete="one-time-code"
					maxlength={6}
					class="w-56 max-w-full"
					required
				/>
				<Button
					type="submit"
					variant={requirement.enabled ? 'danger' : 'primary'}
					loading={busy}
					disabled={busy || code.length !== 6}
				>
					{requirement.enabled ? $t('twofactor.requirement.turnOff') : $t('twofactor.requirement.turnOn')}
				</Button>
			</form>
			{#if error}
				<Alert variant="error" class="mt-4">{error}</Alert>
			{/if}
		{/if}
	{/if}
</Card>
