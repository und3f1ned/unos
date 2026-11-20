<script lang="ts">
	import { Eye, EyeOff } from 'lucide-svelte';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// The first step of the sign-in: the username and the password, which Show reveals so that a
	// long one can be checked before it is sent.

	type Props = {
		username: string;
		password: string;
	};

	let { username = $bindable(), password = $bindable() }: Props = $props();

	let shown = $state(false);
</script>

<div>
	<label for="username" class="form-label">{$t('common.username')}</label>
	<input type="text" id="username" bind:value={username} class="form-input" autocomplete="username" required />
</div>

<div>
	<div class="flex items-end justify-between gap-2">
		<label for="password" class="form-label">{$t('common.password')}</label>
		<!-- Its name is its words, never a label: getByLabel('Password') finds only the field. -->
		<Button
			variant="ghost"
			size="sm"
			class="-mr-2 mb-0.5"
			icon={shown ? EyeOff : Eye}
			pressed={shown}
			aria-controls="password"
			onclick={() => (shown = !shown)}
		>
			{shown ? $t('login.hidePassword') : $t('login.showPassword')}
		</Button>
	</div>
	<input
		type={shown ? 'text' : 'password'}
		id="password"
		bind:value={password}
		class="form-input"
		autocomplete="current-password"
		required
	/>
</div>
