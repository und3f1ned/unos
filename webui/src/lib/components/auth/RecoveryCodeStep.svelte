<script lang="ts">
	import { KeyRound } from 'lucide-svelte';
	import { Input } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The code step of the sign-in with a recovery code in place of the authenticator app's code
	// (ADR-0238).

	type Props = {
		code: string;
	};

	let { code = $bindable() }: Props = $props();
</script>

<div class="text-center mb-4">
	<KeyRound class="w-12 h-12 text-status-info mx-auto mb-2" />
	<h2 class="text-lg font-semibold">{$t('login.recoveryTitle')}</h2>
	<p class="text-sm text-secondary">
		{$t('login.recoveryHint')}
	</p>
</div>

<Input
	id="recovery-code"
	label={$t('login.recoveryLabel')}
	bind:value={code}
	placeholder={$t('login.recoveryPlaceholder')}
	autocomplete="off"
	maxlength={32}
	required
/>
