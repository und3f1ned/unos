<script lang="ts">
	import { ShieldCheck, Usb } from 'lucide-svelte';
	import { Alert, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { securityKeysSupported } from '$lib/webauthn';

	// The code step of the sign-in: the authenticator app's code, or a touch of a security key
	// where the account's keys work (ADR-0409).

	type Props = {
		code: string;
		/** The account has security keys that work at this address. */
		keysHere: boolean;
		/** Where the account's keys work, when not here. */
		keyOrigins: string[];
		/** A key is being touched. */
		usingKey: boolean;
		/** A sign-in is being sent. */
		loading: boolean;
		onusekey: () => void;
	};

	let { code = $bindable(), keysHere, keyOrigins, usingKey, loading, onusekey }: Props = $props();
</script>

<!-- TOTP Input -->
<div class="text-center mb-4">
	<ShieldCheck class="w-12 h-12 text-brand-ring mx-auto mb-2" />
	<h2 class="text-lg font-semibold">{$t('login.totpTitle')}</h2>
	<p class="text-sm text-content-secondary">
		{$t('login.totpHint')}
	</p>
</div>

<!-- A security key in place of the code (ADR-0409), where the account's keys work. -->
{#if keysHere && securityKeysSupported()}
	<Button variant="secondary" class="w-full" icon={Usb} loading={usingKey} disabled={loading} onclick={onusekey}>
		{usingKey ? $t('securitykeys.login.touch') : $t('securitykeys.login.use')}
	</Button>
	<p class="text-center text-sm text-content-muted">{$t('securitykeys.login.or')}</p>
{:else if keysHere}
	<Alert variant="info">{$t('securitykeys.unsupported')}</Alert>
{:else if keyOrigins.length > 0}
	<Alert variant="info">{$t('securitykeys.login.elsewhere', { origins: keyOrigins.join(', ') })}</Alert>
{/if}

<div>
	<label for="totp" class="block text-sm font-medium text-content-secondary mb-1">
		{$t('login.totpLabel')}
	</label>
	<input
		type="text"
		id="totp"
		bind:value={code}
		class="form-input text-center text-2xl tracking-widest"
		placeholder="000000"
		maxlength="6"
		pattern="[0-9]*"
		inputmode="numeric"
		autocomplete="one-time-code"
		required
	/>
</div>
