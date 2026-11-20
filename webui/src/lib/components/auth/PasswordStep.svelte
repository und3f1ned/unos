<script lang="ts">
	import { t } from '$lib/i18n';

	// The first step of the sign-in: the username and the password.

	type Props = {
		username: string;
		password: string;
	};

	let { username = $bindable(), password = $bindable() }: Props = $props();
</script>

<div>
	<label for="username" class="block text-sm font-medium text-content-secondary mb-1">
		{$t('common.username')}
	</label>
	<input type="text" id="username" bind:value={username} class="form-input" required />
</div>

<div>
	<label for="password" class="block text-sm font-medium text-content-secondary mb-1">
		{$t('common.password')}
	</label>
	<input type="password" id="password" bind:value={password} class="form-input" required />
</div>
