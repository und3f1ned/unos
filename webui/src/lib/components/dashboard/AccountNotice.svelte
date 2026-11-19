<script lang="ts">
	import { UserCog } from 'lucide-svelte';
	import { Card } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	// A regular account's dashboard (ADR-0030): who is signed in, and where the account is managed.

	type Props = {
		username: string;
	};

	let { username }: Props = $props();
</script>

<Card>
	<div class="flex items-start gap-3">
		<UserCog class="w-5 h-5 text-info mt-0.5" />
		<div>
			<p class="font-medium">{$t('dashboard.user.signedInAs', { name: username })}</p>
			<p class="text-sm text-secondary">
				{$t('dashboard.user.managed')}
				{$t('dashboard.user.account')}
				<a href="/system#security" class="text-info hover:underline">{$t('dashboard.user.accountLink')}</a>.
			</p>
		</div>
	</div>
</Card>
