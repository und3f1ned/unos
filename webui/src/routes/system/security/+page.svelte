<script lang="ts">
	import { getTOTPStatus } from '$lib/api/auth';
	import { Alert, Button, Skeleton } from '$lib/components/ui';
	import {
		Certificates,
		CertificatesCard,
		FirewallCard,
		GenerateCertificateDialog,
		TwoFactorRequirementCard
	} from '$lib/components/system/security';
	import { t } from '$lib/i18n';
	import { followPage } from '$lib/pages/follow.svelte';
	import { settle, type Settled } from '$lib/pages/read';
	import type { SecuritySnapshot } from '$lib/pages/system/security';
	import type { TOTPStatus } from '$lib/api/auth';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// System > Security (ADR-0431): what applies to the whole NAS, for administrators: the
	// two-factor requirement of every account, the host's firewall and the certificate that HTTPS
	// serves. The administrator's own sign-in settings are on the Account page, every account's
	// sign-ins in the Audit Log. The tab's load reads them all (ADR-0469); each card reads again
	// after its own changes.

	// Turning the requirement on or off takes a code of the administrator's own authenticator app:
	// the card needs to know whether the account has one.
	let ownTwoFactor = $state.raw<Settled<TOTPStatus> | null>(null);
	// The tab's data have come: until then, on the tab's first page, its skeleton.
	let loaded = $state(false);
	let retrying = $state(false);
	let requirement = $state.raw<SecuritySnapshot['requirement'] | null>(null);
	let firewall = $state.raw<SecuritySnapshot['firewall'] | null>(null);
	let firewallAt = $state(0);
	// The certificates, shared by the card and its dialogs, which the tab draws after its content.
	const certificates = new Certificates();

	followPage(
		() => data,
		(snapshot) => apply(snapshot)
	);

	function apply(snapshot: SecuritySnapshot) {
		ownTwoFactor = snapshot.twoFactor;
		requirement = snapshot.requirement;
		firewall = snapshot.firewall;
		firewallAt = snapshot.firewallAt;
		certificates.apply(snapshot.certificates);
		loaded = true;
	}

	async function retryTwoFactor() {
		retrying = true;
		ownTwoFactor = await settle(getTOTPStatus());
		retrying = false;
	}
</script>

<div class="space-y-4">
	<h2 class="text-xl font-semibold">{$t('system.security.title')}</h2>

	{#if !loaded}
		<!-- The tab's first page while its data take longer than a moment (ADR-0469): its three cards. -->
		<div class="space-y-4" aria-busy="true">
			{#each [0, 1, 2] as card (card)}
				<Skeleton shape="card" count={3} />
			{/each}
		</div>
	{:else if ownTwoFactor?.ok && requirement}
		<TwoFactorRequirementCard ownTwoFactor={ownTwoFactor.value.enabled} read={requirement} />
	{:else if ownTwoFactor && !ownTwoFactor.ok}
		<Alert variant="error" title={$t('account.totp.statusError')}>
			<p>{ownTwoFactor.error}</p>
			<Button variant="secondary" size="sm" class="mt-3" loading={retrying} onclick={retryTwoFactor}
				>{$t('common.retry')}</Button
			>
		</Alert>
	{/if}

	{#if firewall}
		<FirewallCard read={firewall} readAt={firewallAt} />
		<CertificatesCard {certificates} />
	{/if}
</div>

<GenerateCertificateDialog {certificates} />
