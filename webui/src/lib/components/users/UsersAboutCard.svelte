<script lang="ts">
	import { Card } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { MIN_PASSWORD_LENGTH } from './password';
</script>

<!-- What the roles, passwords, SMB and two-factor mean on this page. -->
<Card>
	<h2 class="section-title mb-4">{$t('users.about.title')}</h2>
	<ul class="space-y-2 text-sm text-secondary list-disc ml-5">
		<li>{$t('users.about.admins')}</li>
		<li>{$t('users.about.users')}</li>
		<li>{$t('users.about.self')}</li>
		<li>{$t('users.about.passwords', { n: MIN_PASSWORD_LENGTH })}</li>
		<li>{$t('users.about.smb')}</li>
		<li>{$t('users.about.twoFactor')}</li>
	</ul>
</Card>
